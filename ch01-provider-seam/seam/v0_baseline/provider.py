"""
v0, seam: the one file that knows which provider this is.

This file is the upfront cost. It exists before any requirement has changed,
and on day one it does nothing the inline version was not already doing. The
chapter measures whether it earns that back.
"""

from dataclasses import dataclass

from app.fake_sdks import OpenAI

MODEL = "gpt-4o-mini"
_client = OpenAI()


@dataclass(frozen=True)
class Reply:
    text: str
    prompt_tokens: int
    completion_tokens: int


def generate(system: str, user: str, *, max_tokens: int = 512) -> Reply:
    resp = _client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
    )
    return Reply(
        text=resp.choices[0].message.content.strip(),
        prompt_tokens=resp.usage.prompt_tokens,
        completion_tokens=resp.usage.completion_tokens,
    )
