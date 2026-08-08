# Chapter 6: hard fail or tiered degradation

**The decision.** When a dependency is down, does the request fail, or does the
app say something else?

**The stressor.** Kill retrieval. Kill the model. Make it flaky. Then make it
slow rather than dead, which is the one that hurts.

## Run it

```bash
python ch06-degradation/stress.py     # offline, no key, ~50 seconds
```

## How answers are graded

Against **what the healthy pipeline itself answers**, computed at startup
rather than written down, so no design can score well by dumping a whole
document:

- **exact**: identical to the healthy answer.
- **supported**: a real sentence from the right current document, but not the
  answer. Honest degradation, and not the same as being right.
- **unsupported**: not in the current document at all. Stale, or improvised.

That middle column is the point. Two columns would have forced every degraded
answer into either "fine" or "wrong", and most of them are neither.

## The result

**Retrieval down:** availability goes 0% to 100%, correctness goes to 17%.

| design | answered | exact | unsupported |
|---|---|---|---|
| hard fail | 0/24 | 0 | 0 |
| tiered | **24/24** | 4 | **20** |

Twenty confident answers from a cache that was correct when it was taken. The
snapshot says a password reset link lasts 24 hours; the live document says 30
minutes. Nothing in the response tells the user which one they got.

**Model flaky (40% errors):** the fallback is close to a free win, 24/24
answered with 20 exact against hard fail's 17/24. Partial failure is where this
pattern is at its best, and that deserves saying as plainly as the bad news.

**Model slow, not dead** is where the breaker earns its place, and only on
throughput:

| scenario | hard fail | tiered | tiered + breaker |
|---|---|---|---|
| 24 requests, wall | 6.1s | 6.1s | **4.1s** |
| 48 requests, wall | 12.2s | 12.2s | **4.2s** |
| p95, either | 2031ms | 2033ms | 2033ms |

The breaker's advantage **grows with the length of the fault**, 1.5x over 24
requests and 2.9x over 48, because a longer outage gives it more requests to
protect after it trips. It never moved p95 in either run: the requests that hit
an open breaker were already fast, and the ones that tripped it had already
paid the full deadline.

**Nothing in this chapter improves correctness during an outage, because
nothing can.** Every design answers the same questions equally wrongly. They
differ in whether they admit it, and in what the wrongness costs to produce.

Full reasoning, including why the "exact" column under a dead model is an
upper bound rather than a typical result: [ADR.md](ADR.md).
