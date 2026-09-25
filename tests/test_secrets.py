"""Credentials never reach a log.

The Odds API takes its key as a query parameter and Telegram takes its token as
part of the path, and requests quotes the URL in the message of every error it
raises. Printed as it came, that message put the key into the run log in plain
text. The tests here read the whole traceback rather than the message alone,
because an error raised while handling another prints the first one too.
"""

import traceback

import pytest
import requests

import fb
from hub import notify
from valuebets import config
from valuebets.sources import odds_api

KEY = "0dd5k3y5ecret0123456789abcdef"
TOKEN = "123456789:AAF-t0ken_secret-value"


def _printed(exc):
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


# -- the helper --------------------------------------------------------------

def test_a_configured_credential_is_replaced_wherever_it_appears(monkeypatch):
    monkeypatch.setattr(config, "ODDS_API_KEY", KEY)
    assert config.redact(f"failed: {KEY} and again {KEY}") == "failed: *** and again ***"


def test_a_credential_config_does_not_hold_is_caught_by_its_shape():
    text = (f"url: /v4/sports/soccer_epl/odds?regions=eu&apiKey={KEY}&markets=h2h "
            f"and https://api.telegram.org/bot{TOKEN}/sendMessage")
    out = config.redact(text)
    assert KEY not in out and TOKEN not in out
    assert "apiKey=***&markets=h2h" in out and "/bot***/sendMessage" in out


def test_a_key_the_caller_holds_is_replaced_too():
    assert config.redact(f"x {KEY} y", KEY) == "x *** y"


def test_nothing_configured_changes_nothing(monkeypatch):
    """An empty secret must not match everywhere: "".replace inserts at every
    position, which would turn any message into a row of asterisks."""
    for name in config.SECRET_NAMES:
        monkeypatch.setattr(config, name, "")
    assert config.redact("Odds API unreachable") == "Odds API unreachable"


def test_a_scrubbed_error_is_still_the_error_it_was():
    """So `except RequestException` upstream still catches it."""
    clean = config.scrubbed(requests.ConnectionError(f"url ?apiKey={KEY}"))
    assert isinstance(clean, requests.ConnectionError)
    assert KEY not in str(clean)


# -- where the key actually travels -------------------------------------------

class _Session:
    """A requests.Session with the network taken out."""

    def __init__(self, respond):
        self.headers = {}
        self.respond = respond

    def get(self, url, params=None, timeout=None):
        return self.respond(url, params)


def _response(status, url):
    resp = requests.Response()
    resp.status_code, resp.url, resp.reason = status, url, "Server Error"
    resp._content = b"{}"
    return resp


def test_an_unreachable_odds_api_does_not_print_the_key():
    def refuse(url, params):
        raise requests.ConnectionError(
            f"HTTPSConnectionPool(host='api.the-odds-api.com', port=443): Max retries "
            f"exceeded with url: /v4/sports?apiKey={params['apiKey']}")

    client = odds_api.Client(key=KEY, session=_Session(refuse))
    with pytest.raises(requests.ConnectionError) as caught:
        client.get("/sports")
    assert KEY not in _printed(caught.value)


def test_an_odds_api_error_status_does_not_print_the_key():
    """raise_for_status names the full URL: "500 Server Error ... for url"."""
    client = odds_api.Client(key=KEY, session=_Session(lambda url, params: _response(
        500, f"{url}?regions=eu&apiKey={params['apiKey']}")))
    with pytest.raises(requests.HTTPError) as caught:
        client.get("/sports/soccer_epl/odds")
    assert "500" in str(caught.value)
    assert KEY not in _printed(caught.value)


def test_an_unreachable_telegram_does_not_print_the_token(monkeypatch):
    def refuse(url, **kwargs):
        raise requests.ConnectionError(
            f"Max retries exceeded with url: {url.split('api.telegram.org')[1]}")

    monkeypatch.setattr(notify.requests, "post", refuse)
    with pytest.raises(notify.NotifyError) as caught:
        notify._api("sendMessage", token=TOKEN)
    assert "unreachable" in str(caught.value)
    assert TOKEN not in _printed(caught.value)


def test_a_failed_step_does_not_print_the_key(capsys):
    """The lines every failure of an unattended run is printed through — the
    one-line summary, and for a bug the whole traceback as well."""
    def outage():
        raise requests.ConnectionError(f"url: /v4/sports?apiKey={KEY}")

    def bug():
        raise RuntimeError(f"something upstream quoted ?apiKey={KEY}")

    assert fb._step("Fetching prices", outage, skipped=[]) is False
    assert fb._step("Fetching prices", bug, broken=[]) is False
    out = capsys.readouterr().out
    assert "ConnectionError" in out and "RuntimeError" in out and "Traceback" in out
    assert KEY not in out
