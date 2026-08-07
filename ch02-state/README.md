# Chapter 2: where conversation state lives

**The decision.** Does conversation history live in the process that happens to
serve the request, or somewhere every process can reach?

**The stressor.** Start a second worker. Then a third and a fourth. Then
restart one.

## Run it

```bash
python ch02-state/stress.py     # offline, no key, ~15 seconds
```

Real OS processes, not threads. Threads share memory, so a thread-based version
of this experiment would show an in-process dict working perfectly and would
prove the opposite of the truth.

## The workload

24 two-turn conversations whose second turn is meaningless on its own:

```
"How do I reset my password?"   ->  "How long is that link valid?"   ->  30 minutes
"How do I export my data?"      ->  "How long is that link valid?"   ->  seven days
```

Same follow-up sentence, two different correct answers. Only the conversation
tells them apart, so an app that loses the history does not fail, it answers a
different question well.

Correctness is scored on **provenance**: did the answer sentence actually come
out of the document the conversation was about. An earlier version of this
chapter scored "was the gold document retrieved" and reported 100% correct on
an app that was visibly answering the wrong question. See [LESSONS.md](../LESSONS.md).

## The result

| workers | memory + spread | memory + sticky | sqlite + spread |
|---|---|---|---|
| 1 | 100% | 100% | 100% |
| 2 | 92% | 100% | 100% |
| 4 | 62% | 100% | 100% |
| 4, one restarted | 58% | 96% | 100% |

At one worker everything is perfect, which is why this ships.

The failure is silent. Asked how long the password reset link stays valid, the
four-worker build answers "The link is valid for seven days", which is correct
for data exports and wrong here by two orders of magnitude. No exception, no
timeout, HTTP 200.

Two findings worth the chapter:

**The bug is about half-invisible even when it fires.** At four workers only
29% of follow-ups saw any history, but 62% were still answered correctly,
because some follow-ups retrieve well enough on their own. What a team sees is
a partial quality regression that looks like model noise.

**Sticky routing survives scale-out and not a restart.** It held 100% at four
workers, then dropped to 96% when one worker was restarted between turns. Small
enough to blame on something else, and it recurs on every deploy.

The shared store cost 0.47ms per turn against the dict's 0.008ms. That is 50x,
and it is under 0.1% of a realistic 900ms model call.

Full reasoning and the conditions that flip the decision: [ADR.md](ADR.md).
