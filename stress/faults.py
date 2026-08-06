"""
stress/faults.py: apply pressure, then put everything back.

Context managers rather than setup calls, for a reason that cost this repo an
hour before it was written down: a fault profile left set at the end of one
measurement silently poisons the next one, and the resulting numbers look
plausible. A 12% error rate that was supposed to be 0% does not announce
itself; it just makes the second variant look worse than the first.

So every stressor is scoped, and `app.service.reset_all()` runs on exit.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from app import providers, retrieval, service, tools
from app.providers import FaultProfile


@contextmanager
def latency(profile: str | providers.LatencyProfile, *, seed: int | None = None) -> Iterator[None]:
    """Run the block against a named provider latency profile."""
    try:
        providers.configure_mock(latency=profile, seed=seed)
        yield
    finally:
        service.reset_all()


@contextmanager
def flaky(error_rate: float, *, seed: int | None = None) -> Iterator[None]:
    """The provider fails a fraction of calls and recovers on its own."""
    try:
        providers.configure_mock(faults=FaultProfile(error_rate=error_rate), seed=seed)
        yield
    finally:
        service.reset_all()


@contextmanager
def provider_down() -> Iterator[None]:
    """Total provider outage. Different from flaky, and ch06 tests both."""
    try:
        providers.configure_mock(faults=FaultProfile(dead=True))
        yield
    finally:
        service.reset_all()


@contextmanager
def retrieval_down() -> Iterator[None]:
    """The index is gone. The model still works, which is what makes a
    degraded answer possible and therefore tempting."""
    try:
        retrieval.config.dead = True
        yield
    finally:
        service.reset_all()


@contextmanager
def tools_down() -> Iterator[None]:
    try:
        tools.config.dead = True
        yield
    finally:
        service.reset_all()


@contextmanager
def slow_not_dead(profile: str = "agentic") -> Iterator[None]:
    """The harder fault, and the one most fallback logic gets wrong.

    A dead dependency announces itself immediately. A dependency that still
    answers, eventually, holds your workers, exhausts your pool, and takes the
    healthy requests down with it. Circuit breakers exist for this case, not
    for the easy one.
    """
    try:
        providers.configure_mock(latency=profile)
        yield
    finally:
        service.reset_all()
