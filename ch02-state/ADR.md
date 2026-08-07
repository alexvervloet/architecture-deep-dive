# ADR 002: conversation state goes in a shared store, not in the process

**Status:** accepted
**Date:** 2026-08-06
**Measured on:** the offline mock, `python ch02-state/stress.py`, real worker
processes, no key required.

## Context

The reference app keeps conversation history in a module-level dict. So does
most of the LLM sample code on the internet, and so does almost every
prototype, because at one process it is correct, free, and obvious.

The question is what that costs once there is more than one process, and
whether the standard cheap fix (route each session to the same worker) is
enough.

Three designs, same app, same requests:

| design | store | routing |
|---|---|---|
| memory + spread | module-level dict | load balancer, session-unaware |
| memory + sticky | module-level dict | route by session id |
| sqlite + spread | shared file | session-unaware |

The workload is 24 two-turn conversations where the second turn cannot be
answered without the first. "How long is that link valid?" follows either
"How do I reset my password?" (30 minutes) or "How do I export my data?"
(seven days). The follow-up sentence is identical in both; only the
conversation distinguishes them.

Correctness is measured by **provenance**: did the answer sentence come out of
the document the conversation was about. Not "was the right document
retrieved", which reports 100% while the user is being told the wrong thing
(see LESSONS.md).

## Options

**A. In-process dict.** Zero latency, zero infrastructure, and a hidden
requirement that a session's requests reach one particular machine.

**B. In-process dict + sticky routing.** Makes the hidden requirement explicit
in the load balancer, and keeps the speed.

**C. Shared store.** Any worker can serve any request. Costs a read and a
write per turn.

## Decision

Take C. Keep conversation state out of the process from the first day, and
treat sticky routing as a performance optimisation rather than a correctness
mechanism.

## Consequences, measured

**At one worker, all three designs are perfect.** 24/24 for every design. This
is the experiment everyone runs before shipping, and it contains no
information about which design is wrong.

**Scaling out breaks the dict, silently.**

| workers | memory + spread | memory + sticky | sqlite + spread |
|---|---|---|---|
| 1 | 100% | 100% | 100% |
| 2 | 92% | 100% | 100% |
| 4 | 62% | 100% | 100% |
| 4, one restarted | 58% | 96% | 100% |

Nothing raised an exception, nothing timed out, no response was empty. The
follow-ups just got answered from the wrong document, fluently. Asked how long
the password reset link is valid, the app replied "The link is valid for seven
days", which is the correct answer to a question about data exports and wrong
by two orders of magnitude here.

**The bug is roughly half-invisible even when it fires, which is why it
survives.** At four workers only 29% of follow-ups saw any history, but 62%
were still answered correctly. Losing the history costs the answer only about
half the time, because some follow-ups happen to retrieve correctly on their
own. A team watching a quality metric sees a partial regression that looks
like model noise, not a structural fault.

**Sticky routing works, until a process restarts.** It held 100% through the
scale-out experiment. Then one worker was restarted between the two turns (a
deploy, an OOM kill, a scale-in event, a container reschedule) and 21% of
follow-ups lost their history. Correctness only fell to 96%, which is the
trap: the damage is real, small, and easy to attribute to something else. It
also recurs on every single deploy, and it grows with restart frequency, which
is the opposite of the direction a maturing service moves.

**The shared store cost 0.47ms per turn.** Against the dict's 0.008ms that is
roughly 50x, and it is the right way to lose that argument: 0.47ms against a
realistic model call of ~900ms is under 0.1% of the request. The correctness
column is worth more than 0.47ms.

## What would flip this decision

- **Single process, and a hard commitment to stay there.** A CLI tool, a
  desktop app, a notebook. The dict is correct and free there.
- **State that is genuinely per-process and disposable.** A connection pool, a
  compiled regex cache, a warm client. Those belong in the process. The test
  is whether a second request needs to see it.
- **Latency budgets in the single-digit milliseconds.** Not an LLM app. If the
  model call is 900ms, the store is noise; if there is no model call, this
  arithmetic changes completely.
- **A store that is not actually shared.** SQLite on a local disk across
  several machines is the dict again with extra steps and more confidence.

## Limits of this measurement

- **SQLite is the cheapest possible shared store**, on the same machine, with
  no network. 0.47ms is a floor. Postgres or Redis across a network is more
  like 1 to 5ms, still small against a model call, but not 0.47.
- **Routing is modelled, not real.** `spread` is a deterministic pseudo-random
  pick, which is not what least-connections does, but it shares the property
  that matters: it is not session-aware, so a follow-up lands on the right
  worker with probability 1/workers. The measured 29% at four workers matches
  the predicted 25% within the noise of 24 samples.
- **Two-turn conversations.** Longer conversations make the dict worse, not
  better: the chance that every turn lands on one worker falls off fast.
- **The correctness ceiling is 100% by construction.** One conversation
  template was cut because it needed two-hop retrieval and failed even with a
  perfect history. Keeping it would have put a permanent error floor under
  every column and made lost state indistinguishable from a retriever
  limitation.
