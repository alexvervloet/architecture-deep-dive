#!/usr/bin/env python3
"""
01_stress_harness.py: the measuring instrument, demonstrated on itself.

    python examples/01_stress_harness.py       # offline, no key, ~30 seconds

Four runs of the identical workload, each under a different pressure. No
architecture is being compared yet; the point is to show the instrument
produces numbers that separate, so that when a chapter reports a difference
you already know the harness can see one.

Watch the fourth run. A dead dependency and a slow one produce completely
different failure signatures, and the slow one is worse: the dead provider
fails fast and cheap, while the slow provider holds every worker until the
whole pool is gone. That asymmetry is the argument for ch06 and it shows up
here, before any chapter has been written.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from app import providers, service
from stress import faults, harness

load_dotenv()

QUESTIONS = service.QUESTIONS
COUNT = 40
CONCURRENCY = 8


def task(i: int) -> harness.Outcome:
    question = QUESTIONS[i % len(QUESTIONS)]
    answer = service.handle(
        question, request_id=f"r{i:03d}", session_id=f"s{i % 4}", timeout_ms=3000
    )
    return harness.classify(answer, expected_source=service.gold_source(question))


print(f"Provider: {providers.describe()}")
print(f"{COUNT} requests, concurrency {CONCURRENCY}, 3000ms client deadline\n")

reports = []

with faults.latency("fast", seed=1337):
    reports.append(harness.run("healthy (fast model)", task, count=COUNT, concurrency=CONCURRENCY))
    print(reports[-1].summary(), "\n")

with faults.latency("slow", seed=1337):
    reports.append(harness.run("healthy (slow model)", task, count=COUNT, concurrency=CONCURRENCY))
    print(reports[-1].summary(), "\n")

with faults.flaky(0.20, seed=1337):
    reports.append(harness.run("provider 20% errors", task, count=COUNT, concurrency=CONCURRENCY))
    print(reports[-1].summary(), "\n")

with faults.provider_down():
    reports.append(harness.run("provider dead", task, count=COUNT, concurrency=CONCURRENCY))
    print(reports[-1].summary(), "\n")

with faults.slow_not_dead("agentic"):
    reports.append(harness.run("provider slow, not dead", task, count=COUNT, concurrency=CONCURRENCY))
    print(reports[-1].summary(), "\n")

print(harness.compare(*reports))
print()
print("Note the last two rows. Both are 0% correct, and they are not the same")
print("outage: 'dead' fails in milliseconds, 'slow' burns the client deadline on")
print("every single request first. Any strategy that treats them alike is wrong")
print("about one of them.")
