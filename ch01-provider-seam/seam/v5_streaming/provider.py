"""
v5, seam: requirement 5, stream the answer to the client.

This is where the seam stops being free, and the reason is not sloppiness in
the design: a streamed call is genuinely not the same operation as a buffered
one. It returns over time instead of at once, so `Reply` cannot express it,
and one function cannot return both shapes without lying about one of them.

So the interface grows a second method.

This chapter expected that growth to show up as a bigger diff than the inline
version's, and measured the opposite: 32 lines here against 40 inline, across
two files instead of one. The prediction was wrong and the number stands. What
the line count misses is where the cost actually landed:

  1. **Every future provider now implements two things, not one.** The v1 swap
     cost 18 lines against one method. It would cost more against two, and the
     streaming shapes differ far more between vendors than the buffered ones
     do: OpenAI yields chunks carrying `delta.content`, Anthropic hands you a
     context manager with a `text_stream`. The seam now has to paper over a
     real difference rather than a cosmetic one.
  2. **The cost guarantee weakens.** v3's promise was that an uncounted call
     is unreachable. Streamed calls carry no usage block, so this path
     estimates, and the ledger holds two kinds of number without saying so.
  3. **Retry does not carry over.** Retrying mid-stream means retracting
     tokens the caller already has. The buffered path keeps its retry; this
     path cannot. The uniformity the seam was selling is now conditional, and
     nothing in the type signature admits it.

None of those three is a line of diff, which is the honest limit of the
measurement this chapter is built on.
"""

import time
from dataclasses import dataclass
from typing import Iterator

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


def generate_stream(system: str, user: str, *, max_tokens: int = 512) -> Iterator[str]:
    chunks: list[str] = []
    with _client.messages.stream(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[
            {"role": "user", "content": user},
        ],
    ) as stream:
        for chunk in stream.text_stream:
            chunks.append(chunk)
            yield chunk
    _spend_usd.append(
        len(system + user) / 4 / 1000 * PRICE_IN_PER_1K
        + len("".join(chunks)) / 4 / 1000 * PRICE_OUT_PER_1K
    )
