"""
app/tools.py: one tool, because one is enough to make the shape questions real.

The agents dive teaches tool loops properly. Here a tool exists for three
architectural reasons, and only those:

  1. It is a *second* dependency that can be slow or dead independently of
     retrieval and the model (ch06 needs three things to kill, not two).
  2. It writes. `open_ticket` has an effect in the world, which is what makes
     retry policy a correctness question rather than a latency question: a
     retried read costs money, a retried write costs a duplicate ticket. That
     distinction is the reason ch03 cares whether queued work is idempotent.
  3. It is the reason a request can take multiple provider calls, which is what
     turns the latency profile from a single sample into a sum.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.determinism import draws_below
from app.providers import LatencyProfile, mock

DEFAULT_LATENCY = LatencyProfile(base_ms=80.0, jitter_ms=40.0, tail_p=0.03, tail_ms=1200.0)


class ToolUnavailable(RuntimeError):
    """The downstream system the tool calls is not answering."""


@dataclass
class ToolConfig:
    latency: LatencyProfile = DEFAULT_LATENCY
    dead: bool = False
    error_rate: float = 0.0


config = ToolConfig()

# Module-level so it survives across calls within a process, which is the point:
# ch02 shows what happens to in-process state when there is more than one
# process, and this ledger is the writeable counterpart to the conversation
# history. Duplicates here are visible evidence of a retry that should not have
# happened.
TICKETS: list[dict] = []


def reset_tools() -> None:
    config.latency = DEFAULT_LATENCY
    config.dead = False
    config.error_rate = 0.0
    TICKETS.clear()


@dataclass(frozen=True)
class ToolResult:
    name: str
    output: str
    latency_ms: float
    simulated_latency_ms: float


def _pause(label: str, request_id: str) -> tuple[float, float]:
    if config.dead:
        raise ToolUnavailable("tool: downstream system unavailable (simulated outage)")
    if draws_below(mock.seed, config.error_rate, "tool-error", label, request_id):
        raise ToolUnavailable("tool: transient downstream error")
    simulated_ms = config.latency.sample_ms(mock.seed, "tool-latency", label, request_id)
    start = time.perf_counter()
    time.sleep(simulated_ms / 1000.0)
    return (time.perf_counter() - start) * 1000.0, simulated_ms


def open_ticket(
    summary: str, *, tenant: str = "acme", request_id: str = "req", idempotency_key: str | None = None
) -> ToolResult:
    """Create a support ticket. Writes, on purpose.

    `idempotency_key` is optional so that chapters can measure what its absence
    costs. Retry a request without one and you get two tickets; the duplicate
    count is the number ch03's ADR reports, and it is a more persuasive
    argument for idempotency than any amount of prose about exactly-once
    delivery.
    """
    measured_ms, simulated_ms = _pause("open_ticket", request_id)
    if idempotency_key is not None:
        for existing in TICKETS:
            if existing["idempotency_key"] == idempotency_key:
                return ToolResult(
                    "open_ticket",
                    f"ticket {existing['id']} already exists (idempotent replay)",
                    measured_ms,
                    simulated_ms,
                )
    ticket_id = f"T-{len(TICKETS) + 1:04d}"
    TICKETS.append(
        {
            "id": ticket_id,
            "tenant": tenant,
            "summary": summary,
            "idempotency_key": idempotency_key,
        }
    )
    return ToolResult("open_ticket", f"opened ticket {ticket_id}", measured_ms, simulated_ms)
