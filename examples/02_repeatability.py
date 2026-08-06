#!/usr/bin/env python3
"""
02_repeatability.py: the check that licenses every number in this repo.

    python examples/02_repeatability.py        # offline, no key, ~20 seconds

Every ADR here says something like "variant B answered 12% more requests
correctly". That sentence is worth nothing unless the same workload run twice
produces the same result. So this script runs the identical workload twice,
concurrently, and asserts on what must match.

**What must match exactly**, and is asserted:
  - every answer, and the sources behind it
  - every token count
  - every simulated latency, to the microsecond
  - which requests failed, and how

**What must not be asserted**, and is only reported: wall-clock latency.
`time.sleep(0.9)` sleeps for at least 900ms and then however long the OS
takes to come back. Nothing makes that reproducible, and a repo that asserted
on it would fail on a loaded laptop and teach the reader to distrust the
tests instead of the numbers.

That split is the honest version of "deterministic". The workload is
deterministic; the clock is not. Chapters cite simulated latency when the
claim needs to reproduce, and measured latency when the claim is about what a
real machine did, and they say which.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from app import providers, service
from stress import faults, harness

load_dotenv()

COUNT = 30
CONCURRENCY = 8


def task(i: int) -> harness.Outcome:
    question = service.QUESTIONS[i % len(service.QUESTIONS)]
    answer = service.handle(question, request_id=f"r{i:03d}", timeout_ms=2500)
    return harness.classify(answer, expected_source=service.gold_source(question))


def fingerprint(report: harness.Report) -> list[tuple]:
    """Everything about a run that must not vary. Sorted by request id, because
    completion order is a property of the thread pool and deliberately not
    part of the contract."""
    return sorted(
        (o.request_id, o.status, round(o.simulated_ms, 6), o.detail) for o in report.outcomes
    )


print(f"Provider: {providers.describe()}\n")

with faults.flaky(0.15, seed=4242):
    first = harness.run("run A", task, count=COUNT, concurrency=CONCURRENCY)

with faults.flaky(0.15, seed=4242):
    second = harness.run("run B", task, count=COUNT, concurrency=CONCURRENCY)

fp_a, fp_b = fingerprint(first), fingerprint(second)

print(harness.compare(first, second))
print()

if fp_a == fp_b:
    print(f"PASS  {len(fp_a)}/{len(fp_a)} requests identical across both runs:")
    print("      same answers, same sources, same failures, same simulated latency.")
else:
    differences = [(a, b) for a, b in zip(fp_a, fp_b) if a != b]
    print(f"FAIL  {len(differences)} request(s) differed between identical runs.")
    for a, b in differences[:5]:
        print(f"      A: {a}\n      B: {b}")

# Per-request drift, not the aggregate. Two runs can easily land on the same
# p95 by luck at n=30, and quoting that as evidence of reproducible timing
# would be exactly the kind of accidental agreement this repo is supposed to
# be suspicious of.
measured_a = {o.request_id: o.latency_ms for o in first.outcomes}
measured_b = {o.request_id: o.latency_ms for o in second.outcomes}
per_request_drift = [abs(measured_a[k] - measured_b[k]) for k in measured_a]
p95_drift = abs(first.percentile(95) - second.percentile(95))

median_drift = sorted(per_request_drift)[len(per_request_drift) // 2]

print()
print(
    f"Wall clock across those same two runs: per-request drift median {median_drift:.1f}ms, "
    f"max {max(per_request_drift):.1f}ms, while p95 differed by {p95_drift:.0f}ms."
)
print("Aggregates can agree by luck at n=30; individual requests never do. So the")
print("simulated numbers carry any claim that has to reproduce, and a claim about")
print("what a real machine did says so and shows the spread.")

# Concurrency is the part most likely to break determinism later, so prove the
# fingerprint is independent of it rather than assuming.
with faults.flaky(0.15, seed=4242):
    serial = harness.run("run C (serial)", task, count=COUNT, concurrency=1)

if fingerprint(serial) == fp_a:
    print()
    print("PASS  the same workload at concurrency 1 produces the identical fingerprint,")
    print("      so the numbers are a property of the requests, not of the scheduler.")
else:
    print()
    print("FAIL  concurrency changed the result. The per-request seeding in")
    print("      app/determinism.py is not doing its job; fix that before trusting any ADR.")
    sys.exit(1)

if fp_a != fp_b:
    sys.exit(1)
