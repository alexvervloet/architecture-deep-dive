"""
ch06/designs.py: three things to do when a dependency is down.

    hard_fail   a dependency failed, so the request failed
    tiered      fall back: stale cache for retrieval, raw snippet for the model
    breaker     the same fallbacks, plus stop calling a dependency that is not
                answering

The temptation in this chapter is to measure availability and declare the
fallback the winner. Availability is the easy half. A fallback converts an
outage into an answer, and the question that decides the design is *what kind
of answer*, because "we were up" and "we told people the truth" are different
claims and only one of them shows on a status page.

So answers are graded three ways, not two:

    exact        identical to what the healthy pipeline would have said
    supported    a real sentence from the right current document, but not the
                 answer the healthy app gives. An honest degradation: the user
                 got a relevant help article instead of an answer.
    unsupported  not in the current document at all. Stale content, or the
                 model improvising with no context. This is the number a
                 fallback design has to justify.

Grading against the healthy pipeline's own output, rather than against a
hand-written key, is what stops the metric being gameable: a design cannot
score well by dumping the whole document, because the whole document is not
what the healthy app says.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from app import providers, retrieval, service
from app.corpus import DOCUMENTS_BY_ID

SYSTEM_TEMPLATE = service.SYSTEM_TEMPLATE

# A snapshot taken before the last policy change, which is what a cache
# actually is. Two of these are now wrong, and nothing in the cache knows it.
# This is the cost of serving stale data during an outage, made concrete:
# doc-01 says 24 hours where the live document says 30 minutes.
STALE_SNAPSHOT: dict[str, str] = {
    "doc-01": (
        "To reset your password, open Settings > Security > Reset password and follow "
        "the emailed link. The link expires 24 hours after it is sent."
    ),
    "doc-03": (
        "Refunds are available within 14 days of purchase. Open Billing > History, find "
        "the charge, and choose Request refund."
    ),
    "doc-06": (
        "There are three plans: Free, one project and community support. Pro, $12 per "
        "month, unlimited projects. Team, $29 per user per month, adding shared "
        "workspaces, SSO, and a 99.9% uptime commitment."
    ),
}


@dataclass
class Reply:
    text: str
    sources: tuple[str, ...]
    error: str = ""
    timed_out: bool = False
    degraded: str = ""  # which tier answered, empty when the healthy path did
    latency_ms: float = 0.0


def grade(reply: Reply, question: str, healthy_text: str) -> str:
    """exact | supported | unsupported | timeout | error."""
    if reply.timed_out:
        return "timeout"
    if reply.error:
        return "error"
    if reply.text.strip() == healthy_text.strip():
        return "exact"
    gold = service.gold_source(question)
    if gold and service.answered_from(reply.text, gold):
        return "supported"
    return "unsupported"


def _stale_context(question: str) -> tuple[str, tuple[str, ...]]:
    """Serve the cached snapshot. No retrieval, no freshness, no way to tell."""
    words = {w.strip("?.,").lower() for w in question.split() if len(w) > 3}
    best_id, best_score = "", 0
    for doc_id, text in STALE_SNAPSHOT.items():
        score = sum(1 for w in words if w in text.lower())
        if score > best_score:
            best_id, best_score = doc_id, score
    if not best_id:
        return "", ()
    return f"[{best_id}] {STALE_SNAPSHOT[best_id]}", (best_id,)


def _snippet(documents) -> str:
    """The model is gone, so hand back the most relevant sentence we retrieved.

    A defensible degradation: the user gets a real paragraph from the real help
    centre. It is not the answer, and the grading says so.
    """
    if not documents:
        return ""
    return documents[0].text.split(". ")[0].strip() + "."


# ---------------------------------------------------------------------------


def hard_fail(question: str, request_id: str, timeout_ms: float | None) -> Reply:
    """Any dependency error is a failed request. The day-one shape."""
    started = time.perf_counter()
    try:
        hits = retrieval.search(question, request_id=request_id)
        response = providers.generate(
            SYSTEM_TEMPLATE.format(context=retrieval.format_context(hits.documents)),
            question,
            request_id=request_id,
            timeout_ms=timeout_ms,
        )
        return Reply(
            response.text,
            tuple(d.doc_id for d in hits.documents),
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
    except providers.ProviderTimeout as exc:
        return Reply("", (), error=str(exc), timed_out=True,
                     latency_ms=(time.perf_counter() - started) * 1000.0)
    except (retrieval.RetrievalUnavailable, providers.TransientProviderError) as exc:
        return Reply("", (), error=f"{type(exc).__name__}: {exc}",
                     latency_ms=(time.perf_counter() - started) * 1000.0)


def tiered(question: str, request_id: str, timeout_ms: float | None) -> Reply:
    """Never fail if there is anything left to say.

    Tier 1: live retrieval plus the model.
    Tier 2: retrieval is down, so use the stale snapshot as context.
    Tier 3: the model is down, so return the best retrieved snippet, unmodelled.
    Tier 4: nothing is up, so say so.
    """
    started = time.perf_counter()
    degraded = ""
    try:
        hits = retrieval.search(question, request_id=request_id)
        context = retrieval.format_context(hits.documents)
        sources = tuple(d.doc_id for d in hits.documents)
        documents = hits.documents
    except retrieval.RetrievalUnavailable:
        context, sources = _stale_context(question)
        documents = ()
        degraded = "stale-cache"

    try:
        response = providers.generate(
            SYSTEM_TEMPLATE.format(context=context),
            question,
            request_id=request_id,
            timeout_ms=timeout_ms,
        )
        return Reply(response.text, sources, degraded=degraded,
                     latency_ms=(time.perf_counter() - started) * 1000.0)
    except providers.TransientProviderError:
        text = _snippet(documents)
        elapsed = (time.perf_counter() - started) * 1000.0
        if not text:
            return Reply("", sources, error="all tiers unavailable",
                         degraded="exhausted", latency_ms=elapsed)
        return Reply(text, sources, degraded="snippet-no-model", latency_ms=elapsed)


class Breaker:
    """Stop calling a dependency that has stopped answering.

    Only worth having for the fault the other two designs handle worst: a
    dependency that is slow rather than dead. A dead one fails in
    milliseconds and costs nothing to keep trying. A slow one holds a worker
    for the entire client deadline on every single request, and the fallback
    tier never even gets reached until that deadline has already been burned.
    """

    def __init__(self, threshold: int = 3, probe_every: int = 12) -> None:
        self.threshold = threshold
        self.probe_every = probe_every
        self.failures = 0
        self.open = False
        self.since_open = 0
        self.lock = threading.Lock()

    def reset(self) -> None:
        with self.lock:
            self.failures = 0
            self.open = False
            self.since_open = 0

    def allow(self) -> bool:
        with self.lock:
            if not self.open:
                return True
            self.since_open += 1
            if self.since_open >= self.probe_every:
                self.since_open = 0
                return True  # half-open: let one through to see if it recovered
            return False

    def record(self, ok: bool) -> None:
        with self.lock:
            if ok:
                self.failures = 0
                self.open = False
            else:
                self.failures += 1
                if self.failures >= self.threshold:
                    self.open = True


BREAKER = Breaker()


def breaker(question: str, request_id: str, timeout_ms: float | None) -> Reply:
    """The tiered fallbacks, plus a breaker in front of the model."""
    started = time.perf_counter()
    degraded = ""
    try:
        hits = retrieval.search(question, request_id=request_id)
        context = retrieval.format_context(hits.documents)
        sources = tuple(d.doc_id for d in hits.documents)
        documents = hits.documents
    except retrieval.RetrievalUnavailable:
        context, sources = _stale_context(question)
        documents = ()
        degraded = "stale-cache"

    if not BREAKER.allow():
        text = _snippet(documents)
        elapsed = (time.perf_counter() - started) * 1000.0
        if not text:
            return Reply("", sources, error="breaker open, no snippet",
                         degraded="breaker-open", latency_ms=elapsed)
        return Reply(text, sources, degraded="breaker-open", latency_ms=elapsed)

    try:
        response = providers.generate(
            SYSTEM_TEMPLATE.format(context=context),
            question,
            request_id=request_id,
            timeout_ms=timeout_ms,
        )
        BREAKER.record(True)
        return Reply(response.text, sources, degraded=degraded,
                     latency_ms=(time.perf_counter() - started) * 1000.0)
    except providers.TransientProviderError:
        BREAKER.record(False)
        text = _snippet(documents)
        elapsed = (time.perf_counter() - started) * 1000.0
        if not text:
            return Reply("", sources, error="all tiers unavailable",
                         degraded="exhausted", latency_ms=elapsed)
        return Reply(text, sources, degraded="snippet-no-model", latency_ms=elapsed)


DESIGNS = (
    ("hard fail", hard_fail),
    ("tiered fallback", tiered),
    ("tiered + breaker", breaker),
)


def healthy_answers(questions) -> dict[str, str]:
    """What the app says when nothing is wrong. The grading key.

    Computed, not written down, so it cannot drift away from what the app
    actually does.
    """
    service.reset_all()
    providers.configure_mock(latency="instant", seed=1337)
    answers = {}
    for i, question in enumerate(questions):
        reply = hard_fail(question, f"key{i:03d}", None)
        answers[question] = reply.text
    service.reset_all()
    return answers


__all__ = [
    "BREAKER",
    "DESIGNS",
    "DOCUMENTS_BY_ID",
    "Reply",
    "breaker",
    "grade",
    "hard_fail",
    "healthy_answers",
    "tiered",
]
