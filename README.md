# LLM App Architecture: A Guided Deep Dive

Every other repo in this series teaches a component: the API call, the retriever, the agent
loop, the guardrail, the eval. This one teaches the seams between them, which nobody hands
you and every design review asks about.

The question here is not "how do I build a retriever." You already built one. It is where
retrieval lives, what happens to the request when it fails, who owns the conversation state
once there are two workers, and what it costs you to find out later that you put the
boundary in the wrong place.

## Why this dive exists

An LLM app breaks in ways a CRUD app does not, and the breakages are structural rather than
local.

- Requests take seconds to minutes, so the request-and-response shape that works everywhere
  else falls over at single-digit concurrency.
- The expensive dependency is non-deterministic and occasionally down, so "retry and hope"
  is a design decision with a correctness cost rather than a config value.
- You cannot unsend a token, so streaming moves your safety checks somewhere they have less
  to work with.
- The thing you are scaling is weights on a GPU rather than stateless workers, so the usual
  advice about splitting services inverts.

Those are architecture problems. They have answers, the answers conflict, and which one is
right depends on numbers you can measure.

## The method: decide, build both, stress, record

Every chapter follows the same shape.

1. **The decision.** One fork in the road, stated as a question with two defensible
   answers.
2. **Two builds.** The same app both ways. Same behaviour, different shape, so any
   difference you measure comes from the structure.
3. **The stressor.** A script that applies the pressure the decision exists to survive: a
   changed requirement, a burst of concurrency, a dead dependency, a second worker. It runs
   offline, on one laptop, in under a minute.
4. **The number.** What the stressor measured, including when it refutes what the chapter
   expected.
5. **The ADR.** An architecture decision record written from that run, covering context,
   options, decision, consequences, and the conditions that would flip the decision.

The point is never "this architecture is correct." Half of these decisions reverse when a
number changes, and the ADR names the number. What you get at the end is a folder of
decision records backed by measurements, which is exactly the artifact a senior engineer
gets asked to produce.

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
python ch03-queue/stress.py            # chapter 3: overload the worker pool
python ch04-model-tier/stress.py       # chapter 4: price the hop, kill the model
python ch05-streaming/stress.py        # chapter 5: guard a response as it streams
python ch06-degradation/stress.py      # chapter 6: break each dependency in turn
python ch07-indexing/stress.py         # chapter 7: change documents under the index
python ch08-rollout/stress.py          # chapter 8: ship a regression, see who catches it
python ch09-tenancy/stress.py          # chapter 9: try to leak one tenant to another
python ch10-assembly/stress.py         # chapter 10: all nine decisions, three products
```

Two results from the harness worth seeing before any chapter exists.

**A dead dependency and a slow one are not the same outage.** Same workload, same 0%
correct answers, and the wall clock differs by two orders of magnitude. The dead provider
fails 40 requests in 140ms. The slow one holds every worker until each request burns its
full 3s deadline, taking 15,180ms. Anything that treats those alike is wrong about one of
them.

**The day-one app answers questions it has no source for.** Ask it about quantum
entanglement and it replies fluently from a document about two-factor auth. It did not
error, so an availability dashboard scores that request as a success. That is why the
stress harness separates `wrong_source` from `error`, and why "answered at all" is never
the headline number in these ADRs.

Determinism here is a specific claim rather than a vibe. The same workload run twice,
concurrently, produces identical answers, sources, failures, and simulated latency.
`02_repeatability.py` asserts exactly that, including a check that running at concurrency 1
changes nothing. Wall-clock latency does not reproduce and never gets asserted on.
Per-request drift runs a few milliseconds, and the script prints it rather than hiding it.
Claims that need to reproduce cite simulated time. Claims about what a real machine did say
so, and show the spread.

## The chapters

| # | The decision | The stressor | Status |
|---|--------------|--------------|--------|
| 1 | [Inline provider calls vs one seam](ch01-provider-seam/) | Five requirement changes, count the diff | **done**, [ADR](ch01-provider-seam/ADR.md) |
| 2 | [Conversation state in-process vs shared](ch02-state/) | A second worker, then a restart | **done**, [ADR](ch02-state/ADR.md) |
| 3 | [Hold the connection vs queue the work](ch03-queue/) | A burst 4x the worker pool | **done**, [ADR](ch03-queue/ADR.md) |
| 4 | [Model in-process vs its own tier](ch04-model-tier/) | Price the hop, then kill the model | **done**, [ADR](ch04-model-tier/ADR.md) |
| 5 | [What streaming costs your guardrails](ch05-streaming/) | A response that turns unsafe midway | **done**, [ADR](ch05-streaming/ADR.md) |
| 6 | [Hard fail vs tiered degradation](ch06-degradation/) | Kill retrieval, then the provider, then just slow it | **done**, [ADR](ch06-degradation/ADR.md) |
| 7 | [Index at query time vs an ingest pipeline](ch07-indexing/) | Edit the documents mid-run | **done**, [ADR](ch07-indexing/ADR.md) |
| 8 | [Rollout shape: shadow, canary, eval gate](ch08-rollout/) | A planted regression | **done**, [ADR](ch08-rollout/ADR.md) |
| 9 | [Where the tenant boundary goes](ch09-tenancy/) | A leak test, then more tenants | **done**, [ADR](ch09-tenancy/ADR.md) |
| 10 | [The assembly: three products, same decisions](ch10-assembly/) | A latency and cost budget | **done**, [ADR](ch10-assembly/ADR.md) |

No chapter is done until its ADR cites a run that actually happened, per the series'
[authoring principles](https://github.com/alexvervloet/ai-engineering-deep-dive/blob/main/AUTHORING-LESSONS.md).
CI runs every one of these experiments on push, so the numbers in the ADRs are checked
rather than remembered.

Then: [EXERCISES.md](EXERCISES.md), predict-then-run for every chapter, several of which
reproduce this repo's own mistakes on purpose · [TEXTBOOK.md](TEXTBOOK.md), the lecture
version · [LESSONS.md](LESSONS.md), the four times the measurement was wrong before the
code was.

## Where it slots into the series

After [Production](https://github.com/alexvervloet/ai-in-production-deep-dive), #8.
Production teaches the dozen lines around the model call that make one app safe, cheap, and
observable. This dive asks where those lines live once there is more than one of everything.
It pairs with [Observability](https://github.com/alexvervloet/observability-deep-dive),
because the numbers the ADRs cite come from somewhere, and with
[Professional Tools](https://github.com/alexvervloet/professional-tools-deep-dive), because
several of these decisions are exactly what a framework decides for you.

You want the components first. Reading this before you have hand-rolled a retriever and an
agent loop gives you opinions without the experience to check them, which is the failure
mode this whole series is built against.
