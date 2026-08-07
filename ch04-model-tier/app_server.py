#!/usr/bin/env python3
"""
ch04/app_server.py: the app, in one of two shapes.

    python ch04-model-tier/app_server.py --mode inprocess --port 8800
    python ch04-model-tier/app_server.py --mode tiered --port 8800 --model-port 8801

Same endpoints, same answers, two structures:

  inprocess   the model runs inside this process. `providers.generate` is a
              function call.
  tiered      the model runs somewhere else. `providers.generate` is an HTTP
              request to model_server.py.

Both run as a real subprocess, so the harness measures them the way a client
would: from outside, over a socket. That symmetry is the point. If the
in-process build were measured by calling a Python function while the tiered
build was measured over HTTP, the comparison would be counting the harness's
own overhead as an architectural cost.

Three endpoints, and the third one is why the chapter exists:

  /health     no model involved
  /status     no model involved, serves a cached answer
  /ask        needs the model
  /crash      makes the model fail the way models fail

`/health` and `/status` are not padding. The question a tier answers is not
"is the model up" but "when the model is down, is *anything* up".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import providers, retrieval  # noqa: E402

MODE = "inprocess"
MODEL_PORT = 0

SYSTEM_TEMPLATE = """You are the support assistant for a SaaS product.
Answer only from the context below.

CONTEXT:
{context}"""

# A response the app can serve without the model at all. Its whole job is to
# be a request that has no reason to fail when the model does.
CACHED_STATUS = "All systems operational. Last incident: none in the past 30 days."


class ModelUnavailable(RuntimeError):
    pass


def call_model(system: str, user: str, request_id: str) -> dict:
    """The one line that differs between the two designs."""
    if MODE == "inprocess":
        response = providers.generate(system, user, request_id=request_id)
        return {
            "text": response.text,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "simulated_latency_ms": response.simulated_latency_ms,
        }

    payload = json.dumps({"system": system, "user": user, "request_id": request_id}).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{MODEL_PORT}/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, ConnectionError, OSError) as exc:
        # The tier is gone. This process is fine, which is the entire
        # difference the chapter is measuring.
        raise ModelUnavailable(str(exc)) from None


class AppHandler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send(200, {"ok": True, "mode": MODE})
            return
        if parsed.path == "/status":
            self._send(200, {"ok": True, "text": CACHED_STATUS})
            return
        if parsed.path == "/ask":
            params = parse_qs(parsed.query)
            question = params.get("q", ["hello"])[0]
            request_id = params.get("rid", ["req"])[0]
            started = time.perf_counter()
            hits = retrieval.search(question, request_id=request_id)
            context = retrieval.format_context(hits.documents)
            model_started = time.perf_counter()
            try:
                result = call_model(
                    SYSTEM_TEMPLATE.format(context=context), question, request_id
                )
            except ModelUnavailable as exc:
                # Degraded, not dead: the app can still say something true.
                self._send(
                    503,
                    {
                        "ok": False,
                        "degraded": True,
                        "error": f"model tier unavailable: {exc}",
                        "sources": [d.doc_id for d in hits.documents],
                    },
                )
                return
            model_call_ms = (time.perf_counter() - model_started) * 1000.0
            self._send(
                200,
                {
                    "ok": True,
                    "text": result["text"],
                    "sources": [d.doc_id for d in hits.documents],
                    "total_ms": (time.perf_counter() - started) * 1000.0,
                    # Timed around the model call alone. Differencing
                    # end-to-end latency to find the hop does not work: this
                    # app's retrieval jitter is several times larger than the
                    # boundary being measured, so the estimate swings by more
                    # than the quantity. Measured here, retrieval is not in it.
                    "model_call_ms": model_call_ms,
                    "simulated_latency_ms": result["simulated_latency_ms"],
                },
            )
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/crash":
            self._send(404, {"error": "not found"})
            return
        if MODE == "inprocess":
            # The model died. The model is this process. So this process dies,
            # and it takes /health and /status with it.
            os._exit(1)
        try:
            urllib.request.urlopen(
                urllib.request.Request(f"http://127.0.0.1:{MODEL_PORT}/crash", data=b""),
                timeout=2,
            )
        except Exception:
            pass  # the tier exiting mid-request is the expected outcome
        self._send(200, {"ok": True, "crashed": "model tier"})


def main() -> int:
    global MODE, MODEL_PORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("inprocess", "tiered"), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--model-port", type=int, default=0)
    parser.add_argument("--profile", default="instant")
    parser.add_argument("--seed", type=int, default=1337)
    args = parser.parse_args()

    MODE = args.mode
    MODEL_PORT = args.model_port
    providers.configure_mock(latency=args.profile, seed=args.seed)
    retrieval.reset_retrieval()

    ThreadingHTTPServer(("127.0.0.1", args.port), AppHandler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
