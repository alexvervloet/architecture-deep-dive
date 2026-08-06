"""
v1, seam: requirement 1, move from OpenAI to Anthropic.

The same four differences the inline version hit at three call sites are hit
here once. app.py is untouched, and untouched is the number that matters: it
is not "a smaller diff", it is "that file was not opened".
"""

from dataclasses import dataclass

from app.fake_sdks import Anthropic

MODEL = "claude-haiku-4-5"
_client = Anthropic()


@dataclass(frozen=True)
class Reply:
    text: str
    prompt_tokens: int
    completion_tokens: int


def generate(system: str, user: str, *, max_tokens: int = 512) -> Reply:
    resp = _client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[
            {"role": "user", "content": user},
        ],
    )
    return Reply(
        text=resp.content[0].text.strip(),
        prompt_tokens=resp.usage.input_tokens,
        completion_tokens=resp.usage.output_tokens,
    )
