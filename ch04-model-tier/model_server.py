#!/usr/bin/env python3
"""
ch04/model_server.py: the model, as its own process behind a socket.

    python ch04-model-tier/model_server.py --port 8801

This is the whole of the "separate tier" design: the same `providers.generate`
the in-process build calls directly, wrapped in an HTTP server. Everything
else in the chapter follows from that one boundary.

The hop is real. Real socket, real JSON encode and decode, real HTTP parsing,
real kernel scheduling between two processes. The *model* latency on both
sides is the same deterministic mock, so when the two designs are compared,
the difference is the boundary and nothing else. Simulating the hop instead
would have made the chapter's central number a number I chose.

`/crash` exists because the most interesting property of a tier is what
happens when it dies. It calls `os._exit`, not `sys.exit`: no cleanup, no
exception, no chance for a handler to make it look tidier than a real
out-of-memory kill.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import providers  # noqa: E402


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass  # quiet: the harness is the only client

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/crash":
            # A model tier dying the way model tiers actually die.
            os._exit(1)
        if self.path != "/generate":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length) or b"{}")
        try:
            response = providers.generate(
                request["system"],
                request["user"],
                request_id=request.get("request_id", "req"),
            )
        except providers.TransientProviderError as exc:
            self._send(503, {"error": str(exc)})
            return
        self._send(
            200,
            {
                "text": response.text,
                "model": response.model,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "simulated_latency_ms": response.simulated_latency_ms,
            },
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--profile", default="instant")
    parser.add_argument("--seed", type=int, default=1337)
    args = parser.parse_args()

    providers.configure_mock(latency=args.profile, seed=args.seed)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), ModelHandler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
