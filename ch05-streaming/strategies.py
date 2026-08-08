"""
ch05/strategies.py: four places to put the output guard.

All four run the same guard over the same generated text. They differ only in
*when* the guard is allowed to look and *when* bytes are handed to the client,
which is the entire architectural question.

    buffer       generate everything, guard it, then send
    isolated     send each token immediately, guard that token on its own
    accumulated  send each token immediately, guard everything so far
    window(N)    keep N tokens back, guard everything so far, release the rest

Timing is arithmetic, not wall clock: `TOKEN_MS` per token, stated up front and
applied identically to every design. That makes the millisecond figures exactly
reproducible, which the wall-clock chapters cannot be. The parts that would be
dishonest to simulate are not simulated: the guard is real, the text is real,
and the leaked-byte counts are counted from what each design actually handed
over.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cases import Violation, scan, tokenize

# One token's worth of generation time. A fast hosted model streams somewhere
# around 20 to 50 tokens per second per stream; 25ms is inside that range and
# the comparison does not depend on the exact value.
TOKEN_MS = 25.0


@dataclass
class Delivery:
    """What the client actually received, and when."""

    strategy: str
    case: str
    delivered: str = ""
    ttft_ms: float | None = None  # time to first byte the client could render
    total_ms: float = 0.0
    detected: bool = False
    blocked_before_any_leak: bool = False
    unsafe_bytes: int = 0  # bytes of the violation itself that reached the client
    events: list[str] = field(default_factory=list)

    def leaked_fraction(self, violation: Violation | None) -> float:
        if violation is None:
            return 0.0
        span = violation.end - violation.start
        return self.unsafe_bytes / span if span else 0.0


def _count_unsafe(delivered: str, violation: Violation | None) -> int:
    """How many characters of the violating span the client already has.

    Measured against the *span*, not the whole response. Delivering 200 clean
    characters before a leak is not a leak; delivering the first 14 characters
    of somebody's email address is, and half an address is still enough to
    matter once it is on a screen or in a log.
    """
    if violation is None:
        return 0
    overlap_end = min(len(delivered), violation.end)
    return max(0, overlap_end - violation.start)


def run_buffer(text: str) -> Delivery:
    """Generate the whole response, guard it, then send. The safe one."""
    tokens = tokenize(text)
    total_ms = len(tokens) * TOKEN_MS
    violation = scan(text)
    result = Delivery("buffer", "", total_ms=total_ms)
    if violation is not None:
        result.detected = True
        result.blocked_before_any_leak = True
        result.delivered = ""
        result.ttft_ms = None  # nothing was ever sent
        result.events.append(f"blocked before sending ({violation.kind})")
        return result
    result.delivered = text
    # The first byte and the last byte arrive together. That is the cost.
    result.ttft_ms = total_ms
    return result


def run_isolated(text: str) -> Delivery:
    """Send each token straight through, guard that token by itself.

    Nobody defends this once it is written down, and it gets written anyway,
    because "validate the chunk in the chunk handler" is the obvious shape when
    you are inside a streaming callback. A four-character token cannot contain
    an email address, so the guard is looking for a pattern it structurally
    cannot see.
    """
    tokens = tokenize(text)
    violation = scan(text)
    result = Delivery("isolated", "")
    for index, token in enumerate(tokens):
        if scan(token) is not None:
            result.detected = True
            result.events.append(f"stopped at token {index}")
            break
        result.delivered += token
        if result.ttft_ms is None:
            result.ttft_ms = (index + 1) * TOKEN_MS
        result.total_ms = (index + 1) * TOKEN_MS
    result.unsafe_bytes = _count_unsafe(result.delivered, violation)
    return result


def run_accumulated(text: str) -> Delivery:
    """Send each token straight through, guard everything generated so far.

    The honest streaming implementation. Detection works, because the guard
    eventually sees the whole pattern. It just sees it one token *after* the
    pattern completed, and by then every byte of it has been sent.
    """
    tokens = tokenize(text)
    violation = scan(text)
    result = Delivery("accumulated", "")
    seen = ""
    for index, token in enumerate(tokens):
        seen += token
        result.total_ms = (index + 1) * TOKEN_MS
        if scan(seen) is not None:
            result.detected = True
            # The client already has everything up to and including this token:
            # the guard fires after the send, because the send is what made the
            # pattern complete.
            result.delivered = seen
            result.events.append(f"detected at token {index}, after delivering it")
            break
        result.delivered = seen
        if result.ttft_ms is None:
            result.ttft_ms = TOKEN_MS
    if result.ttft_ms is None:
        result.ttft_ms = TOKEN_MS
    result.unsafe_bytes = _count_unsafe(result.delivered, violation)
    return result


def run_window(text: str, hold_tokens: int) -> Delivery:
    """Keep `hold_tokens` back, guard everything so far, release the remainder.

    The production compromise, and the only one with a dial on it. The client
    starts seeing output after `hold_tokens` tokens instead of after the whole
    response, and the guard gets that many tokens of lookahead before anything
    it has not vetted goes out.

    The dial is not free in either direction, which is why the chapter sweeps
    it rather than picking a value.
    """
    tokens = tokenize(text)
    violation = scan(text)
    result = Delivery(f"window({hold_tokens})", "")
    generated = ""
    released = ""
    for index, token in enumerate(tokens):
        generated += token
        result.total_ms = (index + 1) * TOKEN_MS
        found = scan(generated)
        if found is not None:
            result.detected = True
            result.delivered = released
            result.blocked_before_any_leak = _count_unsafe(released, violation) == 0
            result.events.append(
                f"detected at token {index}; {len(generated) - len(released)}"
                f" characters were still held back"
            )
            break
        # Nothing wrong in what has been generated, so everything except the
        # trailing `hold_tokens` is safe to release.
        safe_upto = max(0, len(tokens[: index + 1]) - hold_tokens)
        release_to = len("".join(tokens[:safe_upto]))
        if release_to > len(released):
            released = generated[:release_to]
            if result.ttft_ms is None:
                result.ttft_ms = (index + 1) * TOKEN_MS
        result.delivered = released
    else:
        # Clean response: flush whatever is still held.
        result.delivered = generated
        if result.ttft_ms is None:
            result.ttft_ms = min(len(tokens), hold_tokens + 1) * TOKEN_MS
    result.unsafe_bytes = _count_unsafe(result.delivered, violation)
    return result


STRATEGIES = ("buffer", "isolated", "accumulated", "window")


def run(strategy: str, text: str, hold_tokens: int = 8) -> Delivery:
    if strategy == "buffer":
        return run_buffer(text)
    if strategy == "isolated":
        return run_isolated(text)
    if strategy == "accumulated":
        return run_accumulated(text)
    if strategy == "window":
        return run_window(text, hold_tokens)
    raise ValueError(f"unknown strategy {strategy!r}")
