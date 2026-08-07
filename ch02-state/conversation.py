"""
ch02/conversation.py: two-turn conversations whose second turn cannot be
answered without the first.

The measurement needs lost memory to have a *consequence*, not just a smaller
number in a counter. So every follow-up here is a question that is ambiguous
on its own and unambiguous after the opener, and the app resolves it the way
most real apps do: by building the retrieval query from the recent history
plus the new question.

Lose the history and the query is just the follow-up. The retriever then finds
a different document, and the app answers confidently and wrongly. No
exception, no warning, and the reply reads fine.

The first two pairs are the same follow-up sentence, word for word, with two
different correct answers:

    "How do I reset my password?"  ->  "How long is that link valid?"  ->  30 minutes
    "How do I export my data?"     ->  "How long is that link valid?"  ->  seven days

Nothing about the second message distinguishes them. Only the conversation
does, which is the property the architecture either preserves or loses.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app import providers, retrieval
from app.corpus import DOCUMENTS_BY_ID
from stores import Turn  # chapter-local; stress.py puts this directory on sys.path

SYSTEM_TEMPLATE = """You are the support assistant for a SaaS product.
Answer only from the context below.

CONTEXT:
{context}"""


@dataclass(frozen=True)
class Exchange:
    opener: str
    followup: str
    gold_doc: str
    note: str = ""


CONVERSATIONS: tuple[Exchange, ...] = (
    Exchange(
        "How do I reset my password?",
        "How long is that link valid?",
        "doc-01",
        "without history this retrieves the data-export link instead: seven days, not 30 minutes",
    ),
    Exchange(
        "How do I export my data?",
        "How long is that link valid?",
        "doc-05",
        "identical follow-up to the pair above, different correct answer",
    ),
    Exchange(
        "Can I get a refund?",
        "How long does that take to post?",
        "doc-03",
    ),
    Exchange(
        "How do I cancel my subscription?",
        "Does that delete my data?",
        "doc-04",
    ),
    Exchange(
        "What are the API rate limits?",
        "What happens if I exceed them?",
        "doc-07",
    ),
    Exchange(
        "What plans are available?",
        "Which one includes SSO?",
        "doc-08",
    ),
)

# A seventh pair was cut rather than kept: "Which plan includes SSO?" ->
# "What does that plan cost?" needs two hops (SSO to Team, Team to $29) and
# failed even with a perfect history. Keeping it would have put a permanent
# 4-in-24 error floor under every column, and a reader could not then tell
# which failures came from lost state and which from a retriever that cannot
# chain. Multi-hop retrieval is a real problem; it is not this chapter's.


def answered_from(text: str, doc_id: str) -> bool:
    """Did this answer actually come out of that document?

    Not "was the right document retrieved". Retrieval returns three documents
    and the gold one is usually among them even when the query is missing its
    context, so scoring on the retrieved set reports 100% correct while the
    user is being told the wrong thing. The question that matters is which
    document the sentence came from, and because the mock is extractive, that
    is checkable exactly rather than judged.
    """
    body = DOCUMENTS_BY_ID[doc_id].text.lower()
    return text.rstrip(".").strip().lower() in body


def build_query(history: list[Turn], question: str) -> str:
    """Retrieval query = the last thing the user said, plus what they just said.

    This is the cheap, common version of query rewriting. A fancier app sends
    the history to a model and asks for a standalone question; it would show
    the same failure, one model call more expensively, because the input to
    that rewrite is the history that is missing.
    """
    prior = [t.content for t in history if t.role == "user"]
    if not prior:
        return question
    return f"{prior[-1]} {question}"


@dataclass
class TurnResult:
    text: str
    sources: tuple[str, ...]
    history_turns: int
    store_ms: float
    model_ms: float


def handle_turn(store, session_id: str, question: str, request_id: str) -> TurnResult:
    started = time.perf_counter()
    history = store.load(session_id)
    load_ms = (time.perf_counter() - started) * 1000.0

    query = build_query(history, question)
    hits = retrieval.search(query, request_id=request_id)
    context = retrieval.format_context(hits.documents)
    response = providers.generate(
        SYSTEM_TEMPLATE.format(context=context), query, request_id=request_id
    )

    started_append = time.perf_counter()
    store.append(
        session_id,
        [Turn("user", question), Turn("assistant", response.text)],
    )
    append_ms = (time.perf_counter() - started_append) * 1000.0

    return TurnResult(
        text=response.text,
        sources=tuple(d.doc_id for d in hits.documents),
        history_turns=len(history),
        store_ms=load_ms + append_ms,
        model_ms=response.latency_ms,
    )
