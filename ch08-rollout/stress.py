#!/usr/bin/env python3
"""
stress.py: roll out a good change and a bad one, through five shapes each.

    python ch08-rollout/stress.py     # offline, no key, ~12 seconds

Both candidates are "spend fewer tokens on the context". One shortens the
system preamble and changes no answers. The other clips each document to 120
characters and breaks 3 of the 14 questions in the workload. They are the same
kind of change and the same size of diff.

The measurement that matters is **wrong answers that reached a person**. The
measurement that keeps it honest is **whether the good change ever shipped**,
because any process can score zero bad answers by shipping nothing.
"""

from __future__ import annotations

import os
import sys

CHAPTER = os.path.dirname(os.path.abspath(__file__))
# Repo root ahead of the chapter directory: this file is stress.py and the
# shared harness package is also called stress (see ch06 for the same note).
sys.path.insert(0, os.path.dirname(CHAPTER))
sys.path.append(CHAPTER)

from dotenv import load_dotenv  # noqa: E402

from shapes import canary, gate, gate_then_canary, ship, shadow  # noqa: E402
from versions import (  # noqa: E402
    SUITE_A,
    SUITE_B,
    V1,
    V2_TRIM,
    V2_TRUNCATE,
    WORKLOAD,
    answer,
    configure,
    run_suite,
)

load_dotenv()

REQUESTS = 600


def rollouts_for(candidate):
    return [
        ship(candidate, WORKLOAD, REQUESTS),
        gate(candidate, WORKLOAD, REQUESTS, SUITE_A, "suite A"),
        gate(candidate, WORKLOAD, REQUESTS, SUITE_B, "suite B"),
        canary(candidate, WORKLOAD, REQUESTS),
        shadow(candidate, WORKLOAD, REQUESTS),
        gate_then_canary(candidate, WORKLOAD, REQUESTS, SUITE_A, "suite A"),
    ]


def table(title: str, rows) -> None:
    print(f"\n{title}\n")
    header = (
        f"  {'shape':<26} {'bad served':>11} {'detected at':>12} {'extra calls':>12}"
        f" {'shipped':>10}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in rows:
        detected = "no" if r.detected_at is None else f"req {r.detected_at}"
        if r.blocked_before_deploy:
            shipped = "blocked"
        elif r.rolled_back:
            shipped = "rolled back"
        elif r.shipped_at is None:
            shipped = "no"
        else:
            shipped = f"req {r.shipped_at}"
        print(
            f"  {r.shape:<26} {r.bad_served:>5}/{r.served:<5} {detected:>12}"
            f" {r.extra_calls:>12} {shipped:>10}"
        )
    for r in rows:
        if r.note:
            print(f"    {r.shape:<26} {r.note}")


def main() -> int:
    configure()

    print("Two candidate prompt changes, both 'use fewer context tokens':\n")
    for version in (V1, V2_TRIM, V2_TRUNCATE):
        passed_all = sum(
            1
            for q, gold in WORKLOAD
            if answer(version, q, gold, f"probe-{version.label}-{q}").correct
        )
        print(f"  {version.label:<14} {passed_all}/{len(WORKLOAD)} of the workload answered correctly")

    print(f"\n  suite A covers: {[q for q, _ in SUITE_A][:3]} ... ({len(SUITE_A)} questions)")
    print(f"  suite B covers: {[q for q, _ in SUITE_B][:3]} ... ({len(SUITE_B)} questions)")
    for name, suite in (("A", SUITE_A), ("B", SUITE_B)):
        v1_pass, total = run_suite(V1, suite)
        trunc_pass, _ = run_suite(V2_TRUNCATE, suite)
        verdict = "catches it" if trunc_pass < v1_pass else "MISSES it"
        print(f"  suite {name} on v2-truncate: {trunc_pass}/{total} against v1's"
              f" {v1_pass}/{total}, so it {verdict}")

    print(f"\n{REQUESTS} production requests per rollout.")

    print("\n" + "=" * 88)
    print("CANDIDATE 1: v2-trim, a change that is actually fine")
    print("=" * 88)
    table("Every shape should let this through, and cheaply", rollouts_for(V2_TRIM))

    print("\n" + "=" * 88)
    print("CANDIDATE 2: v2-truncate, which breaks 3 of 14 questions")
    print("=" * 88)
    table("Now the differences matter", rollouts_for(V2_TRUNCATE))

    print("\n" + "=" * 88)
    print("WHAT TO TAKE FROM THIS")
    print("=" * 88)
    print(
        "\n  The gate is precise and sampled. With suite B it stops the regression before"
        "\n  a single user sees it, for twelve extra model calls and no waiting. With"
        "\n  suite A, same size and equally plausible, it ships the regression to"
        "\n  everybody and reports a pass. A gate is exactly as good as its sample, and"
        "\n  nothing about running one tells you which suite you have."
        "\n"
        "\n  The canary is complete and noisy. It sees every question, including the ones"
        "\n  no suite covers, and it pays for that in wrong answers served while the"
        "\n  complaint rate accumulates enough samples to separate from the control."
        "\n"
        "\n  Shadow serves nobody the candidate and costs a second model call on every"
        "\n  request. What it produces is a disagreement rate, not a verdict, and the two"
        "\n  are far apart: it reported 64% of responses differing when only 21% of them"
        "\n  were actually wrong. Most of that gap is answers that changed and stayed"
        "\n  correct. Shadow tells you something moved and hands the question of whether"
        "\n  that was bad to a person, which is a real cost that does not appear in any"
        "\n  column here."
        "\n"
        "\n  They fail in different directions, which is the argument for stacking them"
        "\n  rather than choosing."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
