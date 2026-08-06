"""
v3, inline: requirement 3, report what each request cost.

The arithmetic is trivial and the placement is not. The token counts live on
the response object, so the accounting line has to go next to every call, and
"what did this request cost" is only correct if every call site remembers to
add it. Nothing in the code enforces that, and a missed call site does not
fail, it just under-reports spend, which is the failure mode you find in a
finance review rather than in a test.
"""

import time

from app import retrieval
from app.fake_sdks import Anthropic, AnthropicAPITimeoutError, AnthropicRateLimitError

client = Anthropic()
MODEL = "claude-haiku-4-5"
TIMEOUT_S = 10.0
MAX_ATTEMPTS = 3
BACKOFF_S = 0.05
PRICE_IN_PER_1K = 0.00025
PRICE_OUT_PER_1K = 0.00125
_spend_usd: list[float] = []


def spend_usd() -> float:
    return sum(_spend_usd)


def reset_spend() -> None:
    _spend_usd.clear()


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
    _spend_usd.append(
        resp.usage.input_tokens / 1000 * PRICE_IN_PER_1K
        + resp.usage.output_tokens / 1000 * PRICE_OUT_PER_1K
    )
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
    _spend_usd.append(
        resp.usage.input_tokens / 1000 * PRICE_IN_PER_1K
        + resp.usage.output_tokens / 1000 * PRICE_OUT_PER_1K
    )
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
    _spend_usd.append(
        resp.usage.input_tokens / 1000 * PRICE_IN_PER_1K
        + resp.usage.output_tokens / 1000 * PRICE_OUT_PER_1K
    )
    return resp.content[0].text.strip()
