"""
v2, seam: requirement 2, add a timeout and retry with backoff.

Written once. app.py is untouched again, and this time that is not just
convenience: the retry policy is now guaranteed uniform, which the inline
version cannot promise. Three copies of a loop drift, and the drift is silent
until the one call site somebody forgot is the one taking the traffic.
"""

import time
from dataclasses import dataclass

from app.fake_sdks import Anthropic, AnthropicAPITimeoutError, AnthropicRateLimitError

MODEL = "claude-haiku-4-5"
TIMEOUT_S = 10.0
MAX_ATTEMPTS = 3
BACKOFF_S = 0.05
_client = Anthropic()


@dataclass(frozen=True)
class Reply:
    text: str
    prompt_tokens: int
    completion_tokens: int


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
    return Reply(
        text=resp.content[0].text.strip(),
        prompt_tokens=resp.usage.input_tokens,
        completion_tokens=resp.usage.output_tokens,
    )
