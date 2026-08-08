# Chapter 5: what streaming costs your guardrails

**The decision.** Where does the output guard run, when the output is being
delivered as it is produced?

**The stressor.** Responses that turn unsafe partway through, at known
positions from 6% to 95% of the way in.

## Run it

```bash
python ch05-streaming/stress.py     # offline, no key, instant
```

Timing is arithmetic at 25ms per token, applied identically to every design,
so every figure is exactly reproducible. Detection and leaked bytes are not
simulated: the guard is real and the bytes are counted from what each design
actually handed over.

## The result

Five responses that must be stopped:

| design | caught | client saw none of it | leaked chars | median TTFT |
|---|---|---|---|---|
| buffer | 5/5 | 5/5 | 0 | never sent |
| isolated | 0/5 | 0/5 | 109 | 25ms |
| accumulated | 5/5 | **0/5** | 99 | 25ms |
| window(8) | 5/5 | 5/5 | 0 | 225ms |

**Catching and preventing are different columns.** `accumulated` is the
honest streaming implementation, guarding all text generated so far. It
detected every violation and prevented none of them, because a pattern only
becomes recognisable once it is complete, and completing it is what the last
delivered token did. 100% detection, 0% prevention.

**Guarding the chunk you were handed detects nothing at all.** A
four-character token cannot contain an email address. That design caught 0 of
5, and it is what "validate the chunk in the chunk handler" naturally
produces.

**The window turns the argument into a dial:**

| hold | TTFT | clean stops | leaked chars |
|---|---|---|---|
| 0 | 25ms | 0/5 | 80 |
| 4 | 100ms | 3/5 | 15 |
| **8** | **200ms** | **5/5** | **0** |
| 16 | 400ms | 5/5 | 0 |

Buffering is that table taken to its limit: hold = 75 tokens, 1875ms to first
byte. The window reaches identical safety at 200ms, about **9x better
first-token latency for the same containment**. And buffering charges its
latency to every response: on the clean ones its median TTFT is 1175ms against
the window's 225ms.

**The hold of 8 is not a constant.** It works because the longest violating
span here is 32 characters, exactly 8 tokens. The sweep is the method; 8 is
this corpus's answer.

Full reasoning, and why retraction is not counted as a design:
[ADR.md](ADR.md).
