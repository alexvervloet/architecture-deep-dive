"""
app/providers.py: the only file in this repo that talks to a model provider.

Same keystone as every sibling repo: one function, `generate()`, returns an
`LLMResponse`. Chapter 1 is about whether that sentence is worth anything, so
this file has to be a fair representative of the "seam" side of that argument
rather than a strawman for it.

    PROVIDER=mock   -> deterministic, offline, no key, no network (the default)
    PROVIDER=openai -> OpenAI chat            (needs OPENAI_API_KEY)
    PROVIDER=claude -> Claude messages        (needs ANTHROPIC_API_KEY)

What is different here from the production dive's version: the mock has a
**latency profile** and a **fault profile**, both deterministic. Architecture
decisions only show up under pressure, and the pressure has to be reproducible
or the two builds cannot be compared. A real provider gives you neither on
demand, which is exactly why the mock is the default for every stressor and a
real provider is the opt-in confirmation run.

Keyless run of a real provider degrades to the mock **loudly** (stderr banner
plus a FALLBACK marker in `describe()`), or set PROVIDER_STRICT=1 to make it a
hard error instead. Never let a keyless mock run be mistaken for a real one:
in this repo that would mean publishing an ADR backed by a measurement of
nothing.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, replace
from functools import lru_cache

from app.determinism import draw, draws_below

_OPENAI_CHAT = "gpt-4o-mini"
_CLAUDE_CHAT = "claude-haiku-4-5"
_MOCK_MODEL = "mock-1"

_KEYS = {
    "mock": [],
    "openai": ["OPENAI_API_KEY"],
    "claude": ["ANTHROPIC_API_KEY"],
}


class TransientProviderError(RuntimeError):
    """A retryable failure: the 429/503/timeout family."""


class ProviderTimeout(TransientProviderError):
    """The call exceeded the caller's deadline. Distinct from a refusal to serve.

    Worth its own type because chapters 3 and 6 treat it differently from an
    error: a timeout means the work may still be happening somewhere, and the
    money may still be spent. An architecture that retries timeouts the way it
    retries 503s pays twice.
    """


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float          # measured, wall clock
    simulated_latency_ms: float  # what the mock intended to take; 0.0 for real providers

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


# ---------------------------------------------------------------------------
# Profiles: the knobs the stressors turn
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LatencyProfile:
    """How long the mock pretends a call takes.

    `tail_p` is the point of this dataclass. Mean latency is a comfortable
    number that hides the shape of the problem; the reason queueing and
    timeouts matter is the tail. A profile with base_ms=200 and a 5% chance of
    tail_ms=4000 has a friendly mean and a p99 that eats your connection pool.
    """

    base_ms: float
    jitter_ms: float = 0.0
    tail_p: float = 0.0
    tail_ms: float = 0.0

    def sample_ms(self, seed: int, *labels: object) -> float:
        ms = self.base_ms
        if self.jitter_ms:
            ms += draw(seed, "jitter", *labels) * self.jitter_ms
        if self.tail_p and draws_below(seed, self.tail_p, "tail", *labels):
            ms += self.tail_ms
        return ms


# Named profiles so chapters cite a name, not a magic number, and so changing
# "slow" in one place changes it everywhere it was measured.
PROFILES: dict[str, LatencyProfile] = {
    # Near-instant. For tests and for demonstrating that a structure is fine
    # until it is not.
    "instant": LatencyProfile(base_ms=1.0),
    # A small, fast model on a good day.
    "fast": LatencyProfile(base_ms=120.0, jitter_ms=60.0, tail_p=0.02, tail_ms=600.0),
    # A frontier model doing real work: the profile most chapters run against.
    "slow": LatencyProfile(base_ms=900.0, jitter_ms=400.0, tail_p=0.05, tail_ms=4000.0),
    # An agent turn with several internal steps. This is where holding a
    # connection open stops being free (ch03).
    "agentic": LatencyProfile(base_ms=4000.0, jitter_ms=2000.0, tail_p=0.08, tail_ms=15000.0),
}


@dataclass(frozen=True)
class FaultProfile:
    """How the mock provider misbehaves.

    `error_rate` is a per-call coin flip, so it is honest about being a rate
    and not a quota. `fail_next` is an exact count for chapters that need "the
    first two calls fail, then it recovers". `dead` is the whole dependency
    being gone, which is a different architecture question from flakiness and
    is why ch06 tests both.
    """

    error_rate: float = 0.0
    fail_next: int = 0
    dead: bool = False


@dataclass
class MockConfig:
    """Mutable because stressors reconfigure it between runs; see stress/faults.py
    for the context managers that do it without leaking state into the next run."""

    seed: int = 1337
    latency: LatencyProfile = PROFILES["fast"]
    faults: FaultProfile = FaultProfile()
    _fail_budget: int = 0


mock = MockConfig()


def configure_mock(
    *,
    seed: int | None = None,
    latency: LatencyProfile | str | None = None,
    faults: FaultProfile | None = None,
) -> None:
    """Point the mock at a profile. No effect on real providers, by design:
    a stressor that silently did nothing under PROVIDER=openai would produce a
    chart of nothing."""
    if seed is not None:
        mock.seed = seed
    if latency is not None:
        mock.latency = PROFILES[latency] if isinstance(latency, str) else latency
    if faults is not None:
        mock.faults = faults
        mock._fail_budget = faults.fail_next


def reset_mock() -> None:
    mock.seed = 1337
    mock.latency = PROFILES["fast"]
    mock.faults = FaultProfile()
    mock._fail_budget = 0


def mock_profile_name() -> str:
    for name, profile in PROFILES.items():
        if profile == mock.latency:
            return name
    return "custom"


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------


def _configured_provider() -> str:
    return os.getenv("PROVIDER", "mock").strip().lower()


def _has_required_keys(p: str) -> bool:
    return all(os.getenv(k) for k in _KEYS.get(p, []))


_warned_fallback = False


def _warn_mock_fallback(p: str) -> None:
    global _warned_fallback
    if _warned_fallback:
        return
    _warned_fallback = True
    missing = ", ".join(_KEYS.get(p, []))
    print(
        f"\n!  PROVIDER={p} is set, but {missing} isn't on the environment. Did you\n"
        f"   forget `secrun`? Falling back to the offline mock so this still runs.\n"
        f"   Real model:  secrun python <script>   |   Hard error:  PROVIDER_STRICT=1\n",
        file=sys.stderr,
    )


def provider_name() -> str:
    p = _configured_provider()
    if p in _KEYS and p != "mock" and not _has_required_keys(p):
        if os.getenv("PROVIDER_STRICT"):
            return p
        _warn_mock_fallback(p)
        return "mock"
    return p


def active_model() -> str:
    return {"mock": _MOCK_MODEL, "openai": _OPENAI_CHAT, "claude": _CLAUDE_CHAT}.get(
        provider_name(), _MOCK_MODEL
    )


def describe() -> str:
    configured = _configured_provider()
    p = provider_name()
    if p == "mock" and configured != "mock":
        return (
            f"mock  (FALLBACK: PROVIDER={configured} is set but its key isn't on the "
            f"environment; run under `secrun` for the real model)"
        )
    if p == "mock":
        return (
            f"mock  (offline, deterministic, model={_MOCK_MODEL}, "
            f"latency={mock_profile_name()}, seed={mock.seed})"
        )
    if p == "openai":
        return f"openai  (chat={_OPENAI_CHAT})"
    if p == "claude":
        return f"claude  (chat={_CLAUDE_CHAT})"
    return f"unknown provider {p!r}"


def missing_keys() -> list[str]:
    """Keys the active provider needs that are not on the environment."""
    return [k for k in _KEYS.get(provider_name(), []) if not os.getenv(k)]


def ensure_ready() -> None:
    p = provider_name()
    if p not in _KEYS:
        sys.exit(
            f"PROVIDER={p!r} is not recognized. Set PROVIDER=mock (default), openai, or claude."
        )
    missing = [k for k in _KEYS[p] if not os.getenv(k)]
    if missing:
        sys.exit(
            f"PROVIDER={p} needs {', '.join(missing)} in the environment. Provide them via "
            f"secrun (see ../docs/SECRETS.md). PROVIDER=mock needs no key and runs everything offline."
        )


def warn_if_real_provider_for_stress() -> None:
    """Stressors call this. Running a load test against a real provider costs
    money, hits rate limits, and produces numbers nobody can reproduce, so say
    so rather than letting someone discover it on their bill."""
    if provider_name() != "mock":
        print(
            f"\n!  Stressing PROVIDER={provider_name()}: this spends real money and its\n"
            f"   numbers will not reproduce. The ADRs in this repo are measured on the\n"
            f"   mock. Use a real provider to sanity-check a shape, not to source a number.\n",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# The mock provider
# ---------------------------------------------------------------------------

_NO_CONTEXT = (
    "I don't have anything in the help center about that. Contact support@example.com."
)

_DOC_MARKER = re.compile(r"^\[[a-z0-9-]+\]\s*")


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _answer_from_context(user: str, context: str) -> str:
    """The 'model': it quotes the single most relevant sentence it was given.

    Deliberately extractive. A mock that paraphrases would make answer quality
    a function of the mock's cleverness, and then every chapter measuring
    correctness under fault (ch06) would be measuring this function instead of
    the architecture. Extractive means: good context in, right answer out; bad
    context in, visibly wrong answer out. That property is the whole reason the
    correctness numbers in these ADRs mean anything.
    """
    if not context.strip():
        return _NO_CONTEXT
    question_words = {w.strip("?.,").lower() for w in user.split() if len(w) > 3}
    best, best_score = "", 0
    for line in context.splitlines():
        # Strip the [doc-xx] marker so it cannot be quoted back as prose.
        body = _DOC_MARKER.sub("", line)
        for sentence in body.split(". "):
            score = sum(1 for w in question_words if w in sentence.lower())
            if score > best_score:
                best, best_score = sentence.strip(), score
    if best_score == 0:
        return _NO_CONTEXT
    return best.rstrip(".") + "."


_CLASSES = {
    "billing": ("refund", "charge", "invoice", "billing", "cancel", "plan", "price", "pricing"),
    "security": ("password", "two-factor", "2fa", "sso", "login", "audit"),
    "reliability": ("down", "outage", "status", "incident", "slow", "error", "429"),
}


def _mock_text(system: str, user: str) -> str:
    """What the mock 'model' returns, dispatched on markers in the system prompt.

    Three modes, because the ch01 app calls a model in three places and a
    single-mode mock would make those call sites indistinguishable. All three
    are extractive or rule-based, so an answer is right or wrong for a reason a
    reader can check by hand.
    """
    if "CLASSIFY" in system:
        haystack = user.lower()
        for label, words in _CLASSES.items():
            if any(w in haystack for w in words):
                return label
        return "other"
    if "SUMMARIZE" in system:
        first = user.replace("\n", " ").split(". ")[0].strip()
        return (first.rstrip(".") + ".") if first else ""
    if "CONTEXT:" in system:
        return _answer_from_context(user, system.split("CONTEXT:", 1)[1])
    return _answer_from_context(user, user)


def _mock_generate(
    system: str, user: str, *, request_id: str, attempt: int, timeout_ms: float | None
) -> LLMResponse:
    faults = mock.faults
    if faults.dead:
        raise TransientProviderError("mock: provider is down (simulated outage)")

    if mock._fail_budget > 0:
        mock._fail_budget -= 1
        raise TransientProviderError("mock: simulated transient upstream error (503)")

    if draws_below(mock.seed, faults.error_rate, "provider-error", request_id, attempt):
        raise TransientProviderError("mock: simulated transient upstream error (503)")

    simulated_ms = mock.latency.sample_ms(mock.seed, "provider-latency", request_id, attempt)

    # A timeout is not "the work stops". The upstream keeps going and the tokens
    # are still billed; the caller just stops waiting. Chapters 3 and 6 depend on
    # that being modelled honestly, so we sleep for the deadline, then raise.
    if timeout_ms is not None and simulated_ms > timeout_ms:
        time.sleep(timeout_ms / 1000.0)
        raise ProviderTimeout(
            f"mock: exceeded {timeout_ms:.0f}ms deadline (call wanted {simulated_ms:.0f}ms)"
        )

    start = time.perf_counter()
    time.sleep(simulated_ms / 1000.0)
    measured_ms = (time.perf_counter() - start) * 1000.0

    answer = _mock_text(system, user)
    return LLMResponse(
        text=answer,
        model=_MOCK_MODEL,
        prompt_tokens=_approx_tokens(system + user),
        completion_tokens=_approx_tokens(answer),
        latency_ms=measured_ms,
        simulated_latency_ms=simulated_ms,
    )


# --- Real providers, imported lazily so the offline path never loads an SDK ---


@lru_cache(maxsize=1)
def _openai_client():
    from openai import OpenAI

    return OpenAI()


@lru_cache(maxsize=1)
def _anthropic_client():
    import anthropic

    return anthropic.Anthropic()


def generate(
    system: str,
    user: str,
    *,
    request_id: str = "req",
    attempt: int = 0,
    timeout_ms: float | None = None,
    max_tokens: int = 512,
) -> LLMResponse:
    """The seam. One call, every provider, and it can raise.

    `request_id` and `attempt` exist so the mock's randomness is a property of
    the request rather than of thread scheduling (see app/determinism.py). Real
    providers ignore them, which is the honest cost of the seam: parameters
    that matter to one implementation and are dead weight to the others.
    """
    p = provider_name()
    if p == "mock":
        return _mock_generate(
            system, user, request_id=request_id, attempt=attempt, timeout_ms=timeout_ms
        )

    start = time.perf_counter()
    if p == "openai":
        resp = _openai_client().chat.completions.create(
            model=_OPENAI_CHAT,
            max_tokens=max_tokens,
            timeout=timeout_ms / 1000.0 if timeout_ms else None,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        latency_ms = (time.perf_counter() - start) * 1000
        usage = resp.usage
        return LLMResponse(
            text=resp.choices[0].message.content or "",
            model=_OPENAI_CHAT,
            prompt_tokens=usage.prompt_tokens if usage else _approx_tokens(system + user),
            completion_tokens=usage.completion_tokens if usage else 0,
            latency_ms=latency_ms,
            simulated_latency_ms=0.0,
        )
    if p == "claude":
        resp = _anthropic_client().messages.create(
            model=_CLAUDE_CHAT,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        latency_ms = (time.perf_counter() - start) * 1000
        text = "".join(b.text for b in resp.content if b.type == "text")
        return LLMResponse(
            text=text,
            model=_CLAUDE_CHAT,
            prompt_tokens=resp.usage.input_tokens,
            completion_tokens=resp.usage.output_tokens,
            latency_ms=latency_ms,
            simulated_latency_ms=0.0,
        )
    raise ValueError(f"Unknown PROVIDER={p!r} (expected 'mock', 'openai', or 'claude').")


def with_profile(name: str) -> LatencyProfile:
    """`configure_mock(latency=with_profile('slow'))`, or just pass the string."""
    return PROFILES[name]


__all__ = [
    "FaultProfile",
    "LLMResponse",
    "LatencyProfile",
    "PROFILES",
    "ProviderTimeout",
    "TransientProviderError",
    "active_model",
    "configure_mock",
    "describe",
    "ensure_ready",
    "generate",
    "mock",
    "mock_profile_name",
    "provider_name",
    "replace",
    "reset_mock",
    "warn_if_real_provider_for_stress",
    "with_profile",
]
