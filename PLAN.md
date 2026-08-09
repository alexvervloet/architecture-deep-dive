# Build plan: architecture-deep-dive

Working doc. Continue from the first unchecked milestone. Keep commits small:
one file per commit where the file is likely to stay stable; err on the side
of over-committing.

> **The gap this fills.** The series teaches sixteen components and assembles
> them once, in the capstone. Nothing teaches the assembly as a skill. Search
> the parent repo for "architecture" and you find a voice-pipeline aside, one
> glossary entry, and a resume line. This dive owns the question the others
> skip: *given all these parts, where do the boundaries go, and what does each
> boundary cost?*

## The trap, and the way around it

Architecture teaching degrades into boxes, arrows, and claims nobody can
check. "Decouple your provider layer" is not a lesson; it is a slogan. It
fails [AUTHORING-LESSONS](https://github.com/alexvervloet/ai-engineering-deep-dive/blob/main/AUTHORING-LESSONS.md)
§1 on the first sentence: the example has to prove its own claim.

So every chapter is built the same way:

1. **The decision.** One structural fork, stated as a question with two
   defensible answers.
2. **Two builds.** The same app, both ways, both runnable, both short.
3. **The stressor.** A script that applies the pressure the decision exists to
   survive. It runs on one laptop, offline, in under a minute.
4. **The number.** Whatever the stressor measured, including when it refutes
   the expected result.
5. **The ADR.** An architecture decision record written from that run:
   context, options, decision, consequences, and the conditions under which
   the decision flips.

The deliverable is a folder of real ADRs, each citing a measurement. That is
the artifact a staff engineer is actually asked to produce, and it is what the
reader takes to a design review.

## Ground rules (decided up front)

- **Scope is LLM-specific.** Only decisions that are different *because there
  is a model in the loop*. No general layering, dependency-direction, or
  ports-and-adapters chapters; readers either know them or can get them
  elsewhere. The test for including a chapter: would this decision look the
  same in a CRUD app? If yes, cut it.
- **The stressors are the honesty mechanism.** Four kinds, all reproducible on
  one machine: change a requirement and count the diff; raise concurrency and
  read p95; kill a dependency and read the failure mode; run two workers and
  watch shared state break.
- **Name the payoffs that will not reproduce.** Some architecture wins need
  scale, a team, or a year to show up. At laptop scale a service split is
  measurably *worse* on every number available. Report that number, then name
  what actually buys the split, rather than rigging a benchmark that flatters
  the fashionable answer (AUTHORING-LESSONS §4, §8).
- **Variant names must not pre-judge the winner.** No `naive.py` vs
  `proper.py`. Use `inline.py` vs `seam.py`, `in_process.py` vs `shared_store.py`.
  Presentation lies as easily as data does (§6).
- **One reference app, rebuilt many times.** A small ask-over-a-corpus service:
  retrieve, call a model, use one tool, answer. Small enough to rewrite per
  chapter, real enough to break under load. Chapters vary its *shape*, never
  its behaviour, so any measured difference is attributable to the structure.
- **Offline and free by default.** A deterministic mock provider with a
  configurable latency profile is what the load tests run against; real
  providers are an opt-in second run. A keyless run of a real provider
  degrades to mock **loudly** (banner plus a FALLBACK marker), with
  `PROVIDER_STRICT=1` to make it fail hard instead.
- **Keys** via `secrun` (Keychain wrapper), never `.env`.
- **Reuse the measuring sticks.** The eval harness comes from evals-deep-dive,
  the red-team cases from prompt-injection-deep-dive, the trend tooling from
  observability-deep-dive. Copy the minimal piece in with a comment pointing
  home rather than importing across repos.
- **Structure**: `app/` (the shared reference app and mock provider), then
  `ch01-provider-seam/`, `ch02-state/`, ... each with two variant modules,
  `stress.py`, and `ADR.md`.

## Milestones

- [x] **M0, scaffold**: git init, LICENSE, .gitignore, README, this plan.
- [x] **M1, the reference app + mock provider** (done 2026-08-06): the
      ask-over-a-corpus service, a 12-document corpus, and a mock provider with
      named latency profiles, deterministic fault injection, and token
      accounting. Stress harness reports p50/p95/p99 plus four outcome buckets;
      faults are scoped context managers so a profile cannot leak into the next
      run. Verified: `examples/02_repeatability.py` passes 30/30 identical
      across two concurrent runs *and* against a serial run.
      Three decisions worth remembering, because later chapters depend on them:
      - **Randomness is derived per request, not drawn from a shared RNG.**
        A global seeded RNG is not reproducible under concurrency, because the
        draw a request gets depends on thread order. `draw(seed, label, req_id)`
        makes latency a property of the request. This is what lets the
        repeatability check assert equality instead of "close enough".
      - **Simulated and measured latency are reported side by side, always.**
        Simulated is exact and reproducible; wall clock is not, and asserting
        on it would make the test fail on a busy laptop. Per-request wall-clock
        drift measured at ~4ms median, 16ms max.
      - **Four outcome buckets, not two.** `wrong_source` is separate from
        `error` because the day-one app already answers off-topic questions
        confidently from irrelevant documents, and an availability metric scores
        that as success. Found while writing the example, kept and named rather
        than tuned away (AUTHORING-LESSONS §8).
      The harness already separates a dead provider (40 requests failed in
      140ms) from a slow one (same 0% correct, 15,180ms), which is ch06's
      argument arriving before ch06 was written.
- [x] **M2, ch01 the provider seam** (done 2026-08-06; see
      ch01-provider-seam/ADR.md). Grew from three requirements to five: a
      fourth call site and a streaming call site were added because the first
      three all favoured the seam and a one-sided slate proves nothing.
      Twelve builds, held to byte-identical output by `fairness.py`.
      Headline: **95 lines changed against 192**, upfront cost **1 line**, so
      no break-even point exists to find.
      - **Prediction refuted.** Streaming was included expecting it to cost
        the seam more; the seam changed 32 lines against inline's 40. Reported
        as a refutation rather than quietly dropped (AUTHORING-LESSONS §12).
        The real cost landed where line counting cannot see it: the interface
        doubled, the "no uncounted call" guarantee became conditional, and
        retry stopped applying to the streaming path in *both* builds.
      - **The best finding was an accident.** Spend agreed exactly through v4
        ($0.000099 both) then diverged at v5 ($0.000097 vs $0.000100), because
        a streamed response has no usage block and each build fell back to a
        different, defensible estimate. Silent, and a dashboard draws a smooth
        line through it.
      - **Fake SDKs were necessary, not decorative.** `app/fake_sdks.py`
        copies the real OpenAI and Anthropic signatures, response shapes,
        token field names, error types, and streaming interfaces. Swapping two
        identical fake clients would have made the v1 number fiction.
      - **Counting rule matters and is stated in the tool**: docstrings,
        comments, and blanks excluded (each version carries a different header,
        so counting prose would measure the author); a modified line counts as
        two. Raw counts printed alongside.
- [x] **M3, ch02 where conversation state lives** (done 2026-08-06; see
      ch02-state/ADR.md). Three designs, not two: sticky routing is a real fix
      that real teams ship, and leaving it out would have been a strawman.
      Real OS processes, because threads share memory and would have shown the
      dict working perfectly.
      Correctness by worker count (memory+spread): 100 / 92 / 62%, and 58%
      after restarting one worker. Sticky held 100% through scale-out and fell
      to 96% on the restart. SQLite cost 0.47ms/turn against 0.008ms.
      - **The bug is half-invisible even when it fires.** At 4 workers only 29%
        of follow-ups saw history but 62% were still correct, because some
        follow-ups retrieve fine on their own. That gap is why this survives
        code review: it looks like model noise.
      - **Two things went wrong and are written up in LESSONS.md**: a lenient
        metric ("gold doc in the retrieved set") reported 100% correct on a
        visibly broken app, fixed by scoring answer provenance; and hard-killing
        a worker deadlocked the harness through a shared mp.Queue, fixed with a
        Manager queue plus timeouts on every blocking get.
      - **One conversation template was cut**, not kept: it needed two-hop
        retrieval and failed even with perfect history, which would have put a
        permanent error floor under every column.
      - Verified reproducible: every correctness and history figure is identical
        across runs; only the wall-clock store column moves.
- [x] **M4, ch03 sync vs queued work** (done 2026-08-07; see
      ch03-queue/ADR.md). Three designs again: load shedding earns its place
      and beat both others on latency and cost.
      Burst of 32 against 8 workers, 3s deadline: **all three answered exactly
      15**. Throughput is capacity, not front-door shape. What the shape decides
      is the fate of the other 17: sync destroys them ($0.00106 billed for work
      that finished after the client left), queue preserves them (32/32
      retrievable), shed never starts them.
      - **Shedding won latency because it stopped lying**: p50 1254ms vs sync's
        3002ms, and half the spend ($0.00104 vs $0.00202) for the same 15
        answers.
      - **The percentile definition decided the headline.** Scoring latency over
        answered requests only put sync at p95 2277ms and hid the problem
        entirely; including abandoned requests moved it to 3007ms. This is the
        exact trap called out in stress/harness.py during M1, and I walked into
        it anyway. Fixed by making abandoned count and rejected not.
      - **Connection-seconds is the resource metric that matters**: 75.6 vs 0.3,
        a factor of 224. Peak-concurrency was dropped as a headline because
        synchronized polling makes it an artifact.
      - **Expected wobble, got none.** Two runs gave identical counts, spend,
        waste, and connection-seconds; only p50/p95 moved by a few ms. Written
        up as a property of this workload, not a guarantee.
- [x] **M5, ch04 the model as its own tier** (done 2026-08-07; see
      ch04-model-tier/ADR.md). **The open question below is resolved: the
      chapter stays.** It was written expecting to lose and did not.
      Real subprocesses, real sockets; only model latency is simulated, and
      identically on both sides.
      - **The hop costs 1.45ms**, timed around the model call alone, which is
        **0.13%** of a realistic 1158ms request. Not "worse on every number";
        worse by a tenth of a percent.
      - **Blast radius is the whole purchase.** Model killed with os._exit
        (an OOM kill, not a catchable exception): in-process scores 0/10 on
        /health and 0/10 on a cached /status, because they were the same
        process. Tiered scores 10/10 on both and fails /ask in milliseconds
        with a stated reason. Neither can answer without a model, and the
        table says so.
      - **A measurement bug nearly set the headline.** Differencing end-to-end
        medians gave 0.86 to 2.73ms across runs, because retrieval jitter is
        several times the hop. Fixed by instrumenting inside the app; both
        figures are printed so the reader sees why one is unused. In LESSONS.md.
      - **Batching is arithmetic, not measurement**, and is labelled that way
        in its own section heading: the repo cannot measure a provider's fixed
        per-call overhead, so it emits the break-even threshold instead.
      - **The fleet arguments are named and explicitly not counted**: GPU
        utilisation, independent scaling, one copy of the weights, rolling a
        model version. Probably larger than anything measured; no claim rests
        on them.
- [x] **M6, ch05 what streaming costs the architecture** (done 2026-08-07; see
      ch05-streaming/ADR.md). Four placements, not three: a hold-back window
      was added because buffer-versus-stream is a false choice, and the window
      is what production actually ships.
      - **Catching and preventing are different columns, and that is the
        chapter.** The honest streaming build (guard all text so far) detected
        5/5 violations and prevented 0/5, leaking 99 characters. A pattern is
        only recognisable once complete, and completing it is what the last
        delivered token did.
      - **Guarding the chunk you were handed detects nothing**: 0/5. A
        4-character token cannot contain an email address. Not a straw man, it
        is what a streaming callback naturally invites.
      - **window(8): 5/5 caught, 5/5 contained, 0 leaked, 200ms TTFT** against
        buffering's 1875ms. Same safety, ~9x better first-token latency. And
        buffering charges its latency to the clean responses too (1175ms vs
        225ms), which is most of the traffic.
      - **The sweep is the deliverable, not the number 8.** Hold size is a
        function of the longest pattern the guard must recognise (32 chars here
        = 8 tokens). Stated as a method to re-derive, not a constant.
      - **A regex gap nearly became an architecture finding**: the api_key
        pattern missed `sk-live-...` because the alphanumeric run stops at the
        second hyphen, making one case invisible to all four designs at once.
        Found and fixed before the numbers were taken; noted in cases.py.
      - **Retraction was deliberately not implemented as a fifth design.**
        Sending "disregard that" is a UI action, not a guarantee: the bytes
        were rendered and logged. Counting it as prevention would be counting a
        wish.
      - Output is byte-identical across runs (no RNG; timing is arithmetic).
- [x] **M7, ch06 degradation and failure isolation** (done 2026-08-07; see
      ch06-degradation/ADR.md). Six scenarios, three designs. Reuses
      stress/harness.py from M1 rather than building new machinery, which is
      the first time that investment paid off directly.
      - **Three grades, not two.** exact / supported / unsupported, graded
        against what the healthy pipeline itself answers (computed at startup,
        not hand-written) so no design can score by dumping a document. Two
        grades would have forced every degraded answer into "fine" or "wrong"
        when most are neither.
      - **Retrieval down: availability 0% -> 100%, correctness -> 17%.** Twenty
        of 24 answers came confidently from a stale snapshot (link valid "24
        hours" vs the live "30 minutes"), with nothing in the response marking
        it. That is the chapter.
      - **The fallback also has a genuinely good case**, reported as plainly as
        the bad one: under 40% flaky, tiered answers 24/24 with 20 exact
        against hard fail's 17/24.
      - **The breaker is a capacity device whose payoff scales with fault
        length**: 1.5x wall-clock saving over 24 requests, 2.9x over 48, and
        **zero p95 improvement in either**. Requests hitting an open breaker
        were already fast; the ones that tripped it had already paid the
        deadline. Worth citing against the folklore.
      - **An artifact named rather than hidden**: the snippet tier scores 11/24
        "exact" under a dead model only because this repo's model is
        extractive, so the top document's first sentence sometimes *is* its
        answer. Flagged in the ADR and in the script output as an upper bound.
      - **A name collision worth knowing about**: the chapter script is
        stress.py and the shared harness package is stress/. Python puts the
        script's directory at sys.path[0], so the import resolved to itself.
        Fixed by inserting the repo root ahead of it, with the reason in a
        comment.
- [x] **M8, ch07 the offline pipeline** (done 2026-08-07; see
      ch07-indexing/ADR.md). Four strategies, not two: build-at-boot is the one
      most code ships by accident, and write-through is what actually wins.
      Uses the `updated_at` field reserved in app/corpus.py during M1.
      - **Write-through matched per-request correctness (60/60) at one
        fifteenth of the embedding calls** (4 vs 60), because its work scales
        with the edit rate rather than the query rate. Per-request indexing
        adds 36ms and $0.000012 to every request forever to catch three edits.
      - **Build-at-boot was worst**: 13/60 confidently obsolete and never
        catching up. Its staleness clock is the deploy cadence, so it degrades
        as a service stabilises.
      - **Read worst-lag, not correctness.** Correctness scales with how often
        the workload asks about an edited document (6 of 14 here, stated in the
        code); lag is a property of the schedule alone. The sweep has no knee,
        which is said rather than implied.
      - **The chapter measured nothing on its first run and looked fine doing
        it.** A scheduled index nine ticks behind scored 60/60, because the
        edits changed sentences the retriever never quoted. Separately, three
        questions failed against a *fresh* index, putting a 12/60 floor under
        every column. Both fixed; `assert_edits_are_visible()` now refuses to
        print a table unless every edit changes an answer. In LESSONS.md.
      - **Edit ticks are coprime with the sweep intervals** (11/27/43, not
        10/25/40), because round ticks landed on rebuild boundaries and handed
        some intervals a free worst-case lag of zero.
      - Output byte-identical across runs; ~4 seconds.
- [x] **M9, ch08 rollout as architecture** (done 2026-08-07; see
      ch08-rollout/ADR.md). Five shapes, two candidates, 600 requests each.
      Both candidates are "spend fewer tokens on the context": one is safe
      (14/14) and one breaks 3 of 14, and the diffs look the same.
      - **The eval gate is a coin flip on its sample.** Two plausible
        six-question suites: suite B blocks the regression at request 0 for 12
        model calls; suite A ships it to everybody (129/600 bad) and reports a
        clean pass. Running both is what makes this a finding about gates
        rather than about one suite.
      - **The canary caught what the suite missed and charged 8 bad answers
        and 290 requests**, both set by signal noise rather than regression
        size. Detection fired the instant it reached its 30th sample.
      - **Shadow reported 64% disagreement against a true 21% breakage rate.**
        Most of the gap is answers that changed and stayed correct, which is
        exactly why disagreement is a flag for a human and not a verdict. Costs
        a second model call on every request.
      - **Stacking is not redundancy**: gate A + canary scored identically to
        canary alone, because the gate contributed nothing when its suite
        missed. That is the argument for both, and the table shows it.
      - **Feedback is modelled as a noisy thumbs-down (60%/3%), not a gold
        label.** Nearly every difference between the shapes comes from that one
        choice; the ADR names the invented constants and says detection latency
        scales with them.
      - **A good change is rolled out too**, so no shape can score well by
        shipping nothing.
- [x] **M10, ch09 tenancy and permission boundaries** (done 2026-08-07; see
      ch09-tenancy/ADR.md). Four placements, not three: filtering *after the
      model* is the one that actually ships, because it is the only one that
      asks nothing of the retrieval layer, and it is the one that leaks.
      - **The leak is concrete**: Globex asked about password reset expiry and
        was told Acme's negotiated 24-hour exception, verbatim. Citation list
        clean, answer not.
      - **The predicted recall loss did not appear** at 2 tenants / top-3, so
        it was measured against what it depends on instead of being asserted.
        The sweep found the condition: only at k=1, and it scales with tenants.
      - **The best finding fell out of the sweep**: at k=1 the answers *lost*
        by filtering after retrieval equal the answers *leaked* by filtering
        after the model, exactly, at every point (1/3/7/15/31). One event, two
        endings: a forbidden document ranks first, and you either serve it or
        delete it and have nothing left.
      - **Leak rate rises 6% to 12% then plateaus**, because it depends on how
        often a forbidden doc wins first place, not how many exist. Stated
        that way rather than as "grows with tenants".
      - Leak detection is substring provenance, so the counts are a floor: a
        real model paraphrasing would evade it. Said in the ADR.
      - Synthetic tenants are templated for the sweep; the shape is
        trustworthy, the absolute rates depend on how competitive I made them.
- [ ] **M11, ch10 the assembly**: put the nine ADRs on one board, add a latency
      and cost budget, and derive a target architecture for three different
      products (a chat feature, a batch document pipeline, a voice agent).
      Same decisions, three different answers, which is the whole point of the
      dive.
- [ ] **M12, series wiring**: EXERCISES.md (grown per chapter, may land
      earlier), TEXTBOOK.md chapter matching the series pattern, GitHub remote
      plus submodule in the parent repo, entries in the parent README,
      CHOOSING.md, CAREERS.md, and GLOSSARY.md, and CI (pinned install plus
      smoke runs of every stress script).

## Open questions (decide when reached)

- ch03: whether the queue is a real broker or an in-process worker pool with a
  durable store. Prefer the smaller one that still shows resumability; only
  reach for a broker if the lesson collapses without it.
- ~~ch04: whether the honest verdict makes this chapter worth its length~~,
  resolved 2026-08-07: it does. The hop is 0.13% of a realistic request and
  the containment result (0/10 vs 10/10 on requests that never needed a model)
  is measurable on one laptop, so it did not need the fleet argument it was
  expected to need.
- ch09: how much of knowledge-desk to copy in. Prefer a minimal two-tenant
  corpus over porting real permission logic.
- Whether the reference app should be the capstone's askrepo rather than a new
  toy. Argument for: readers already know it. Argument against: chapters need
  to rewrite the whole app repeatedly, which askrepo is too big for. Leaning
  toy, with the ADRs cross-referenced to askrepo's actual shape.
