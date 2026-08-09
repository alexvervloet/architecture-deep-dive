#!/usr/bin/env python3
"""
stress.py: change the documents while the app is answering questions.

    python ch07-indexing/stress.py     # offline, no key, ~4 seconds

Sixty requests over sixty ticks. At ticks 11, 27 and 43 a document is edited,
and each edit changes the exact sentence that answers a question the workload
asks: the password reset link goes from 30 minutes to 15, the refund window
from 30 days to 60, the rate limits double.

Nothing about those edits is exotic. They are Tuesday for a help centre. The
question is how long each design keeps telling people the old number, and what
it pays for the privilege.

An answer is wrong here in the same sense as chapter 6: it is not supported by
the document as it exists *now*. A stale index does not produce an error or an
empty result. It produces a confident, well-formed, obsolete fact.
"""

from __future__ import annotations

import os
import sys

CHAPTER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(CHAPTER))

from dotenv import load_dotenv  # noqa: E402

from app import providers, service  # noqa: E402
from pipeline import (  # noqa: E402
    EDITS,
    METER,
    Corpus,
    PerRequestIndex,
    ProcessStartIndex,
    ScheduledIndex,
    WriteThroughIndex,
)

load_dotenv()

TICKS = 60

# Questions whose answers the edits change, mixed with questions they do not.
# The mix matters: if every question targeted an edited document, staleness
# would look catastrophic; if none did, it would look free. Six of these
# fourteen are affected, which is a guess at a plausible ratio and is stated
# rather than hidden, because the headline percentages scale with it.
#
# Three questions were reworded rather than kept. "How do I export my data?",
# "What plans are available?" and "Where do I find service status?" each
# retrieved a competing document even against a perfectly fresh index, putting
# a 12-in-60 error floor under every column and making staleness impossible to
# separate from ordinary retrieval failure. Same documents, phrasings that the
# keyword retriever can actually resolve. Fixing the retriever is the RAG
# dive's job; this chapter needs a clean ceiling to measure against.
WORKLOAD = (
    ("How many minutes before the reset link expires?", "doc-01"),
    ("How many days do I have to request a refund?", "doc-03"),
    ("How many business days until a refund posts?", "doc-03"),
    ("How many API requests per minute on Pro?", "doc-07"),
    ("What is the per minute request limit on Free?", "doc-07"),
    ("How do I reset my password?", "doc-01"),
    ("How do I get a downloadable archive of my data?", "doc-05"),
    ("How do I cancel my subscription?", "doc-04"),
    ("How much is the Team plan per user?", "doc-06"),
    ("Which plan includes SSO?", "doc-08"),
    ("How quickly are incidents posted after detection?", "doc-09"),
    ("How long are audit logs retained?", "doc-10"),
    ("What happens if I lose my two-factor device?", "doc-02"),
    ("Does cancelling delete my data?", "doc-04"),
)

SYSTEM_TEMPLATE = service.SYSTEM_TEMPLATE


def assert_edits_are_visible() -> None:
    """Every edit must change the answer to at least one question in the workload.

    Without this the chapter measures nothing, and it silently measured nothing
    once already: the first version edited a sentence that the retriever never
    quoted, so a scheduled index with a nine-tick lag scored a perfect 60/60.
    The staleness was real, the consequence was invisible, and the table looked
    plausible. An edit that no question can see is not a test of freshness.
    """
    before, after = Corpus(), Corpus()
    for edit in EDITS:
        after.apply(edit)
    index_before, index_after = PerRequestIndex(before).get(0), PerRequestIndex(after).get(0)

    unseen = []
    for edit in EDITS:
        visible = False
        for question, gold in WORKLOAD:
            if gold != edit.doc_id:
                continue
            text_before, _ = answer_with(index_before, question, "assert")
            text_after, _ = answer_with(index_after, question, "assert")
            if text_before.strip() != text_after.strip():
                visible = True
                break
        if not visible:
            unseen.append(edit.doc_id)
    if unseen:
        raise SystemExit(
            f"edits to {', '.join(unseen)} change no answer in the workload; "
            f"this run would measure nothing"
        )


def answer_with(index, question: str, request_id: str) -> tuple[str, list[str]]:
    doc_ids = index.search(question)
    response = providers.generate(
        SYSTEM_TEMPLATE.format(context=index.context(doc_ids)),
        question,
        request_id=request_id,
    )
    return response.text, doc_ids


def supported_by_current(text: str, corpus: Corpus, doc_id: str) -> bool:
    return text.rstrip(".").strip().lower() in corpus.current_text(doc_id).lower()


