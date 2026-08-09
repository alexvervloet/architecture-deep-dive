#!/usr/bin/env python3
"""
stress.py: ask both tenants the same questions and see what comes back.

    python ch09-tenancy/stress.py     # offline, no key, ~5 seconds

Two tenants share an installation and each has internal notes the other must
never see. Every question below is asked by both tenants, and every one of them
is a question whose answer the *other* tenant has a competing internal document
about: password expiry, pricing, rate limits, refunds, exports, SSO.

Three things are measured, and the third is the one that makes this a design
question rather than a security lecture:

    leaks     another tenant's text in the answer. Must be zero.
    correct   answered from the right document this tenant is allowed to see.
    scanned   candidate documents the search had to score, per query.

A design with zero leaks and poor recall is not obviously better than one with
zero leaks and good recall, and the two safe-but-different designs here differ
by exactly that.
"""

from __future__ import annotations

import os
import sys

CHAPTER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(CHAPTER))
sys.path.append(CHAPTER)

from dotenv import load_dotenv  # noqa: E402

from app import providers, service  # noqa: E402
import tenants as T  # noqa: E402
from tenants import (  # noqa: E402
    DESIGNS,
    SHARED,
    build_per_tenant_indexes,
    forbidden_for,
)

load_dotenv()

SYSTEM_TEMPLATE = service.SYSTEM_TEMPLATE

# Each question has a shared help-centre document that is the right answer for
# any tenant, and at least one tenant-internal document that competes for it.
QUESTIONS = (
    ("How long is the password reset link valid?", "doc-01"),
    ("How much is the Team plan per user?", "doc-06"),
    ("What are the API rate limits?", "doc-07"),
    ("How long do refunds take to post?", "doc-03"),
    ("How do I get a downloadable archive of my data?", "doc-05"),
    ("Which plan includes SSO?", "doc-08"),
    ("How long are audit logs retained?", "doc-10"),
    ("How do I cancel my subscription?", "doc-04"),
)


def context_of(documents) -> str:
    return "\n".join(f"[{d.doc_id}] {d.text}" for d in documents)


def leaked_from(text: str, tenant: str) -> str:
    """Which forbidden document this answer came out of, if any.

    Substring against the document body, the same provenance test the rest of
    the repo uses. It is strict on purpose: a partial quote of another
    customer's negotiated rate is a leak, and a summary that happens not to
    match any substring would be missed by this test, which is stated in the
    ADR rather than papered over.
    """
    needle = text.rstrip(".").strip().lower()
    if not needle:
        return ""
    for document in forbidden_for(tenant):
        if needle in document.text.lower():
            return document.doc_id
    return ""


def run(retrieve) -> dict:
    providers.configure_mock(latency="instant", seed=1337)
    leaks, correct, scanned, total = 0, 0, 0, 0
    examples = []

    for tenant in T.TENANTS:
        for i, (question, gold) in enumerate(QUESTIONS):
            result = retrieve(question, tenant)
            scanned += result.scanned
            total += 1
            response = providers.generate(
                SYSTEM_TEMPLATE.format(context=context_of(result.documents)),
                question,
                request_id=f"{tenant}-{i}",
            )
            leaked = leaked_from(response.text, tenant)
            if leaked:
                leaks += 1
                if len(examples) < 2:
                    examples.append((tenant, question, response.text, leaked))
            elif service.answered_from(response.text, gold):
                correct += 1

    return {
        "leaks": leaks,
        "correct": correct,
        "total": total,
        "scanned": scanned / max(1, total),
        "examples": examples,
    }


