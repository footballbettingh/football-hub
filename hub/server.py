"""The local server: seven pages and their assets, on the standard library.

No framework on purpose. It reads the same artifacts the static export reads
and renders the same pages from the same builders, so a page cannot look right
here and be broken on Pages.

It serves GET only. Nothing here rebuilds anything: the jobs that write
artifacts are CLI commands, listed in the README, run from a terminal where
their output and their exit code are in front of you. It binds to 127.0.0.1
because it is a way to look at local files, not a service.
"""

import gzip
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import components, pages

STATIC_DIR = Path(__file__).resolve().parent / "static"
HOST = "127.0.0.1"
PORT = 8756
WOFF2 = "font/woff2"

# Derived from the nav rather than written out again: a hand-kept second list
# is how a page ends up in the menu and 404s when you click it.
ROUTES = {components.Links("server").href(page): page
          for page, _, _ in components.PAGES}


class Handler(BaseHTTPRequestHandler):
    server_version = "FootballHub"

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):
        """Silence the per-request log. Nothing here is worth a line."""

    def _send(self, body, content_type="text/html; charset=utf-8",
              status=HTTPStatus.OK, cache=False):
        if isinstance(body, str):
            body = body.encode("utf-8")

        # GitHub Pages compresses what it serves, so a local page that does not
        # is a page whose weight you cannot read off this server. The card is
        # mostly one long JSON array and gives up about nine tenths of itself.
        # Below a kilobyte the header costs more than the saving, and woff2 and
        # png arrive compressed already — running them through gzip spends CPU
        # to make them very slightly larger.
        compressible = content_type.startswith(
            ("text/", "application/json", "application/javascript",
             "application/xml", "image/svg+xml"))
        gzipped = (compressible and len(body) > 1024
                   and "gzip" in self.headers.get("Accept-Encoding", ""))
        if gzipped:
            body = gzip.compress(body, compresslevel=6)

        self.send_response(status)
        if gzipped:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Pages reflect files that a job may have just rewritten, so they must
        # never come from the browser cache. Assets are fine to keep.
        self.send_header("Cache-Control", "public, max-age=3600" if cache
                         else "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # -- routing ----------------------------------------------------------

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ROUTES:
            try:
                self._send(pages.render(ROUTES[path], components.Links("server")))
            except Exception as exc:                        # noqa: BLE001
                # A broken artifact should explain itself in the browser rather
                # than only in the terminal the user is not looking at.
                self._send(_error_page(exc), status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if path.startswith("/assets/"):
            return self._asset(path[len("/assets/"):])

        if path == "/favicon.ico":
            return self._send(b"", "image/x-icon")

        self._send("<h1>404</h1>", status=HTTPStatus.NOT_FOUND)

    def do_HEAD(self):
        self.do_GET()

    def _asset(self, name):
        target = (STATIC_DIR / name).resolve()
        if not target.is_file() or STATIC_DIR.resolve() not in target.parents:
            return self._send("not found", "text/plain", HTTPStatus.NOT_FOUND)
        # mimetypes does not know woff2 on every Python, and the wrong type
        # makes the browser refuse the font without saying why.
        kind = (WOFF2 if target.suffix == ".woff2"
                else mimetypes.guess_type(target.name)[0]
                or "application/octet-stream")
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
