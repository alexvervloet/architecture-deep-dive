# Chapter 10: the assembly

**The decision.** All nine of them at once, applied to three products that want
different things.

**The stressor.** A latency and cost budget, assembled from the constants the
earlier chapters measured, then checked against the assembled system running.

## Run it

```bash
python ch10-assembly/stress.py     # offline, no key, ~40 seconds
```

Every constant used comes from an earlier chapter and carries its source in
[profiles.py](profiles.py). Nothing is estimated.

## Three products, nine decisions

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

Six differ, three do not. The three that agree are the ones to stop arguing
about: the seam and the model tier were right for an overnight batch job and a
real-time voice agent alike, because 1 line and 1.45ms are below what any
budget here can notice.

The voice agent is the interesting column. It takes **in-process state**, which
ch02 spent a chapter arguing against, because a call is one connection pinned to
one worker for its whole life so ch02's failure cannot occur. And ch05's guard
window costs 200ms of its 300ms budget, which the derivation flags rather than
waves through.

## The budget holds for the mean, and cannot see the tail

| product | predicted mean | measured mean | predicted p95 | measured p95 | measured max |
|---|---|---|---|---|---|
| support chat | 1322ms | 1198ms | **none** | 1295ms | 4995ms |
| batch pipeline | 1357ms | 1233ms | **none** | 1331ms | 5030ms |
| voice agent | 1321ms | 1197ms | **none** | 1295ms | 4994ms |

Every constant these chapters produced is a mean, so no amount of adding them
yields a tail. Support chat's mean is 1198ms and its slowest request in the same
run took 4995ms, **4.2x**, with nothing broken. The only way to get that number
was to build it and run it.

That empty column started as `"model_p95_ms": 3000` sitting in the
measured-constants table as though a chapter had produced it. None had. It was
removed rather than sourced.

## The finding that surprised me

**The architecture is nearly invisible in the latency budget.** Six of nine
decisions differ across these products and their mean latencies land within
36ms of each other, because the model call is **98%** of every request.

That is not an argument that the decisions do not matter. It is an argument
about which column to defend them in:

- ch02 was worth **38 points of correctness**
- ch04 was worth **10/10 health checks** during a model outage
- ch09 was worth **a leaked contract**
- ch06 was worth **2.9x wall clock** under a slow dependency

None of those appear in a latency budget. A design review that only budgets
latency would have rejected all four.

Full reasoning: [ADR.md](ADR.md).
