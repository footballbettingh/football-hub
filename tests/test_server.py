"""The local server, spoken to over a real socket.

It binds to 127.0.0.1, which keeps other machines out and not other websites:
a page anywhere can point a name of its own at 127.0.0.1 and read this server
as its own origin. So what it answers to, and what it tells the browser, are
tested the way a browser would meet them.
"""

import http.client
import threading
from http.server import ThreadingHTTPServer

import pytest

from hub import server


@pytest.fixture(scope="module")
def port():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _get(port, path, host):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.putrequest("GET", path, skip_host=True)
    connection.putheader("Host", host)
    connection.endheaders()
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response, body


@pytest.mark.parametrize("host", ["127.0.0.1:{port}", "localhost:{port}", "LOCALHOST:{port}"])
def test_it_answers_to_its_own_names(port, host):
    response, _ = _get(port, "/assets/style.css", host.format(port=port))
    assert response.status == 200


@pytest.mark.parametrize("host", ["attacker.example:{port}", "127.0.0.1.attacker.example",
                                  "localhost:1", ""])
def test_a_page_reached_under_another_name_is_refused(port, host):
    """DNS rebinding: the request reaches 127.0.0.1 but names the attacker's
    host. Refused before any page is built, so nothing from data/ is sent."""
    response, body = _get(port, "/", host.format(port=port))
    assert response.status == 421
    assert b"127.0.0.1" in body


def test_every_response_carries_the_security_headers(port):
    for path in ("/assets/style.css", "/no-such-page"):
        response, _ = _get(port, path, f"127.0.0.1:{port}")
        assert response.getheader("X-Content-Type-Options") == "nosniff"
        assert response.getheader("X-Frame-Options") == "DENY"
        assert "frame-ancestors 'none'" in response.getheader("Content-Security-Policy")


def test_the_static_folder_cannot_be_walked_out_of(port):
    response, _ = _get(port, "/assets/../server.py", f"127.0.0.1:{port}")
    assert response.status == 404
