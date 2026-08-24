"""
v5, inline: requirement 5, stream the answer to the client.

One call site wanted streaming. One call site changed. The other three did not
move, and nobody had to agree on what a "streaming interface" means, because
there is no interface: there is a call to the SDK, and the SDK already has a
streaming shape.

The cost is real but local, and it is written in the comments below: this call
site lost its retry, and its cost number is now an estimate rather than a
measurement. Both are invisible from outside the function.
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


def answer(
    question: str, tenant: str = "acme", on_token=None
) -> tuple[str, tuple[str, ...]]:
    hits = retrieval.search(question, tenant=tenant)
    context = retrieval.format_context(hits.documents)
    chunks: list[str] = []
    with client.messages.stream(
        model=MODEL,
        max_tokens=512,
        system=f"Answer only from the context.\n\nCONTEXT:\n{context}",
        messages=[
            {"role": "user", "content": question},
        ],
    ) as stream:
        for chunk in stream.text_stream:
            chunks.append(chunk)
            if on_token is not None:
                on_token(chunk)
    text = "".join(chunks).strip()
    # Two things changed here, and neither is a line of diff.
    # 1. The retry loop is gone. Retrying a stream means retracting tokens the
    #    caller has already seen, which is a different problem, so this call
    #    site silently lost the reliability the previous version gave it.
    # 2. A streamed response carries no usage block, so spend is estimated from
    #    characters. The ledger now mixes measured and estimated numbers and
    #    cannot tell you which is which.
    _spend_usd.append(
        (len(context) + len(question)) / 4 / 1000 * PRICE_IN_PER_1K
        + len(text) / 4 / 1000 * PRICE_OUT_PER_1K
    )
    return text, tuple(d.doc_id for d in hits.documents)


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


def escalate(question: str) -> str:
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=128,
                system="SUMMARIZE the request as a one-line escalation note.",
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
