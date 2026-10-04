"""Tiny stdlib HTTP dashboard.

Serves one HTML page and a JSON endpoint. The page polls ``/api/state``
and renders the profit counter, equity curve, leaderboard, signal scanner,
open positions and recent trades. No external JavaScript.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

HTML_PATH = Path(__file__).with_name("index.html")


class DashboardServer:
    def __init__(self, state_provider: Callable[[], dict], host: str = "0.0.0.0", port: int = 8080):
        self.state_provider = state_provider
        self.host, self.port = host, port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _handler(self):
        provider = self.state_provider
        html = HTML_PATH.read_bytes()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/api/state"):
                    try:
                        body = json.dumps(provider()).encode()
                    except Exception as exc:  # never crash the server
                        body = json.dumps({"error": str(exc)}).encode()
                    self._send(200, body, "application/json")
                elif self.path in ("/", "/index.html"):
                    self._send(200, html, "text/html; charset=utf-8")
                else:
                    self._send(404, b"not found", "text/plain")

        return Handler

    def start(self) -> "DashboardServer":
        self._httpd = ThreadingHTTPServer((self.host, self.port), self._handler())
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="dashboard")
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()


def file_state_provider(path: str | Path) -> Callable[[], dict]:
    path = Path(path)

    def provider() -> dict:
        if not path.exists():
            return {"error": f"{path} not found; is the engine running?"}
        with open(path) as fh:
            return json.load(fh)

    return provider


def start_dashboard(state_provider: Callable[[], dict], port: int = 8080, host: str = "0.0.0.0") -> DashboardServer:
    return DashboardServer(state_provider, host=host, port=port).start()
