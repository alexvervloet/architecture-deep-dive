#!/usr/bin/env python3
"""
stress.py: the same three designs, below capacity and then above it.

    python ch03-queue/stress.py        # offline, no key, ~22 seconds

Two scenarios, because a chapter that only shows the overload case is
advertising, not measurement:

  **Below capacity.** Fewer requests in flight than workers. Every design
  answers everything, quickly. Sync wins on simplicity and there is no
  argument to have.

  **Above capacity.** A burst four times the pool size, which is an ordinary
  Monday for anything with a marketing page. This is where the shapes separate,
  and where the number that matters is not latency.

A note on reproducibility. Each request's *service time* is deterministic
(derived from its id, see app/determinism.py), but queueing delay is emergent:
it depends on how threads actually interleave. This chapter was written
expecting the counts to wobble between runs and they did not. Two runs gave
identical answered, abandoned, rejected, spend, waste, and connection-second
figures, with p50/p95 differing by a handful of milliseconds. One burst against
fixed capacity with fixed service times turns out to schedule almost
identically every time. Treat that as a property of this workload rather than
a promise: the millisecond columns are still wall clock.
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

CHAPTER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(CHAPTER))  # repo root, for `app`
sys.path.insert(0, CHAPTER)  # chapter-local modules

from dotenv import load_dotenv  # noqa: E402

from app import providers, service  # noqa: E402
from designs import (  # noqa: E402
    QueuedServer,
    SheddingServer,
    Stats,
    SyncServer,
    cost_of,
)

load_dotenv()

CAPACITY = 8
DEADLINE_S = 3.0
QUESTIONS = service.QUESTIONS


def drive(server, count: int, label: str) -> Stats:
    """Fire `count` requests at a server, all at once, each with its own client."""
    stats = Stats(label=label)
    futures = []
    started = time.perf_counter()

    # One thread per client, because a client waiting on a slow response is
    # exactly what we are measuring the cost of.
    with ThreadPoolExecutor(max_workers=count) as clients:
        submissions = [
            clients.submit(server.run, f"q{i:03d}", QUESTIONS[i % len(QUESTIONS)], DEADLINE_S)
            for i in range(count)
        ]
        for submission in submissions:
            outcome, work_future = submission.result()
            stats.outcomes.append(outcome)
            futures.append((outcome, work_future))

    stats.wall_ms = (time.perf_counter() - started) * 1000.0

    # Now settle what the abandoned work actually cost. The client left; the
    # worker did not stop. Anything that completes from here on is spend with
    # no recipient.
    for outcome, work_future in futures:
        if outcome.status != "abandoned" or work_future is None:
            continue
        try:
            answer = work_future.result(timeout=120)
        except Exception:
            continue
        if answer is None:  # QueuedServer stores its result rather than returning it
            stored = server.results.get(outcome.request_id)
            if stored is not None:
                outcome.cost_usd = cost_of(stored)
            continue
        outcome.cost_usd = cost_of(answer)
        if not outcome.recoverable:
            outcome.wasted_usd = outcome.cost_usd

    stats.peak_connections = server.gauge.peak
    server.shutdown()
    return stats


def table(title: str, rows: list[Stats], note: str) -> None:
    print(f"\n{title}\n")
    header = (
        f"  {'design':<28} {'answered':>9} {'abandoned':>10} {'rejected':>9}"
        f" {'p50 ms':>8} {'p95 ms':>8} {'spent':>9} {'wasted':>9} {'conn-s':>8}"
        f" {'recoverable':>12}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for s in rows:
        print(
            f"  {s.label:<28} {s.count('answered'):>9} {s.count('abandoned'):>10}"
            f" {s.count('rejected'):>9} {s.percentile(50):>8.0f} {s.percentile(95):>8.0f}"
            f" ${s.spent_usd:>8.5f} ${s.wasted_usd:>8.5f} {s.connection_seconds:>8.1f}"
            f" {s.recoverable_count:>7}/{s.total:<4}"
        )
    print(f"\n  {note}")


def scenario(count: int, title: str, note: str) -> list[Stats]:
    rows = []
    for build in (
        lambda: SyncServer(CAPACITY),
        lambda: QueuedServer(CAPACITY),
        lambda: SheddingServer(CAPACITY, max_queue_depth=CAPACITY),
    ):
        service.reset_all()
        providers.configure_mock(latency="slow", seed=1337)
        server = build()
        rows.append(drive(server, count, server.label))
    table(title, rows, note)
    return rows


def main() -> int:
    providers.configure_mock(latency="slow", seed=1337)
    print(f"Provider: {providers.describe()}")
    print(
        f"Worker capacity {CAPACITY}, client deadline {DEADLINE_S:.0f}s, "
        f"'slow' model profile (~1.3s mean, 5% tail at +4s)"
    )

    scenario(
        CAPACITY // 2,
        f"BELOW CAPACITY: {CAPACITY // 2} concurrent requests against {CAPACITY} workers",
        "Everything answers, fast, everywhere. Nothing here argues for a queue.",
    )

    rows = scenario(
        CAPACITY * 4,
        f"ABOVE CAPACITY: a burst of {CAPACITY * 4} against {CAPACITY} workers",
        "Same code, same workers, four times the arrivals.",
    )

    sync, queued, shed = rows
    print("\n\nWhat the burst actually cost\n")
    print(
        f"  sync   answered {sync.count('answered')}/{sync.total} and destroyed the rest:"
        f" ${sync.wasted_usd:.5f} of work finished"
        f"\n         after its client had gone. Held {sync.connection_seconds:.0f}"
        f" connection-seconds."
    )
    print(
        f"  queue  answered {queued.count('answered')}/{queued.total} inside the same deadline,"
        f" wasted ${queued.wasted_usd:.5f}, and still holds"
        f"\n         {queued.recoverable_count}/{queued.total} answers for whoever asks again."
        f" Held {queued.connection_seconds:.1f} connection-seconds,"
        f"\n         about {sync.connection_seconds / max(0.1, queued.connection_seconds):.0f}x"
        f" less than sync."
    )
    print(
        f"  shed   answered {shed.count('answered')}/{shed.total} for ${shed.spent_usd:.5f},"
        f" roughly half what sync spent to answer"
        f"\n         the same number, and refused {shed.count('rejected')} in about a"
        f" millisecond each. Nobody waited 3s to be told no."
    )
    print(
        "\n  The interesting column is not p95. It is 'wasted': money already spent on"
        "\n  answers that no longer have anyone to go to. Under sync that spend rises with"
        "\n  load, and the abandoned work keeps occupying the same workers the surviving"
        "\n  requests are queued behind."
    )
    print(
        "\n  Queueing did not make anything faster. It made the work survivable, and it"
        "\n  moved the cost into the client protocol, which now has to know about"
        "\n  'not ready yet'."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
