"""
ch02/runner.py: real worker processes, and a router that decides where a
request lands.

Threads would not do here. Threads share `_MEMORY`, so an in-process dict would
look perfectly correct and the chapter would prove the opposite of the truth.
The bug requires separate address spaces, so these are separate OS processes,
started with spawn (the macOS default), each importing the app fresh and each
getting its own dict.

That is also the honest reason this bug survives so long in real projects: on
one laptop, with one worker, the design is correct. It becomes wrong at the
moment you scale out, which is the moment nobody is reading this code.
"""

from __future__ import annotations

import multiprocessing as mp
import os
from dataclasses import dataclass

from app import providers, retrieval
from stores import (  # noqa: F401
    DESIGNS,
    Design,
    SqliteStore,
    db_path_for,
    make_store,
    route,
)


@dataclass
class Request:
    request_index: int
    session_id: str
    question: str
    expect_doc: str
    turn: int  # 0 = opener, 1 = follow-up


@dataclass
class Response:
    request_index: int
    session_id: str
    turn: int
    worker: int
    text: str
    sources: tuple[str, ...]
    history_turns: int
    store_ms: float
    correct: bool


def worker_loop(worker_id: int, store_kind: str, db_path: str, seed: int, inbox, outbox) -> None:
    """One worker process: its own imports, its own memory, its own store handle."""
    import conversation  # imported here so the child owns its module state

    providers.configure_mock(latency="instant", seed=seed)
    retrieval.reset_retrieval()
    store = make_store(store_kind, db_path)

    while True:
        item = inbox.get()
        if item is None:
            store.close()
            return
        request: Request = item
        result = conversation.handle_turn(
            store,
            request.session_id,
            request.question,
            request_id=f"r{request.request_index:04d}",
        )
        outbox.put(
            Response(
                request_index=request.request_index,
                session_id=request.session_id,
                turn=request.turn,
                worker=worker_id,
                text=result.text,
                sources=result.sources,
                history_turns=result.history_turns,
                store_ms=result.store_ms,
                correct=conversation.answered_from(result.text, request.expect_doc),
            )
        )


class Fleet:
    """A pool of worker processes plus the routing policy in front of them."""

    def __init__(self, design: Design, workers: int, seed: int, tag: str) -> None:
        self.design = design
        self.workers = workers
        self.seed = seed
        self.db_path = db_path_for(tag)
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.db_path + suffix):
                os.remove(self.db_path + suffix)
        if design.store == "sqlite":
            SqliteStore.initialize(self.db_path)
        self.ctx = mp.get_context("spawn")
        # A Manager queue, not a plain mp.Queue, and this is not a style choice.
        # `restart()` hard-kills a worker. A plain Queue is shared memory plus a
        # lock, and a child terminated while its feeder thread is mid-write to
        # the *shared* outbox leaves that lock held forever, so every later
        # collect() blocks and the whole run hangs with no error. It is
        # intermittent, which is worse: this hung on the third run after passing
        # twice. A Manager queue is proxied by its own server process, so a dead
        # client cannot corrupt it. See LESSONS.md.
        self.manager = mp.Manager()
        self.outbox = self.manager.Queue()
        self.inboxes: list = []
        self.procs: list = []
        for worker_id in range(workers):
            self._start(worker_id)

    def _start(self, worker_id: int) -> None:
        inbox = self.ctx.Queue()
        proc = self.ctx.Process(
            target=worker_loop,
            args=(worker_id, self.design.store, self.db_path, self.seed, inbox, self.outbox),
            daemon=True,
        )
        proc.start()
        if worker_id < len(self.inboxes):
            self.inboxes[worker_id] = inbox
            self.procs[worker_id] = proc
        else:
            self.inboxes.append(inbox)
            self.procs.append(proc)

    def restart(self, worker_id: int) -> None:
        """Kill a worker and bring up a replacement with empty memory.

        This is not an exotic fault. It is a deploy, an OOM kill, a scale-in
        event, or a container reschedule: the most routine thing that happens
        to a production process.
        """
        self.procs[worker_id].terminate()
        self.procs[worker_id].join(timeout=5)
        self._start(worker_id)

    def send(self, request: Request) -> int:
        worker_id = route(
            self.design.routing, request.session_id, request.request_index, self.workers, self.seed
        )
        self.inboxes[worker_id].put(request)
        return worker_id

    def collect(self, count: int, timeout: float = 60.0) -> list[Response]:
        """Wait for `count` responses, and fail loudly rather than hang.

        A missing response means a worker died holding work, which is a bug in
        this harness and not a finding about the design under test. Blocking
        forever would have let that masquerade as a slow run.
        """
        out = []
        for i in range(count):
            try:
                out.append(self.outbox.get(timeout=timeout))
            except Exception as exc:
                raise RuntimeError(
                    f"only {i}/{count} responses came back for {self.design.label} "
                    f"with {self.workers} workers: a worker died holding work ({exc})"
                ) from None
        return out

    def shutdown(self) -> None:
        for inbox in self.inboxes:
            inbox.put(None)
        for proc in self.procs:
            proc.join(timeout=5)
            if proc.is_alive():
                proc.terminate()
        self.manager.shutdown()
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.db_path + suffix):
                os.remove(self.db_path + suffix)


def build_requests(rounds: int, conversations) -> list[Request]:
    """Openers and follow-ups for `rounds` sessions per conversation template.

    Openers for every session go first, then follow-ups. That models concurrent
    users rather than one person talking twice, and it matters: it is what
    makes a session's two turns land on unrelated workers under a router that
    does not know about sessions.
    """
    sessions = []
    for r in range(rounds):
        for i, exchange in enumerate(conversations):
            sessions.append((f"s{r}-{i}", exchange))
    requests: list[Request] = []
    index = 0
    for turn in (0, 1):
        for session_id, exchange in sessions:
            question = exchange.opener if turn == 0 else exchange.followup
            requests.append(
                Request(
                    request_index=index,
                    session_id=session_id,
                    question=question,
                    expect_doc=exchange.gold_doc,
                    turn=turn,
                )
            )
            index += 1
    return requests
