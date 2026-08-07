# ADR 003: accept and queue slow work, and shed load rather than making people wait

**Status:** accepted
**Date:** 2026-08-07
**Measured on:** the offline mock, `python ch03-queue/stress.py`, ~22 seconds,
no key required.

## Context

An HTTP handler that does the work and returns the answer is correct for a
20ms query. LLM requests are 1 to 30 seconds, which is long enough that the
connection itself becomes a scarce resource and long enough that clients give
up before the server finishes.

The part that makes this different from ordinary slow-endpoint advice: **the
work has already cost money when the client leaves.** A dropped CRUD request
wastes some CPU. A dropped agent turn wastes tokens that are already billed,
and the abandoned work keeps occupying the workers that the surviving requests
are queued behind.

Three designs, same worker pool (8), same 3s client deadline, same 'slow'
model profile (~1.3s mean, 5% tail at +4s):

| | behaviour |
|---|---|
| sync | hold the connection until the answer is ready |
| queue | accept, return a job id, client polls |
| shed | hold the connection, but refuse immediately when the queue is deep |

## Decision

Queue anything a human is not actively waiting on, and shed load rather than
enqueueing unboundedly for anything they are. Do not hold connections through
model calls.

## Consequences, measured

**Below capacity, there is no argument.** Four concurrent requests against
eight workers: every design answers everything, p50 ~1.2s. Sync wins on
simplicity. Any chapter that skipped this scenario would be selling something.

**Above capacity, all three answered exactly the same number.** A burst of 32
against 8 workers, 3s deadline:

| design | answered | p50 | p95 | spent | wasted | conn-s | recoverable |
|---|---|---|---|---|---|---|---|
| sync | 15/32 | 3002ms | 3007ms | $0.00202 | $0.00106 | 75.6 | 15/32 |
| queue | 15/32 | 3040ms | 3048ms | $0.00202 | $0.00000 | 0.3 | **32/32** |
| shed | 15/32 | **1254ms** | **2479ms** | **$0.00104** | $0.00007 | 27.6 | 15/32 |

This is the finding, and it is not the one the chapter title suggests.
**Queueing did not help anyone get an answer faster or in greater numbers.**
Throughput is set by capacity and service time, not by the shape of the
front door. What the shape decides is what happens to the 17 requests that did
not make it:

- **sync destroys them.** $0.00106 of completed work finished after its client
  had gone, and nothing anywhere holds the results. Those 17 people get
  nothing, and the tokens are billed.
- **queue preserves them.** Same 15 answered inside the deadline, but
  **32/32** results exist and are retrievable by id. The 17 slow ones are
  "not ready yet" rather than "gone". A browser refresh, a flaky mobile
  connection, or a user who came back in a minute all now work.
- **shed never starts them.** 16 requests were refused in about a millisecond,
  so the pool only ever worked on what it could finish.

**Shedding produced the best latency by a wide margin, because it stopped
lying.** p50 1254ms against sync's 3002ms, and p95 2479ms against 3007ms.
Sync's median request spent the full deadline waiting and then got nothing.
The measurement only shows this because abandoned requests are included in the
percentiles; scoring latency over answered requests only would have reported
sync at p95 2277ms and hidden the entire problem.

**Shedding answered the same 15 requests for half the money.** $0.00104
against $0.00202. Sync spent the difference on answers that were thrown away.

**Connections are the resource that actually runs out.** 75.6 connection-
seconds for sync against 0.3 for the queue, a factor of 224. Sync holds a
socket, a pool slot, and a concurrency-limit row for the entire model call.
This is what turns a slow dependency into an outage: the abandoned work
competes for the same workers as the live requests.

**What the queue costs is the client protocol.** Not latency, not money.
Someone has to write the polling or the websocket or the webhook, and "not
ready yet" becomes a state the UI must handle. That is a real cost and it is
paid in a different currency than the columns above.

## What would flip this decision

- **Load reliably below capacity.** Sync is simpler, and simplicity is worth
  something. The below-capacity table is the evidence for the other side.
- **A human waiting on a chat response.** Streaming a sync response is often
  better UX than a job id, because first-token latency is what the user
  perceives. That is chapter 5, and it interacts with this decision.
- **Sub-second model calls.** If the call is 200ms, connection-seconds stop
  mattering and this whole analysis is noise.
- **Work that cannot be made idempotent.** A queue implies retries, and
  retries imply duplicate side effects. The reference app's `open_ticket`
  takes an idempotency key for exactly this reason; work that cannot is a
  reason to be careful, not a reason to hold the connection.

## Limits of this measurement

- **Queueing delay is emergent, and turned out to be steadier than expected.**
  Service times are derived per request and exactly reproducible; thread
  interleaving is not, so this chapter was written expecting the counts to
  wobble. Two runs produced identical answered/abandoned/rejected counts,
  identical spend, identical waste, and identical connection-seconds, with
  p50/p95 differing by 1 to 6ms. That is a property of this workload (one
  burst, fixed capacity, deterministic service times), not a guarantee, and
  the millisecond columns are still wall clock.
- **Threads, not processes or async.** Fine here because the simulated latency
  is `time.sleep`, which releases the GIL. It would be wrong for CPU-bound
  work.
- **The connection cost model is a flat charge per round trip**, not a TCP
  simulation. It makes the sync-versus-poll comparison meaningful without
  pretending to be a network model.
- **One burst shape.** All 32 requests arrive at once. A steady arrival rate
  above capacity produces the same qualitative result more slowly, and an
  unbounded queue under sustained overload eventually produces a worse one
  than any of these, which is why the shed threshold exists.
