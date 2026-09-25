"""Reading the Odds API's response into the price files.

The one thing in the project that costs money to fetch had no test of the code
that reads what was bought. A best price read as the consensus, or a total read
off the wrong line, would price every card from then on and say nothing.
"""

import pandas as pd
import pytest

from valuebets import config
from valuebets.sources import odds_api


def _book(name, home, draw, away, totals=()):
    markets = [{"key": "h2h", "outcomes": [
        {"name": "Arsenal", "price": home}, {"name": "Draw", "price": draw},
        {"name": "Chelsea", "price": away}]}]
    if totals:
        markets.append({"key": "totals", "outcomes": [
            {"name": side, "price": price, "point": point}
            for point, over, under in totals
            for side, price in (("Over", over), ("Under", under))]})
    return {"key": name, "markets": markets}


EVENT = {
    "id": "e1", "home_team": "Arsenal", "away_team": "Chelsea",
    "commence_time": "2026-09-27T14:00:00Z",
    "bookmakers": [
        _book("a", 2.00, 3.40, 4.00, totals=[(2.5, 1.90, 1.95), (3.5, 3.00, 1.40)]),
        _book("b", 2.10, 3.30, 3.80, totals=[(2.5, 1.85, 2.00)]),
        _book("c", 1.95, 3.50, 4.20, totals=[(2.5, 1.95, 1.90)]),
    ],
}


def test_best_is_the_top_price_and_the_consensus_the_median():
    best, books = odds_api.consensus_prices(EVENT, "best")
    median, _ = odds_api.consensus_prices(EVENT, "median")
    assert books == 3
    assert best == {"Arsenal": 2.10, "Draw": 3.50, "Chelsea": 4.20}
    assert median == {"Arsenal": 2.00, "Draw": 3.40, "Chelsea": 4.00}


def test_a_total_is_read_off_its_own_line_only():
    assert odds_api.totals_prices(EVENT, 2.5, "best") == (1.95, 2.00)
    assert odds_api.totals_prices(EVENT, 2.5, "median") == (1.90, 1.95)
    # One book quotes 3.5; the other lines are not blended into it.
    assert odds_api.totals_prices(EVENT, 3.5, "median") == (3.00, 1.40)
    assert odds_api.totals_prices(EVENT, 1.5) is None


def test_no_h2h_market_means_no_prices():
    assert odds_api.consensus_prices({"bookmakers": []}) == (None, 0)


def test_the_overround_is_the_sum_of_the_inverse_prices():
    prices = {"Arsenal": 2.0, "Draw": 4.0, "Chelsea": 4.0}
    assert odds_api._overround(prices, "Arsenal", "Chelsea") == 1.0
    assert odds_api._overround({"Arsenal": 2.0}, "Arsenal", "Chelsea") is None


class _Client:
    last_cost = 2

    def get(self, path, params=None):
        if path == "/sports":
            return [{"key": "soccer_epl", "group": "Soccer"}]
        return [EVENT, {"id": "e2", "home_team": "X", "away_team": "Y",
                        "commence_time": "2026-09-27T16:00:00Z", "bookmakers": []}]

    def report(self):
        return "credits used 2, remaining 498"


@pytest.fixture
def fetched(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ODDS_API_KEY", "test-key")
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(config, "SITE_DIR", tmp_path / "site")
    return odds_api.fetch_odds("soccer_epl", client=_Client())


def test_a_fetch_becomes_one_row_per_priced_fixture(fetched):
    assert len(fetched) == 1                          # the unpriced one is dropped
    row = fetched.iloc[0]
    assert (row["home_odds"], row["draw_odds"], row["away_odds"]) == (2.10, 3.50, 4.20)
    assert (row["home_odds_cons"], row["draw_odds_cons"], row["away_odds_cons"]) == (
        2.00, 3.40, 4.00)
    assert (row["over25_odds"], row["under25_odds"]) == (1.95, 2.00)
    assert (row["over25_odds_cons"], row["under25_odds_cons"]) == (1.90, 1.95)
    assert row["over35_odds"] == 3.00 and "over15_odds" not in fetched.columns
    assert row["n_bookmakers"] == 3
    assert row["date"] == pd.Timestamp("2026-09-27")
    assert row["commence_time"] == "2026-09-27T14:00:00Z"


def test_the_league_code_is_left_to_the_caller(fetched):
    """The pipeline fills it from hub.leagues, which knows every league."""
    assert fetched.iloc[0]["competition"] is None


def test_an_unknown_sport_spends_nothing(tmp_path, monkeypatch):
    """The free /sports listing is asked first, so a mistyped key is refused
    before the call that costs credits."""
    monkeypatch.setattr(config, "ODDS_API_KEY", "test-key")
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(config, "SITE_DIR", tmp_path / "site")
    asked = []

    class Recording(_Client):
        def get(self, path, params=None):
            asked.append(path)
            return super().get(path, params)

    with pytest.raises(SystemExit, match="Unknown sport"):
        odds_api.fetch_odds("soccer_nowhere", client=Recording())
    assert asked == ["/sports"]
