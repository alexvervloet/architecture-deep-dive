# ADR 006: degrade deliberately, and put a breaker in front of slowness

**Status:** accepted
**Date:** 2026-08-07
**Measured on:** `python ch06-degradation/stress.py`, offline, no key, ~50s.

## Context

A dependency fails. The app can return an error, or it can return something
else. "Something else" is usually presented as strictly better, and this
chapter exists because it is not: a fallback does not restore service, it
changes the failure, and the new failure is harder to see.

Three designs, one workload, six scenarios:

| | behaviour |
|---|---|
| hard fail | any dependency error fails the request |
| tiered fallback | stale cache when retrieval is down, raw snippet when the model is down |
| tiered + breaker | the same fallbacks, plus stop calling a dependency that is not answering |

Answers are graded three ways, against **what the healthy pipeline itself
answers**, so no design can score well by dumping a whole document:

- **exact**: identical to the healthy answer.
- **supported**: a real sentence from the right current document, but not the
  answer. An honest degradation, and not the same as being right.
- **unsupported**: not in the current document at all. Stale, or improvised.

## Decision

Fall back deliberately, label the degraded tier in the response, and put a
circuit breaker in front of the model. Do not treat availability as the
success metric.

## Consequences, measured

**Retrieval down (24 requests):**

| design | answered | exact | supported | unsupported | error |
|---|---|---|---|---|---|
| hard fail | 0/24 | 0 | 0 | 0 | 24 |
| tiered | **24/24** | 4 | 0 | **20** | 0 |
| tiered + breaker | 24/24 | 4 | 0 | 20 | 0 |

Availability went from 0% to 100% and correctness went to 17%. The other 83%
are confident answers from a cache that was right when it was taken: the
snapshot says a password reset link lasts 24 hours, the live document says 30
minutes. Nothing in the response says which one the user got.

**Model down (24 requests):**

| design | answered | exact | supported | unsupported |
|---|---|---|---|---|
| hard fail | 0/24 | 0 | 0 | 0 |
| tiered | 24/24 | 11 | 9 | 4 |

The snippet tier is the fallback that behaves best, because it serves real
current text. 9 of 24 land in "supported": a relevant help-centre sentence
that is not the answer. That is a defensible product decision, and it is
visible in a column rather than hidden inside an availability number.

**Model failing 40% of calls (the common one):** hard fail answers 17/24, all
of them exactly right. Tiered answers 24/24 with 20 exact. Here the fallback is
close to a free win, which is worth saying plainly: partial failure is where
this pattern is at its best.

**Model slow, not dead, 24-request burst:**

| design | answered | timeout | p95 | wall |
|---|---|---|---|---|
| hard fail | 0/24 | 24 | 2031ms | 6.1s |
| tiered | 24/24 | 0 | 2033ms | 6.1s |
| tiered + breaker | 24/24 | 0 | 2029ms | **4.1s** |

**Sustained, 48 requests:**

| design | answered | p95 | wall |
|---|---|---|---|
| hard fail | 0/48 | 2033ms | 12.2s |
| tiered | 48/48 | 2032ms | 12.2s |
| tiered + breaker | 48/48 | 2033ms | **4.2s** |

**The breaker is a capacity device and it pays off more slowly than its
reputation suggests.** (The p95 column is wall clock and the three designs sit
within a few milliseconds of each other there, which reruns reshuffle. Read it
as "no difference", not as a ranking.) Over 24 requests it saved a third of the wall clock;
over 48 it saved 2.9x, because a longer fault gives it more requests to
protect after it trips. It never moved p95 at all, in either run: the requests
that hit an open breaker were already fast, and the ones that tripped it had
already paid the full deadline. Anyone quoting a breaker as a latency-
percentile fix should be asked for the measurement.

**Nothing here improves correctness during an outage, because nothing can.**
Every design answers the same questions equally wrongly. They differ in
whether they admit it and in what the wrongness costs to produce.

## What would flip this decision

- **A wrong answer costs more than no answer.** Dosages, prices, legal or
  compliance text, anything a user will act on without checking. Hard fail is
  the correct design there, and the 20/24 unsupported row above is why.
- **No stale data worth serving.** The cache tier is only defensible if the
  snapshot is usually still true. For fast-moving content it converts an
  outage into misinformation with better uptime.
- **A dependency that is never slow, only dead.** The breaker's entire measured
  advantage is against slowness. Against a dead dependency, failing fast is
  already free.
- **Short outages only.** If faults resolve in seconds, the breaker trips
  around the time the problem ends and the added state is not worth it.

## Limits of this measurement

- **The "exact" column under a dead model is flattering, and partly an
  artifact.** The snippet tier scores 11/24 exact, because this repo's model is
  extractive: the first sentence of the top document sometimes *is* the answer
  it would have produced. Against a real generative model, a raw snippet would
  land in "supported" far more often than in "exact". Read 11/24 as an upper
  bound on the snippet tier's quality, not as a typical result.
- **The stale cache's error rate is a property of the snapshot I wrote.** Two
  of its three documents are outdated, which is a choice, not a finding. The
  transferable point is that a cache's correctness decays and nothing in the
  serving path knows by how much.
- **Breaker parameters were not tuned.** Threshold 3, probe every 12. A more
  aggressive breaker would trip sooner and might move p95; that would be a
  different experiment and the ADR does not claim it would not.
- **The degraded tier is labelled internally but the grading ignores the
  label.** A real product would show the user "our search is down, this may be
  out of date", which changes the cost of an unsupported answer considerably.
  That is a product decision this measurement cannot make.
