"""
v3, seam: requirement 3, report what each request cost.

One accounting line, in the one place a token count can exist. The property
this buys is stronger than a smaller diff: a model call that is not counted is
not reachable, because there is no way to reach the provider except through
this function. The inline version can only promise that nobody forgot.
"""

import time
from dataclasses import dataclass

from app.fake_sdks import Anthropic, AnthropicAPITimeoutError, AnthropicRateLimitError

MODEL = "claude-haiku-4-5"
TIMEOUT_S = 10.0
MAX_ATTEMPTS = 3
BACKOFF_S = 0.05
PRICE_IN_PER_1K = 0.00025
PRICE_OUT_PER_1K = 0.00125
_client = Anthropic()
_spend_usd: list[float] = []


@dataclass(frozen=True)
class Reply:
    text: str
    prompt_tokens: int
    completion_tokens: int


def spend_usd() -> float:
    return sum(_spend_usd)


def reset_spend() -> None:
    _spend_usd.clear()


def generate(system: str, user: str, *, max_tokens: int = 512) -> Reply:
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = _client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[
                    {"role": "user", "content": user},
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
    return Reply(
        text=resp.content[0].text.strip(),
        prompt_tokens=resp.usage.input_tokens,
        completion_tokens=resp.usage.output_tokens,
    )
