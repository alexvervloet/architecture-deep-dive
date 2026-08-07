# LLM App Architecture: A Guided Deep Dive

Every other repo in this series teaches a **component**: the API call, the
retriever, the agent loop, the guardrail, the eval. This one teaches the
**seams between them**, which is the part nobody hands you and every design
review asks about.

The question here is not "how do I build a retriever." You already built one.
It is: *where does retrieval live, what happens to the request when it fails,
who owns the conversation state when there are two workers, and what does it
cost me to find out later that I put the boundary in the wrong place?*

## Why this dive exists

An LLM app breaks in ways a CRUD app does not, and the breakages are
structural rather than local:

- Requests take **seconds to minutes**, so the request/response shape that
  works everywhere else falls over at single-digit concurrency.
- The expensive dependency is **non-deterministic and occasionally down**, so
  "retry and hope" is a design decision with a correctness cost, not a config
  value.
- You **cannot unsend a token**, so streaming moves your safety checks to a
  place where they have less to work with.
- The thing you are scaling is **weights on a GPU**, not stateless workers, so
  the usual advice about splitting services inverts.

Those are architecture problems. They have answers, the answers conflict, and
which one is right depends on numbers you can measure.

## The method: decide, build both, stress, record

Every chapter follows the same shape:

1. **The decision.** One fork in the road, stated as a question with two
   defensible answers.
2. **Two builds.** The same app both ways. Same behaviour, different shape, so
   any difference measured is attributable to the structure.
3. **The stressor.** A script that applies the pressure the decision exists to
   survive: a changed requirement, a burst of concurrency, a dead dependency,
   a second worker. It runs offline, on one laptop, in under a minute.
4. **The number.** What the stressor measured, including when it refutes what
   the chapter expected.
5. **The ADR.** An architecture decision record written from that run:
   context, options, decision, consequences, and the conditions that would
   flip the decision.

The point is never "this architecture is correct." Half of these decisions
reverse when a number changes, and the ADR names the number. What you get at
the end is a folder of decision records backed by measurements, which is
exactly the artifact a senior engineer is asked to produce.

## Run it

Everything below is offline, needs no key, and costs nothing.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # config only (PROVIDER); no keys go here
python check_setup.py

python examples/00_reference_app.py    # the app, one request at a time
python examples/01_stress_harness.py   # the same workload under five pressures
python examples/02_repeatability.py    # proves the numbers reproduce

python ch01-provider-seam/fairness.py  # chapter 1: are the variants the same app?
python ch01-provider-seam/measure.py   # chapter 1: what did each change cost?
python ch02-state/stress.py            # chapter 2: start a second worker
```

Two results from the harness worth seeing before any chapter exists.

**A dead dependency and a slow one are not the same outage.** Same workload,
same 0% correct answers, and the wall clock differs by two orders of
magnitude: the dead provider fails 40 requests in 140ms, the slow one holds
every worker until each request burns its full 3s deadline, taking 15,180ms.
Anything that treats those alike is wrong about one of them.

**The day-one app answers questions it has no source for.** Ask it about
quantum entanglement and it replies fluently from a document about two-factor
auth. It did not error, so an availability dashboard scores that request as a
success. That is why the harness separates `wrong_source` from `error`, and
why "answered at all" is never the headline number in these ADRs.

Determinism here is a specific claim, not a vibe. The same workload run twice,
concurrently, produces identical answers, sources, failures, and *simulated*
latency, and `02_repeatability.py` asserts exactly that, including a check that
running at concurrency 1 changes nothing. Wall-clock latency is not
reproducible and is never asserted on: per-request drift runs a few
milliseconds, and the script prints it rather than hiding it. Claims that need
to reproduce cite simulated time; claims about what a real machine did say so
and show the spread.

## The chapters

| # | The decision | The stressor | Status |
|---|--------------|--------------|--------|
| 1 | [Inline provider calls vs one seam](ch01-provider-seam/) | Five requirement changes, count the diff | **done**, [ADR](ch01-provider-seam/ADR.md) |
| 2 | [Conversation state in-process vs shared](ch02-state/) | A second worker, then a restart | **done**, [ADR](ch02-state/ADR.md) |
| 3 | Hold the connection vs queue the work | Concurrent slow requests | planned |
| 4 | Model in-process vs its own tier | The hop, then the fleet argument | planned |
| 5 | What streaming costs your guardrails | A response that turns unsafe midway | planned |
| 6 | Hard fail vs tiered degradation | Kill retrieval, then the provider, then just slow it | planned |
| 7 | Index at query time vs an ingest pipeline | Staleness against per-request cost | planned |
| 8 | Rollout shape: shadow, canary, eval gate | A planted regression | planned |
| 9 | Where the tenant boundary goes | A leak test | planned |
| 10 | The assembly: three products, same decisions | A latency and cost budget | planned |

See [PLAN.md](PLAN.md) for the build order and the ground rules. Chapters land
incrementally; the status column is the source of truth. A chapter is not
"done" until its ADR cites a run that actually happened, per the series'
[authoring principles](https://github.com/alexvervloet/ai-engineering-deep-dive/blob/main/AUTHORING-LESSONS.md).

## Where it slots into the series

After [Production](https://github.com/alexvervloet/ai-in-production-deep-dive)
(8). Production teaches the dozen lines around the model call that make one app
safe, cheap, and observable; this dive asks where those lines live once there
is more than one of everything. It pairs with
[Observability](https://github.com/alexvervloet/observability-deep-dive) (the
numbers the ADRs cite come from somewhere) and with
[Professional Tools](https://github.com/alexvervloet/professional-tools-deep-dive)
(several of these decisions are exactly what a framework decides for you).

You want the components first. Reading this before you have hand-rolled a
retriever and an agent loop gives you opinions without the experience to check
them, which is the failure mode this whole series is built against.
