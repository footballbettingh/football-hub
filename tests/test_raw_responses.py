"""The raw Odds API responses, kept small and never lost.

Each fetch leaves its whole response on disk: nothing reads it, but it is the
per-bookmaker detail the parsed price files throw away. Plain, they were the
one thing in `data/` that grew without bound; gzipped they are a twentieth of
the size, and the plain ones already there are compressed on the way past.
"""

import gzip
import json
from datetime import datetime, timezone

import pytest

from valuebets import config
from valuebets.sources import odds_api

EVENTS = [{"id": "e1", "home_team": "Arsenal", "away_team": "Chelsea",
           "commence_time": "2026-09-27T14:00:00Z", "bookmakers": []}]
WHEN = datetime(2026, 9, 26, 9, 30, tzinfo=timezone.utc)


@pytest.fixture
def raw(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    return tmp_path


def _unpacked(path):
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))


def test_a_response_is_kept_gzipped(raw):
    odds_api.keep_response("soccer_epl", EVENTS, WHEN)
    kept = raw / "odds_soccer_epl_20260926T093000Z.json.gz"
    assert _unpacked(kept) == EVENTS
    assert not list(raw.glob("*.json"))


def test_the_plain_ones_already_there_are_compressed_not_lost(raw):
    old = raw / "odds_soccer_epl_20260812T153746Z.json"
    old.write_text(json.dumps([{"id": "old"}]), encoding="utf-8")
    odds_api.keep_response("soccer_epl", EVENTS, WHEN)
    assert not old.exists()
    assert _unpacked(raw / "odds_soccer_epl_20260812T153746Z.json.gz") == [{"id": "old"}]


def test_another_league_whose_key_starts_the_same_is_left_alone(raw):
    """soccer_germany_bundesliga is the start of soccer_germany_bundesliga2 —
    a glob alone would take the other league's files with it."""
    other = raw / "odds_soccer_germany_bundesliga2_20260812T153746Z.json"
    other.write_text("[]", encoding="utf-8")
    unrelated = raw / "fduk_E0_2526.csv"
    unrelated.write_text("Div,Date\n", encoding="utf-8")
    odds_api.keep_response("soccer_germany_bundesliga", EVENTS, WHEN)
    assert other.exists() and unrelated.exists()


def test_a_file_that_will_not_compress_is_left_and_the_fetch_goes_on(raw, monkeypatch):
    old = raw / "odds_soccer_epl_20260812T153746Z.json"
    old.write_text("[]", encoding="utf-8")
    real = odds_api.files.write_gzip

    def fails_for_the_old_one(path, text, **kwargs):
        if "20260812" in str(path):
            raise OSError("disk full")
        return real(path, text, **kwargs)

    monkeypatch.setattr(odds_api.files, "write_gzip", fails_for_the_old_one)
    odds_api.keep_response("soccer_epl", EVENTS, WHEN)
    assert old.exists()
    assert (raw / "odds_soccer_epl_20260926T093000Z.json.gz").exists()


class _Client:
    """Enough of the API for one fetch, and no network."""
    last_cost = 2

    def get(self, path, params=None):
        return [{"key": "soccer_epl", "group": "Soccer"}] if path == "/sports" else EVENTS

    def report(self):
        return "credits used 2, remaining 498"


def test_a_fetch_leaves_its_response_gzipped(raw, monkeypatch):
    monkeypatch.setattr(config, "ODDS_API_KEY", "test-key")
    monkeypatch.setattr(config, "SITE_DIR", raw / "site")
    odds_api.fetch_odds("soccer_epl", client=_Client())
    kept = list(raw.glob("odds_soccer_epl_*.json.gz"))
    assert len(kept) == 1 and _unpacked(kept[0]) == EVENTS
