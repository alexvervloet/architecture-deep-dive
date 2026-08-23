# Chapter 21: The Seams Between the Parts

*This is the textbook chapter for the Architecture deep dive, a bonus that slots in after Production (8). Every other dive in this series teaches a component; this one teaches where the boundaries between them go, and what each boundary costs. The [README](README.md) is the lab manual and the ten ADRs are the findings; this is the lecture. It covers why architecture teaching usually degrades into slideware, the one discipline that stops it, and the ten decisions that come out differently when you insist on a number.*

---

## 21.1 The subject nobody can teach honestly

There is a reason architecture writing is mostly boxes and arrows. The claims are true at a scale and over a timespan that no example can reach. "Decouple your provider layer" is defensible advice, and the evidence for it is a codebase you cannot see, over three years you did not live through, at a company that may no longer exist. So the genre settles for diagrams and confident tone, and the reader comes away with opinions they cannot check.

That failure is not inevitable, and this dive is an argument that it is not. The trick is to stop asking "is this architecture good?" (unanswerable, and scale-dependent) and start asking "what does this boundary cost, and what does it buy, under a pressure I can apply on this laptop in under a minute?" Those questions have numbers. The numbers are small and local and they do not settle the three-year question, but they are real, and a design review armed with four real numbers beats one armed with three years of confident tone.

So every chapter here has the same shape. One structural fork, stated as a question with two defensible answers. The same app built both ways, held to identical behaviour so a difference cannot be bought by doing less. A stressor that applies the pressure the decision exists to survive: a changed requirement, a burst of concurrency, a second worker, a dead dependency, an edited document, a planted regression, a hostile neighbour. Then a number, including when the number refutes what the chapter expected. Then an ADR written from that run.

The deliverable is not ten opinions. It is ten measurements and a folder of decision records, which is the artifact a staff engineer is actually asked to produce and almost never gets to base on evidence.

## 21.2 What makes this different from ordinary systems design

A fair question, and one the dive answers with a rule: if a decision would look the same in a CRUD app, it is not in this dive. No layering chapter, no dependency-direction chapter, no ports and adapters. Those are real and well covered elsewhere.

What is left is the set of decisions that are different *because there is a model in the loop*, and they are different for four reasons that recur in every chapter.

**Requests take seconds, not milliseconds.** The accept-work-respond shape that every web framework teaches is correct for a 20ms query and stops being correct somewhere around a second. Chapter 3 finds the consequence at single-digit concurrency: eight workers, thirty-two arrivals, and every design answers exactly fifteen.

**The work has already cost money when it fails.** A dropped CRUD request wastes some CPU. A dropped agent turn wastes tokens that are already billed and workers that are still occupied. This is why chapter 3's headline column is not latency but "wasted": money spent on answers with nobody left to receive them.

**You cannot unsend a token.** Streaming moves your safety checks to a place where they have less to work with, and chapter 5 measures exactly how much less: an output guard watching the accumulated text detects every violation and prevents none of them, because a pattern only becomes recognisable once it is complete, and completing it is what the last delivered token did.

**The thing you are scaling is weights on a GPU.** It fails by being OOM-killed rather than by raising an exception you can catch, which inverts the usual advice about splitting services. Chapter 4 kills a model with `os._exit` and finds that an in-process design loses its health checks and its cached responses along with the model, while a tiered one keeps both for 1.45ms a request.

## 21.3 The discipline: measure the consequence, not the mechanism

The single most valuable habit in this dive, and the one that transfers furthest, is refusing to score a mechanism when you can score its consequence.

Chapter 2 makes the case by getting it wrong first. Conversation state in a process, four workers, a load balancer that does not know about sessions: the obvious metric is "how many requests saw their history", and it reported 29%, beautifully close to the 1-in-4 the model predicts. It is also nearly useless, because it measures the mechanism. The metric that matters is what the user was told, and when the chapter switched to grading answer provenance, correctness came out at 62%.

The gap between those two numbers is the entire reason the bug survives code review. Losing the history costs the answer only about half the time, because some follow-ups retrieve well enough on their own. A team watching quality sees a partial regression that looks like model noise.

This pattern repeats. Chapter 6 grades three ways instead of two, because "exact / supported / unsupported" catches the case that "right / wrong" cannot: a fallback serving a real help-centre paragraph that is not the answer. Chapter 9 counts leaked contract terms rather than mis-set flags. Chapter 7 asks whether the app quoted a policy that is no longer true, not whether an index timestamp lagged.

And chapter 7 is where the discipline bites the author. Its first version edited documents the retriever never quoted from, so a scheduled index sitting nine ticks behind the corpus scored a perfect sixty out of sixty. The staleness was completely real and the metric could not see any of it. Nothing errored. The table looked plausible. The only reason to doubt it was noticing that a nine-tick lag with zero wrong answers makes no sense. That chapter now runs an assertion at startup that refuses to produce a table unless every edit changes at least one answer, and the general form of that assertion is the most portable thing in this repo: **before trusting a comparison, verify that the thing you are varying can change the number at all.**

