"""Fixture whose start-up fails until a dependency marker exists."""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MARKER = Path("state") / "dependency.ready"


class Handler(BaseHTTPRequestHandler):
    """Minimal JSON health endpoint."""

    def do_GET(self) -> None:  # noqa: N802 - http.server API naming
        if self.path == "/health":
            body = json.dumps({"status": "ok"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        print(f"request: {format % args}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="flaky-dependency-app fixture")
    parser.add_argument("--port", type=int, default=8124)
    args = parser.parse_args()
    if not MARKER.exists():
        print("connection refused: postgres at localhost:5432", file=sys.stderr, flush=True)
        raise SystemExit(1)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"listening on http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
