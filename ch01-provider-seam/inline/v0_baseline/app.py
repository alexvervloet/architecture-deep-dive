"""
v0, inline: the app as most apps start. Three features, three SDK call sites.

Nothing is wrong with this code. It is short, it is obvious, and every call
site says exactly what it does with no indirection to chase. If the app never
changes, this version wins on every measure, and the chapter says so.
"""

from app import retrieval
from app.fake_sdks import OpenAI

client = OpenAI()
MODEL = "gpt-4o-mini"


def triage(question: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "CLASSIFY the support request into one label."},
            {"role": "user", "content": question},
        ],
        max_tokens=8,
    )
    return resp.choices[0].message.content.strip()


def answer(question: str, tenant: str = "acme") -> tuple[str, tuple[str, ...]]:
    hits = retrieval.search(question, tenant=tenant)
    context = retrieval.format_context(hits.documents)
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": f"Answer only from the context.\n\nCONTEXT:\n{context}",
            },
            {"role": "user", "content": question},
        ],
        max_tokens=512,
    )
    return resp.choices[0].message.content.strip(), tuple(d.doc_id for d in hits.documents)


def digest(text: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "SUMMARIZE the text in one sentence."},
            {"role": "user", "content": text},
        ],
        max_tokens=64,
    )
    return resp.choices[0].message.content.strip()
