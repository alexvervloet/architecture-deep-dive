"""
v2, inline: requirement 2, add a timeout and retry with backoff.

This is the version worth arguing about. The retry loop is identical at all
three call sites, and a reader will notice immediately that it wants to be
extracted into a helper. That instinct is correct, and acting on it is exactly
what "build the seam" means: the moment you write `def _call_with_retry(...)`
you have conceded the chapter's point and paid the seam's cost anyway, just
later and under deadline.

So this version keeps the duplication, because that is what incremental change
under a deadline actually produces, and because the honest comparison is
between "no seam" and "seam", not between "seam" and "a seam introduced
halfway through a reliability fix".
"""

import time

from app import retrieval
from app.fake_sdks import Anthropic, AnthropicAPITimeoutError, AnthropicRateLimitError

client = Anthropic()
MODEL = "claude-haiku-4-5"
TIMEOUT_S = 10.0
MAX_ATTEMPTS = 3
BACKOFF_S = 0.05


def triage(question: str) -> str:
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=8,
                system="CLASSIFY the support request into one label.",
                messages=[
                    {"role": "user", "content": question},
                ],
                timeout=TIMEOUT_S,
            )
            break
        except (AnthropicRateLimitError, AnthropicAPITimeoutError):
            if attempt == MAX_ATTEMPTS - 1:
                raise
            time.sleep(BACKOFF_S * (2**attempt))
    return resp.content[0].text.strip()


def answer(question: str, tenant: str = "acme") -> tuple[str, tuple[str, ...]]:
    hits = retrieval.search(question, tenant=tenant)
    context = retrieval.format_context(hits.documents)
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=512,
                system=f"Answer only from the context.\n\nCONTEXT:\n{context}",
                messages=[
                    {"role": "user", "content": question},
                ],
                timeout=TIMEOUT_S,
            )
            break
        except (AnthropicRateLimitError, AnthropicAPITimeoutError):
            if attempt == MAX_ATTEMPTS - 1:
                raise
            time.sleep(BACKOFF_S * (2**attempt))
    return resp.content[0].text.strip(), tuple(d.doc_id for d in hits.documents)


def digest(text: str) -> str:
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=64,
                system="SUMMARIZE the text in one sentence.",
                messages=[
                    {"role": "user", "content": text},
                ],
                timeout=TIMEOUT_S,
            )
            break
        except (AnthropicRateLimitError, AnthropicAPITimeoutError):
            if attempt == MAX_ATTEMPTS - 1:
                raise
            time.sleep(BACKOFF_S * (2**attempt))
    return resp.content[0].text.strip()
