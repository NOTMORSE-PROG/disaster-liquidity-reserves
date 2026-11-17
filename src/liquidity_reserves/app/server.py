"""Local HTTP server for the reserve app (standard library only).

python -m liquidity_reserves.app [--host 127.0.0.1] [--port 8765] [--open]

The server binds to the loopback interface by default. It serves the static
page and a small JSON API; all numbers come from liquidity_reserves.service.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import APPLICANT, APPLICANT_ROLE, PROJECT_TITLE, __version__
from ..examples import example_catalogue
from ..portfolio import InputError
from ..service import (
    calculate,
    cost_defaults,
    default_inputs,
    evidence,
    export,
    financing,
    qaoa_run,
    quantum_check,
    range_evidence,
    recorded_validation,
    surrogate,
    sweep,
)

STATIC = Path(__file__).resolve().parent / "static"
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}
MAX_BODY_BYTES = 256 * 1024
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
}


class Handler(BaseHTTPRequestHandler):
    server_version = f"LiquidityReserves/{__version__}"
    quiet = False

    def log_message(self, format, *args):  # noqa: A002 - signature from the base class
        if not self.quiet:
            sys.stderr.write(f"{self.address_string()} {format % args}\n")

    # -- responses ---------------------------------------------------------

    def _send(self, status, body: bytes, content_type: str, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in {**SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, data):
        body = json.dumps(data, allow_nan=False).encode("utf-8")
        self._send(status, body, "application/json")

    def _error(self, status, messages):
        if isinstance(messages, str):
            messages = [messages]
        self._json(status, {"errors": messages})

    def _body(self):
        if "json" not in (self.headers.get("Content-Type") or ""):
            raise InputError("send the request body as JSON")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise InputError("invalid Content-Length") from error
        if length <= 0:
            raise InputError("request body is empty")
        if length > MAX_BODY_BYTES:
            raise InputError("request body is too large")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InputError("request body is not valid JSON") from error

    def _guarded(self, action):
        try:
            action()
        except InputError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, error.messages)
        except Exception:  # pragma: no cover - reported to the analyst, logged for the maintainer
            traceback.print_exc()
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "The calculation failed unexpectedly. The server log has the details.",
            )

    # -- routes --------------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        routes = {
            "/api/health": lambda: self._json(200, {"status": "ok", "version": __version__}),
            "/api/examples": lambda: self._json(
                200,
                {
                    "examples": example_catalogue(),
                    "project": PROJECT_TITLE,
                    "applicant": f"{APPLICANT} ({APPLICANT_ROLE})",
                    "version": __version__,
                },
            ),
            "/api/defaults": lambda: self._json(
                200,
                default_inputs(
                    query.get("example", ["public-two-bond"])[0],
                    _int(query.get("countries", ["5"])[0]),
                ),
            ),
            "/api/validation": lambda: self._json(200, recorded_validation()),
            "/api/evidence": lambda: self._json(200, evidence()),
            "/api/range-evidence": lambda: self._json(200, range_evidence()),
            "/api/cost-defaults": lambda: self._json(200, cost_defaults()),
        }
        if url.path in routes:
            self._guarded(routes[url.path])
            return
        if url.path == "/favicon.ico":
            self._static("/static/icon.svg")
            return
        if url.path == "/report":
            self._static("/static/report.html")
            return
        self._static(url.path)

    def do_POST(self):
        url = urlparse(self.path)
        actions = {
            "/api/calculate": calculate,
            "/api/sweep": sweep,
            "/api/quantum": quantum_check,
            "/api/financing": financing,
            "/api/qaoa": qaoa_run,
            "/api/surrogate": surrogate,
        }
        if url.path in actions:
            action = actions[url.path]
            self._guarded(lambda: self._json(200, action(self._body())))
            return
        if url.path == "/api/export":
            fmt = parse_qs(url.query).get("format", ["json"])[0]

            def send():
                name, content_type, body = export(self._body(), fmt)
                self._send(
                    200,
                    body,
                    content_type,
                    {"Content-Disposition": f'attachment; filename="{name}"'},
                )

            self._guarded(send)
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def _static(self, path: str):
        name = "index.html" if path in ("/", "/index.html") else path.removeprefix("/static/")
        if path not in ("/", "/index.html") and not path.startswith("/static/"):
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        target = (STATIC / name).resolve()
        if target.parent != STATIC or target.suffix not in STATIC_TYPES or not target.is_file():
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        self._send(200, target.read_bytes(), STATIC_TYPES[target.suffix])


def _int(text):
    try:
        return int(text)
    except ValueError as error:
        raise InputError("countries must be a whole number") from error


def make_server(host="127.0.0.1", port=8765, quiet=False):
    handler = type("QuietHandler" if quiet else "Handler", (Handler,), {"quiet": quiet})
    return ThreadingHTTPServer((host, port), handler)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local reserve sizing app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="open the app in a browser")
    args = parser.parse_args(argv)
    server = make_server(args.host, args.port)
    url = f"http://{args.host}:{server.server_address[1]}/"
    print(f"Reserve app running at {url} (Ctrl+C to stop)", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
