"""Local HTTP server: static dashboard files + /api/summary (spec §4.6).

Requests never trigger parsing; a background thread calls `pipeline.refresh`
every `cfg.refresh_seconds`. Both the API handler and the static file
handler take `UsageServer.lock` before touching the store/filesystem so a
background refresh can never race a read.
"""
import json
import logging
import mimetypes
import posixpath
import re
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .config import Config
from .cost import Runner, default_runner
from .office import Office
from .pipeline import refresh
from .store import Store
from .summary import board_stats, build_summary

log = logging.getLogger(__name__)

PERIOD_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


def _content_type(path) -> str:
    ext = path.suffix.lower()
    if ext in _CONTENT_TYPES:
        return _CONTENT_TYPES[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


class _Handler(BaseHTTPRequestHandler):
    server_version = "UsageDashboard/1.0"

    def __init__(self, *args, app: UsageServer, **kwargs):
        self.app = app
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):  # quiet: tests hit this a lot
        pass

    def _write_json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _resolve_static(self, url_path: str):
        """Map a request path to a file under cfg.dashboard_dir, or None if
        the request escapes that directory (path traversal) or otherwise
        does not resolve to a file on disk."""
        path = unquote(url_path.split("?", 1)[0])
        if path in ("", "/"):
            path = "/index.html"
        norm = posixpath.normpath(path).lstrip("/")
        base = self.app.cfg.dashboard_dir.resolve()
        candidate = (base / norm).resolve()
        if not candidate.is_relative_to(base):
            return None
        if not candidate.is_file():
            return None
        return candidate

    def _host_is_local(self) -> bool:
        """Only this machine's own names reach the dashboard. A page on any
        other host name that rebinds its DNS to 127.0.0.1 (DNS rebinding)
        must not read session titles and messages through the browser."""
        port = self.app.port
        return self.headers.get("Host", "") in ("127.0.0.1:%d" % port, "localhost:%d" % port)

    def do_GET(self):  # noqa: N802 (stdlib method name)
        try:
            if not self._host_is_local():
                self._write_json(403, {"error": "the dashboard answers only to 127.0.0.1 and localhost"})
                return
            url = urlparse(self.path)
            if url.path == "/api/office":
                payload = self.app.office.payload()
                # The Office keeps itself database-free; the board is joined on here.
                with self.app.lock:
                    payload["board"] = board_stats(self.app.store, date.today().isoformat(),
                                                   self.app.store.meta_get("cost_available") == "1")
                self._write_json(200, payload)
                return
            if url.path == "/api/summary":
                query = parse_qs(url.query)
                # `period` is a month or a day (spec Sec 11); `month` is the older name.
                period = (query.get("period") or query.get("month") or [None])[0]
                if period is not None and not PERIOD_RE.match(period):
                    self._write_json(400, {"error": "period must be YYYY-MM or YYYY-MM-DD, got %r" % period})
                    return
                with self.app.lock:
                    payload = build_summary(self.app.store, self.app.cfg, period, date.today(),
                                            self.app.last_warnings)
                self._write_json(200, payload)
                return
            with self.app.lock:
                target = self._resolve_static(url.path)
                if target is None:
                    self._write_json(404, {"error": "not found: %s" % url.path})
                    return
                data = target.read_bytes()
            self._write_bytes(200, data, _content_type(target))
        except Exception as exc:  # noqa: BLE001 -- must never crash the server thread
            log.exception("request failed: %s", self.path)
            try:
                self._write_json(500, {"error": str(exc)})
            except Exception:
                pass


class UsageServer:
    def __init__(self, cfg: Config, store: Store, runner: Runner = default_runner,
                port: int | None = None) -> None:
        self.cfg = cfg
        self.store = store
        self.runner = runner
        self.lock = threading.Lock()
        self.office = Office(cfg.state_dir, cfg.claude_dir, cfg.office_working_seconds, cfg.office_gone_after_seconds)
        self.last_warnings = []
        self._stop = threading.Event()
        self._refresh_thread: threading.Thread | None = None
        self._serve_thread: threading.Thread | None = None
        bind_port = cfg.port if port is None else port

        def handler(*args, **kwargs):
            return _Handler(*args, app=self, **kwargs)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", bind_port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]

    def refresh_once(self):
        with self.lock:
            self.last_warnings = refresh(self.store, self.cfg, self.runner)
        return self.last_warnings

    def _refresh_guarded(self) -> None:
        try:
            self.refresh_once()
        except Exception:  # noqa: BLE001 -- background loop must survive a bad refresh
            log.exception("background refresh failed; will retry in %ds", self.cfg.refresh_seconds)

    def _refresh_loop(self) -> None:
        while not self._stop.wait(self.cfg.refresh_seconds):
            self._refresh_guarded()

    def start(self) -> None:
        """Runs the first refresh synchronously (so the first page load has
        data), then starts the background refresh loop and the HTTP server,
        both as daemon threads. Non-blocking."""
        self._stop.clear()
        self.refresh_once()
        self._refresh_thread = threading.Thread(target=self._refresh_loop, name="usage-refresh", daemon=True)
        self._serve_thread = threading.Thread(target=self.httpd.serve_forever, name="usage-http", daemon=True)
        self._refresh_thread.start()
        self._serve_thread.start()

    def serve_forever(self) -> None:
        """Blocking variant used by the CLI: starts everything, then blocks
        the calling thread until `stop()` is called (e.g. from a signal
        handler) or Ctrl-C."""
        self.start()
        try:
            self._stop.wait()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._refresh_thread is not None:
            self._refresh_thread.join(timeout=5)
        if self._serve_thread is not None:
            self._serve_thread.join(timeout=5)
