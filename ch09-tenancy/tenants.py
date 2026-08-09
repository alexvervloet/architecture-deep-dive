"""
ch09/tenants.py: four places to put the tenant boundary.

Two customers share an installation. Each has internal notes the other must
never see. The shared help centre is visible to both. The only question is
where the permission check happens relative to retrieval and to the model
call, and the four answers are not equally safe or equally good:

    after_model      retrieve from everything, answer, then filter what you
                     show. Leaks.
    after_retrieval  retrieve from everything, drop what this tenant may not
                     see, then answer. Safe, and loses recall under a condition
                     the chapter had to go looking for (small top-k).
    in_retrieval     apply the tenant predicate inside the search. Safe, keeps
                     recall, and makes a cross-tenant cache unsound.
    per_tenant_index one index per tenant. Safe, fastest per query, and pays
                     for it at build time and in duplicated shared documents.

The first is the one that ships, because it is the only one that does not
require the retrieval layer to know about tenants. Filtering the citation list
after the fact looks like a permission check and is not one: by then the
forbidden text has been in the prompt, and whatever the model did with it is
already in the answer.

The tenant documents here are deliberately *competitive*. They use the same
vocabulary as the shared help centre, because a boundary is only tested when
the thing on the other side of it is something the retriever wants.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.corpus import DOCUMENTS, Document

SHARED = tuple(d for d in DOCUMENTS if d.tenant == "shared")

# Internal notes. Every one of them is written to rank well against a question
# an ordinary user of the *other* tenant would ask, which is the point: a
# boundary that is only tested against irrelevant documents is not tested.
TENANT_DOCS: tuple[Document, ...] = (
    Document(
        "acme-01", "acme", 1, "Acme internal: password policy exception",
        "Acme has a negotiated exception: their password reset links expire after 24 "
        "hours rather than 30 minutes, because their helpdesk batches requests "
        "overnight. Do not mention this arrangement to other customers.",
    ),
    Document(
        "acme-02", "acme", 1, "Acme internal: negotiated terms",
        "Acme Corp is on a custom Team agreement at $19 per user per month with a "
        "3-year commitment. Renewal is due in March. Do not quote this rate to other "
        "customers under any circumstances.",
    ),
    Document(
        "acme-03", "acme", 1, "Acme internal: rate limit override",
        "Acme's organization has a raised API rate limit of 30000 requests per minute, "
        "agreed as part of their migration. This override is not available on any "
        "published plan.",
    ),
    Document(
        "acme-04", "acme", 1, "Acme internal: refund escalation",
        "Refunds for Acme go through their named account manager rather than the "
        "self-serve Billing flow, and their approved refund window is 90 days rather "
        "than the standard 30.",
    ),
    Document(
        "globex-01", "globex", 1, "Globex internal: migration plan",
        "Globex is migrating 4,200 seats off a competitor in two waves. Their security "
        "review flagged audit log retention as a blocker and we committed to 24 months "
        "for them specifically.",
    ),
    Document(
        "globex-02", "globex", 1, "Globex internal: SSO rollout",
        "Globex enables SSO in stages, and password login stays available for their "
        "sales org until the second wave completes. Require SSO is deliberately off "
        "for them until then.",
    ),
    Document(
        "globex-03", "globex", 1, "Globex internal: data export exception",
        "Globex data exports are delivered to a private bucket rather than an emailed "
        "link, and their archive links are valid for 30 days instead of seven.",
    ),
    Document(
        "globex-04", "globex", 1, "Globex internal: pricing",
        "Globex pays $24 per user per month on Team, discounted for volume, with a "
        "12-month commitment and quarterly true-up.",
    ),
)

# Extra tenants for the crowding sweep, generated rather than written out.
# Each one gets the same four kinds of internal note as Acme and Globex, with
# its own numbers, because the question the sweep asks is what happens to a
# shared index as the *proportion* of documents any given tenant may not see
# goes up. Two tenants is a demo; a real installation has hundreds.
_TEMPLATES = (
    ("password policy", "{name} has a negotiated exception: their password reset links "
     "expire after {a} hours rather than 30 minutes, agreed with their helpdesk."),
    ("negotiated terms", "{name} is on a custom Team agreement at ${a} per user per "
     "month with a multi-year commitment. Do not quote this rate to other customers."),
    ("rate limit override", "{name}'s organization has a raised API rate limit of "
     "{a}000 requests per minute, agreed as part of their migration."),
    ("refund escalation", "Refunds for {name} go through their named account manager "
     "rather than the self-serve Billing flow, with a {a}-day approved refund window."),
)


def _generated_tenant_docs(index: int) -> tuple[Document, ...]:
    name = f"Tenant{index}"
    slug = f"t{index}"
    return tuple(
        Document(
            f"{slug}-{i:02d}", slug, 1, f"{name} internal: {title}",
            body.format(name=name, a=10 + index * 3 + i),
        )
        for i, (title, body) in enumerate(_TEMPLATES)
    )


ALL_DOCS: tuple[Document, ...] = SHARED + TENANT_DOCS
TENANTS: tuple[str, ...] = ("acme", "globex")


def configure(extra_tenants: int = 0) -> None:
    """Rebuild the corpus with `extra_tenants` synthetic tenants beyond the two
    hand-written ones. Everything downstream reads ALL_DOCS and TENANTS."""
    global ALL_DOCS, TENANTS
    generated: tuple[Document, ...] = ()
    names = ["acme", "globex"]
    for i in range(extra_tenants):
        generated += _generated_tenant_docs(i)
        names.append(f"t{i}")
    ALL_DOCS = SHARED + TENANT_DOCS + generated
    TENANTS = tuple(names)
    build_per_tenant_indexes()


def visible_to(tenant: str) -> tuple[Document, ...]:
    return tuple(d for d in ALL_DOCS if d.tenant in ("shared", tenant))


def forbidden_for(tenant: str) -> tuple[Document, ...]:
    return tuple(d for d in ALL_DOCS if d.tenant not in ("shared", tenant))


@dataclass
class Retrieval:
    documents: tuple[Document, ...]
    scanned: int  # candidate documents the search had to score


def _score(question: str, doc: Document) -> int:
    words = {w.strip("?.,").lower() for w in question.split() if len(w) > 3}
    haystack = f"{doc.title} {doc.text}".lower()
    return sum(1 for w in words if w in haystack)


def _rank(question: str, candidates: tuple[Document, ...], top_k: int) -> tuple[Document, ...]:
    ranked = sorted(candidates, key=lambda d: (-_score(question, d), d.doc_id))
    return tuple(d for d in ranked if _score(question, d) > 0)[:top_k]


# ---------------------------------------------------------------------------
# The four boundary placements
# ---------------------------------------------------------------------------


def retrieve_after_model(question: str, tenant: str, top_k: int = 3) -> Retrieval:
    """No filtering before the prompt. Everything is a candidate."""
    return Retrieval(_rank(question, ALL_DOCS, top_k), len(ALL_DOCS))


def retrieve_after_retrieval(question: str, tenant: str, top_k: int = 3) -> Retrieval:
    """Rank globally, then drop what this tenant may not see.

    Safe, and it silently costs recall: the forbidden documents already spent
    the top-k slots before they were removed, so the tenant's own relevant
    document can be pushed out of a list it should have been in. Nothing
    errors, and the answer just gets worse.
    """
    ranked = _rank(question, ALL_DOCS, top_k)
    allowed = tuple(d for d in ranked if d.tenant in ("shared", tenant))
    return Retrieval(allowed, len(ALL_DOCS))


def retrieve_in_retrieval(question: str, tenant: str, top_k: int = 3) -> Retrieval:
    """Filter inside the search, so top-k is drawn only from allowed documents.

    Safe and no recall loss. The costs are elsewhere: the index has to support
    a per-tenant predicate, and any cache keyed on the question alone is now
    wrong, because the same question has different correct results per tenant.
    """
    candidates = visible_to(tenant)
    return Retrieval(_rank(question, candidates, top_k), len(candidates))


_PER_TENANT_INDEX: dict[str, tuple[Document, ...]] = {}


def build_per_tenant_indexes() -> dict[str, int]:
    """One index per tenant, with the shared documents copied into each.

    Returns documents indexed per tenant, which is the cost this design pays:
    the shared corpus is duplicated once per tenant, so total indexed documents
    grow with tenants even though the shared content did not change.
    """
    _PER_TENANT_INDEX.clear()
    for tenant in TENANTS:
        _PER_TENANT_INDEX[tenant] = visible_to(tenant)
    return {t: len(docs) for t, docs in _PER_TENANT_INDEX.items()}


def retrieve_per_tenant_index(question: str, tenant: str, top_k: int = 3) -> Retrieval:
    candidates = _PER_TENANT_INDEX[tenant]
    return Retrieval(_rank(question, candidates, top_k), len(candidates))


DESIGNS = (
    ("filter after model", retrieve_after_model, True),
    ("filter after retrieval", retrieve_after_retrieval, False),
    ("filter in retrieval", retrieve_in_retrieval, False),
    ("index per tenant", retrieve_per_tenant_index, False),
)
