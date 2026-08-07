# ADR 004: run the model as its own tier

**Status:** accepted
**Date:** 2026-08-07
**Measured on:** the offline mock, `python ch04-model-tier/stress.py`, ~42
seconds, real subprocesses over real sockets, no key required.

## Context

The model can be a function call inside the app process, or a service the app
talks to over a socket. Every other split-the-service argument applies here,
plus one that does not exist in a CRUD system: the thing being separated is
**weights and a GPU**, and it fails in ways ordinary code does not. An
out-of-memory kill, a CUDA fault, or a segfault in a native inference library
does not raise a Python exception you can catch. It ends the process.

This chapter was written expecting to lose. The build plan's open question was
whether it should exist at all, or fold into chapter 6 as a blast-radius
paragraph, on the theory that a service split is measurably worse on every
number available on one laptop and its real payoff needs a fleet.

Both designs run as real subprocesses and are driven over HTTP, so the
comparison never charges one design for the harness's own overhead.

## Options

**A. In-process.** `providers.generate` is a function call. No serialization,
no socket, no second thing to deploy.

**B. Separate tier.** The app posts to a model server. One more process, one
more failure mode, one more deployment.

## Decision

Take B. The measured cost is small enough to be irrelevant against a real
model call, and the failure containment is not obtainable any other way.

## Consequences, measured

**The hop costs 1.45ms.** Timed around the model call itself: 1.31ms
in-process against 2.75ms tiered, for a real socket, real JSON encode and
decode, real HTTP parse, and real cross-process scheduling.

**Against a realistic model call, that is 0.13%.** With the 'slow' profile
(~1.3s), the tiered request medians 1158ms. The boundary did not get more
expensive; the request got bigger. This is the number that resolves the
chapter's open question: the split is not "measurably worse on every number",
it is worse by a tenth of a percent.

**Killing the model kills the whole app when the model is in it.** The model
process is terminated with `os._exit`, no cleanup and no exception, which is
how an OOM kill actually arrives. Then 10 rounds of three requests, only one
of which needs a model:

| design | /health | /status | /ask ok | /ask 503 with reason |
|---|---|---|---|---|
| in-process | 0/10 | 0/10 | 0/10 | 0/10 |
| tiered | **10/10** | **10/10** | 0/10 | **10/10** |

Neither design can answer a question without a model, and the middle column
is honest about that. The difference is everything else. In-process, the
health check dies, the cached status response dies, and every endpoint that
never needed a model dies, because they were all the same process. Tiered,
the app is still there: it serves what it can, and the requests that genuinely
need the model fail in milliseconds with a stated reason instead of a
connection timeout.

That is the entire purchase. 1.45ms per request buys the difference between
"the model is down" and "the product is down".

**Batching break-even is arithmetic, not a measurement, and is labelled as
such in the output.** A tier can batch concurrent calls; an in-process model
in a worker cannot. With a fixed per-call overhead F amortised over a batch of
N, the tier wins when `F * (1 - 1/N) > 1.45ms`, so F must exceed 2.90ms at
N=2 and 1.50ms at N=32. The repo cannot measure F, because the mock has no
real per-call overhead to amortise. Read the table as a design tool: measure
your provider's fixed overhead, and if it is below those numbers, batching is
not the reason to split.

## What would flip this decision

- **A single-process deployment with nothing else to protect.** A CLI, a
  notebook, a desktop app. There is no "rest of the app" to keep alive.
- **A hosted provider.** If the model is already an API call to somebody
  else's service, this decision was made for you and the tier is theirs. This
  chapter is about self-hosted weights.
- **Sub-10ms end-to-end budgets.** Then 1.45ms is 15% and the arithmetic
  inverts. That is not an LLM app.
- **A team that cannot operate two processes.** The tier is only free if
  deploying, monitoring, and versioning a second service is already routine.
  That cost is real and this measurement does not include it.

## Limits of this measurement

- **The hop is localhost.** Loopback TCP, no network. Same host in a
  container adds little; same availability zone adds perhaps 0.2 to 1ms;
  cross-zone is milliseconds and would change the percentage but not the
  conclusion against a 1.3s call.
- **Measuring the hop by differencing end-to-end latency does not work**, and
  the first version of this chapter did exactly that. Retrieval jitter in the
  reference app is several times larger than the boundary, so the estimate
  swung between 0.9ms and 2.7ms across runs. The number above comes from a
  timer inside the app around the model call alone. See LESSONS.md.
- **The crash is a process death, not an exception.** That is deliberate and
  it is the fair comparison: an in-process model that raises a catchable
  exception is recoverable and would show nothing here. The failures that
  motivate a tier are the ones that are not exceptions.
- **The fleet arguments are not measured**, because they cannot be on one
  laptop: GPU utilisation across tenants, independent scaling of app and model
  capacity, one copy of the weights instead of one per worker, and rolling a
  model version without redeploying the app. They are real and they are
  probably larger than anything above. This ADR does not count them, and no
  claim here rests on them.
