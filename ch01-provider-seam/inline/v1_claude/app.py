"""
v1, inline: requirement 1, move from OpenAI to Anthropic.

Every call site changed, and not superficially. The system prompt leaves the
messages list and becomes a top-level argument, max_tokens stops being
optional, and the response body is a list of content blocks instead of a
string. Three call sites, three kinds of change each.
"""

from app import retrieval
from app.fake_sdks import Anthropic

client = Anthropic()
MODEL = "claude-haiku-4-5"


def triage(question: str) -> str:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=8,
        system="CLASSIFY the support request into one label.",
        messages=[
            {"role": "user", "content": question},
        ],
    )
    return resp.content[0].text.strip()


def answer(question: str, tenant: str = "acme") -> tuple[str, tuple[str, ...]]:
    hits = retrieval.search(question, tenant=tenant)
    context = retrieval.format_context(hits.documents)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=f"Answer only from the context.\n\nCONTEXT:\n{context}",
        messages=[
            {"role": "user", "content": question},
        ],
    )
    return resp.content[0].text.strip(), tuple(d.doc_id for d in hits.documents)


def digest(text: str) -> str:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=64,
        system="SUMMARIZE the text in one sentence.",
        messages=[
            {"role": "user", "content": text},
        ],
    )
    return resp.content[0].text.strip()
