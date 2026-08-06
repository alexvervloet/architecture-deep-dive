"""
app/corpus.py: the fixed knowledge base the reference app answers from.

Small on purpose. Twelve documents is enough for retrieval to be wrong
sometimes (which the chapters need) and small enough that you can hold the
whole corpus in your head while reading a trace.

Two fields exist for chapters that have not been written yet, and they are
here now because backfilling them later would invalidate every measurement
taken before the change:

  `tenant`      which customer owns the document. `acme` and `globex` have
                documents that must never appear in each other's answers,
                which is the leak test in ch09.
  `updated_at`  a day number, not a date. Chapter 7 measures staleness: how
                long a query-time index and a scheduled index disagree after a
                document changes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Document:
    doc_id: str
    tenant: str
    updated_at: int
    title: str
    text: str


# `shared` documents are visible to every tenant; the product docs of a
# fictional SaaS. The tenant-owned ones are internal notes, and they are the
# ones that must not cross a boundary.
DOCUMENTS: tuple[Document, ...] = (
    Document(
        "doc-01", "shared", 1, "Resetting your password",
        "To reset your password, open Settings > Security > Reset password and follow "
        "the emailed link. The link expires 30 minutes after it is sent. If it expires, "
        "request a new one; old links cannot be revived.",
    ),
    Document(
        "doc-02", "shared", 1, "Two-factor authentication",
        "Enable two-factor auth under Settings > Security > Two-factor. Save the backup "
        "codes shown when you enable it. If you lose your device and your backup codes, "
        "support must verify your identity manually, which takes up to three days.",
    ),
    Document(
        "doc-03", "shared", 1, "Refunds",
        "Refunds are available within 30 days of purchase. Open Billing > History, find "
        "the charge, and choose Request refund. Approved refunds post to the original "
        "payment method in 5 to 10 business days.",
    ),
    Document(
        "doc-04", "shared", 1, "Cancelling a subscription",
        "Cancel anytime under Billing > Plan > Cancel. The plan stays active until the "
        "end of the current billing period and you are not charged again. Cancelling "
        "does not delete your data; export it first if you want a copy.",
    ),
    Document(
        "doc-05", "shared", 1, "Exporting your data",
        "Export under Settings > Data > Export. We build a downloadable archive and "
        "email a link when it is ready, usually within an hour. The link is valid for "
        "seven days.",
    ),
    Document(
        "doc-06", "shared", 1, "Plans and pricing",
        "There are three plans: Free, one project and community support. Pro, $12 per "
        "month, unlimited projects. Team, $29 per user per month, adding shared "
        "workspaces, SSO, and a 99.9% uptime commitment.",
    ),
    Document(
        "doc-07", "shared", 1, "Rate limits",
        "API requests are limited to 60 per minute on Free, 600 per minute on Pro, and "
        "6000 per minute on Team. Exceeding the limit returns HTTP 429 with a "
        "Retry-After header. Limits are per organization, not per key.",
    ),
    Document(
        "doc-08", "shared", 1, "Single sign-on",
        "SSO is available on the Team plan and supports SAML 2.0 and OIDC. Configure it "
        "under Settings > Organization > SSO. Enabling SSO does not disable password "
        "login until you also turn on Require SSO.",
    ),
    Document(
        "doc-09", "shared", 1, "Service status and incidents",
        "Live status is published at status.example. Incidents are posted within 15 "
        "minutes of detection and updated every 30 minutes until resolved. Postmortems "
        "for Sev1 incidents are published within five business days.",
    ),
    Document(
        "doc-10", "shared", 1, "Data retention",
        "Deleted projects are recoverable for 30 days, then purged. Audit logs are "
        "retained for 12 months on Team and 30 days on other plans.",
    ),
    # Tenant-owned. Answering an Acme user with doc-12, or a Globex user with
    # doc-11, is the failure ch09 measures.
    Document(
        "doc-11", "acme", 1, "Acme internal: negotiated terms",
        "Acme Corp is on a custom Team agreement at $19 per user per month with a "
        "3-year commitment, negotiated by their VP of Engineering. Renewal is due in "
        "March. Do not quote this rate to other customers.",
    ),
    Document(
        "doc-12", "globex", 1, "Globex internal: migration plan",
        "Globex is migrating 4,200 seats off a competitor in two waves. Wave one is "
        "engineering, wave two is sales. Their security review flagged our audit log "
        "retention as a blocker and we committed to 24 months for them specifically.",
    ),
)

DOCUMENTS_BY_ID = {doc.doc_id: doc for doc in DOCUMENTS}


def visible_to(tenant: str) -> tuple[Document, ...]:
    """The documents a given tenant is allowed to see.

    The reference app calls this. Chapter 9 is entirely about *where* this call
    belongs, and shows that calling it in the wrong place still passes every
    test that does not specifically look for a leak.
    """
    return tuple(doc for doc in DOCUMENTS if doc.tenant in ("shared", tenant))
