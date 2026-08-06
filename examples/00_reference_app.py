#!/usr/bin/env python3
"""
00_reference_app.py: the app every chapter rebuilds, running once.

    python examples/00_reference_app.py        # offline, no key, ~2 seconds

Nothing here is an architecture lesson yet. This is the baseline: one request
path, three dependencies, no retries, no fallback, no queue, no cache. The
shape a competent engineer writes on day one and ships on day two.

Read the timing breakdown at the bottom. The model call is most of the
request, which is the fact every later chapter is downstream of: when your
p95 is dominated by a dependency you do not control and cannot make faster,
the only moves left are structural.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from app import providers, service

load_dotenv()
providers.configure_mock(latency="slow", seed=1337)

print(f"Provider: {providers.describe()}\n")

for i, question in enumerate(
    [
        "How do I reset my password?",
        "How long do refunds take to post?",
        "Do you support quantum entanglement?",  # nothing in the corpus covers this
        "This is broken, please open a ticket for me.",  # takes the tool path
        "How long are audit logs retained?",  # a clean hit, for contrast
    ]
):
    answer = service.handle(question, request_id=f"demo-{i}")
    print(f"Q: {question}")
    print(f"A: {answer.text}")
    print(
        f"   sources={','.join(answer.sources) or 'none'}"
        f"  calls={answer.provider_calls}"
        f"  tokens={answer.prompt_tokens}+{answer.completion_tokens}"
    )
    t = answer.timings
    print(
        f"   retrieval={t.retrieval_ms:.0f}ms  model={t.provider_ms:.0f}ms"
        f"  tool={t.tool_ms:.0f}ms  total={t.total_ms:.0f}ms\n"
    )

print(f"Session remembers {service.turns()} messages, in this process only.")
print("Chapter 2 starts a second worker and watches that number go to zero.")
print()
print("Look at question 3. Nothing in the corpus is about quantum entanglement, and")
print("the app answered anyway, fluently, from a document about two-factor auth. It")
print("did not fail, it did not warn, and an uptime dashboard would call that request")
print("a success. That is why the stress harness has a `wrong_source` bucket separate")
print("from `error`: an availability number that cannot see this request is measuring")
print("the wrong thing, and chapter 6 is where that distinction decides an argument.")