## 21.4 The findings that were not the plan

A dive that only confirmed its own outline would be suspicious. Four chapters came out against expectation, and they are the ones worth reading twice.

**The abstraction tax was one line.** Chapter 1 expected the provider seam to cost something upfront and pay itself back over several requirement changes. Its upfront cost measured at a single significant line, because `provider.py` adds almost exactly what `app.py` stops carrying. There is no break-even point to find. And the requirement chosen specifically to favour the inline version, streaming one call site, still went to the seam on lines (32 against 40). The seam's real cost showed up where line counting cannot see it: its interface doubled, its cost-accounting guarantee became conditional, and retry silently stopped applying to the streaming path in *both* builds.

**The best finding in chapter 1 was an accident.** Both builds priced the same four calls at exactly $0.000099 through four requirements, then disagreed at the fifth: $0.000097 against $0.000100. A streamed response carries no usage block, so both fell back to estimating from characters, using different and equally defensible bases. One requirement took the ledger from exact and agreeing to estimated and diverging, with no error, no warning, and a cost dashboard that would draw a smooth line straight through it.

**The chapter that expected to lose, won.** Chapter 4 was written expecting to be folded into another one, on the theory that a service split is measurably worse on every number available on a laptop. It is worse by a tenth of a percent, and it buys the difference between "the model is down" and "the product is down."

**Circuit breakers do not fix percentiles.** Chapter 6 measured a breaker saving a third of the wall clock over a 24-request burst and moving p95 by nothing at all. Over 48 requests it saved 2.9x, still moving p95 by nothing. Requests that hit an open breaker were already fast; the ones that tripped it had already paid the full deadline. That is worth knowing before quoting a breaker as a latency fix.

## 21.5 Two failure modes that produce confident, plausible, wrong tables

Both of the dive's genuine methodological errors are worth naming, because they look nothing alike and fail the same way.

The first is a **floor**: a defect that affects every column equally, so the effect you are studying cannot be separated from it. Chapter 2 had a conversation template that needed two-hop retrieval and failed even with a perfect history; chapter 7 had three questions that missed against a perfectly fresh index. Both put a constant error under every design and were cut rather than kept, with the reason recorded in the code.

The second is a **ceiling**, and it is much harder to notice, because a perfect score reads as good news. That is chapter 7's sixty out of sixty. The tell in both cases was two metrics disagreeing: a mechanism metric moving exactly as predicted while an outcome metric did not move at all. When that happens, suspect the metric before the mechanism.

The habit that catches both is to state your correctness ceiling and floor explicitly before running the comparison, and to make the harness enforce them.

## 21.6 What the assembly says about all of it

The last chapter applies all nine decisions to three products at once (a support chat, an overnight batch pipeline, a voice agent), builds a latency budget from the constants the earlier chapters measured, and then runs the assembled systems to see whether the budget holds.

Two things come out, and the second is the one to remember.

The budget predicts the mean and cannot predict the tail. Every constant these chapters produced is a mean, so no amount of adding them yields a tail, and the empty column in that table is the honest result rather than a gap to be filled. Support chat's mean is 1198ms and its slowest request in the same run took 4995ms, with nothing broken. The only way to get that number is to build the thing and run it. (An earlier draft of that file carried a fabricated p95 constant sitting among the measured ones as though a chapter had produced it. It had not. Removing it was more useful than sourcing it.)

And then the finding that reframes the whole dive: **the architecture is nearly invisible in a latency budget.** Six of nine decisions come out differently across those three products, and their mean latencies land within 36ms of each other, because the model call is 98% of every request. Eight of the nine decisions moved the budget by an amount no user could perceive.

That is not an argument that the decisions do not matter. It is an argument about which column to defend them in. Chapter 2 was worth 38 points of correctness. Chapter 4 was worth ten out of ten health checks surviving an outage. Chapter 9 was worth a leaked contract. Chapter 6 was worth 2.9x wall clock under a slow dependency. None of those appear in a latency budget, and a design review that only budgets latency would have rejected all four.

If you take one thing from this dive into your next design review, take that. The question is never "is this fast enough". It is "which column does this decision win, and did anyone measure it".

## 21.7 Where this sits

Read this after Production (8), and after you have hand-rolled a retriever and an agent loop, because reading it earlier gives you opinions without the experience to check them, which is the failure mode the whole series is built against. It pairs with Observability (the numbers these ADRs cite come from somewhere) and with Professional Tools (several of these decisions are exactly what a framework decides on your behalf, and now you know what it decided).

Then go and write an ADR for a decision your own system already made by accident. Most of them were made that way. The value is not in the diagram; it is in the sentence that says what would flip it, and the number underneath.
