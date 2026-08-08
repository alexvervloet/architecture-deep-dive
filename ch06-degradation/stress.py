#!/usr/bin/env python3
"""
stress.py: break each dependency in turn, and grade what comes out.

    python ch06-degradation/stress.py     # offline, no key, ~50 seconds

Six scenarios, three designs, and two columns that disagree with each other:

    answered      what an uptime dashboard sees
    exact         what the user actually needed

The fourth fault is the one that matters. A dead dependency fails in
milliseconds and is cheap to keep retrying. A dependency that is merely *slow*
holds a worker for the whole client deadline on every request, and the fallback
tier is not even reached until that deadline has already been spent. The M1
harness noticed this before this chapter existed: same 0% correct, 140ms
against 15,180ms.
"""

from __future__ import annotations

import os
import sys

CHAPTER = os.path.dirname(os.path.abspath(__file__))
# Repo root goes *before* the chapter directory, and this file is the reason:
# it is called stress.py and the repo's shared harness lives in a package
# called stress. Python already puts the script's own directory at sys.path[0],
# so `from stress import harness` would import this file. Inserting the repo
# root ahead of it resolves the name to the package; the chapter directory
# stays on the path behind it, which is how `designs` is still found.
sys.path.insert(0, os.path.dirname(CHAPTER))

from dotenv import load_dotenv  # noqa: E402

from app import providers, retrieval, service  # noqa: E402
from designs import BREAKER, DESIGNS, grade, healthy_answers  # noqa: E402
from stress import harness  # noqa: E402

load_dotenv()

COUNT = 24
CONCURRENCY = 8
QUESTIONS = service.QUESTIONS
TIMEOUT_MS = 2000.0

GRADES = ("exact", "supported", "unsupported", "timeout", "error")


def build_task(handler, key: dict[str, str]):
    def task(i: int) -> harness.Outcome:
        question = QUESTIONS[i % len(QUESTIONS)]
        reply = handler(question, f"r{i:03d}", TIMEOUT_MS)
        return harness.Outcome(
            request_id=f"r{i:03d}",
            status=grade(reply, question, key[question]),
            latency_ms=reply.latency_ms,
            simulated_ms=0.0,
            detail=reply.degraded or reply.error,
        )

    return task


def scenario(name: str, setup, key: dict[str, str], note: str, count: int = COUNT) -> None:
    print(f"\n{name}\n")
    header = (
        f"  {'design':<18} {'answered':>9} {'exact':>7} {'supported':>10}"
        f" {'unsupported':>12} {'timeout':>8} {'error':>6} {'p95 ms':>8} {'wall':>7}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for label, handler in DESIGNS:
        service.reset_all()
        BREAKER.reset()
        providers.configure_mock(latency="instant", seed=1337)
        setup()
        report = harness.run(label, build_task(handler, key), count=count, concurrency=CONCURRENCY)
        counts = {g: sum(1 for o in report.outcomes if o.status == g) for g in GRADES}
        answered = counts["exact"] + counts["supported"] + counts["unsupported"]
        print(
            f"  {label:<18} {answered:>4}/{count:<4} {counts['exact']:>7}"
            f" {counts['supported']:>10} {counts['unsupported']:>12}"
            f" {counts['timeout']:>8} {counts['error']:>6}"
            f" {report.percentile(95, GRADES):>8.0f} {report.wall_ms / 1000:>6.1f}s"
        )
    service.reset_all()
    BREAKER.reset()
    print(f"\n  {note}")


def main() -> int:
    print("Grading against what the healthy pipeline itself answers, so no design can")
    print("score well by dumping a whole document. 'supported' means a real sentence")
    print("from the right current document that is not the answer; 'unsupported' means")
    print("it is not in the current document at all, which is stale or improvised.\n")

    key = healthy_answers(QUESTIONS)
    print(f"Grading key built from {len(key)} healthy responses.")
    print(f"{COUNT} requests at concurrency {CONCURRENCY}, {TIMEOUT_MS:.0f}ms client deadline.")

    print("\n" + "=" * 92)
    print("SIX SCENARIOS")
    print("=" * 92)

    scenario(
        "FAULT 0: nothing wrong",
        lambda: None,
        key,
        "The control. All three designs are the same code path when nothing is broken.",
    )

    def kill_retrieval() -> None:
        retrieval.config.dead = True

    scenario(
        "FAULT 1: retrieval is down (the model is fine)",
        kill_retrieval,
        key,
        "Hard fail returns nothing. The fallbacks answer from a cache that was correct"
        "\n  when it was taken and is not correct now.",
    )

    def kill_provider() -> None:
        providers.configure_mock(faults=providers.FaultProfile(dead=True))

    scenario(
        "FAULT 2: the model is down (retrieval is fine)",
        kill_provider,
        key,
        "The fallbacks hand back a real help-centre sentence. Honest, useful, and not"
        "\n  the answer: that is what the 'supported' column is for.",
    )

    def flaky_provider() -> None:
        providers.configure_mock(faults=providers.FaultProfile(error_rate=0.4))

    scenario(
        "FAULT 3: the model fails 40% of calls",
        flaky_provider,
        key,
        "Partial failure, which is the common one, and the one that makes an"
        "\n  availability metric look almost fine.",
    )

    def slow_provider() -> None:
        providers.configure_mock(latency="agentic", seed=1337)

    scenario(
        "FAULT 4: the model is slow, not dead (4s+ against a 2s deadline)",
        slow_provider,
        key,
        "The hard one. Compare the p95 and wall-clock columns, not the answer columns:"
        "\n  every design gets the answers equally wrong, and they do not all cost the same.",
    )

    scenario(
        "FAULT 5: the same slowness, sustained (48 requests instead of 24)",
        slow_provider,
        key,
        "A breaker needs time to earn anything. In a short burst most requests are"
        "\n  already in flight before it trips; over a longer fault its advantage grows,"
        "\n  which is the honest scope of the pattern.",
        count=48,
    )

    print("\n" + "=" * 92)
    print("WHAT TO TAKE FROM THIS")
    print("=" * 92)
    print(
        "\n  A fallback does not restore service, it changes the failure. Read the"
        "\n  'answered' column next to 'exact': availability goes up, correctness does"
        "\n  not follow it, and the gap is filled with answers that are stale or"
        "\n  unsupported. Whether that trade is right depends on what a wrong answer"
        "\n  costs in your product, which is a question no measurement here can settle."
        "\n"
        "\n  The breaker is a capacity device, not a correctness one, and it is slower to"
        "\n  pay off than its reputation suggests: on the 24-request burst it saved"
        "\n  wall-clock time and did not move p95 at all, because most of those requests"
        "\n  were already in flight before it tripped. Compare fault 4 with fault 5."
        "\n"
        "\n  Nothing in this chapter improves correctness during an outage, because"
        "\n  nothing can. Every design answers the same questions equally wrongly; they"
        "\n  differ in whether they admit it and in what the wrongness costs to produce."
        "\n"
        "\n  One caveat on the 'exact' column under a dead model. The snippet tier scores"
        "\n  exact 11/24, which is flattering and partly an artifact: this repo's model is"
        "\n  extractive, so the first sentence of the top document sometimes *is* the"
        "\n  answer it would have given. Against a real model that generates rather than"
        "\n  extracts, a raw snippet would land in 'supported' far more often than in"
        "\n  'exact'. Read that column as an upper bound on the fallback's quality."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
