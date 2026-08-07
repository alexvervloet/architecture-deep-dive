# Lessons

Things that did not go according to plan, written down when they happened.

## 2026-08-06, ch02: a lenient metric reported 100% correct on a broken app

**Expected:** scoring a follow-up as correct when the conversation's gold
document appears in the retrieved set would measure whether lost history hurt
the answer.

**What happened:** every design scored 100% correct in every experiment, while
the "saw any history" column dropped to 29% exactly as predicted. The app was
demonstrably answering from the wrong document, and the metric could not see
it. Retrieval returns three documents, and the gold one is usually still
somewhere in the top three even when the query has lost its context; the
answer just does not come from it.

**Fix:** score on provenance instead. `answered_from(text, doc_id)` checks
that the answer sentence is actually in that document, which is exact rather
than judged because the mock is extractive. Correctness immediately separated:
100% with history against 58% at four workers.

**Next time:** when a metric shows no movement while a mechanism metric moves
exactly as predicted, suspect the metric before the mechanism. The tell was
the two columns disagreeing, not either column on its own.

## 2026-08-06, ch02: hard-killing a worker deadlocked the whole harness

**Expected:** `Process.terminate()` on an idle worker, to simulate an OOM kill
between two turns, would be safe. The worker was blocked on a read from its
own inbox and had no work in flight.

**What happened:** the run hung with no output and no error, after passing
twice. A `multiprocessing.Queue` is shared memory plus a lock, and `put()` is
asynchronous: a feeder thread does the actual write. The parent can receive an
item while the child's feeder thread is still inside the *shared* outbox's
critical section. Terminate there and the lock is never released, so every
later `get()` blocks forever. Intermittent by nature, and it looked exactly
like a slow run.

**Fix:** the outbox is a `Manager().Queue()`, proxied by its own server
process, so a dead client cannot corrupt it. `collect()` also takes a timeout
now and raises with the counts, so a future version of this fails loudly in
seconds instead of hanging.

**Next time:** any harness that kills processes on purpose should not share a
plain `mp.Queue` with them, and every blocking `get()` in a measurement
harness should have a timeout. A hang is the worst failure mode available: it
produces no evidence and wastes the most time.
