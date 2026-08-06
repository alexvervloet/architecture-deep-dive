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
- [ ] **M3, ch02 where conversation state lives**: in-process dict vs a shared
      store. Stressor: two workers behind one entry point, same session id.
      Measure: percentage of turns whose history silently vanished. Expected
      to be a clean falsification of the single-worker mental model; if the
      loss rate is not stark, the chapter says so and reports what it took to
      make it stark. Pairs with context-engineering-deep-dive.
- [ ] **M4, ch03 sync request vs queued work**: hold the connection vs accept,
      queue, and poll or stream. Stressor: N concurrent agent runs against the
      slow provider profile. Measure: p95, timeout rate, connections held, and
      the cost of a client disconnect (work lost vs resumable). The reason
      this is LLM-specific: request time is seconds to minutes, so the shape
      that works for CRUD stops working at single-digit concurrency.
- [ ] **M5, ch04 the model as its own tier**: in-process model call vs a
      separate inference service (Ollama as the real second tier). Measure the
      added hop honestly, then measure what the tier buys that the number
      cannot show: warm weights, batching, GPU scheduling, and blast radius
      when the model tier dies. This is the chapter most likely to produce an
      uncomfortable result; keep it (§8).
- [ ] **M6, ch05 what streaming costs the architecture**: you cannot unsend a
      token. Stressor: a response that turns unsafe partway through. Compare
      buffer-then-check, chunk-wise checking, and stream-with-retraction.
      Measure: time to first token against bytes already delivered when the
      guard fires. Reuses the prompt-injection dive's cases.
- [ ] **M7, ch06 degradation and failure isolation**: hard fail vs tiered
      fallback. Stressor: kill retrieval, kill the provider, then make the
      provider slow rather than dead (the harder fault). Measure uptime *and*
      answer correctness under each fault, because a fallback that answers
      confidently without sources trades an outage for a wrong answer. Report
      both numbers or the chapter is propaganda.
- [ ] **M8, ch07 the offline pipeline**: embed and index at query time vs an
      ingest pipeline on a schedule. Measure: staleness window against
      per-request latency and embedding spend. Quantifies an antipattern that
      the RAG dive only warns about.
- [ ] **M9, ch08 rollout as architecture**: versioned prompts, shadow traffic,
      canary, and an eval gate in front of deploys. Stressor: plant a
      regression in a prompt and see which shapes catch it before a user does,
      and how many requests leak through each. Reuses the evals harness.
- [ ] **M10, ch09 tenancy and permission boundaries**: filter after retrieval
      vs filter inside retrieval vs an index per tenant. Stressor: a leak test
      with documents that must never cross. Measure: leaks and the latency
      cost of each boundary. Pairs with the knowledge-desk project.
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
- ch04: whether the honest verdict makes this chapter worth its length. If the
  measurable result is "worse locally, and the real payoff needs a fleet",
  consider folding it into ch06 as a blast-radius section rather than padding
  it to chapter size.
- ch09: how much of knowledge-desk to copy in. Prefer a minimal two-tenant
  corpus over porting real permission logic.
- Whether the reference app should be the capstone's askrepo rather than a new
  toy. Argument for: readers already know it. Argument against: chapters need
  to rewrite the whole app repeatedly, which askrepo is too big for. Leaning
  toy, with the ADRs cross-referenced to askrepo's actual shape.
