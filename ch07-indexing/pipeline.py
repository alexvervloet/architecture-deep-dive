"""
ch07/pipeline.py: four places to build the index, and a corpus that changes.

Retrieval needs an index. The index is derived from documents, documents
change, and the only interesting question is *when* the derivation runs:

    per_request     rebuild the index on every request
    process_start   build it once at boot and never again
    scheduled       rebuild it on a timer
    write_through   update it when a document changes

The first is the antipattern the RAG dive warns about. The second is the one
almost everybody actually ships, usually without deciding to: load the
documents into a list at import time, embed them, and never think about it
again. Its staleness is unbounded and its clock is your deploy cadence.

What makes this an LLM-app problem rather than a generic caching problem is
the cost shape. Rebuilding a derived index is normally CPU you can spare;
rebuilding an embedding index is a per-token bill to a third party, so
"just refresh more often" has a price tag, and "refresh on every request"
has an absurd one. The chapter puts numbers on both ends.

Staleness is measured by its consequence, not by a counter: a stale index
makes the app quote a policy that is no longer true, and that answer is graded
wrong against the current document.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.corpus import DOCUMENTS, Document

# Embedding prices, in the neighbourhood of a small hosted embedding model
# (text-embedding-3-small was $0.02 per million tokens in 2026). Latency is
# simulated per document so that per-request indexing has a visible cost
# without needing a network.
EMBED_USD_PER_1K_CHARS = 0.000005
EMBED_MS_PER_DOC = 3.0


@dataclass
class Meter:
    """Everything the pipeline spends, counted in one place."""

    embed_calls: int = 0
    embed_chars: int = 0
    embed_ms: float = 0.0

    @property
    def embed_usd(self) -> float:
        return self.embed_chars / 1000 * EMBED_USD_PER_1K_CHARS

    def reset(self) -> None:
        self.embed_calls = 0
        self.embed_chars = 0
        self.embed_ms = 0.0


METER = Meter()


def embed(documents: tuple[Document, ...], *, sleep: bool = True) -> dict[str, str]:
    """Stand-in for embedding a set of documents.

    Returns the indexed text, because the retrieval in this repo is keyword
    based. The vectors are not the point; the *cost of producing them* and the
    *moment they were produced* are, and both are faithfully accounted here.
    """
    METER.embed_calls += 1
    chars = sum(len(d.text) for d in documents)
    METER.embed_chars += chars
    duration = EMBED_MS_PER_DOC * len(documents)
    METER.embed_ms += duration
    if sleep:
        time.sleep(duration / 1000.0)
    return {d.doc_id: d.text for d in documents}


# ---------------------------------------------------------------------------
# A corpus that changes underneath the index
# ---------------------------------------------------------------------------


@dataclass
class Edit:
    tick: int
    doc_id: str
    new_text: str
    note: str


# Three policy changes, spread across the run. Each one changes the sentence
# that answers a question the workload asks, so a stale index does not merely
# lag: it states a number that is no longer true.
#
# The tick numbers are deliberately not round. An earlier version used 10, 25
# and 40, which are multiples of several of the rebuild intervals the sweep
# tries, so those intervals scored a worst-case lag of zero purely because an
# edit happened to land on a rebuild. 11, 27 and 43 are coprime with the
# intervals and give each schedule its honest average.
EDITS: tuple[Edit, ...] = (
    Edit(
        11,
        "doc-01",
        "To reset your password, open Settings > Security > Reset password and follow "
        "the emailed link. The link expires 15 minutes after it is sent. If it expires, "
        "request a new one; old links cannot be revived.",
        "reset link shortened from 30 to 15 minutes",
    ),
    Edit(
        27,
        "doc-03",
        "Refunds are available within 60 days of purchase. Open Billing > History, find "
        "the charge, and choose Request refund. Approved refunds post to the original "
        "payment method in 3 to 5 business days.",
        "refund window widened to 60 days, posting time shortened",
    ),
    Edit(
        43,
        "doc-07",
        "API requests are limited to 120 per minute on Free, 1200 per minute on Pro, and "
        "12000 per minute on Team. Exceeding the limit returns HTTP 429 with a "
        "Retry-After header. Limits are per organization, not per key.",
        "rate limits doubled across all plans",
    ),
)


class Corpus:
    """The live documents. Edits land here; indexes catch up when they catch up."""

    def __init__(self) -> None:
        self.documents: dict[str, Document] = {d.doc_id: d for d in DOCUMENTS}
        self.version = 0

    def apply(self, edit: Edit) -> None:
        old = self.documents[edit.doc_id]
        self.documents[edit.doc_id] = Document(
            old.doc_id, old.tenant, old.updated_at + 1, old.title, edit.new_text
        )
        self.version += 1

    def snapshot(self) -> tuple[Document, ...]:
        return tuple(self.documents.values())

    def current_text(self, doc_id: str) -> str:
        return self.documents[doc_id].text


# ---------------------------------------------------------------------------
# The four index strategies
# ---------------------------------------------------------------------------


@dataclass
class Index:
    """Indexed text plus the corpus version it was built from.

    `built_at_version` is what makes staleness observable rather than
    theoretical: the index knows which generation of the corpus it saw, and
    the harness can compare that to the live one. A real vector store rarely
    carries this, which is exactly why staleness is so easy to ship.
    """

    text_by_id: dict[str, str] = field(default_factory=dict)
    built_at_version: int = -1

    def search(self, question: str, top_k: int = 3) -> list[str]:
        words = {w.strip("?.,").lower() for w in question.split() if len(w) > 3}
        scored = []
        for doc_id, text in self.text_by_id.items():
            score = sum(1 for w in words if w in text.lower())
            if score:
                scored.append((-score, doc_id))
        scored.sort()
        return [doc_id for _, doc_id in scored[:top_k]]

    def context(self, doc_ids: list[str]) -> str:
        return "\n".join(f"[{doc_id}] {self.text_by_id[doc_id]}" for doc_id in doc_ids)


class PerRequestIndex:
    """Rebuild everything, every request. Never stale, never affordable."""

    label = "per request"

    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus

    def get(self, tick: int) -> Index:
        documents = self.corpus.snapshot()
        return Index(embed(documents), self.corpus.version)


class ProcessStartIndex:
    """Build once at boot. The default nobody chose.

    Staleness here has no upper bound. It is not "a few minutes behind", it is
    "however long since the last deploy", which on a stable service is the
    worst answer in this file.
    """

    label = "process start"

    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self.index = Index(embed(corpus.snapshot(), sleep=False), corpus.version)

    def get(self, tick: int) -> Index:
        return self.index


class ScheduledIndex:
    """Rebuild on a timer. Bounded staleness, bounded cost, one dial."""

    def __init__(self, corpus: Corpus, interval: int) -> None:
        self.corpus = corpus
        self.interval = interval
        self.label = f"scheduled/{interval}"
        self.index = Index(embed(corpus.snapshot(), sleep=False), corpus.version)
        self.last_build = 0

    def get(self, tick: int) -> Index:
        if tick - self.last_build >= self.interval:
            self.index = Index(embed(self.corpus.snapshot(), sleep=False), self.corpus.version)
            self.last_build = tick
        return self.index


class WriteThroughIndex:
    """Re-embed one document when that document changes.

    Fresh and cheap, and it buys both by coupling the write path to the index.
    Every path that can modify a document now has to remember to call this,
    and the one that forgets produces a permanently stale entry that no timer
    will ever repair. That failure mode is worse than the one it replaces,
    because it is silent and unbounded rather than periodic and predictable.
    """

    label = "write through"

    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self.index = Index(embed(corpus.snapshot(), sleep=False), corpus.version)

    def on_edit(self, doc_id: str) -> None:
        document = self.corpus.documents[doc_id]
        self.index.text_by_id.update(embed((document,), sleep=False))
        self.index.built_at_version = self.corpus.version

    def get(self, tick: int) -> Index:
        return self.index
