# Chapter 3: hold the connection, or queue the work

**The decision.** When a request takes seconds, does the client hold a
connection until the answer is ready?

**The stressor.** A burst four times the size of the worker pool, with a client
deadline shorter than the queue.

## Run it

```bash
python ch03-queue/stress.py     # offline, no key, ~22 seconds
```

Two scenarios: below capacity, then above it. A chapter that only showed the
overload case would be advertising.

## The three designs

| | behaviour |
|---|---|
| **sync** | hold the connection until the work is done |
| **queue** | accept, return a job id, client polls for the result |
| **shed** | hold the connection, but refuse immediately when the queue is deep |

Shedding is here because it is a real answer that real teams ship, and leaving
it out would have made this a two-way fight with a predetermined winner.

## The result

Below capacity, every design answers everything in ~1.2s and sync wins on
simplicity.

Above capacity (32 requests, 8 workers, 3s deadline):

| design | answered | p50 | p95 | spent | wasted | conn-s | recoverable |
|---|---|---|---|---|---|---|---|
| sync | 15/32 | 3002ms | 3007ms | $0.00202 | $0.00106 | 75.6 | 15/32 |
| queue | 15/32 | 3040ms | 3048ms | $0.00202 | $0.00000 | 0.3 | **32/32** |
| shed | 15/32 | **1254ms** | **2479ms** | **$0.00104** | $0.00007 | 27.6 | 15/32 |

**All three answered exactly 15.** Throughput is set by capacity and service
time, not by the shape of the front door. What the shape decides is what
happens to the other 17: sync **destroys** them (work completed after the
client left, $0.00106 billed for nothing), the queue **preserves** them
(32/32 results retrievable by id), and shedding **never starts** them.

**Shedding produced the best latency, because it stopped lying.** p50 1254ms
against sync's 3002ms. Sync's median request waited the entire deadline and
then received nothing. This is only visible because abandoned requests are
included in the percentiles; scoring answered requests only would have put
sync at p95 2277ms and hidden the problem completely.

**Shedding answered the same 15 for half the money**, $0.00104 against
$0.00202.

**Connections are what actually run out.** 75.6 connection-seconds against 0.3,
a factor of 224.

What the queue costs is not latency or money, it is the client protocol:
someone has to write the polling, and "not ready yet" becomes a state the UI
must handle.

Full reasoning and the conditions that flip the decision: [ADR.md](ADR.md).
