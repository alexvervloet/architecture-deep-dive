# Chapter 7: index at query time, or run a pipeline

**The decision.** When does the index get built from the documents?

**The stressor.** Edit three documents while the app is answering questions,
changing the exact sentences that answer them.

## Run it

```bash
python ch07-indexing/stress.py     # offline, no key, ~4 seconds
```

Output is byte-identical across runs. Embedding cost and latency are modelled
at $0.000005 per 1k characters and 3ms per document; the ratios between designs
do not depend on those constants.

## The result

| design | correct | stale-wrong | embed calls | embed $ | worst lag |
|---|---|---|---|---|---|
| per request | 60/60 | 0 | 60 | $0.00071 | 0 ticks |
| process start | 47/60 | 13 | 1 | $0.00001 | never |
| scheduled/10 | 56/60 | 4 | 6 | $0.00007 | 9 ticks |
| **write through** | **60/60** | **0** | **4** | **$0.00001** | **0 ticks** |

**Write-through matches per-request correctness at one fifteenth of the
embedding calls**, because its work is proportional to how often documents
change rather than how often anyone asks a question. Those two rates differ by
orders of magnitude in any real system.

**Per-request indexing adds 36ms and $0.000012 to every request, forever, to
catch three edits.** Grow the corpus and that scales; the edit rate does not.

**Build-at-boot was the worst here**, 13 of 60 answers confidently obsolete and
never catching up. Its staleness clock is your deploy cadence, so it gets worse
as a service stabilises. It is also what most code does by accident.

**The sweep, and which column to read:**

| interval | correct | embed calls | worst lag |
|---|---|---|---|
| 2 | 60/60 | 30 | 1 tick |
| 5 | 58/60 | 12 | 4 ticks |
| 10 | 56/60 | 6 | 9 ticks |
| 60 | 47/60 | 1 | not within the run |

Read **worst lag**, not correctness. Correctness depends on how often the
workload asks about an edited document, so it moves when the question mix
moves. The lag is a property of the schedule alone.

The real cost of write-through is not in the table: every path that can modify
a document has to remember to call it, and the one that forgets leaves an entry
stale forever with no timer coming to fix it. That is why the decision is
write-through **plus** a slow scheduled rebuild as a safety net.

## An earlier version of this chapter measured nothing

The first set of edits changed sentences the retriever never quoted, so a
scheduled index sitting nine ticks behind scored a perfect 60/60. The staleness
was real, the consequence was invisible, and the table looked entirely
plausible. `assert_edits_are_visible()` now runs at startup and refuses to
produce a table unless every edit changes at least one answer. See
[LESSONS.md](../LESSONS.md).

Full reasoning: [ADR.md](ADR.md).
