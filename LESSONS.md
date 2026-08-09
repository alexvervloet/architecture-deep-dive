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

## 2026-08-07, ch07: the experiment ran, produced a clean table, and measured nothing

**Expected:** editing a document mid-run would make a stale index give a wrong
answer, so correctness would separate the index strategies.

**What happened:** a scheduled index sitting nine ticks behind the corpus
scored a perfect 60/60. The edits changed sentences the keyword retriever
never selected, so the app kept quoting an unchanged sentence from the same
document and scored correct. The staleness was completely real and the metric
could not see any of it. Nothing errored, and the table looked plausible
enough that the only reason to doubt it was noticing that a nine-tick lag with
zero wrong answers makes no sense.

There was a second, separate version of the same fault in the same chapter:
three workload questions retrieved a competing document even against a
perfectly fresh index, putting a 12-in-60 error floor under every column and
making staleness indistinguishable from ordinary retrieval failure.

**Fix:** `assert_edits_are_visible()` runs at startup and refuses to produce a
table unless every edit changes the answer to at least one workload question.
The three floor-producing questions were reworded against the same documents.
Both fixes live in the code with the reason attached.

**Next time:** an experiment needs a check that its independent variable
actually reaches the metric, and that check belongs in the harness rather than
in the author's head. Before trusting a comparison, verify that the thing being
varied can change the number at all. A floor or a ceiling will otherwise
quietly swallow the effect, and the ceiling is much harder to notice, because a
perfect score reads as good news.

## 2026-08-07, ch02: several workers racing to create the same SQLite database

**Expected:** each worker process opening the shared database with
`PRAGMA journal_mode=WAL` and `CREATE TABLE IF NOT EXISTS` was safe, since
`sqlite3.connect(timeout=30)` waits for a busy database.

**What happened:** `sqlite3.OperationalError: database is locked`, on a
regression run, after the chapter had been written, committed, and rerun
cleanly several times. Setting the journal mode needs exclusive access to the
file and does not honour the busy timeout on that path, so when several
freshly spawned workers hit a brand-new database at once, one wins and the
rest fail immediately. A pure startup race, invisible most of the time.

**Fix:** the parent creates and configures the database once before spawning
anyone (`SqliteStore.initialize`). Workers only ever open an existing
database, set `busy_timeout`, and run no DDL. Also clean up the `-wal` and
`-shm` sidecar files, which the original teardown left behind. Three
consecutive runs now pass with identical results.

**Next time:** shared-store setup belongs to whoever starts the processes, not
to the processes. And the loud-failure work from the earlier lesson paid for
itself here: the timeout on `collect()` turned this into a clear
"only 15/24 responses came back" message in seconds, instead of the hang it
would have been a day earlier.

## 2026-08-07, ch04: measuring a small thing by differencing two big ones

**Expected:** the cost of the model-tier hop could be read off as the
difference between end-to-end request medians for the two designs.

**What happened:** the answer moved between 0.86ms and 2.73ms across runs, a
3x swing on the chapter's headline number. The reference app's retrieval stage
has ~10ms of jitter, which is several times the hop being measured, so
differencing two totals mostly measured the jitter. Nothing looked broken; the
number was just quietly unreliable, and either value would have been believed.

**Fix:** move the timer inside the app, around the model call alone, and
return it in the response. Retrieval is then not in the measurement at all.
The hop came out at 1.45ms, and the chapter now prints both the isolated
figure and the end-to-end difference so the reader can see why the second one
is not used.

**Next time:** never estimate a small quantity as the difference of two larger
noisy ones. Instrument the boundary itself. The warning sign was rerunning and
getting a materially different headline, which is worth treating as a
measurement bug rather than as noise to be averaged away.

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
