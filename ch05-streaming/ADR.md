# ADR 005: stream behind a hold-back window, not raw and not buffered

**Status:** accepted
**Date:** 2026-08-07
**Measured on:** `python ch05-streaming/stress.py`, offline, no key, instant.
Timing is arithmetic at 25ms/token; detection and leaked bytes are real.

## Context

You cannot unsend a token. An output guard is a function of the text it can
see, and streaming hands text to the user before the guard has seen the rest
of it. That is the whole problem, and it is specific to this kind of system:
nothing about a JSON API forces you to publish the first half of a response
before deciding whether the second half is allowed.

Four placements of the same guard, over the same eight responses (five that
must be stopped, three that must pass, one of which contains the allowlisted
support address so a guard that blocks it is broken in the other direction):

| | behaviour |
|---|---|
| buffer | generate everything, guard, then send |
| isolated | send each token, guard that token alone |
| accumulated | send each token, guard everything so far |
| window(N) | hold N tokens back, guard everything so far, release the rest |

## Decision

Stream behind a hold-back window. Size it from the longest thing the guard
needs to recognise, not from a round number.

## Consequences, measured

**Five responses that must be stopped:**

| design | caught | client saw none of it | leaked chars | median TTFT |
|---|---|---|---|---|
| buffer | 5/5 | 5/5 | 0 | never sent |
| isolated | 0/5 | 0/5 | 109 | 25ms |
| accumulated | 5/5 | **0/5** | 99 | 25ms |
| window(8) | 5/5 | 5/5 | 0 | 225ms |

**Catching a violation and preventing it are different columns, and the
difference is the chapter.** `accumulated` is the honest streaming
implementation: it guards the full text generated so far, and it detected
every one of the five. It also delivered essentially all of every secret,
because the guard can only recognise a pattern once the pattern is complete,
and completing it is what the last delivered token did. Detection rate 100%,
prevention rate 0%.

**Guarding the chunk you are handed detects nothing.** `isolated` caught 0/5.
A four-character token cannot contain an email address, so the guard is
looking for a pattern it structurally cannot see. This shape is not a straw
man; it is what "validate the chunk in the chunk handler" produces, and inside
a streaming callback it is the obvious thing to write.

**Where the violation sits changes the leak, not the verdict:**

| case | violation at | span | accumulated leaked | window(8) leaked |
|---|---|---|---|---|
| injection_echo | 6% | 32 | 32/32 | 0/32 |
| leak_early | 16% | 20 | 18/20 | 0/20 |
| leak_middle | 57% | 23 | 22/23 | 0/23 |
| key_late | 80% | 20 | 15/20 | 0/20 |
| leak_late | 95% | 14 | 12/14 | 0/14 |

Where accumulated stops just short, that is the regex matching a prefix
(`gmail.co` before the final `m` arrives). Luck, not a design property.

**The window sweep turns the argument into a dial:**

| hold | TTFT | caught | clean stops | leaked chars |
|---|---|---|---|---|
| 0 | 25ms | 5/5 | 0/5 | 80 |
| 2 | 50ms | 5/5 | 1/5 | 40 |
| 4 | 100ms | 5/5 | 3/5 | 15 |
| 6 | 150ms | 5/5 | 4/5 | 5 |
| **8** | **200ms** | **5/5** | **5/5** | **0** |
| 16 | 400ms | 5/5 | 5/5 | 0 |

**Buffering is the bottom of that table taken to its limit**: hold = 75 tokens,
1875ms to first byte for these responses. The window reaches identical safety
on this case set at 200ms, roughly **9x better first-token latency for the
same containment**.

**And buffering charges that latency to every response, including the clean
ones.** On the three clean responses, buffer's median TTFT is 1175ms against
the window's 225ms. Most traffic is clean, so buffering pays the full
generation time on 100% of responses to contain the small fraction that are
not.

## What would flip this decision

- **A guard that needs the whole response.** Anything judging overall tone,
  factual consistency against sources, or a policy that depends on the
  conclusion cannot work on a prefix at any window size. Buffer those, or run
  them as a second pass and accept that the first pass was streamed.
- **An LLM-as-judge guard.** The window assumes the guard is cheap enough to
  run per token. A judge model per token is absurd; the window then has to
  hold whole sentences and re-check on boundaries, and the latency arithmetic
  changes completely.
- **No streaming requirement.** If nobody is watching the tokens appear,
  buffer. It is simpler and strictly safer, and the latency nobody perceives
  costs nothing.
- **Regulatory "must never emit".** A window is a probability reduction, not a
  proof. If a single leaked character is a reportable event, buffer.

## Limits of this measurement

- **The hold of 8 is not a universal constant.** It works here because the
  longest violating span in this case set is 32 characters, which is exactly 8
  tokens. A multi-line credential or a leaked paragraph needs a bigger window.
  The sweep is the method; 8 is this corpus's answer. Size the window from the
  longest pattern your guard must recognise, and re-derive it when you add a
  pattern.
- **Timing is arithmetic, not wall clock.** 25ms per token, applied identically
  to all four designs, so every millisecond figure is exactly reproducible.
  Real streams are jittery and TTFT includes network and prefill, which shifts
  every row by roughly the same amount and does not change the ordering.
- **The guard is regex, and the chapter takes it as given.** Whether regex
  detectors are any good is the prompt-injection dive's argument. One gap
  found while writing this (`sk-[A-Za-z0-9]{12,}` does not match
  `sk-live-9Kd83jXmQ0aZ`, because the run stops at the second hyphen) made a
  case invisible to every design at once, which would have read as an
  architecture result. Fixed before the numbers above were taken.
- **Retraction is not modelled as a fifth design.** Sending a "disregard that"
  event after the fact is what a UI can do, not what an architecture can
  guarantee: the bytes were rendered, possibly screenshotted, and certainly
  logged. Counting a retraction as prevention would be counting a wish.
