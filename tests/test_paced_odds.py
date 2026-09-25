"""Paying for prices at the pace the quota allows.

The free tier is 500 credits a month, and the card is only as good as the
prices under it: the model is nine parts closing line to one part itself. The
old rule bought every league at once whenever the newest price was eight days
old, so a pick went into the ledger on prices 3.7 days old on average. These
tests hold the replacement to its two promises — the leagues that play soon,
stalest first, and never more than the month has left.
"""

from datetime import date

import pandas as pd
import pytest

import fb
from hub import pipeline
from valuebets.sources import odds_api

NOW = pd.Timestamp("2026-09-25T09:00:00Z")


# -- how much to spend -------------------------------------------------------

def test_the_allowance_is_todays_share_of_what_is_left_above_the_floor():
    assert pipeline.daily_allowance(500, 30, floor=50) == pytest.approx(15.0)
    assert pipeline.daily_allowance(80, 3, floor=50) == pytest.approx(10.0)


def test_nothing_is_spent_below_the_floor():
    assert pipeline.daily_allowance(40, 10, floor=50) == 0.0


def test_the_last_day_before_the_reset_may_spend_what_is_left():
    assert pipeline.daily_allowance(90, 1, floor=50) == pytest.approx(40.0)
    assert pipeline.daily_allowance(90, 0, floor=50) == pytest.approx(40.0)


# -- what to spend it on -----------------------------------------------------

def test_the_stalest_leagues_are_bought_first_and_no_more_than_it_pays_for():
    soon = {"a": NOW + pd.Timedelta(days=1), "b": NOW + pd.Timedelta(days=2),
            "c": NOW + pd.Timedelta(hours=12)}
    ages = {"a": 3.0, "b": 1.0}                   # c has never been bought
    assert pipeline.paced_order(soon, ages, allowance=6, cost=2) == ["c", "a", "b"]
    assert pipeline.paced_order(soon, ages, allowance=5, cost=2) == ["c", "a"]
    assert pipeline.paced_order(soon, ages, allowance=1.9, cost=2) == []


def test_a_price_bought_hours_ago_is_not_bought_again():
    soon = {"a": NOW + pd.Timedelta(days=1)}
    assert pipeline.paced_order(soon, {"a": 0.2}, allowance=100, cost=2) == []


def test_equally_stale_leagues_go_in_kick_off_order():
    soon = {"late": NOW + pd.Timedelta(days=2), "early": NOW + pd.Timedelta(hours=5)}
    assert pipeline.paced_order(soon, {}, allowance=100, cost=2) == ["early", "late"]


# -- when the month turns ----------------------------------------------------

def test_until_a_reset_is_seen_the_first_of_the_month_is_assumed(tmp_path):
    state = tmp_path / "odds_quota.json"
    assert pipeline.days_to_reset(100, date(2026, 9, 25), state) == 6


def test_a_reset_is_recognised_by_the_used_count_falling(tmp_path):
    """The API reports what has been used since the last reset, never when the
    next one is. A count that goes down between two runs is a reset."""
    state = tmp_path / "odds_quota.json"
    pipeline.days_to_reset(420, date(2026, 10, 11), state)
    assert pipeline.days_to_reset(12, date(2026, 10, 12), state) == 31
    assert pipeline.days_to_reset(300, date(2026, 11, 5), state) == 7


def test_a_reset_late_in_the_month_still_comes_round_in_february():
    assert pipeline._days_until(date(2027, 2, 20), 31) == 8
    assert pipeline._days_until(date(2026, 12, 31), 31) == 31


# -- the whole of it, with the network taken out ------------------------------

class _Client:
    def __init__(self, remaining, used=0):
        self.remaining, self.used = remaining, used


def _paced(monkeypatch, remaining, events, ages, days_left=3):
    """Run fetch_odds_paced against fakes; return the leagues it bought."""
    bought, said = [], []
    monkeypatch.setattr(pipeline, "_redraw_plan", lambda progress: None)
    monkeypatch.setattr(pipeline, "league_plan", lambda: sorted(events))
    monkeypatch.setattr(odds_api, "Client", lambda: _Client(remaining))
    monkeypatch.setattr(odds_api, "list_events", lambda sport, client: [
        {"commence_time": (NOW + pd.Timedelta(days=d)).isoformat()} for d in events[sport]])
    monkeypatch.setattr(pipeline, "quote_ages", lambda now: ages)
    monkeypatch.setattr(pipeline, "days_to_reset", lambda used, today: days_left)
    monkeypatch.setattr(pipeline, "_fetch_one", lambda sport, *a, **k: bought.append(sport) or 10)
    pipeline.fetch_odds_paced(progress=said.append, now=NOW)
    return bought, said


def test_only_the_leagues_that_play_soon_are_bought(monkeypatch):
    events = {"plays_tomorrow": [1.0, 8.0], "plays_next_week": [5.0],
              "plays_in_two_days": [2.0]}
    bought, _ = _paced(monkeypatch, remaining=80, events=events,
                       ages={"plays_tomorrow": 4.0, "plays_in_two_days": 1.0,
                             "plays_next_week": 9.0})
    assert bought == ["plays_tomorrow", "plays_in_two_days"]


def test_what_the_allowance_cannot_cover_waits_and_says_so(monkeypatch):
    events = {"a": [1.0], "b": [1.5]}
    bought, said = _paced(monkeypatch, remaining=55, events=events,
                          ages={"a": 3.0, "b": 2.0})       # (55 - 50) / 3 < 2
    assert bought == []
    assert any("left for another day: a, b" in line for line in said)


def test_nothing_is_bought_blind(monkeypatch):
    bought, said = _paced(monkeypatch, remaining=None, events={"a": [1.0]}, ages={})
    assert bought == []
    assert any("bought blind" in line for line in said)


# -- which rule a run uses ---------------------------------------------------

@pytest.fixture
def run_prices(monkeypatch):
    """Everything in `fb.py run` except the choice of how to buy prices."""
    from hub import card, evidence
    calls = []
    monkeypatch.setattr(pipeline, "fetch_results", lambda: None)
    monkeypatch.setattr(pipeline, "fetch_odds_paced", lambda: calls.append("paced"))
    monkeypatch.setattr(pipeline, "fetch_odds",
                        lambda sports=None: calls.append(("all", sports)))
    monkeypatch.setattr(card, "build", lambda: None)
    monkeypatch.setattr(evidence, "build", lambda: None)
    monkeypatch.setattr(fb, "_odds_age_days", lambda: 2.0)

    def run(*flags):
        fb.main(["run", "--skip-model", "--no-notify", "--no-evidence", *flags])
        return calls
    return run


def test_a_run_buys_at_the_paced_rate_by_default(run_prices):
    assert run_prices() == ["paced"]


def test_named_leagues_are_bought_as_named(run_prices):
    assert run_prices("--sports", "soccer_epl") == [("all", ["soccer_epl"])]


def test_odds_every_still_buys_the_whole_plan_once_the_prices_are_that_old(run_prices):
    assert run_prices("--odds-every", "8") == []           # 2 days old: not yet
    assert run_prices("--odds-every", "0") == [("all", None)]


def test_no_odds_buys_nothing(run_prices):
    assert run_prices("--no-odds") == []
