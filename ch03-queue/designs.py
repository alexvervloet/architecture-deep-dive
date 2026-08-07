"""
ch03/designs.py: three ways to answer a request that takes seconds.

The shape every web framework teaches is: accept a connection, do the work,
write the response, close. It is correct for a 20ms database query and it
stops being correct somewhere around a second, which is where every LLM
request starts.

Three designs, all with the same worker capacity and the same workload:

  sync    hold the connection until the answer is ready
  queue   accept, enqueue, return a job id, let the client come back
  shed    hold the connection, but refuse work when the queue is too deep

The thing that makes this chapter LLM-specific is not that requests are slow.
It is that they are slow *and* each one has already cost money by the time the
client gives up. A dropped CRUD request wastes a few milliseconds of CPU. A
dropped agent turn wastes tokens that are already billed, and in every design
below except one, the work carries on running after the only person who wanted
it has left.
"""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

from app import service

# Same prices as ch01, so cost numbers are comparable across chapters.
PRICE_IN_PER_1K = 0.00025
PRICE_OUT_PER_1K = 0.00125

# A connection is not free to hold even when idle: it is a socket, a slot in a
# pool, and a row in someone's concurrency limit. Charging a flat cost per
# round trip lets the "how long was a connection held" column mean something
# without pretending to model TCP.
SUBMIT_MS = 1.0
POLL_MS = 1.0
POLL_INTERVAL_S = 0.25


def cost_of(answer: service.Answer) -> float:
    return (
        answer.prompt_tokens / 1000 * PRICE_IN_PER_1K
        + answer.completion_tokens / 1000 * PRICE_OUT_PER_1K
    )


@dataclass
class Outcome:
    request_id: str
    status: str  # "answered" | "abandoned" | "rejected"
    time_to_answer_ms: float  # for abandoned: how long the client waited before quitting
    connection_ms: float  # how long this request occupied a server connection
    cost_usd: float = 0.0
    wasted_usd: float = 0.0  # spent on work nobody received
    recoverable: bool = False  # could the client come back later and get it?


@dataclass
class Stats:
    label: str
    outcomes: list[Outcome] = field(default_factory=list)
    wall_ms: float = 0.0
    peak_connections: int = 0

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    def percentile(self, p: float, statuses: tuple[str, ...] = ("answered", "abandoned")) -> float:
        """Time to outcome, including the requests that gave up waiting.

        Abandoned requests are in by default and rejected ones are out, and
        both choices matter. Excluding abandonments computes the latency of the
        requests that happened to be fast enough not to time out, a number that
        improves as the system gets worse. Including rejections would flatter
        shedding, whose whole trick is answering "no" in a millisecond.
        """
        values = sorted(o.time_to_answer_ms for o in self.outcomes if o.status in statuses)  # noqa: E501
        if not values:
            return 0.0
        rank = max(1, min(len(values), int(round(p / 100.0 * len(values)))))
        return values[rank - 1]

    @property
    def spent_usd(self) -> float:
        return sum(o.cost_usd for o in self.outcomes)

    @property
    def wasted_usd(self) -> float:
        return sum(o.wasted_usd for o in self.outcomes)

    @property
    def recoverable_count(self) -> int:
        """How many of these requests have an answer someone can still collect.

        The column that separates "not yet" from "gone". A sync server that
        answered 15 of 32 has destroyed the other 17, tokens and all; a queue
        that answered 15 of 32 within the deadline still holds all 32.
        """
        return sum(1 for o in self.outcomes if o.status == "answered" or o.recoverable)

    @property
    def connection_seconds(self) -> float:
        return sum(o.connection_ms for o in self.outcomes) / 1000.0


