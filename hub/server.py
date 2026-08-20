"""The local server: five pages and four endpoints, on the standard library.

No framework on purpose. This is one user on one machine looking at files that
another thread rebuilds; Flask would add an install step and a dependency to
keep current in exchange for routing that fits in forty lines.

It binds to 127.0.0.1 and nothing else. The pages expose buttons that spend API
credits and rewrite the dataset, which is fine for a process only this machine
can reach and would not be fine on a network.
"""

import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import artifacts, components, jobs, pages

STATIC_DIR = Path(__file__).resolve().parent / "static"
HOST = "127.0.0.1"
PORT = 8756

# Derived from the nav rather than written out again: a hand-kept second list
# is how a page ends up in the menu and 404s when you click it.
ROUTES = {components.Links("server").href(page): page
          for page, _, _ in components.PAGES}


def _links():
    """Server-mode links, with the control strip built from live status."""
    links = components.Links("server")
    runner = jobs.runner()
    snapshot = runner.snapshot()
    links.control_html = components.control_strip(
        links, artifacts.status(), list(runner.jobs.values()), busy=snapshot["label"])
    return links


class Handler(BaseHTTPRequestHandler):
    server_version = "FootballHub"

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):
        """Silence the per-request log — the job log is the interesting one."""

    def _send(self, body, content_type="text/html; charset=utf-8",
              status=HTTPStatus.OK, cache=False):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Pages reflect files that a job may have just rewritten, so they must
        # never come from the browser cache. Assets are fine to keep.
        self.send_header("Cache-Control", "public, max-age=3600" if cache
                         else "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, status=HTTPStatus.OK):
        self._send(json.dumps(payload), "application/json; charset=utf-8", status)

    # -- routing ----------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ROUTES:
            try:
                self._send(pages.render(ROUTES[path], _links()))
            except Exception as exc:                        # noqa: BLE001
                # A broken artifact should explain itself in the browser rather
                # than only in the terminal the user is not looking at.
                self._send(_error_page(exc), status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if path.startswith("/assets/"):
            return self._asset(path[len("/assets/"):])

        if path == "/api/status":
            since = int((parse_qs(parsed.query).get("since") or ["0"])[0])
            snapshot = jobs.runner().snapshot(since)
            snapshot["artifacts"] = artifacts.status()
            return self._json(snapshot)

        if path == "/favicon.ico":
            return self._send(b"", "image/x-icon")

        self._send("<h1>404</h1>", status=HTTPStatus.NOT_FOUND)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/run/"):
            return self._json({"ok": False, "message": "unknown endpoint"},
                              HTTPStatus.NOT_FOUND)
        key = path[len("/api/run/"):]
        runner = jobs.runner()
        if key not in runner.jobs:
            return self._json({"ok": False, "message": f"Unknown job {key!r}"},
                              HTTPStatus.NOT_FOUND)
        ok, message = runner.start(key)
        # 409 means "busy", which is the only way a known job refuses to start.
        return self._json({"ok": ok, "message": message},
                          HTTPStatus.OK if ok else HTTPStatus.CONFLICT)

    def _asset(self, name):
        target = (STATIC_DIR / name).resolve()
        if not target.is_file() or STATIC_DIR.resolve() not in target.parents:
            return self._send("not found", "text/plain", HTTPStatus.NOT_FOUND)
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(target.read_bytes(), kind, cache=True)


def _error_page(exc):
    return f"""<!doctype html><meta charset="utf-8">
<title>Error — Football Hub</title>
<body style="font:15px/1.6 system-ui;max-width:60ch;margin:60px auto;padding:0 20px">
<h1 style="font-size:20px">That page could not be built</h1>
<p><code>{type(exc).__name__}: {components.e(exc)}</code></p>
<p>The data underneath it is probably half-written or missing. Go back and
rebuild it, or check the terminal for the full traceback.</p>
<p><a href="/">Back to the card</a></p>"""


def serve(host=HOST, port=PORT, open_browser=True):
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"Football Hub on {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return httpd
