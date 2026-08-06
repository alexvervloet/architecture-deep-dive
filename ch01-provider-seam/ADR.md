# ADR 001: put every model call behind one seam

**Status:** accepted
**Date:** 2026-08-06
**Measured on:** the offline mock, `python ch01-provider-seam/measure.py` and
`fairness.py`, both reproducible with no key.

## Context

An app calls a model from more than one place. The question is whether those
calls go straight to the SDK at each call site, or through one function that
every call site shares.

The usual argument for the seam is portability, and it is usually made without
numbers. This chapter builds the same four-feature app both ways, applies five
requirements to both, and counts the churn. Both variants are held to
byte-identical output by `fairness.py`, so a smaller diff cannot be bought by
quietly doing less.

The five requirements, chosen because each one is a real thing that happens to
an app in its first year:

1. Swap the provider (OpenAI to Anthropic).
2. Add a timeout and retry with backoff.
3. Report what each request cost.
4. Add a fourth model-backed feature.
5. Stream one call site to the client.

Requirement 5 was included specifically because it was expected to favour the
inline variant.

## Options

**A. Inline.** Each call site talks to the SDK directly. No indirection, every
call site legible on its own, nothing to learn before reading the code.

**B. Seam.** One `provider.py` exposing `generate(system, user)`. Call sites
contain no provider vocabulary.

## Decision

Take the seam, from the first call site, and expect it to stop being free the
first time a call site needs a fundamentally different interaction shape.

## Consequences, measured

**The seam's upfront cost was 1 line.** 40 significant lines against 39, at
v0, before any requirement. `provider.py` adds almost exactly what `app.py`
stops carrying. The "abstraction tax" that this decision is usually argued
about, at this scale, is a rounding error. There is no break-even point to
find in the table below because there was nothing to pay back.

**Churn per requirement, significant lines (comments and docstrings excluded):**

| Requirement | inline | seam |
|---|---|---|
| 1. swap provider | 33, in `app.py` | 18, in `provider.py` |
| 2. timeout + retry | 78, in `app.py` | 30, in `provider.py` |
| 3. cost accounting | 19, in `app.py` | 11, in `provider.py` |
| 4. a fourth feature | 22, in `app.py` | 4, in `app.py` |
| 5. stream one call site | 40, in `app.py` | 32, in `app.py` + `provider.py` |
| **total** | **192** | **95** |

The seam changed 49% as many lines across the five requirements. For the first
three, `app.py` was never opened at all, which is a stronger claim than a small
diff: those changes could not have broken a feature, because no feature's code
was in the blast radius.

**Requirement 2 is the real gap, and it is a gap about drift, not typing.**
78 lines against 30, because the retry loop is identical at three call sites.
The line count understates it: three copies of a retry policy drift, and the
drift is silent until the one call site somebody forgot to update is the one
taking traffic. The seam cannot have that bug. Note also what the inline
variant's honest v2 looks like: a reader's instinct is to extract a helper,
and acting on that instinct *is* building the seam, late and under deadline.

**Requirement 4 shows the compounding.** 22 lines against 4. Everything the
earlier requirements added has to be re-typed by hand at a new inline call
site (retry loop, timeout, exception tuple, cost line), and omitting any of
them is a silent bug rather than an error. The seam's new feature was retried,
timed out, and cost-accounted because it could not not be.

**Requirement 5 refuted the prediction.** Streaming was expected to cost the
seam more, because a streamed call cannot be expressed as the seam's single
buffered return type and forces a second method. The seam still changed fewer
lines: 32 against 40. The prediction was wrong; the number stands.

What the line count missed is that the cost landed somewhere it cannot see:

- The seam's interface doubled. Every future provider must now implement two
  methods, and streaming shapes differ far more between vendors than buffered
  ones do (OpenAI yields chunks with `delta.content`; Anthropic gives a
  context manager with a `text_stream`). The v1 swap cost 18 lines against one
  method; it would cost more against two.
- The v3 guarantee weakened. "An uncounted model call is unreachable" became
  "unreachable, unless it streams".
- Retry silently does not apply to the streaming path, in *both* variants,
  because retrying mid-stream means retracting tokens the caller already has.
  Neither design admits this in its signature.

**The cost ledger broke quietly, and that was the most useful finding.**
`fairness.py` reports spend per build. Through v4 both variants agree exactly
($0.000099). At v5 they disagree: $0.000097 inline against $0.000100 seam.
Neither is a bug. A streamed response carries no usage block, so both fell
back to estimating from characters, and the two estimates use different and
equally defensible bases (the inline version measures the context it built,
the seam measures the full system prompt it was handed). One requirement took
the ledger from exact and agreeing to estimated and diverging, with no error,
no warning, and a cost dashboard that would draw a smooth line straight
through the change.

## What would flip this decision

- **One call site, and no plan for a second.** At n=1 the seam is pure
  overhead; every number above is driven by repetition.
- **The call sites genuinely differ in kind.** The measured advantage comes
  from call sites that want the same interaction. A codebase where one path
  streams, one does batch, one does tool loops, and one does embeddings is a
  codebase where a single `generate()` is a lie, and requirement 5 is the
  first sight of that.
- **A framework already owns the seam.** If LiteLLM or similar is in the
  stack, this decision is already made, and the question becomes what its
  abstraction hides. See the professional-tools dive, ch01.

## Limits of this measurement

- **One app, four call sites, five requirements, chosen by the author.** A
  different requirement set gives different numbers. The requirement most
  favourable to inline was included deliberately, and the seam still won it on
  lines, which is some evidence the slate was not stacked.
- **Churn counts a modified line as two** (one removed, one added). It inflates
  both variants equally, so the ratio holds; the absolute numbers are churn,
  not new code.
- **The seam here is minimal.** A real one grows config, provider registries,
  fallbacks, and its own tests, none of which are in the 1-line upfront cost.
  Read that number as the floor, not the typical case.
- **Line count is a proxy for cost, and a poor one.** It is used because it is
  measurable and hard to fudge under the fairness gate. The three most
  important consequences in requirement 5 are invisible to it, and this ADR
  argues them in prose because that is the honest place to argue them.
