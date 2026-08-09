# Chapter 8: rollout as architecture

**The decision.** What stands between a prompt change and your users?

**The stressor.** Two candidate changes that look identical on the ticket, one
of which breaks 3 of 14 questions.

## Run it

```bash
python ch08-rollout/stress.py     # offline, no key, ~12 seconds
```

Byte-identical across runs.

## The two candidates

Both are honestly described as "spend fewer tokens on the context":

- **v2-trim**: shorter system preamble, full documents. **14/14** correct.
- **v2-truncate**: each document clipped to 120 characters. **11/14** correct.

Nothing in the diff distinguishes them. That is why rollout machinery exists.

Production feedback is a **thumbs-down** (60% on a wrong answer, 3% on a right
one), not a gold label. Offline you know which responses are wrong; online you
have a noisy proxy that needs volume. Almost every difference below comes from
that one fact.

## The result

The safe candidate shipped cleanly through every shape. The regression:

| shape | bad served | detected at | extra calls | outcome |
|---|---|---|---|---|
| ship | 129/600 | never | 0 | shipped |
| gate (suite A) | **129/600** | never | 12 | shipped |
| gate (suite B) | **0/600** | request 0 | 12 | blocked |
| canary 10% | 8/600 | request 290 | 0 | rolled back |
| shadow | 0/600 | request 39 | 600 | never promoted |
| gate (suite A) + canary | 8/600 | request 290 | 12 | rolled back |

**The gate is a coin flip.** Two six-question suites, both plausible. One stops
the regression before any user sees it, for twelve model calls and no waiting.
The other ships it to everybody and reports a pass. A gate is exactly as good
as its sample, and running it tells you nothing about which suite you have.

**The canary caught what the suite missed, and charged for it**: 8 wrong
answers and 290 requests. Both numbers are set by the noise in the signal, not
by the size of the regression. Detection fired the moment the canary reached
its 30th sample.

**Shadow exposed nobody and could not reach a verdict.** It reported **64%
disagreement against a true 21% breakage rate**, because most of the gap is
answers that changed and stayed correct. It costs a second model call on every
request and produces a queue for a human, which is a real cost in no column
here.

**Stacking is not redundancy.** Gate A plus canary scored exactly what the
canary scored alone, because the gate contributed nothing when its suite
missed. That is the argument for having both.

Full reasoning, and why the 129-versus-0 split is an illustration rather than a
discovered fact: [ADR.md](ADR.md).
