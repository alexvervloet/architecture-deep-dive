# ADR 009: apply the tenant predicate inside retrieval

**Status:** accepted
**Date:** 2026-08-07
**Measured on:** `python ch09-tenancy/stress.py`, offline, no key, ~5s,
byte-identical across runs.

## Context

Two customers share an installation. Each has internal notes the other must
never see: negotiated rates, rate-limit overrides, migration plans. The shared
help centre is visible to both.

The question is where the permission check sits relative to retrieval and to
the model call. Four answers, and the first is the one that ships, because it
is the only one that asks nothing of the retrieval layer:

| | behaviour |
|---|---|
| filter after model | rank globally, answer, then filter the citations shown |
| filter after retrieval | rank globally, drop forbidden documents, then answer |
| filter in retrieval | apply the tenant predicate inside the search |
| index per tenant | one index each, shared documents duplicated into all |

Every question in the workload is one the *other* tenant has a competing
internal note about, because a boundary is only tested when the thing on the
other side of it is something the retriever wants.

## Decision

Filter inside retrieval. Treat filtering after the model as a security bug
rather than a design option, and index-per-tenant as an optimisation to reach
for when tenant count is small and isolation requirements are hard.

## Consequences, measured

**Two tenants, 18 documents, 16 requests per design:**

| design | leaks | correct | docs scanned/query | indexes |
|---|---|---|---|---|
| filter after model | **1** | 14/16 | 18.0 | 1 |
| filter after retrieval | 0 | 15/16 | 18.0 | 1 |
| filter in retrieval | 0 | 15/16 | 14.0 | 1 |
| index per tenant | 0 | 15/16 | 14.0 | 2 |

**Filtering after the model leaks, and the leak is exactly what you would fear.**
Globex asked how long a password reset link stays valid and was told: *"Acme has
a negotiated exception: their password reset links expire after 24 hours rather
than 30 minutes, because their helpdesk batches requests overnight."* The
citation list shown to the user was clean. The answer was not. A permission
check that runs after the model has already read the document is not a
permission check.

**The three safe designs scored identically at these settings.** 15/16 each.
This chapter predicted that filtering after retrieval would lose recall, and at
two tenants with top-3 retrieval it did not: the forbidden documents took slots
two and three and pushed nothing important out.

**So the prediction was measured against what it actually depends on:**

| tenants | docs | forbidden | leaks | k=1 after | k=1 inside | k=3 after | k=3 inside |
|---|---|---|---|---|---|---|---|
| 2 | 18 | 22% | 1/16 | 12/16 | 13/16 | 15/16 | 15/16 |
| 4 | 26 | 46% | 3/32 | 24/32 | 27/32 | 31/32 | 31/32 |
| 8 | 42 | 67% | 7/64 | 48/64 | 55/64 | 63/64 | 63/64 |
| 16 | 74 | 81% | 15/128 | 96/128 | 111/128 | 127/128 | 127/128 |
| 32 | 138 | 90% | 31/256 | 192/256 | 223/256 | 255/256 | 255/256 |

**The recall loss and the leak are the same event with two endings.** At k=1
the answers lost by filtering after retrieval are 1, 3, 7, 15, 31 across the
sweep, and the answers leaked by filtering after the model are 1, 3, 7, 15, 31.
Identical at every point. A forbidden document ranks first; the naive design
serves it, and the filter-afterwards design deletes it and has nothing left to
answer from. Same ranking, same frequency, two different consequences.

**At k=3 the recall effect vanishes at every tenant count**, because the
tenant's own answer is still in the list once the forbidden entries are
removed. The cost of filtering after retrieval is therefore real and
conditional: it needs k to be small relative to how many forbidden documents
can outrank the answer. Filtering inside retrieval has no such condition, which
is the reason to prefer it even where the two currently tie.

**The leak rate rises from 6% to about 12% and plateaus**, because it is
governed by how often a forbidden document wins first place, not by how many
of them exist. In absolute terms every added tenant still means more leaked
answers and more customers exposed to each one.

**Filtering inside retrieval also scans less**: 14 documents per query against
18, because it never considers what it cannot return. That advantage grows with
tenant count.

**Index-per-tenant is fastest per query and pays at build time.** Two indexes
holding 28 documents for a corpus of 18 unique ones, because the 10 shared
documents are copied into each. That duplication grows with tenants while the
shared corpus does not.

## What would flip this decision

- **A retrieval layer that cannot filter.** Some vector stores make predicate
  filtering slow or unavailable, which is exactly the pressure that produces
  the filter-afterwards design. Then index-per-tenant, not post-filtering.
- **Hard isolation requirements.** If tenants must not share storage at all for
  contractual or regulatory reasons, index-per-tenant is the only option here
  and the duplication is the price.
- **Very many tenants.** Index-per-tenant's shared-document duplication scales
  with tenant count; at thousands of tenants a filtered shared index is the
  only affordable shape.
- **No tenant-private content at all.** If everything in the corpus is visible
  to everyone, none of this applies and the cheapest retrieval wins.

## Limits of this measurement

- **The leak test is substring provenance.** It catches an answer quoting a
  forbidden document. It would miss a genuine summary or paraphrase that shares
  no substring, which a real model produces far more often than this extractive
  mock does. Read the leak counts as a floor.
- **The synthetic tenants are templated**, four notes each with varied numbers.
  That makes the sweep's shape trustworthy and its absolute rates a property of
  how competitive I made those templates.
- **The predicted recall loss did not appear at the settings the chapter was
  designed around**, and the sweep exists because of that. The k=1 result is a
  found condition, not the original hypothesis, and it is reported that way.
- **Index-per-tenant's build cost is counted in documents, not seconds.** The
  embedding-spend arithmetic for that lives in chapter 7.
- **Two-level permissions only** (shared or one tenant). Real systems have
  groups, roles, and per-document ACLs, where filtering inside retrieval gets
  harder and post-filtering gets more tempting, which is precisely when this
  ADR matters most.
