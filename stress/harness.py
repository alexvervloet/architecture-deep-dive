"""
stress/harness.py: run a workload under pressure and report what happened.

Every ADR in this repo cites numbers from this file, so its job is to be
boring and honest rather than clever.

Three things it does that a naive load loop does not:

**It reports simulated and measured latency side by side.** The simulated
number is exactly reproducible; the measured one never is, because
`time.sleep` and the OS scheduler are involved. If a chapter's claim rests on
the measured number alone, the claim is not reproducible, and the reader can
see that directly instead of taking the author's word for it.

**It distinguishes four outcomes, not two.** ok / failed is not enough for
this repo. A request can succeed with the wrong sources (the fallback that
answers from nothing), time out (work possibly still running upstream, money
possibly still spent), or fail outright. Chapter 6 exists because those are
different, and a harness that collapses them into a success rate would make
its central argument invisible.

**It refuses to average the thing that matters.** Mean latency is reported
because people ask for it, but p50/p95/p99 are what the tables use. The reason
queueing chapters exist is the tail, and a mean hides the tail by
construction.
"""

from __future__ import annotations

import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Sequence

from app import providers
from app.service import Answer

# Threads, not processes or asyncio, for one reason: the mock's latency is
# `time.sleep`, which releases the GIL, so a thread pool models a concurrent
# server accurately enough for these measurements while staying readable. It
# would be wrong for CPU-bound work. It is not wrong here, and ch03 says so
# explicitly rather than leaving the reader to wonder.
DEFAULT_WORKERS = 16


@dataclass
class Outcome:
    request_id: str
    status: str  # "ok" | "wrong_source" | "timeout" | "error"
    latency_ms: float
    simulated_ms: float
    detail: str = ""


@dataclass
class Report:
    label: str
    outcomes: list[Outcome] = field(default_factory=list)
    wall_ms: float = 0.0
    concurrency: int = 0

    def _lat(self, statuses: Sequence[str] | None = None) -> list[float]:
        pool = self.outcomes if statuses is None else [o for o in self.outcomes if o.status in statuses]
        return sorted(o.latency_ms for o in pool)

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def success_rate(self) -> float:
        """Answered at all, correctly or not. Deliberately NOT the headline
        number: see `correct_rate`, and see ch06 for why reporting only this
        one would be dishonest."""
        return (self.count("ok") + self.count("wrong_source")) / max(1, self.total)

    @property
    def correct_rate(self) -> float:
        """Answered from the right document. The number that survives contact
        with a fallback strategy."""
        return self.count("ok") / max(1, self.total)

    def percentile(self, p: float, statuses: Sequence[str] | None = None) -> float:
        """Latency percentile over completed requests.

        Timeouts are included by default, and they should be: excluding them
        computes the latency of the requests that were fast enough not to time
        out, which is a number that only ever improves as your system gets
        worse.
        """
        values = self._lat(statuses)
        if not values:
            return 0.0
        if len(values) == 1:
            return values[0]
        # Nearest-rank. With 50 samples there is no meaningful difference
        # between interpolation methods, and nearest-rank is the one a reader
        # can verify by hand against the raw outcomes.
        rank = max(1, min(len(values), int(round(p / 100.0 * len(values)))))
        return values[rank - 1]

    @property
    def throughput_rps(self) -> float:
        return self.total / max(1e-9, self.wall_ms / 1000.0)

    def summary(self) -> str:
        lines = [
            f"{self.label}",
            f"  requests      {self.total} at concurrency {self.concurrency}",
            f"  ok            {self.count('ok')}"
            f"   wrong-source {self.count('wrong_source')}"
            f"   timeout {self.count('timeout')}"
            f"   error {self.count('error')}",
            f"  correct rate  {self.correct_rate:.0%}   (answered-at-all: {self.success_rate:.0%})",
            f"  latency ms    p50 {self.percentile(50):.0f}"
            f"   p95 {self.percentile(95):.0f}"
            f"   p99 {self.percentile(99):.0f}"
            f"   max {max(self._lat() or [0]):.0f}",
            f"  simulated ms  sum {sum(o.simulated_ms for o in self.outcomes):.0f}"
            f"   (exactly reproducible; the p-values above are not)",
            f"  wall clock    {self.wall_ms:.0f}ms   throughput {self.throughput_rps:.1f} req/s",
        ]
        return "\n".join(lines)


def classify(answer: Answer, expected_source: str = "") -> Outcome:
    """Turn one Answer into one Outcome.

    `wrong_source` is the interesting bucket. It catches the case that makes
    naive availability numbers lie: the app was up, it replied promptly, and
    the reply came from a document that cannot support it.
    """
    if answer.error.startswith("ProviderTimeout"):
        status = "timeout"
    elif answer.error:
        status = "error"
    elif expected_source and expected_source not in answer.sources:
        status = "wrong_source"
    else:
        status = "ok"
    return Outcome(
        request_id=answer.request_id,
        status=status,
        latency_ms=answer.timings.total_ms,
        simulated_ms=answer.timings.simulated_ms,
        detail=answer.error or ("sources=" + ",".join(answer.sources)),
    )


def run(
    label: str,
    task: Callable[[int], Outcome],
    *,
    count: int = 50,
    concurrency: int = DEFAULT_WORKERS,
) -> Report:
    """Fire `count` requests through `task`, `concurrency` at a time.

    `task` takes the request index and returns an Outcome. Keeping the harness
    ignorant of what the task actually does is what lets ch03 hand it a queued
    variant and ch09 hand it a leak test without either chapter forking this
    file.
    """
    providers.warn_if_real_provider_for_stress()
    report = Report(label=label, concurrency=concurrency)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        report.outcomes = list(pool.map(task, range(count)))
    report.wall_ms = (time.perf_counter() - started) * 1000.0
    return report


def compare(*reports: Report) -> str:
    """One table, one row per variant. The shape every chapter's ADR quotes."""
    header = (
        f"{'variant':<28} {'correct':>8} {'answered':>9} {'p50':>7} {'p95':>8} "
        f"{'p99':>8} {'timeout':>8} {'error':>6}"
    )
    rows = [header, "-" * len(header)]
    for r in reports:
        rows.append(
            f"{r.label:<28} {r.correct_rate:>7.0%} {r.success_rate:>8.0%} "
            f"{r.percentile(50):>7.0f} {r.percentile(95):>8.0f} {r.percentile(99):>8.0f} "
            f"{r.count('timeout'):>8} {r.count('error'):>6}"
        )
    return "\n".join(rows)


def mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0
