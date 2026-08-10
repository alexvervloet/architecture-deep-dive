# ADR 010: the assembly, and what a latency budget is for

**Status:** accepted
**Date:** 2026-08-07
**Measured on:** `python ch10-assembly/stress.py`, offline, no key, ~40s.

## Context

Nine chapters, nine decisions, each backed by a measurement. This one asks what
happens when you apply all nine at once to a real product, and whether the
per-decision numbers add up to a system you can predict.

Three products, chosen to be far apart:

- **support chat**: a person watching tokens appear. 1.5s to first token.
- **batch pipeline**: nobody waiting. 10,000 documents overnight, cost rules.
- **voice agent**: a person listening to silence. 300ms to first audio.

Every constant used comes from an earlier chapter and carries its source in the
code. Nothing is estimated.

## Decision

Derive each product's architecture from the nine ADRs, budget the latency by
adding the measured constants, and then **build it and measure it**, because
the budget can only tell you about the mean.

## Consequences, measured

**Six of nine decisions differ across the three products; three do not.**

| chapter | support chat | batch pipeline | voice agent |
|---|---|---|---|
| ch01 seam | one seam | one seam | one seam |
| ch02 state | shared store | none | in-process, pinned to the call |
| ch03 shape | sync + streaming | queue | sync, shed early |
| ch04 tier | separate | separate | separate |
| ch05 guard | window(8) | buffer | window(8), re-derive it |
| ch06 degradation | tiered + breaker | hard fail, retry | tiered + breaker |
| ch07 indexing | write through | index once per job | write through |
| ch08 rollout | gate + canary | gate + shadow | gate + canary |
| ch09 tenancy | filter in retrieval | none | filter in retrieval |

The three that agree are the useful ones to stop arguing about. The provider
seam and the model tier were right for an overnight batch job and a real-time
voice agent alike, because their measured costs (1 line, 1.45ms) are below
anything either budget can notice.

The voice agent is where the nine decisions collide most usefully. It takes
in-process state, which ch02 spent a whole chapter arguing against, because a
call is one connection pinned to one worker for its life, so ch02's failure
mode cannot occur. And ch05's 8-token guard window costs 200ms of its 300ms
budget, which the chapter flags rather than waves through.

**The composition arithmetic holds for the mean:**

| product | budget | predicted TTFT | measured TTFT | error |
|---|---|---|---|---|
| support chat | 1500ms | 222ms | 222ms | +0.0ms |
| batch pipeline | none | 1357ms | 1233ms | -124ms |
| voice agent | 300ms | 221ms | 221ms | +0.0ms |

The two windowed products predict exactly, because their first token is fixed
overhead plus a constant hold. The batch prediction is 124ms out, 9%, and it is
the only one whose prediction includes a *distribution* mean rather than a
fixed cost. That is the pattern in miniature: constants compose, distributions
do not.

**The budget cannot predict the tail, and there is nothing honest to put in
that column:**

| product | predicted mean | measured mean | predicted p95 | measured p95 | measured max |
|---|---|---|---|---|---|
| support chat | 1322ms | 1198ms | none | 1295ms | 4995ms |
| batch pipeline | 1357ms | 1233ms | none | 1331ms | 5030ms |
| voice agent | 1321ms | 1197ms | none | 1295ms | 4994ms |

Every constant these nine chapters produced is a mean. No amount of adding
means yields a tail. Support chat's mean is 1198ms and its slowest request in
the same run took 4995ms, **4.2x**, with nothing broken: that is a 5% tail at
+4s doing what a 5% tail does. The only way to get that number was to build the
assembled system and run it.

An earlier draft of `profiles.py` carried `"model_p95_ms": 3000` in the
measured-constants table as though a chapter had produced it. None had. It was
removed rather than sourced, and the empty column is the honest result.

**The architecture is nearly invisible in the latency budget.** The three
products differ on six of nine decisions and their mean latencies are within
36ms of each other, because the model call is **98%** of every request. Eight
of the nine decisions moved the budget by an amount no user could perceive.

That is not an argument that the decisions do not matter. It is an argument
about which column to defend them in:

- ch02 was worth **38 points of correctness** (62% to 100% at four workers).
- ch04 was worth **10/10 health checks** surviving a model outage.
- ch09 was worth **a leaked contract**.
- ch06 was worth **2.9x wall clock** under a sustained slow dependency.

None of those appear in a latency budget. A design review that only budgets
latency would have rejected all four.

## What would flip this decision

- **A much faster model.** At 98% of the request, the model is the budget. A
  200ms model inverts this entirely and the fixed overheads start to matter.
- **A product with a hard tail SLO.** Then the mean-based budget is not just
  incomplete, it is the wrong instrument, and the tail has to be measured per
  component before anything is composed.
- **More than one model call per request.** Agent loops multiply the dominant
  term and change every ratio here.

## Limits of this measurement

- **The prediction is not independently validated.** The same simulated
  latencies drive both the budget and the run, so a component's cost cannot
  disagree with itself. What is under test is the composition, and the tail
  behaviour, which are the parts that could have gone wrong and did.
- **The decision function is mine.** `decide()` encodes how I read the nine
  ADRs onto three product profiles. Another engineer could read ch02
  differently for the voice agent and be defensible. The ADRs are measurements;
  the mapping is judgement, and it is in code so it can be argued with.
- **Three products, chosen to be far apart.** Products that differ less would
  produce fewer than six differences, which would be a less interesting table
  and an equally true one.
- **Cost per request is identical across products** ($0.000063) because they
  ask the same questions of the same model. The cost *shape* differences that
  the decisions cause (double inference under shadow, wasted spend under sync
  overload, embedding spend under per-request indexing) are measured in their
  own chapters and not re-derived here.
