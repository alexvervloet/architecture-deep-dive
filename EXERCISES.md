# Exercises

Every chapter here ends in a number. These exercises exist to make you distrust
those numbers in the productive way: predict first, run second, and when the
two disagree, work out which one is wrong. Several of the findings in this repo
were found exactly that way, and two of them started as a table that looked
fine and measured nothing.

Write your prediction down before running anything. The prediction being wrong
is the useful outcome.

## Chapter 1: the provider seam

1. **Predict, then count.** Before running `measure.py`, write down what you
   think the seam's upfront cost is in lines. Most people guess between 20 and
   60. It measured 1. Then work out why: open `inline/v0_baseline/app.py` and
   `seam/v0_baseline/` side by side and account for where the lines went.
2. **Break the fairness gate on purpose.** Delete the retry loop from one call
   site in `inline/v2_reliability/app.py` and re-run `fairness.py`. It should
   still pass, because retry does not change the happy path. Now make the
   change that *does* break it (return a truncated answer) and watch the gate
   refuse the measurement. What class of cheating does the gate catch, and what
   class does it miss?
3. **Add a sixth requirement that favours inline.** The chapter tried and
   failed with streaming. Find one that actually wins: a change that touches
   exactly one call site and needs something the seam's interface cannot
   express. Measure it. If you cannot find one, that is a result too.

## Chapter 2: conversation state

1. **Predict the correctness at 4 workers.** The history column is 29%, close
   to the 1/W the model predicts. Correctness was 62%. Before reading the ADR,
   explain the gap. (The answer is in how often a memoryless follow-up is
   right by luck, and it is the reason this bug survives review.)
2. **Make sticky routing fail harder.** The restart experiment cost sticky
   routing 4 points of correctness. Restart *two* workers, or restart one
   twice. At what restart frequency does sticky routing stop being a fix and
   start being a slow leak?
3. **Break the determinism.** Replace the `route()` implementation with
   `random.randint(0, workers - 1)` and run twice. The correctness numbers will
   move between runs. That is what the whole `app/determinism.py` apparatus
   exists to prevent, and feeling the difference is worth two minutes.

## Chapter 3: sync, queue, or shed

1. **Predict who answers the most.** Three shapes, 32 requests, 8 workers.
   Write down which shape answers the most requests inside the deadline. They
   all answer exactly 15. Explain why before reading the ADR.
2. **Move the percentile definition and watch the conclusion move.** In
   `designs.py`, change `Stats.percentile` to default to `("answered",)` only.
   Sync's p95 drops from 3007ms to about 2277ms and the chapter's argument
   evaporates. This is the single most common way a latency number
   lies.
3. **Find the shed threshold that beats both.** `SheddingServer` takes
   `max_queue_depth`. Sweep it. There is a value that maximises answered
   requests without the wasted spend; find it, and then work out what it
   depends on (hint: service time and deadline, not the number you picked).

## Chapter 4: the model tier

1. **Predict the hop, then measure it two ways.** Guess the localhost HTTP cost
   in milliseconds. Then compute it by differencing end-to-end medians, and by
   the isolated timer the chapter uses. The first swings between 0.9 and 2.7ms
   across runs; the second lands on 1.45. Why does the naive estimator fail?
2. **Make the crash catchable.** Change `/crash` in `model_server.py` from
   `os._exit(1)` to `raise RuntimeError(...)`. Re-run. The blast-radius table
   collapses, because a catchable exception is not the failure this chapter is
   about. Which real failures are `os._exit` and which are the exception?
3. **Price the hop against a fast model.** Set the profile to `instant` and
   compute the hop as a percentage of the request. Then `slow`. The same 1.45ms
   is 0.13% or 5%, and that ratio is the entire decision.

## Chapter 5: streaming and guardrails

1. **Predict the leak for `accumulated`.** It catches 5 of 5 violations. Guess
   how many it *prevents*. The answer is zero, and the reason is one sentence
   long.
2. **Add a violation the window cannot contain.** Every case here has a span
   under 32 characters. Write one that is longer than the hold (a multi-line
   credential, say) and re-run the sweep. Find the new threshold. This is the
   exercise that makes "hold 8 tokens" stop being a magic number.
3. **Implement retraction as a fifth strategy.** Send the tokens, then send a
   "disregard" event when the guard fires, and count how many characters were
   on screen first. Then argue with the ADR about whether that should be
   allowed to score as prevention.

