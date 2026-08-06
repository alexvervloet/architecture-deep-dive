"""
app/fake_sdks.py: offline stand-ins shaped exactly like the real SDKs.

Chapter 1 counts the lines it takes to swap providers. That count is fiction
unless the code being swapped is really provider-shaped, so these shims copy
the parts of the OpenAI and Anthropic Python SDKs that a call site actually
touches:

  OpenAI      client.chat.completions.create(...)
              -> resp.choices[0].message.content
              -> resp.usage.prompt_tokens / completion_tokens
              streaming: iterate chunks, chunk.choices[0].delta.content

  Anthropic   client.messages.create(system=..., messages=[...])
              -> resp.content[0].text          (a list of blocks, not a string)
              -> resp.usage.input_tokens / output_tokens
              streaming: with client.messages.stream(...) as s: s.text_stream

Those differences are the whole chapter. `content` is a list of blocks in one
and a string in the other; the system prompt is a top-level argument in one
and a message with `role="system"` in the other; token fields have different
names; `max_tokens` is optional in one and required in the other; the error
types live in different modules. None of that is incidental complexity a
wrapper can wish away, and a chapter that swapped `Client()` for `Client()`
would prove nothing.

Everything routes to the same deterministic mock, so both sides produce
identical text and identical token counts. Any difference the chapter measures
is structural, which is the only kind of difference worth an ADR.

These are not a general-purpose fake. They cover the calls ch01 makes, and
they raise a clear error on anything else rather than silently doing something
plausible.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterator

from app import providers

# --- Errors, in the same shape and the same places as the real SDKs ---------
# Retry code in the wild catches these by name, and the names differ per
# provider. Chapter 1's reliability change has to catch the right ones, which
# is one of the costs the swap column is measuring.


class OpenAIError(Exception):
    pass


class OpenAIRateLimitError(OpenAIError):
    pass


class OpenAIAPITimeoutError(OpenAIError):
    pass


class AnthropicError(Exception):
    pass


class AnthropicRateLimitError(AnthropicError):
    pass


class AnthropicAPITimeoutError(AnthropicError):
    pass


def _request_id(system: str, user: str) -> str:
    """Stable per (system, user), so the mock's latency and failures stay a
    property of the request even though SDK call sites do not pass an id."""
    return hashlib.blake2b(f"{system}|{user}".encode(), digest_size=6).hexdigest()


def _call(system: str, user: str, timeout_ms: float | None) -> providers.LLMResponse:
    return providers._mock_generate(
        system, user, request_id=_request_id(system, user), attempt=0, timeout_ms=timeout_ms
    )


def _split_messages(messages: list[dict]) -> tuple[str, str]:
    """OpenAI-style messages -> (system, user). The last user turn is the one
    that matters for this app; a real client would send the whole history."""
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    user = "\n".join(m["content"] for m in messages if m["role"] == "user")
    return system, user


# --- OpenAI shape -----------------------------------------------------------


@dataclass
class _OAMessage:
    content: str


@dataclass
class _OAChoice:
    message: _OAMessage


@dataclass
class _OAUsage:
    prompt_tokens: int
    completion_tokens: int


@dataclass
class _OAResponse:
    choices: list[_OAChoice]
    usage: _OAUsage
    model: str


@dataclass
class _OADelta:
    content: str | None


@dataclass
class _OAStreamChoice:
    delta: _OADelta


@dataclass
class _OAChunk:
    choices: list[_OAStreamChoice]


class _OpenAICompletions:
    def create(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens: int | None = None,
        timeout: float | None = None,
        stream: bool = False,
    ):
        system, user = _split_messages(messages)
        timeout_ms = timeout * 1000 if timeout else None
        try:
            resp = _call(system, user, timeout_ms)
        except providers.ProviderTimeout as exc:
            raise OpenAIAPITimeoutError(str(exc)) from None
        except providers.TransientProviderError as exc:
            raise OpenAIRateLimitError(str(exc)) from None
        if stream:
            return _openai_stream(resp.text)
        return _OAResponse(
            choices=[_OAChoice(_OAMessage(resp.text))],
            usage=_OAUsage(resp.prompt_tokens, resp.completion_tokens),
            model=model,
        )


def _openai_stream(text: str) -> Iterator[_OAChunk]:
    for word in text.split(" "):
        yield _OAChunk(choices=[_OAStreamChoice(_OADelta(word + " "))])
    yield _OAChunk(choices=[_OAStreamChoice(_OADelta(None))])


class _OpenAIChat:
    def __init__(self) -> None:
        self.completions = _OpenAICompletions()


class OpenAI:
    """`from openai import OpenAI` stands in for this."""

    def __init__(self, api_key: str | None = None) -> None:
        self.chat = _OpenAIChat()


# --- Anthropic shape --------------------------------------------------------


@dataclass
class _AnthropicTextBlock:
    text: str
    type: str = "text"


@dataclass
class _AnthropicUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _AnthropicResponse:
    content: list[_AnthropicTextBlock]
    usage: _AnthropicUsage
    model: str


class _AnthropicStream:
    def __init__(self, text: str) -> None:
        self._text = text

    def __enter__(self) -> "_AnthropicStream":
        return self

    def __exit__(self, *exc_info) -> None:
        return None

    @property
    def text_stream(self) -> Iterator[str]:
        for word in self._text.split(" "):
            yield word + " "


class _AnthropicMessages:
    def create(
        self,
        *,
        model: str,
        max_tokens: int,  # required by the real SDK, unlike OpenAI's
        messages: list[dict],
        system: str | None = None,
        timeout: float | None = None,
    ):
        user = "\n".join(m["content"] for m in messages if m["role"] == "user")
        timeout_ms = timeout * 1000 if timeout else None
        try:
            resp = _call(system or "", user, timeout_ms)
        except providers.ProviderTimeout as exc:
            raise AnthropicAPITimeoutError(str(exc)) from None
        except providers.TransientProviderError as exc:
            raise AnthropicRateLimitError(str(exc)) from None
        return _AnthropicResponse(
            content=[_AnthropicTextBlock(resp.text)],
            usage=_AnthropicUsage(resp.prompt_tokens, resp.completion_tokens),
            model=model,
        )

    def stream(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: list[dict],
        system: str | None = None,
    ) -> _AnthropicStream:
        user = "\n".join(m["content"] for m in messages if m["role"] == "user")
        resp = _call(system or "", user, None)
        return _AnthropicStream(resp.text)


class Anthropic:
    """`import anthropic; anthropic.Anthropic()` stands in for this."""

    def __init__(self, api_key: str | None = None) -> None:
        self.messages = _AnthropicMessages()