def main() -> int:
    T.configure(0)
    per_tenant_sizes = build_per_tenant_indexes()

    print(f"{len(T.TENANTS)} tenants, {len(T.ALL_DOCS)} documents "
          f"({len(SHARED)} shared, {len(T.ALL_DOCS) - len(SHARED)} tenant-internal).")
    print(f"{len(QUESTIONS)} questions asked by each tenant, "
          f"{len(QUESTIONS) * len(T.TENANTS)} requests per design.")
    print("Every question has a competing internal document belonging to the other tenant.\n")

    print("=" * 84)
    print("WHERE THE BOUNDARY GOES")
    print("=" * 84)
    print()
    header = (
        f"  {'design':<24} {'leaks':>7} {'correct':>9} {'docs scanned/query':>20}"
        f" {'indexes':>9}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))

    results = {}
    for label, retrieve, _leaky in DESIGNS:
        result = run(retrieve)
        results[label] = result
        indexes = len(per_tenant_sizes) if label == "index per tenant" else 1
        print(
            f"  {label:<24} {result['leaks']:>7} {result['correct']:>4}/{result['total']:<4}"
            f" {result['scanned']:>20.1f} {indexes:>9}"
        )

    print(
        f"\n  Index-per-tenant maintains {len(per_tenant_sizes)} indexes holding"
        f" {sum(per_tenant_sizes.values())} documents in total,"
        f"\n  because the {len(SHARED)} shared documents are copied into each one. That"
        f" duplication grows"
        f"\n  with tenants while the shared corpus does not."
    )

    leaky = results["filter after model"]
    print("\n" + "=" * 84)
    print("WHAT A LEAK LOOKS LIKE")
    print("=" * 84)
    for tenant, question, text, doc_id in leaky["examples"]:
        print(f"\n  {tenant} asked: {question}")
        print(f"  answered:   {text}")
        print(f"  that text is from {doc_id}, which belongs to the other tenant.")
    print(
        "\n  Filtering after the model call is the design that ships, because it is the"
        "\n  only one that needs nothing from the retrieval layer: rank globally, answer,"
        "\n  then remove the citations the user is not allowed to see. The citation list"
        "\n  is clean and the answer is not. The forbidden document was in the prompt, and"
        "\n  a permission check that runs after the model has already read the document"
        "\n  is not a permission check."
    )

    after = results["filter after retrieval"]
    inside = results["filter in retrieval"]
    print("\n" + "=" * 84)
    print("THE TWO SAFE DESIGNS, AND WHEN THEY STOP BEING EQUIVALENT")
    print("=" * 84)
    print(
        f"\n  At two tenants and top-3 retrieval they are indistinguishable on quality:"
        f"\n  {after['correct']}/{after['total']} against {inside['correct']}"
        f"/{inside['total']}. This chapter predicted that filtering after retrieval"
        f"\n  would lose recall, and at these settings it does not. The forbidden"
        f"\n  documents took slots two and three and pushed nothing important out."
        f"\n"
        f"\n  The prediction was not wrong, it was unconditional. Below it is measured"
        f"\n  against the two things it actually depends on: how many tenants share the"
        f"\n  index, and how many documents the retriever is allowed to return."
    )

    print("\n" + "=" * 84)
    print("SWEEP: tenants sharing one index, at top-1 and top-3")
    print("=" * 84)
    print()
    header = (
        f"  {'tenants':>8} {'docs':>6} {'forbidden':>10} {'leaks':>10}"
        f" {'k=1 after':>11} {'k=1 inside':>11} {'k=3 after':>11} {'k=3 inside':>11}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    rows = []
    for extra in (0, 2, 6, 14, 30):
        T.configure(extra)
        leaky_r = run(T.retrieve_after_model)
        a1 = run(lambda q, tn: T.retrieve_after_retrieval(q, tn, 1))
        i1 = run(lambda q, tn: T.retrieve_in_retrieval(q, tn, 1))
        a3 = run(lambda q, tn: T.retrieve_after_retrieval(q, tn, 3))
        i3 = run(lambda q, tn: T.retrieve_in_retrieval(q, tn, 3))
        share = 1 - (len(SHARED) + 4) / len(T.ALL_DOCS)
        rows.append((len(T.TENANTS), leaky_r, a1, i1))
        print(
            f"  {len(T.TENANTS):>8} {len(T.ALL_DOCS):>6} {share:>9.0%}"
            f" {leaky_r['leaks']:>4}/{leaky_r['total']:<5}"
            f" {a1['correct']:>5}/{a1['total']:<5} {i1['correct']:>5}/{i1['total']:<5}"
            f" {a3['correct']:>5}/{a3['total']:<5} {i3['correct']:>5}/{i3['total']:<5}"
        )
    T.configure(0)

    gaps = [(n, i1["correct"] - a1["correct"], leaky["leaks"]) for n, leaky, a1, i1 in rows]
    print(
        "\n  Read the k=1 pair against the leaks column. They are the same numbers:"
        + "".join(f"\n    {n:>2} tenants: {gap} answer{'' if gap == 1 else 's'} lost by"
                 f" filtering after retrieval, {leaks} leaked by filtering after the model"
                 for n, gap, leaks in gaps)
    )
    print(
        "\n  That is not a coincidence, it is one event with two endings. A forbidden"
        "\n  document ranks first; the naive design serves it, and the filter-afterwards"
        "\n  design deletes it and has nothing left to answer from. Same ranking, same"
        "\n  frequency, and it grows with every tenant added to the index."
        "\n"
        "\n  At k=3 the effect disappears entirely, at every tenant count, because the"
        "\n  tenant's own answer is still in the list after the forbidden entries are"
        "\n  removed. So the recall cost of filtering after retrieval is real and is"
        "\n  conditional on k being small relative to how many forbidden documents can"
        "\n  outrank the answer. Filtering inside retrieval does not have the condition,"
        "\n  which is the reason to prefer it even where both currently score the same."
        "\n"
        "\n  The leak rate rises from 6% to about 12% and then plateaus, because it is"
        "\n  governed by how often a forbidden document wins first place rather than by"
        "\n  how many of them there are. In absolute terms every added tenant still means"
        "\n  more leaked answers, and more customers exposed to each one."
        "\n"
        "\n  The real cost of filtering inside retrieval is in none of these columns: the"
        "\n  same question now has different correct results per tenant, so any cache"
        "\n  keyed on the question alone is unsound, and a shared embedding cache is"
        "\n  exactly the kind of thing added later for speed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
