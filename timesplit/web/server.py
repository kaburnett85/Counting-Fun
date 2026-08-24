"""The dashboard's HTTP server.

Deliberately the standard library: Flask or FastAPI would add tens of
megabytes of resident memory and a dependency tree to serve a handful of pages
to one person on localhost.

Two things here are load-bearing:

* **Lifecycle.** The server is not running most of the time. The tray starts
  it on demand and it shuts itself down after ten quiet minutes, so the
  background process stays small.
* **Security.** Any web page you visit can issue requests to 127.0.0.1, so a
  bare localhost server would let any site read your work history. Access
  needs a per-launch token, and anything that changes data additionally needs
  a matching Origin and a token header, which a cross-site request cannot
  forge.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..logging_setup import get as get_logger

log = get_logger("web")

COOKIE_NAME = "ts_auth"


class DashboardServer:
    def __init__(self, engine, cfg, *, port: int | None = None):
        self.engine = engine
        self.cfg = cfg
        self.port = port if port is not None else cfg.dashboard.port
        #: Regenerated on every start: a token cannot outlive the process.
        self.token = secrets.token_urlsafe(32)
        self.last_request = time.time()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._watchdog: threading.Thread | None = None
        self._stopping = threading.Event()

    # ---- lifecycle -----------------------------------------------------

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}?t={self.token}"

    def start(self) -> str:
        if self._httpd is not None:
            self.last_request = time.time()
            return self.url()

        from .routes import Router

        router = Router(self.engine, self.cfg, self)
        handler = _make_handler(router, self)

        # Port 0 means "let the OS pick" (the tests use it); otherwise try a
        # few ports up from the configured one, in case something else has it.
        candidates = [0] if self.port == 0 else [self.port + o for o in range(12)]
        last_error: OSError | None = None
        for candidate in candidates:
            try:
                self._httpd = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
                break
            except OSError as exc:
                last_error = exc
                continue
        if self._httpd is None:
            raise RuntimeError(f"could not bind a dashboard port: {last_error}")
        # Read back what was actually bound -- with port 0 the OS chose it.
        self.port = int(self._httpd.server_address[1])

        self._httpd.daemon_threads = True
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="timesplit-web", daemon=True
        )
        self._thread.start()
        self.last_request = time.time()
        self._start_watchdog()
        log.info("dashboard listening on 127.0.0.1:%d", self.port)
        return self.url()

    def _start_watchdog(self) -> None:
        minutes = self.cfg.dashboard.auto_shutdown_min
        if minutes <= 0:
            return

        def watch() -> None:
            timeout = minutes * 60
            while not self._stopping.wait(15):
                if time.time() - self.last_request > timeout:
                    log.info("dashboard idle for %d minutes; shutting it down", minutes)
                    self.stop()
                    return

        self._watchdog = threading.Thread(target=watch, name="timesplit-web-idle", daemon=True)
        self._watchdog.start()

    def stop(self) -> None:
        self._stopping.set()
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:  # pragma: no cover - best effort on teardown
                pass
        self._thread = None

    def open_in_browser(self) -> str:
        url = self.start()
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception as exc:  # pragma: no cover - depends on the desktop
            log.warning("could not open a browser: %s", exc)
        return url


def _make_handler(router, server: DashboardServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TimeSplit"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        # ---- plumbing ----

        def log_message(self, fmt: str, *args) -> None:
            # Access logs would record window titles in URLs. Stay quiet.
            return

        def _cookie_token(self) -> str:
            raw = self.headers.get("Cookie") or ""
            for part in raw.split(";"):
                name, _, value = part.strip().partition("=")
                if name == COOKIE_NAME:
                    return value
            return ""

        def _origin_ok(self) -> bool:
            """A cross-site page cannot set these to our own origin."""
            expected = {
                f"http://127.0.0.1:{server.port}",
                f"http://localhost:{server.port}",
            }
            origin = self.headers.get("Origin")
            if origin:
                return origin in expected
            referer = self.headers.get("Referer")
            if referer:
                parsed = urlparse(referer)
                return f"{parsed.scheme}://{parsed.netloc}" in expected
            # No Origin and no Referer means it is not a browser form post.
            return False

        def _send(self, status: int, body: bytes, content_type: str,
                  extra_headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                # connect-src 'self' is required: without it default-src 'none'
                # blocks the page's own fetch() calls, and every correction
                # silently fails in the browser.
                "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "img-src data:; connect-src 'self'; form-action 'self'; base-uri 'none'",
            )
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _html(self, html: str, status: int = 200, headers=None) -> None:
            self._send(status, html.encode("utf-8"), "text/html; charset=utf-8", headers)

        def _json(self, payload: dict, status: int = 200) -> None:
            self._send(
                status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8"
            )

        def _deny(self) -> None:
            self._html(
                "<h1>Not authorised</h1><p>Open the dashboard from the TimeSplit "
                "tray icon.</p>",
                status=403,
            )

        # ---- routing ----

        def do_GET(self) -> None:  # noqa: N802
            server.last_request = time.time()
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)

            # The tray hands over the token in the URL once; from then on it
            # rides in a strict same-site cookie and the clean URL is shown.
            supplied = (query.get("t") or [""])[0]
            if supplied and secrets.compare_digest(supplied, server.token):
                target = parsed.path or "/"
                keep = {k: v for k, v in query.items() if k != "t"}
                if keep:
                    target += "?" + "&".join(f"{k}={v[0]}" for k, v in keep.items())
                self._send(
                    302,
                    b"",
                    "text/plain",
                    {
                        "Location": target,
                        "Set-Cookie": (
                            f"{COOKIE_NAME}={server.token}; Path=/; SameSite=Strict; HttpOnly"
                        ),
                    },
                )
                return

            if not secrets.compare_digest(self._cookie_token(), server.token):
                self._deny()
                return

            try:
                result = router.get(parsed.path, query)
            except Exception:
                log.exception("dashboard error on GET %s", parsed.path)
                self._html("<h1>Something went wrong</h1><p>See the log file.</p>", status=500)
                return

            if result is None:
                self._html("<h1>Not found</h1>", status=404)
                return
            status, body, content_type = result
            self._send(status, body, content_type)

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

        def do_POST(self) -> None:  # noqa: N802
            server.last_request = time.time()
            parsed = urlparse(self.path)

            if not secrets.compare_digest(self._cookie_token(), server.token):
                self._deny()
                return
            if not self._origin_ok():
                self._json({"ok": False, "error": "bad origin"}, status=403)
                return

            length = int(self.headers.get("Content-Length") or 0)
            if length > 1_000_000:
                self._json({"ok": False, "error": "too large"}, status=413)
                return
            raw = self.rfile.read(length) if length else b""
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()

            if content_type == "application/json":
                header_token = self.headers.get("X-TS-Token") or ""
                if not secrets.compare_digest(header_token, server.token):
                    self._json({"ok": False, "error": "missing token"}, status=403)
                    return
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._json({"ok": False, "error": "bad json"}, status=400)
                    return
            else:
                form = parse_qs(raw.decode("utf-8", "replace"))
                payload = {k: v[0] for k, v in form.items()}
                if not secrets.compare_digest(str(payload.get("token", "")), server.token):
                    self._deny()
                    return

            try:
                result = router.post(parsed.path, payload)
            except Exception:
                log.exception("dashboard error on POST %s", parsed.path)
                self._json({"ok": False, "error": "internal error"}, status=500)
                return

            if result is None:
                self._json({"ok": False, "error": "not found"}, status=404)
                return
            status, body, content_type_out = result
            if content_type_out == "redirect":
                self._send(303, b"", "text/plain", {"Location": body.decode("utf-8")})
                return
            self._send(status, body, content_type_out)

    return Handler
