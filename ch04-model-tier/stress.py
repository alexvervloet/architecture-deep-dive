#!/usr/bin/env python3
"""
stress.py: what the hop costs, and what it buys.

    python ch04-model-tier/stress.py     # offline, no key, ~40 seconds

Three measurements, in the order that keeps the chapter honest:

  1. **The hop, priced twice.** Same workload, both designs, first against an
     instant model so the boundary is the whole cost, then against a realistic
     one so the boundary is a share of something. Splitting a service is
     *always* slower, and this chapter reports how much before it reports
     anything favourable.

  2. **The blast radius.** Kill the model the way models die, then send a
     mixed workload of requests, only some of which need a model. This is the
     measurement the hop is buying, and it is the only one that cannot be
     argued away.

  3. **The break-even for batching.** A separate tier can batch requests that
     an in-process model cannot. How much amortisation does it take to pay
     back the measured hop? This is arithmetic on a measured number, not a
     measurement, and it is labelled as such.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

CHAPTER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(CHAPTER))

from app import service  # noqa: E402

PY = sys.executable
APP_PORT = 8800
MODEL_PORT = 8801
QUESTIONS = service.QUESTIONS


def wait_until_up(port: int, path: str = "/health", timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.05)
    return False


class Deployment:
    """One or two processes, started and stopped together."""

    def __init__(self, mode: str, profile: str) -> None:
        self.mode = mode
        self.procs: list[subprocess.Popen] = []
        if mode == "tiered":
            self.procs.append(
                subprocess.Popen(
                    [PY, os.path.join(CHAPTER, "model_server.py"),
                     "--port", str(MODEL_PORT), "--profile", profile],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            )
            if not wait_until_up(MODEL_PORT):
                raise RuntimeError("model tier did not come up")
        cmd = [PY, os.path.join(CHAPTER, "app_server.py"),
               "--mode", mode, "--port", str(APP_PORT), "--profile", profile]
        if mode == "tiered":
            cmd += ["--model-port", str(MODEL_PORT)]
        self.procs.insert(0, subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        if not wait_until_up(APP_PORT):
            raise RuntimeError("app did not come up")

    def stop(self) -> None:
        for proc in self.procs:
            proc.terminate()
        for proc in self.procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def get(path: str, timeout: float = 30.0) -> tuple[int, dict, float]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{APP_PORT}{path}", timeout=timeout) as r:
            body = json.loads(r.read())
            return r.status, body, (time.perf_counter() - started) * 1000.0
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}"), (time.perf_counter() - started) * 1000.0
    except Exception as exc:
        return 0, {"error": str(exc)}, (time.perf_counter() - started) * 1000.0


def measure_hop(profile: str, count: int = 40) -> dict[str, dict]:
    results = {}
    for mode in ("inprocess", "tiered"):
        deployment = Deployment(mode, profile)
        try:
            get("/ask?q=warmup&rid=warm")  # exclude first-call import and connect costs
            latencies, model_calls, simulated = [], [], []
            for i in range(count):
                question = QUESTIONS[i % len(QUESTIONS)].replace(" ", "+").replace("?", "")
                status, body, ms = get(f"/ask?q={question}&rid=h{i:03d}")
                if status == 200:
                    latencies.append(ms)
                    model_calls.append(body.get("model_call_ms", 0.0))
                    simulated.append(body.get("simulated_latency_ms", 0.0))
            results[mode] = {
                "median_ms": statistics.median(latencies) if latencies else 0.0,
                "mean_ms": statistics.fmean(latencies) if latencies else 0.0,
                "median_model_ms": statistics.median(model_calls) if model_calls else 0.0,
                "simulated_ms": statistics.fmean(simulated) if simulated else 0.0,
                "n": len(latencies),
            }
        finally:
            deployment.stop()
    return results


def measure_blast_radius() -> dict[str, dict]:
    """Kill the model, then ask the app for things that do and do not need it."""
    results = {}
    for mode in ("inprocess", "tiered"):
        deployment = Deployment(mode, "instant")
        try:
            before = {
                "health": get("/health", timeout=3)[0],
                "status": get("/status", timeout=3)[0],
                "ask": get("/ask?q=refund&rid=b0", timeout=5)[0],
            }
            try:
                urllib.request.urlopen(
                    urllib.request.Request(f"http://127.0.0.1:{APP_PORT}/crash", data=b""),
                    timeout=3,
                )
            except Exception:
                pass  # the process exiting mid-request is the point
            time.sleep(0.5)

            counts = {"health": 0, "status": 0, "ask": 0, "ask_degraded": 0}
            trials = 10
            for i in range(trials):
                if get("/health", timeout=3)[0] == 200:
                    counts["health"] += 1
                if get("/status", timeout=3)[0] == 200:
                    counts["status"] += 1
                status, body, _ = get(f"/ask?q=refund&rid=a{i}", timeout=5)
                if status == 200:
                    counts["ask"] += 1
                elif body.get("degraded"):
                    counts["ask_degraded"] += 1
            results[mode] = {"before": before, "after": counts, "trials": trials}
        finally:
            deployment.stop()
    return results


def main() -> int:
    print("Model tier: in-process function call vs a separate process over a socket.")
    print("Both apps run as real subprocesses and are measured over HTTP, so the")
    print("comparison does not charge one design for the harness's own overhead.\n")

    print("=" * 78)
    print("1. WHAT THE HOP COSTS")
    print("=" * 78)

    instant = measure_hop("instant")
    hop_ms = instant["tiered"]["median_model_ms"] - instant["inprocess"]["median_model_ms"]
    print("\n  Against an instant model, timing the model call itself:\n")
    print(f"    {'design':<12} {'model call ms':>14} {'whole request ms':>18}")
    for mode in ("inprocess", "tiered"):
        r = instant[mode]
        print(f"    {mode:<12} {r['median_model_ms']:>14.2f} {r['median_ms']:>18.2f}")
    print(
        f"\n    The hop costs {hop_ms:.2f}ms, measured around the call alone."
        f"\n    Note the right-hand column: differencing whole-request medians gives"
        f"\n    {instant['tiered']['median_ms'] - instant['inprocess']['median_ms']:+.2f}ms,"
        f" because this app's retrieval jitter is several times"
        f"\n    larger than the boundary. That estimator swung between 0.9 and 2.7ms"
        f"\n    across runs before the measurement was moved inside the app."
    )

    # Fewer samples here purely for runtime: 40 sequential 1.3s requests per
    # design is two minutes of waiting to re-derive a constant already measured
    # above. The number this pass exists to produce is the ratio, not a
    # tighter estimate of the hop.
    slow = measure_hop("slow", count=12)
    share = hop_ms / max(1e-9, slow["tiered"]["median_ms"]) * 100
    print("\n  Against a realistic model (~1.3s mean):\n")
    print(f"    {'design':<12} {'model call ms':>14} {'whole request ms':>18}")
    for mode in ("inprocess", "tiered"):
        r = slow[mode]
        print(f"    {mode:<12} {r['median_model_ms']:>14.1f} {r['median_ms']:>18.1f}")
    print(
        f"\n    The boundary did not get more expensive; the request got bigger."
        f"\n    The same {hop_ms:.2f}ms hop is now {share:.2f}% of a"
        f" {slow['tiered']['median_ms']:.0f}ms request."
    )

    print("\n" + "=" * 78)
    print("2. WHAT THE HOP BUYS: BLAST RADIUS")
    print("=" * 78)

    blast = measure_blast_radius()
    print("\n  The model process is killed with os._exit, the way an OOM kill arrives.")
    print("  Then 10 rounds of three requests, only one of which needs a model.\n")
    print(f"    {'design':<12} {'/health':>9} {'/status':>9} {'/ask ok':>9} {'/ask 503':>10}")
    for mode in ("inprocess", "tiered"):
        after = blast[mode]["after"]
        trials = blast[mode]["trials"]
        print(
            f"    {mode:<12} {after['health']:>4}/{trials:<4} {after['status']:>4}/{trials:<4}"
            f" {after['ask']:>4}/{trials:<4} {after['ask_degraded']:>5}/{trials:<4}"
        )
    print(
        "\n    In-process, the model dying is the app dying: health checks, cached"
        "\n    responses, and everything else go with it. Tiered, the app is still"
        "\n    there and answers what it can without a model, and the requests that"
        "\n    do need one fail in milliseconds with a reason instead of a timeout."
    )

    print("\n" + "=" * 78)
    print("3. BREAK-EVEN FOR BATCHING (arithmetic on the measured hop, not a measurement)")
    print("=" * 78)
    print(
        f"\n  A separate tier can batch concurrent requests into one model call; an"
        f"\n  in-process model in a worker process cannot. Batching saves time only if"
        f"\n  it saves more than the {hop_ms:.2f}ms hop it costs. With a per-call fixed"
        f"\n  overhead F amortised across a batch of N, the tier wins when:"
        f"\n\n      F * (1 - 1/N)  >  {hop_ms:.2f}ms\n"
    )
    print(f"    {'batch N':>8} {'F needed to break even':>26}")
    for n in (2, 4, 8, 16, 32):
        needed = hop_ms / (1 - 1 / n)
        print(f"    {n:>8} {needed:>22.2f}ms")
    print(
        "\n    Read this as a design tool, not a result. It says: measure your"
        "\n    provider's fixed per-call overhead, and if it is smaller than these"
        "\n    numbers, batching will not pay for the split and you need a different"
        "\n    reason to want one. This repo cannot measure F, because the mock has no"
        "\n    real per-call overhead to amortise."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