class _ConnectionGauge:
    """Tracks how many clients are holding a connection at once."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current = 0
        self.peak = 0

    def open(self) -> None:
        with self._lock:
            self.current += 1
            self.peak = max(self.peak, self.current)

    def close(self) -> None:
        with self._lock:
            self.current -= 1


def _work(request_id: str, question: str) -> service.Answer:
    return service.handle(question, request_id=request_id, session_id=request_id)


class SyncServer:
    """Hold the connection until the work is done.

    Note what happens when a client gives up: nothing. The server has no idea,
    the worker keeps going, the tokens keep being billed, and the capacity
    stays occupied. That last part is the one that turns a slow day into an
    outage, because the abandoned work is competing with the requests that
    still have someone waiting for them.
    """

    label = "sync (hold the connection)"

    def __init__(self, capacity: int) -> None:
        self.pool = ThreadPoolExecutor(max_workers=capacity)
        self.gauge = _ConnectionGauge()

    def run(self, request_id: str, question: str, deadline_s: float) -> tuple[Outcome, Future]:
        started = time.perf_counter()
        self.gauge.open()
        future = self.pool.submit(_work, request_id, question)
        try:
            answer = future.result(timeout=deadline_s)
            elapsed = (time.perf_counter() - started) * 1000.0
            self.gauge.close()
            return (
                Outcome(request_id, "answered", elapsed, elapsed, cost_of(answer)),
                future,
            )
        except TimeoutError:
            elapsed = (time.perf_counter() - started) * 1000.0
            self.gauge.close()
            # The client is gone. The work is not: `future` is still running,
            # and whatever it spends is charged to nobody's benefit.
            return (
                Outcome(request_id, "abandoned", elapsed, elapsed, recoverable=False),
                future,
            )

    def shutdown(self) -> None:
        self.pool.shutdown(wait=True)


class QueuedServer:
    """Accept, enqueue, hand back a job id, let the client come back for it.

    The client's connection is held for a millisecond instead of seconds, and
    the result outlives the caller: a browser refresh, a dropped mobile
    connection, or a client that simply stops waiting no longer destroys work
    that has already been paid for.

    What it costs is not latency. It is that the client protocol changes:
    someone has to write the polling (or the websocket, or the webhook), and
    "the answer is not ready yet" becomes a state the UI must have.
    """

    label = "queue (accept, then poll)"

    def __init__(self, capacity: int) -> None:
        self.pool = ThreadPoolExecutor(max_workers=capacity)
        self.results: dict[str, service.Answer] = {}
        self.lock = threading.Lock()
        self.gauge = _ConnectionGauge()
        self.inbox: queue.Queue = queue.Queue()

    def _run_and_store(self, request_id: str, question: str) -> None:
        answer = _work(request_id, question)
        with self.lock:
            self.results[request_id] = answer

    def run(self, request_id: str, question: str, deadline_s: float) -> tuple[Outcome, Future]:
        started = time.perf_counter()
        self.gauge.open()
        future = self.pool.submit(self._run_and_store, request_id, question)
        self.gauge.close()
        connection_ms = SUBMIT_MS

        # The client polls. Each poll is a short round trip, not a held socket.
        while True:
            time.sleep(POLL_INTERVAL_S)
            self.gauge.open()
            with self.lock:
                answer = self.results.get(request_id)
            self.gauge.close()
            connection_ms += POLL_MS
            if answer is not None:
                elapsed = (time.perf_counter() - started) * 1000.0
                return (
                    Outcome(
                        request_id,
                        "answered",
                        elapsed,
                        connection_ms,
                        cost_of(answer),
                        recoverable=True,
                    ),
                    future,
                )
            if (time.perf_counter() - started) > deadline_s:
                elapsed = (time.perf_counter() - started) * 1000.0
                # The client stopped polling, but the job keeps its result. It
                # is still there when anyone asks again, which is what makes
                # this "not yet" rather than "gone".
                return (
                    Outcome(
                        request_id,
                        "abandoned",
                        elapsed,
                        connection_ms,
                        recoverable=True,
                    ),
                    future,
                )

    def shutdown(self) -> None:
        self.pool.shutdown(wait=True)


class SheddingServer:
    """Hold the connection, but say no early when the queue is too deep.

    The unfashionable option, and often the right one for anything a human is
    waiting on. It trades throughput for a promise: if you are accepted, you
    get an answer quickly, and if you are not, you find out immediately and
    can decide for yourself whether to come back.

    A rejection is not a failure in the same sense an abandonment is. Nobody
    paid for it.
    """

    label = "shed (refuse when full)"

    def __init__(self, capacity: int, max_queue_depth: int) -> None:
        self.pool = ThreadPoolExecutor(max_workers=capacity)
        self.capacity = capacity
        self.max_inflight = capacity + max_queue_depth
        self.inflight = 0
        self.lock = threading.Lock()
        self.gauge = _ConnectionGauge()

    def run(self, request_id: str, question: str, deadline_s: float) -> tuple[Outcome, Future | None]:
        with self.lock:
            if self.inflight >= self.max_inflight:
                # Immediate, cheap, honest. HTTP 503 with a Retry-After.
                return Outcome(request_id, "rejected", 0.0, SUBMIT_MS), None
            self.inflight += 1

        started = time.perf_counter()
        self.gauge.open()
        future = self.pool.submit(_work, request_id, question)
        try:
            answer = future.result(timeout=deadline_s)
            elapsed = (time.perf_counter() - started) * 1000.0
            return Outcome(request_id, "answered", elapsed, elapsed, cost_of(answer)), future
        except TimeoutError:
            elapsed = (time.perf_counter() - started) * 1000.0
            return Outcome(request_id, "abandoned", elapsed, elapsed, recoverable=False), future
        finally:
            self.gauge.close()
            with self.lock:
                self.inflight -= 1

    def shutdown(self) -> None:
        self.pool.shutdown(wait=True)
