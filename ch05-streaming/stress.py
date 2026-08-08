#!/usr/bin/env python3
"""
stress.py: a response that turns unsafe partway through.

    python ch05-streaming/stress.py     # offline, no key, instant

Three experiments:

  1. **The four designs on the same eight responses.** Five of them contain
     something that must not reach a user; three are clean, including one that
     contains the allowlisted support address, because a guard that blocks that
     is broken in the other direction.

  2. **Position matters more than design.** The same violation early in a
     response and late in a response produce opposite verdicts. Any advice that
     does not mention where the violation sits is advice about one case.

  3. **The window sweep.** Holding N tokens back is the only design here with a
     dial. Sweeping it turns "streaming versus safety" from an argument into a
     number you can choose.

Timing is arithmetic at 25ms per token, applied identically everywhere, so
every figure below is exactly reproducible. Detection and leaked bytes are not
simulated: the guard is real and the bytes are counted from what each design
actually handed over.
"""

from __future__ import annotations

import os
import sys

CHAPTER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(CHAPTER))
sys.path.insert(0, CHAPTER)

from cases import CASES, tokenize  # noqa: E402
from strategies import TOKEN_MS, run  # noqa: E402

HOLD = 8


def fmt_ms(value: float | None) -> str:
    return "never" if value is None else f"{value:.0f}"


def main() -> int:
    print(f"Output guard placement. {TOKEN_MS:.0f}ms per token, 4 characters per token.")
    print(f"Window design holds {HOLD} tokens ({HOLD * 4} characters) back.\n")

    print("=" * 84)
    print("1. FOUR DESIGNS, EIGHT RESPONSES")
    print("=" * 84)

    unsafe = [c for c in CASES if c.should_flag]
    clean = [c for c in CASES if not c.should_flag]

    print(f"\n  {len(unsafe)} responses that must be stopped:\n")
    header = (
        f"  {'design':<14} {'caught':>8} {'clean stop':>11} {'leaked chars':>13}"
        f" {'median TTFT':>12}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for strategy in ("buffer", "isolated", "accumulated", "window"):
        caught = clean_stop = leaked = 0
        ttfts = []
        for case in unsafe:
            result = run(strategy, case.text, HOLD)
            caught += int(result.detected)
            clean_stop += int(result.unsafe_bytes == 0)
            leaked += result.unsafe_bytes
            if result.ttft_ms is not None:
                ttfts.append(result.ttft_ms)
        label = f"window({HOLD})" if strategy == "window" else strategy
        median = sorted(ttfts)[len(ttfts) // 2] if ttfts else None
        print(
            f"  {label:<14} {caught:>4}/{len(unsafe):<3} {clean_stop:>7}/{len(unsafe):<3}"
            f" {leaked:>13} {fmt_ms(median):>12}"
        )

    print(f"\n  {len(clean)} responses that must go through untouched:\n")
    print(f"  {'design':<14} {'delivered whole':>16} {'median TTFT':>12}")
    print("  " + "-" * 44)
    for strategy in ("buffer", "isolated", "accumulated", "window"):
        whole = 0
        ttfts = []
        for case in clean:
            result = run(strategy, case.text, HOLD)
            whole += int(result.delivered == case.text)
            if result.ttft_ms is not None:
                ttfts.append(result.ttft_ms)
        label = f"window({HOLD})" if strategy == "window" else strategy
        median = sorted(ttfts)[len(ttfts) // 2] if ttfts else None
        print(f"  {label:<14} {whole:>12}/{len(clean):<3} {fmt_ms(median):>12}")

    print("\n  'clean stop' means the client received none of the violating span.")
    print("  'caught' only means the guard noticed, which is not the same thing.")

    print("\n" + "=" * 84)
    print("2. WHERE THE VIOLATION SITS DECIDES THE ANSWER")
    print("=" * 84)
    print()
    header = (
        f"  {'case':<16} {'violation at':>13} {'span':>6} {'accumulated leak':>18}"
        f" {'window leak':>12} {'buffer TTFT':>12}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for case in unsafe:
        violation = case.violation
        if violation is None:
            print(f"  {case.name:<16} {'GUARD MISSED THIS CASE ENTIRELY':>60}")
            continue
        position = violation.start / len(case.text)
        span = violation.end - violation.start
        acc = run("accumulated", case.text)
        win = run("window", case.text, HOLD)
        buf = run("buffer", case.text)
        print(
            f"  {case.name:<16} {position:>12.0%} {span:>6}"
            f" {acc.unsafe_bytes:>12}/{span:<4} {win.unsafe_bytes:>7}/{span:<4}"
            f" {fmt_ms(buf.total_ms):>12}"
        )
    print(
        "\n  Read the accumulated column as a fraction of the span. It is never a small"
        "\n  part of the secret: by the time the pattern is recognisable, almost all of"
        "\n  it has been sent. Where it stops just short, that is the regex matching a"
        "\n  prefix (gmail.co before the final m arrives), which is luck, not a design."
    )

    print("\n" + "=" * 84)
    print("3. THE WINDOW SWEEP: buying safety with first-token latency")
    print("=" * 84)
    print()
    print(f"  {'hold':>6} {'TTFT':>8} {'caught':>8} {'clean stops':>12} {'leaked chars':>13}")
    print("  " + "-" * 52)
    for hold in (0, 2, 4, 6, 8, 12, 16, 24):
        caught = clean_stop = leaked = 0
        for case in unsafe:
            result = run("window", case.text, hold)
            caught += int(result.detected)
            clean_stop += int(result.unsafe_bytes == 0)
            leaked += result.unsafe_bytes
        ttft = hold * TOKEN_MS if hold else TOKEN_MS
        print(
            f"  {hold:>6} {ttft:>7.0f}ms {caught:>4}/{len(unsafe):<3}"
            f" {clean_stop:>8}/{len(unsafe):<3} {leaked:>13}"
        )
    longest = max(len(tokenize(c.text)) for c in unsafe)
    print(
        f"\n  Buffering the whole response is the bottom of this table taken to its"
        f"\n  limit: hold = {longest} tokens ({longest * TOKEN_MS:.0f}ms to first byte) for these"
        f" responses."
        f"\n  The dial is the design. Pick the smallest hold that stops what you care"
        f"\n  about, and pay for it in first-token latency."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
