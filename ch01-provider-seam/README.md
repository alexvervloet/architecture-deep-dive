# Chapter 1: the provider seam

**The decision.** Do model calls go straight to the SDK at every call site, or
through one function that every call site shares?

**The stressor.** Five requirements, applied to both builds, with the diff
counted.

## Run it

```bash
python ch01-provider-seam/fairness.py    # first: are these the same app?
python ch01-provider-seam/measure.py     # then: what did each change cost?
```

Order matters. `fairness.py` loads all twelve builds and checks they produce
byte-identical output for the same inputs. A line count is trivially winnable
by doing less work, so until that passes, `measure.py` is measuring nothing.

## What is here

```
inline/     v0_baseline  v1_claude  v2_reliability  v3_cost  v4_feature  v5_streaming
seam/       v0_baseline  v1_claude  v2_reliability  v3_cost  v4_feature  v5_streaming
```

Twelve builds of the same four-feature app. Each version is the previous one
with exactly one requirement applied, so the diff between two adjacent
directories *is* the cost of that requirement. Nothing is generated: these are
the files, and you can read any diff yourself with `diff -u`.

Worth reading in full: [`inline/v0_baseline/app.py`](inline/v0_baseline/app.py)
and [`seam/v0_baseline/`](seam/v0_baseline/) for the starting point, then
[`inline/v2_reliability/app.py`](inline/v2_reliability/app.py), which is where
the argument gets real.

The SDK calls are real in shape, not toys. [`app/fake_sdks.py`](../app/fake_sdks.py)
mimics the actual OpenAI and Anthropic call signatures, response shapes, token
field names, error types, and streaming interfaces, over the deterministic
mock. A chapter that swapped one identical fake client for another would prove
nothing.

## The result

The seam changed 95 lines across five requirements; inline changed 192. Its
upfront cost was 1 line, so there is no break-even point to find.

The requirement chosen to favour inline (streaming one call site) still went
to the seam on lines, 32 against 40, which refuted this chapter's own
prediction. The real cost of that requirement showed up somewhere line
counting cannot see: the seam's interface doubled, its cost-accounting
guarantee weakened, and retry silently stopped applying to the streaming path
in *both* builds.

The finding worth taking to a design review is the ledger. Through v4 both
variants price the same four calls at exactly $0.000099. At v5 they disagree,
$0.000097 against $0.000100, because a streamed response has no usage block and
each build fell back to a different, defensible estimate. No error, no warning,
and a cost dashboard would draw a smooth line through it.

Full reasoning, consequences, and the conditions that flip the decision:
[ADR.md](ADR.md).
