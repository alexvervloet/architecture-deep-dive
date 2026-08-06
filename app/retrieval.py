"""
app/retrieval.py: the retrieval dependency, with its own failure switch.

Retrieval is a *separate* dependency from the provider, and the chapters need
to kill them independently: an app that survives a dead vector store and dies
on a slow model has a different shape from one that does the reverse. So this
module has its own latency and its own fault flag rather than sharing the
provider's.

The scoring is keyword overlap, the same dumb-but-deterministic approach the
RAG dive starts from. It is not good, and that is useful: it retrieves the
wrong document often enough that "the app answered, but from the wrong
context" is a real outcome the correctness measurements have to distinguish
from "the app failed". An architecture that turns outages into wrong answers
is a real design choice, and ch06 can only price it if wrong answers actually
occur.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.corpus import Document, visible_to
from app.determinism import draws_below
from app.providers import LatencyProfile, mock

# Retrieval against a local index is fast, and it stays fast in this repo so
# that when a chapter shows retrieval dominating a request, the reader knows it
# was configured to, not that a keyword scan is secretly expensive.
DEFAULT_LATENCY = LatencyProfile(base_ms=15.0, jitter_ms=10.0, tail_p=0.01, tail_ms=200.0)


class RetrievalUnavailable(RuntimeError):
    """The index is gone: a dead vector store, an expired connection pool."""


@dataclass
class RetrievalConfig:
    latency: LatencyProfile = DEFAULT_LATENCY
    dead: bool = False
    error_rate: float = 0.0
    top_k: int = 3


config = RetrievalConfig()


def reset_retrieval() -> None:
    config.latency = DEFAULT_LATENCY
    config.dead = False
    config.error_rate = 0.0
    config.top_k = 3


@dataclass(frozen=True)
class Retrieved:
    documents: tuple[Document, ...]
    latency_ms: float
    simulated_latency_ms: float


def _score(question: str, doc: Document) -> int:
    words = {w.strip("?.,").lower() for w in question.split() if len(w) > 3}
    haystack = f"{doc.title} {doc.text}".lower()
    return sum(1 for w in words if w in haystack)


def search(question: str, *, tenant: str = "acme", request_id: str = "req") -> Retrieved:
    """Top-k documents for a question, restricted to what `tenant` may see.

    The tenant filter is applied *before* scoring here, which is one of the
    three positions ch09 compares. It is the safe one and it is not free: the
    candidate set shrinks per tenant, so a shared cache across tenants becomes
    unsound. That tradeoff is the chapter.
    """
    if config.dead:
        raise RetrievalUnavailable("retrieval: index unavailable (simulated outage)")
    if draws_below(mock.seed, config.error_rate, "retrieval-error", request_id):
        raise RetrievalUnavailable("retrieval: transient index error")

    simulated_ms = config.latency.sample_ms(mock.seed, "retrieval-latency", request_id)
    start = time.perf_counter()
    time.sleep(simulated_ms / 1000.0)
    measured_ms = (time.perf_counter() - start) * 1000.0

    candidates = visible_to(tenant)
    ranked = sorted(candidates, key=lambda d: (-_score(question, d), d.doc_id))
    hits = tuple(d for d in ranked if _score(question, d) > 0)[: config.top_k]
    return Retrieved(documents=hits, latency_ms=measured_ms, simulated_latency_ms=simulated_ms)


def format_context(documents: tuple[Document, ...]) -> str:
    """One document per line, id marker then body.

    Titles are deliberately left out. They are strong retrieval signal and
    terrible answer material: "Resetting your password" scores as well against
    the question as the sentence that actually answers it, so a title in the
    context makes the mock quote the heading instead of the instruction. Every
    correctness number in this repo would then be measuring a formatting
    accident.
    """
    return "\n".join(f"[{d.doc_id}] {d.text}" for d in documents)
