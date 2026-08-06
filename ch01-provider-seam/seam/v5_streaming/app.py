"""
v5, seam: requirement 5, stream the answer to the client.

The call site change here is small, but note what it is: `answer` now calls a
different function on the seam, because the seam had to grow one. The cost of
this requirement is in provider.py, not here, and the diff numbers say so.
"""

from app import retrieval

from . import provider


def triage(question: str) -> str:
    return provider.generate(
        "CLASSIFY the support request into one label.", question, max_tokens=8
    ).text


def answer(
    question: str, tenant: str = "acme", on_token=None
) -> tuple[str, tuple[str, ...]]:
    hits = retrieval.search(question, tenant=tenant)
    context = retrieval.format_context(hits.documents)
    chunks: list[str] = []
    for chunk in provider.generate_stream(
        f"Answer only from the context.\n\nCONTEXT:\n{context}", question, max_tokens=512
    ):
        chunks.append(chunk)
        if on_token is not None:
            on_token(chunk)
    return "".join(chunks).strip(), tuple(d.doc_id for d in hits.documents)


def digest(text: str) -> str:
    return provider.generate(
        "SUMMARIZE the text in one sentence.", text, max_tokens=64
    ).text


def escalate(question: str) -> str:
    return provider.generate(
        "SUMMARIZE the request as a one-line escalation note.", question, max_tokens=128
    ).text
