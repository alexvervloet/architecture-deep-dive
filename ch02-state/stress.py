#!/usr/bin/env python3
"""
stress.py: start a second worker.

    python ch02-state/stress.py        # offline, no key, ~40 seconds

Three experiments, in the order the mistake actually gets made:

  1. **One worker.** All three designs are perfect. This is the experiment
     everyone runs, on their laptop, before shipping.
  2. **Scale out.** Same code, same requests, more workers. One design falls
     over, and it falls over *silently*: no errors, no timeouts, just wrong
     answers that read fine.
  3. **Restart a worker.** The standard fix for experiment 2 survives
     experiment 2 and not this one, which is the argument the chapter exists
     to make.

Correctness here means "answered from the document the conversation was
about". A follow-up like "How long is that link valid?" has two different
correct answers depending on what was asked before it, so an app that loses
the history does not fail, it answers a different question well.
"""

from __future__ import annotations

import os
import sys
import time

CHAPTER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(CHAPTER))  # repo root, for `app`
sys.path.insert(0, CHAPTER)  # chapter-local modules, inherited by spawned workers

from dotenv import load_dotenv  # noqa: E402

from app import providers  # noqa: E402
from conversation import CONVERSATIONS  # noqa: E402
from runner import Fleet, build_requests  # noqa: E402
from stores import DESIGNS  # noqa: E402

load_dotenv()

ROUNDS = 4  # sessions per conversation template
SEED = 1337


def run(design, workers: int, restart_after_openers: bool = False) -> dict:
    fleet = Fleet(design, workers=workers, seed=SEED, tag=design.store)
    requests = build_requests(ROUNDS, CONVERSATIONS)
    openers = [r for r in requests if r.turn == 0]
    followups = [r for r in requests if r.turn == 1]

    started = time.perf_counter()
    for request in openers:
        fleet.send(request)
    opener_responses = fleet.collect(len(openers))

    if restart_after_openers:
        fleet.restart(0)

    for request in followups:
        fleet.send(request)
    followup_responses = fleet.collect(len(followups))
    wall_ms = (time.perf_counter() - started) * 1000.0

    fleet.shutdown()

    correct = sum(1 for r in followup_responses if r.correct)
    with_history = sum(1 for r in followup_responses if r.history_turns > 0)
    all_responses = opener_responses + followup_responses
    return {
        "correct": correct,
        "total": len(followup_responses),
        "with_history": with_history,
        "store_ms": sum(r.store_ms for r in all_responses) / max(1, len(all_responses)),
        "wall_ms": wall_ms,
        "responses": followup_responses,
    }


def table(title: str, rows: list[tuple[str, dict]], note: str = "") -> None:
    print(f"\n{title}\n")
    header = f"  {'design':<18} {'follow-ups correct':>20} {'saw any history':>17} {'store ms/turn':>14}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for label, result in rows:
        pct = result["correct"] / max(1, result["total"])
        hist = result["with_history"] / max(1, result["total"])
        print(
            f"  {label:<18} {result['correct']:>6}/{result['total']:<3} {pct:>8.0%}"
            f" {hist:>16.0%} {result['store_ms']:>14.3f}"
        )
    if note:
        print(f"\n  {note}")


def main() -> int:
    print(f"Provider: {providers.describe()}")
    print(f"{len(CONVERSATIONS)} conversation templates x {ROUNDS} rounds"
          f" = {len(CONVERSATIONS) * ROUNDS} sessions, two turns each\n")

    # --- 1. one worker --------------------------------------------------
    rows = [(d.label, run(d, workers=1)) for d in DESIGNS]
    table(
        "EXPERIMENT 1: one worker (your laptop)",
        rows,
        "All three designs are perfect. Nothing here tells you which one is wrong.",
    )

    # --- 2. scale out ---------------------------------------------------
    for workers in (2, 4):
        rows = [(d.label, run(d, workers=workers)) for d in DESIGNS]
        expected = 1 / workers
        table(
            f"EXPERIMENT 2: {workers} workers, same code, same requests",
            rows,
            f"A session-unaware router lands the follow-up on the right worker about"
            f" {expected:.0%} of the time.",
        )

    # --- 3. restart a worker --------------------------------------------
    workers = 4
    rows = [(d.label, run(d, workers=workers, restart_after_openers=True)) for d in DESIGNS]
    table(
        f"EXPERIMENT 3: {workers} workers, one restarted between the turns",
        rows,
        "A deploy, an OOM kill, or a scale-in event. Sticky routing was the fix for"
        "\n  experiment 2; this is the experiment it does not survive.",
    )

    print("\n\nWhat a lost turn actually produces\n")
    memory_spread = run(DESIGNS[0], workers=4)
    by_template = {f"s{r}-{i}": CONVERSATIONS[i] for r in range(ROUNDS) for i in range(len(CONVERSATIONS))}
    shown = 0
    for response in memory_spread["responses"]:
        if response.correct or response.history_turns > 0 or shown >= 2:
            continue
        exchange = by_template[response.session_id]
        print(f"  session {response.session_id}: the worker saw 0 turns of history")
        print(f"    the user had asked:  {exchange.opener}")
        print(f"    then asked:          {exchange.followup}")
        print(f"    the app replied:     {response.text}")
        print(f"    it should have come from {exchange.gold_doc}; it came from {response.sources[0]}\n")
        shown += 1
    print(
        "  No exception, no timeout, no empty response. The app answered a question the"
        "\n  user did not ask, in a fluent sentence, from a document about something else."
        "\n  Every dashboard counting HTTP 200s scored those as successes."
    )
    print(
        "\n\nRun this twice: every correctness and history figure above is identical,"
        "\nbecause routing is derived from the request rather than drawn at dispatch"
        "\ntime. The store-ms column is wall clock and will not repeat exactly."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
