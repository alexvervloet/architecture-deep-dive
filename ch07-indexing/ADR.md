# ADR 007: update the index when documents change, and schedule a rebuild as a safety net

**Status:** accepted
**Date:** 2026-08-07
**Measured on:** `python ch07-indexing/stress.py`, offline, no key, ~4s,
byte-identical across runs.

## Context

Retrieval needs an index; the index is derived from documents; documents
change. The only real question is when the derivation runs.

What makes this different from ordinary cache invalidation is the cost shape.
Rebuilding a derived index is usually CPU you can spare. Rebuilding an
*embedding* index is a per-token bill to a third party, so "just refresh more
often" has a price tag and "refresh every request" has an absurd one.

Sixty requests over sixty ticks, with three documents edited mid-run at ticks
11, 27 and 43: a password reset link goes from 30 minutes to 15, a refund
window from 30 days to 60, rate limits double. An answer counts as correct only
if it is supported by the document *as it exists now*.

## Options

| | behaviour |
|---|---|
| per request | rebuild the whole index on every request |
| process start | build once at boot, never again |
| scheduled | rebuild on a timer |
| write through | re-embed one document when that document changes |

## Decision

Write through on edit, with a slow scheduled rebuild behind it as a safety
net for the writes that miss.

## Consequences, measured

| design | correct | stale-wrong | embed calls | embed $ | worst lag |
|---|---|---|---|---|---|
| per request | 60/60 | 0 | 60 | $0.00071 | 0 ticks |
| process start | 47/60 | 13 | 1 | $0.00001 | never |
| scheduled/10 | 56/60 | 4 | 6 | $0.00007 | 9 ticks |
| **write through** | **60/60** | **0** | **4** | **$0.00001** | **0 ticks** |

**Write-through matches per-request correctness at one fifteenth of the
embedding calls.** It re-embeds one document per edit instead of the whole
corpus per tick, which is the whole trick: the work is proportional to how
often documents change, not to how often anyone asks a question. Those two
numbers differ by orders of magnitude in every real system.

**Per-request indexing is never stale and never affordable.** It adds 36ms and
$0.000012 to every single request, forever, to catch three edits. Scale the
corpus from 12 documents to 12,000 and both numbers scale with it while the
edit rate does not.

**Build-at-boot is the one nobody chooses, and it was the worst here.** 13 of
60 answers were confidently obsolete, and it never caught up, because nothing
was ever going to rebuild it. Its staleness is not "a few minutes"; it is
"however long since the last deploy", which gets *worse* as a service
stabilises.

**The schedule sweep:**

| interval | correct | embed calls | worst lag |
|---|---|---|---|
| 1 | 60/60 | 60 | 0 ticks |
| 2 | 60/60 | 30 | 1 tick |
| 5 | 58/60 | 12 | 4 ticks |
| 10 | 56/60 | 6 | 9 ticks |
| 20 | 54/60 | 3 | not within the run |
| 60 | 47/60 | 1 | not within the run |

**Read the lag column, not the correctness column.** Correctness is roughly
proportional to spend across this range with no convenient knee, and it depends
on how often the workload asks about an edited document. Change the question
mix and every correctness number moves. The lag does not: it is a property of
the schedule alone, so it is the number that belongs in a design document.

**The real cost of write-through is not in the table.** Every code path that
can modify a document has to remember to call it, and the one that forgets
leaves an entry stale forever with no timer coming to repair it. That failure
is silent and unbounded, where a schedule's is periodic and predictable. Hence
the decision: write through for freshness, plus a slow rebuild so a missed
write has a ceiling on how long it can lie.

## What would flip this decision

- **A corpus that changes more often than it is queried.** Then per-request
  indexing stops being absurd, because you were going to re-embed anyway.
- **No control of the write path.** If documents arrive by a nightly dump from
  a system you do not own, write-through is not available and the schedule is
  the design.
- **Staleness that is genuinely free.** Archival or reference corpora that do
  not change do not need any of this; build at boot and stop thinking about it.
  The failure mode above is specific to content that changes and is asserted as
  current.
- **Embeddings that are not billed per token.** A local embedding model changes
  the cost column dramatically, though not the latency one.

## Limits of this measurement

- **Twelve documents.** A full rebuild costs 36ms here; at 12,000 documents it
  is minutes, and the case against per-request indexing gets stronger while the
  case for write-through gets much stronger. Nothing in the ordering depends on
  corpus size, but the magnitudes do.
- **Six of fourteen workload questions target an edited document.** That ratio
  is a guess, it is stated in the code, and every correctness percentage here
  scales with it. The lag figures do not.
- **The edit ticks are deliberately coprime with the sweep intervals.** An
  earlier version used 10, 25 and 40, which are multiples of several intervals,
  so those schedules recorded a worst-case lag of zero purely because an edit
  landed on a rebuild.
- **An earlier version of this chapter measured nothing, and looked fine
  doing it.** The first edits changed sentences the retriever never quoted, so
  a scheduled index with a nine-tick lag scored a perfect 60/60. The staleness
  was real and the consequence was invisible. `assert_edits_are_visible()` now
  runs at startup and refuses to produce a table unless every edit changes at
  least one answer. See LESSONS.md.
- **Embedding cost and latency are modelled**, at $0.000005 per 1k characters
  and 3ms per document, in the neighbourhood of a small hosted embedding model.
  The ratios between designs do not depend on those constants; the absolute
  dollars do.
