# Chapter 9: where the tenant boundary goes

**The decision.** Where does the permission check sit, relative to retrieval and
the model call?

**The stressor.** Two customers in one installation, and every question has a
competing internal note belonging to the other one.

## Run it

```bash
python ch09-tenancy/stress.py     # offline, no key, ~5 seconds
```

Byte-identical across runs.

## The result

| design | leaks | correct | docs scanned/query | indexes |
|---|---|---|---|---|
| filter after model | **1** | 14/16 | 18.0 | 1 |
| filter after retrieval | 0 | 15/16 | 18.0 | 1 |
| filter in retrieval | 0 | 15/16 | 14.0 | 1 |
| index per tenant | 0 | 15/16 | 14.0 | 2 |

**The leak is exactly what you would fear.** Globex asked how long a password
reset link stays valid and got back: *"Acme has a negotiated exception: their
password reset links expire after 24 hours rather than 30 minutes, because
their helpdesk batches requests overnight."* The citation list shown to the
user was clean. The answer was not.

Filtering after the model is the design that ships, because it is the only one
that asks nothing of the retrieval layer. A permission check that runs after the
model has read the document is not a permission check.

## The prediction that needed a condition

The three safe designs tied at 15/16. This chapter predicted that filtering
*after* retrieval would lose recall, and at two tenants with top-3 it did not.
So the claim was measured against what it depends on:

| tenants | leaks | k=1 after | k=1 inside | k=3 after | k=3 inside |
|---|---|---|---|---|---|
| 2 | 1/16 | 12/16 | 13/16 | 15/16 | 15/16 |
| 8 | 7/64 | 48/64 | 55/64 | 63/64 | 63/64 |
| 32 | 31/256 | 192/256 | 223/256 | 255/256 | 255/256 |

**The recall loss and the leak are the same event with two endings.** At k=1 the
answers lost by filtering after retrieval are 1, 3, 7, 15, 31 across the sweep;
the answers leaked by filtering after the model are 1, 3, 7, 15, 31. Identical
at every point. A forbidden document ranks first: the naive design serves it,
and the filter-afterwards design deletes it and has nothing left to answer from.

At k=3 the recall effect disappears at every tenant count. So the cost of
filtering after retrieval is real and conditional on k being small relative to
how many forbidden documents can outrank the answer. Filtering inside retrieval
has no such condition, which is the reason to prefer it even where the two tie
today.

The real cost of filtering inside retrieval is in none of these columns: the
same question now has different correct results per tenant, so any cache keyed
on the question alone is unsound.

Full reasoning, and why the leak counts are a floor: [ADR.md](ADR.md).
