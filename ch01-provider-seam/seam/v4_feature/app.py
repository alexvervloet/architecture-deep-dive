"""
v4, seam: requirement 4, add a fourth model-backed feature.

Four lines, and the new feature is retried, timed out, and cost-accounted
because it cannot not be. This is the compounding the earlier versions were
paying for.
"""

from app import retrieval

from . import provider


def triage(question: str) -> str:
    return provider.generate(
        "CLASSIFY the support request into one label.", question, max_tokens=8
    ).text


def answer(question: str, tenant: str = "acme") -> tuple[str, tuple[str, ...]]:
    hits = retrieval.search(question, tenant=tenant)
    context = retrieval.format_context(hits.documents)
    reply = provider.generate(
        f"Answer only from the context.\n\nCONTEXT:\n{context}", question, max_tokens=512
    )
    return reply.text, tuple(d.doc_id for d in hits.documents)


def digest(text: str) -> str:
    return provider.generate(
        "SUMMARIZE the text in one sentence.", text, max_tokens=64
    ).text


def escalate(question: str) -> str:
    return provider.generate(
        "SUMMARIZE the request as a one-line escalation note.", question, max_tokens=128
    ).text
