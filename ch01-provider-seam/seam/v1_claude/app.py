"""
v0, seam: the same three features, with no provider vocabulary anywhere.

Compare this file to inline/v0_baseline/app.py. It is shorter, but the variant
as a whole is longer, because provider.py exists. That is the trade being
measured: fewer lines here, an extra file and an extra concept there.
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
