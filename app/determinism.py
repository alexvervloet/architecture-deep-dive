"""
app/determinism.py: reproducible randomness that survives concurrency.

Every stressor in this repo runs the same workload twice and compares numbers.
That only means something if the workload is *identical* both times, and the
usual tool for that (seed a global RNG) quietly fails here, because the
stressors are concurrent. With a shared `random.Random`, the draw a request
gets depends on which thread reached the RNG first, so two runs of the same
workload produce different per-request latencies and different failures. The
comparison then measures thread scheduling as much as architecture.

So there is no shared RNG. Every random value is *derived* from a hash of
(seed, and a label naming exactly which decision this is):

    draw(seed, "provider-latency", request_id, attempt)

Same inputs, same value, forever, on any thread, in any order, on any machine.
Request 41's latency is a property of request 41, not of when it happened to
run. That is what lets `examples/02_repeatability.py` assert equality rather
than "close enough".

What this does NOT make deterministic: wall-clock time. `time.sleep(0.05)`
sleeps for at least 50ms and no more than the OS feels like. The simulated
latency is exact and reproducible; the measured latency is not, and the stress
harness reports both so the difference is always visible. See the note in
stress/harness.py.
"""

from __future__ import annotations

import hashlib
import struct

# 2**53, the largest integer a float64 represents exactly. Dividing by it keeps
# `draw` uniform in [0, 1) without floating-point clumping.
_MAX_53 = float(1 << 53)


def draw(seed: int, *labels: object) -> float:
    """A uniform value in [0, 1), derived from `seed` and `labels`.

    `labels` should name the decision, not just the request: use
    ("provider-latency", req_id) rather than (req_id,). Two different decisions
    about the same request must not draw the same number, or the retriever's
    failures and the provider's slow tail will arrive in lockstep and look like
    a correlation that does not exist.
    """
    key = "|".join(str(part) for part in (seed, *labels)).encode("utf-8")
    digest = hashlib.blake2b(key, digest_size=8).digest()
    # Take the low 53 bits so the division below is exact.
    return (struct.unpack("<Q", digest)[0] >> 11) / _MAX_53


def draws_below(seed: int, probability: float, *labels: object) -> bool:
    """True with the given `probability`, deterministically.

    The obvious way to inject a 5% error rate. Note this is a per-decision coin
    flip, not a quota: over 100 requests you get *about* five failures, not
    exactly five. When a chapter needs an exact count, use `fail_next` on the
    provider instead of a rate.
    """
    if probability <= 0.0:
        return False
    if probability >= 1.0:
        return True
    return draw(seed, *labels) < probability