def run(make_strategy, *, sleep_for_embeddings: bool) -> dict:
    """One full timeline against one index strategy."""
    providers.configure_mock(latency="instant", seed=1337)
    METER.reset()
    corpus = Corpus()
    strategy = make_strategy(corpus)

    correct = stale_wrong = 0
    stale_ticks_by_edit: dict[int, int | None] = {e.tick: None for e in EDITS}

    for tick in range(TICKS):
        for edit in EDITS:
            if edit.tick == tick:
                corpus.apply(edit)
                if isinstance(strategy, WriteThroughIndex):
                    strategy.on_edit(edit.doc_id)

        question, gold = WORKLOAD[tick % len(WORKLOAD)]
        index = strategy.get(tick)
        text, _ = answer_with(index, question, f"t{tick:03d}")

        if supported_by_current(text, corpus, gold):
            correct += 1
        else:
            stale_wrong += 1

        # How long did each edit take to become visible to this index?
        for edit in EDITS:
            if tick >= edit.tick and stale_ticks_by_edit[edit.tick] is None:
                if index.text_by_id.get(edit.doc_id) == corpus.current_text(edit.doc_id):
                    stale_ticks_by_edit[edit.tick] = tick - edit.tick

    lags = [v for v in stale_ticks_by_edit.values() if v is not None]
    never = sum(1 for v in stale_ticks_by_edit.values() if v is None)
    return {
        "label": strategy.label,
        "correct": correct,
        "wrong": stale_wrong,
        "embed_calls": METER.embed_calls,
        "embed_usd": METER.embed_usd,
        "embed_ms": METER.embed_ms,
        "max_lag": max(lags) if lags else None,
        "never_caught_up": never,
    }


def table(rows: list[dict], title: str) -> None:
    print(f"\n{title}\n")
    header = (
        f"  {'design':<16} {'correct':>9} {'stale-wrong':>12} {'embed calls':>12}"
        f" {'embed $':>10} {'embed ms':>10} {'worst lag':>11}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in rows:
        lag = "never" if r["never_caught_up"] else f"{r['max_lag']} ticks"
        print(
            f"  {r['label']:<16} {r['correct']:>4}/{TICKS:<4} {r['wrong']:>12}"
            f" {r['embed_calls']:>12} ${r['embed_usd']:>9.5f} {r['embed_ms']:>10.0f}"
            f" {lag:>11}"
        )


def main() -> int:
    providers.configure_mock(latency="instant", seed=1337)
    assert_edits_are_visible()
    print(f"{TICKS} requests over {TICKS} ticks. Three documents are edited mid-run:")
    for edit in EDITS:
        print(f"  tick {edit.tick:>2}: {edit.doc_id}, {edit.note}")
    print(
        "\nAn answer is correct only if it is supported by the document as it exists"
        "\nnow. A stale index does not error; it states an obsolete fact confidently."
    )

    print("\n" + "=" * 88)
    print("1. THE FOUR DESIGNS")
    print("=" * 88)

    rows = [
        run(PerRequestIndex, sleep_for_embeddings=True),
        run(ProcessStartIndex, sleep_for_embeddings=False),
        run(lambda c: ScheduledIndex(c, 10), sleep_for_embeddings=False),
        run(WriteThroughIndex, sleep_for_embeddings=False),
    ]
    table(rows, "Same workload, same edits, four index strategies")

    per_request, process_start = rows[0], rows[1]
    print(
        f"\n  Per-request indexing is the only design that is never stale, and it costs"
        f"\n  {per_request['embed_calls']} embedding calls against everyone else's handful."
        f" Correctness is perfect"
        f"\n  and the price is {per_request['embed_ms'] / TICKS:.0f}ms and"
        f" ${per_request['embed_usd'] / TICKS:.6f} added to every single request,"
        f"\n  forever, to catch three edits."
    )
    print(
        f"\n  Build-at-boot answered {process_start['wrong']} of {TICKS} requests with"
        f" text that is no longer true,"
        f"\n  and never caught up, because nothing was ever going to rebuild it."
    )

    print("\n" + "=" * 88)
    print("2. THE SCHEDULE SWEEP: staleness against spend")
    print("=" * 88)
    print()
    header = (
        f"  {'interval':>9} {'correct':>9} {'stale-wrong':>12} {'embed calls':>12}"
        f" {'embed $':>10} {'worst lag':>11}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for interval in (1, 2, 5, 10, 20, 30, 60):
        r = run(lambda c, i=interval: ScheduledIndex(c, i), sleep_for_embeddings=False)
        lag = "never" if r["never_caught_up"] else f"{r['max_lag']} ticks"
        print(
            f"  {interval:>9} {r['correct']:>4}/{TICKS:<4} {r['wrong']:>12}"
            f" {r['embed_calls']:>12} ${r['embed_usd']:>9.5f} {lag:>11}"
        )

    print(
        "\n  This is the dial, and the column to read is 'worst lag', not 'correct'."
        "\n  Correctness here is roughly proportional to spend over this range, with no"
        "\n  convenient knee to point at, and in any case it depends on how often the"
        "\n  workload happens to ask about an edited document. Change the question mix"
        "\n  and every correctness number moves. The lag does not: it is a property of"
        "\n  the schedule alone, so it is the number to write into a design document."
        "\n"
        "\n  'never' in that column means 'not within this 60-tick run'. A 20-tick"
        "\n  schedule does catch up eventually; it just had not by the time the run"
        "\n  ended, which is the honest thing for the harness to say rather than"
        "\n  extrapolating."
    )

    write_through = rows[3]
    print(
        f"\n  Write-through gets what the sweep is trying to buy: {write_through['correct']}"
        f"/{TICKS} correct for"
        f"\n  {write_through['embed_calls']} embedding calls, because it re-embeds one document"
        f" per edit instead of"
        f"\n  the whole corpus per tick. The bill for that is not in this table: every code"
        f"\n  path that can change a document now has to remember to call it, and the one"
        f"\n  that forgets leaves an entry stale forever, with no timer coming to fix it."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