## Chapter 6: degradation

1. **Predict availability and correctness separately.** For "retrieval down,
   tiered fallback", write down both numbers. Availability goes 0% to 100%;
   correctness goes to 17%. Most people predict the first and not the second.
2. **Tune the breaker until it moves p95.** It never did in the chapter:
   threshold 3, probe every 12. Find parameters that do move p95, then find
   what they cost you in false trips when the dependency is merely slow once.
3. **Make the stale cache honest.** Add a freshness stamp to the cached answers
   and surface "this may be out of date" in the response. Then re-grade: does a
   labelled stale answer belong in `unsupported`, or does it need a fifth
   grade? Your answer is a product decision, which is the point.

## Chapter 7: the indexing pipeline

1. **Predict which design is worst.** Four strategies. Most people pick
   per-request as the villain because the chapter frames it as the antipattern.
   Build-at-boot scored worse (47/60 against 60/60). Why?
2. **Break the assertion on purpose.** Change one edit in `pipeline.py` so it
   modifies a sentence no question retrieves. `assert_edits_are_visible()`
   should refuse to run. Delete the assertion and watch the run produce a
   clean, plausible, meaningless table. This is the chapter's own bug,
   reproducible in thirty seconds.
3. **Find the write-through failure.** Comment out the `on_edit` call for one
   document and re-run. Nothing errors; one document is stale forever. Now add
   the scheduled rebuild the ADR recommends as a safety net and measure the
   ceiling it puts on that failure.

## Chapter 8: rollout

1. **Predict the gate's verdict before you see the suites.** Suite A and suite
   B are both six questions and both plausible. One blocks the regression, one
   ships it to everybody. Guess which, then check. The point is that you
   cannot.
2. **Write a third suite that catches it for the wrong reason.** Make a suite
   that blocks the *safe* candidate too. Now you have a gate with a false
   positive, and the "shipped" column starts to matter.
3. **Make the canary too sensitive.** Drop `CANARY_MARGIN` to 0.01 and run the
   safe candidate. Roll back a good change and measure how often. Detection
   speed and false rollbacks are the same dial.

## Chapter 9: tenancy

1. **Predict the relationship between the leak count and the k=1 recall loss.**
   They are identical at every point in the sweep (1, 3, 7, 15, 31). Work out
   why before reading the ADR; it is one sentence and it is the best thing in
   the chapter.
2. **Defeat the leak detector.** It is a substring test. Make the mock
   paraphrase instead of extract, and watch the leak count drop to zero while
   the leak continues. Then decide what you would actually deploy to detect
   this.
3. **Add a third permission level.** Give some documents a "partner" tenancy
   visible to two tenants but not all. Which of the four designs survives the
   change without restructuring? That answer is why real systems drift toward
   post-filtering.

## Chapter 10: the assembly

1. **Predict a fourth product.** Pick something real (an IDE autocomplete, a
   nightly compliance report, an email triage bot), write its profile, and
   derive all nine decisions by hand. Then add it to `PRODUCTS` and see whether
   `decide()` agrees with you. Where it does not, one of you is wrong and it is
   worth finding out which.
2. **Break the budget.** Change the provider profile to `fast` (a 120ms model)
   and re-run. The model stops being 98% of the request, and the fixed
   overheads that were invisible start to matter. At what model speed does the
   architecture become the budget?
3. **Try to predict the tail.** The chapter refuses, because every constant it
   has is a mean. Go and measure a p95 for one component in isolation, add it
   to `MEASURED` with its source, and see whether component p95s compose into a
   system p95. (They do not, and finding out why is the exercise.)

## Across the whole dive

1. **Re-run everything and diff.** Six of the chapters are byte-identical
   across runs; the others move only in wall-clock columns. Confirm it, and
   for any that move, decide whether the moving number is load-bearing for the
   chapter's claim.
2. **Find a claim that is not backed by a number.** There are some: the
   fleet arguments in ch04, the review queue in ch08, the cache unsoundness in
   ch09. Each is named as unmeasured in its ADR. Pick one and design the
   experiment that would settle it.
3. **Read the four LESSONS.md entries, then go looking for a fifth.** Two of
   them are metrics that reported success on broken systems. That failure mode
   is not extinct in this repo; it is just not currently known.
