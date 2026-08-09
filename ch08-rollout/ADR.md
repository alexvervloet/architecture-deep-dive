# ADR 008: gate on an eval suite, then canary, and treat neither as sufficient

**Status:** accepted
**Date:** 2026-08-07
**Measured on:** `python ch08-rollout/stress.py`, offline, no key, ~12s,
byte-identical across runs.

## Context

A prompt change is a deploy. Unlike a code deploy it has no type checker, no
compiler, and no stack trace when it goes wrong: it just answers differently,
and "differently" is only visible against something.

Two candidate changes, both honestly described as "spend fewer tokens on the
context", both the same size of diff:

- **v2-trim**: shorter system preamble, full documents. 14/14 of the workload
  still correct.
- **v2-truncate**: each document clipped to 120 characters. 11/14 correct, so
  3 of 14 questions now answer from the wrong part of the right document.

Nothing in the diff distinguishes them. That is the premise: rollout machinery
exists because you cannot tell by reading.

Production feedback is modelled as a **thumbs-down**, not a gold label: 60% on
a wrong answer, 3% on a right one. Offline you know exactly which responses are
wrong; online you have a noisy proxy that needs volume before it means
anything. Most of the differences below come from that one fact.

## Consequences, measured

600 production requests per rollout. The safe candidate shipped cleanly through
every shape with zero bad answers, so the interesting table is the regression:

| shape | bad served | detected at | extra calls | outcome |
|---|---|---|---|---|
| ship | 129/600 | never | 0 | shipped |
| gate (suite A) | **129/600** | never | 12 | shipped |
| gate (suite B) | **0/600** | request 0 | 12 | blocked |
| canary 10% | 8/600 | request 290 | 0 | rolled back |
| shadow | 0/600 | request 39 | 600 | never promoted |
| gate (suite A) + canary | 8/600 | request 290 | 12 | rolled back |

**The gate is precise, sampled, and therefore a coin flip.** Two six-question
suites, both plausible, one written from the most common questions and one from
a spread. Suite B stops the regression before a single user sees it, for twelve
extra model calls and no waiting at all. Suite A ships it to everybody and
reports a clean pass. The gate is exactly as good as its sample, and running it
tells you nothing about which suite you have.

**The canary is complete and slow.** It sees every question, including the ones
no suite covers, and it caught what suite A missed. It cost 8 wrong answers and
290 requests, and both numbers are set by the noise in the signal rather than by
the size of the regression: detection fired the moment the canary accumulated
its 30th sample. A quieter regression, or a smaller canary share, moves that
number and not in a good direction.

**Shadow exposes nobody and cannot reach a verdict.** Zero bad answers served,
flagged at request 39, and 600 extra model calls, one per request, doubling
inference spend for the duration. What it produced was a **64% disagreement
rate against a true 21% breakage rate**. Most of that gap is answers that
changed and stayed correct. Shadow can tell you something moved; it cannot tell
you whether that was bad, so its output is a queue for a human, which is a real
cost that appears in no column here.

**Stacking is not redundancy.** `gate (suite A) + canary` scored exactly what
the canary scored alone, because the gate contributed nothing when its suite
missed. That is the argument for having both rather than evidence against it:
the gate is free and instant when it works, and the canary is the only thing
covering what the suite does not.

## Decision

Gate on an eval suite, then canary. Expect the gate to catch the regressions
your suite happens to sample and nothing else. Use shadow for changes where a
wrong answer is expensive enough to justify double inference and a human
reviewer.

## What would flip this decision

- **A workload small enough to enumerate.** If the eval suite can cover every
  question your users actually ask, the gate stops being a sample and the
  canary becomes optional.
- **Too little traffic to canary.** The canary needed 30 samples at 10%
  exposure, which is 300 requests. A service doing 300 requests a week cannot
  detect anything this way, and the gate plus shadow is the whole toolkit.
- **A wrong answer that cannot be tolerated even eight times.** Then shadow,
  and pay for the second inference and the reviewer.
- **Changes that alter behaviour that no automatic signal can grade** (tone,
  formatting, persona). None of these shapes help; that is a human review
  problem wearing a deploy costume.

## Limits of this measurement

- **Both eval suites were chosen by me.** Suite A missing all three affected
  questions is not a discovered fact, it is a constructed illustration of a
  real phenomenon. The transferable claim is the mechanism (a sampled gate is
  blind outside its sample), not the 129-versus-0 split, which is as large or
  small as the sample I picked.
- **The thumbs-down rates are invented**, at 60% and 3%. Detection latency
  scales directly with the gap between them, so the "request 290" figure is a
  property of those constants. Real feedback is far sparser, which makes real
  canaries slower than this one, not faster.
- **The canary share and thresholds were not tuned** (10%, 30 samples, 6 point
  margin). A more sensitive canary detects sooner and rolls back on noise more
  often; the chapter does not measure that failure mode.
- **Shadow's disagreement rate is compared against a known breakage rate**,
  which is only possible because this repo has gold labels. That comparison is
  the point being made and is also the thing a real shadow deployment cannot
  do for itself.
- **One regression, one safe change.** A regression that broke rarer questions
  would evade both the gate and the canary for longer, and this chapter does
  not sweep that.
