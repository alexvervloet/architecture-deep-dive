# Chapter 4: the model as its own tier

**The decision.** Does the model run inside the app process, or as a service
the app talks to?

**The stressor.** Price the hop, then kill the model the way models actually
die.

## Run it

```bash
python ch04-model-tier/stress.py     # offline, no key, ~42 seconds
```

Both designs run as real subprocesses and are driven over HTTP, so neither is
charged for the harness's own overhead. The tier boundary is a real socket
with real JSON and real cross-process scheduling; only the model's *latency*
is simulated, and identically on both sides.

## The result

**The hop costs 1.45ms.** Timed around the model call alone: 1.31ms in-process
against 2.75ms tiered. Against a realistic 1.3s model call, the tiered request
medians 1158ms, so the boundary is **0.13%** of the request.

**Then the model is killed with `os._exit`**, which is how an OOM kill
arrives: no cleanup, no exception. Ten rounds of three requests, only one of
which needs a model:

| design | /health | /status | /ask ok | /ask 503 with reason |
|---|---|---|---|---|
| in-process | 0/10 | 0/10 | 0/10 | 0/10 |
| tiered | **10/10** | **10/10** | 0/10 | **10/10** |

Neither can answer a question without a model. The difference is everything
else: in-process, the health check and the cached status response die too,
because they were the same process. 1.45ms per request buys the difference
between "the model is down" and "the product is down".

This chapter was written expecting to lose. The build plan asked whether it
should exist at all, on the theory that a service split is worse on every
number measurable on one laptop. It is worse by a tenth of a percent, and the
containment is not obtainable any other way, so the chapter stayed.

The batching section is arithmetic on the measured hop, not a measurement, and
says so in its own heading. The repo cannot measure a provider's fixed
per-call overhead, so it gives you the break-even threshold to measure against
instead.

Full reasoning, and the fleet arguments this deliberately does **not** count:
[ADR.md](ADR.md).
