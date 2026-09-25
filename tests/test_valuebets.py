"""The value-betting half: the backtest behind the Evidence page.

It had no tests of its own. These hold the three things its verdict rests on:
that a bet is priced as the card prices the same line, that a bet is only one
the rules allow, and that the walk-forward never sees a result before it bets.
"""

import numpy as np
import pandas as pd
import pytest

from confidence import config as cf_config
from confidence.implied import devig
from valuebets import backtest, markets

TEAMS = [f"club{i}" for i in range(10)]


def _dataset(seasons=3, seed=4):
    """A ten-club league with closing prices around a known truth."""
    rng = np.random.default_rng(seed)
    strength = dict(zip(TEAMS, rng.normal(0, 0.3, len(TEAMS))))
    rows, day = [], pd.Timestamp("2022-08-06")
    for _ in range(seasons):
        for home in TEAMS:
            for away in TEAMS:
                if home == away:
                    continue
                lam = np.exp(0.35 + strength[home] - 0.6 * strength[away])
                mu = np.exp(0.10 + strength[away] - 0.6 * strength[home])
                p_home = float(np.clip(0.45 + 0.3 * np.tanh(strength[home] - strength[away]),
                                       0.15, 0.75))
                p_away = float(np.clip(0.75 - p_home, 0.1, 0.6))
                p_draw = 1 - p_home - p_away
                margin = 1.06
                rows.append({
                    "date": day, "competition": "LGE", "home_team": home, "away_team": away,
                    "home_goals": int(rng.poisson(lam)), "away_goals": int(rng.poisson(mu)),
                    "home_odds": 1 / (p_home * margin), "draw_odds": 1 / (p_draw * margin),
                    "away_odds": 1 / (p_away * margin),
                    "over25_odds": 1.9, "under25_odds": 1.95,
                })
                day += pd.Timedelta(days=1)
    return pd.DataFrame(rows)


class _Model:
    """Probabilities fixed in advance, to test the pricing and not the fit."""

    def predict_probabilities(self, home, away):
        return np.array([0.5, 0.3, 0.2])

    def predict_over_under(self, home, away, line):
        return 0.6


# -- a selection is priced the way the card prices the same line -------------

def test_the_market_side_is_de_vigged_the_cards_way():
    row = pd.Series({"home_odds": 1.8, "draw_odds": 3.6, "away_odds": 4.8,
                     "home_goals": 2, "away_goals": 1})
    chosen = markets.MatchResult().selections(_Model(), row, "a", "b")
    expected = devig([1.8, 3.6, 4.8], cf_config.DEVIG)
    assert [s.market_prob for s in chosen] == pytest.approx(list(expected))
    assert [s.won for s in chosen] == [True, False, False]


def test_an_unplayed_match_prices_without_settling():
    row = pd.Series({"home_odds": 1.8, "draw_odds": 3.6, "away_odds": 4.8})
    chosen = markets.MatchResult().selections(_Model(), row, "a", "b", settle=False)
    assert [s.won for s in chosen] == [None, None, None]


def test_the_totals_line_settles_on_the_total():
    market = markets.OverUnder(2.5)
    assert market.columns == ("over25_odds", "under25_odds")
    three = pd.Series({"home_goals": 2, "away_goals": 1})
    two = pd.Series({"home_goals": 1, "away_goals": 1})
    assert list(market.results(three)) == [True, False]
    assert list(market.results(two)) == [False, True]


# -- a bet is only one the rules allow ------------------------------------------

@pytest.fixture(scope="module")
def dataset():
    return _dataset()


@pytest.fixture(scope="module")
def bets(dataset):
    cfg = backtest.BacktestConfig(min_training_matches=90, min_edge=0.02)
    return backtest.run(dataset, cfg), cfg


def test_every_bet_is_inside_the_band_and_over_the_edge(bets):
    frame, cfg = bets
    assert len(frame) > 0
    assert frame["odds"].between(cfg.odds_min, cfg.odds_max).all()
    assert (frame["edge"] >= cfg.min_edge).all()
    assert np.allclose(frame["edge"], frame["model_prob"] - frame["market_prob"])


def test_each_bet_pays_its_price_or_loses_its_stake(bets):
    frame, cfg = bets
    expected = np.where(frame["won"], cfg.stake * (frame["odds"] - 1), -cfg.stake)
    assert np.allclose(frame["pnl"], expected)
    head = backtest.summary(frame, cfg.stake)
    assert head["roi"] == pytest.approx(frame["pnl"].sum() / (len(frame) * cfg.stake) * 100)


def test_nothing_is_bet_before_the_model_has_its_matches(dataset, bets):
    frame, cfg = bets
    first_allowed = dataset.sort_values("date")["date"].iloc[cfg.min_training_matches]
    assert frame["date"].min() >= first_allowed


def test_a_later_result_changes_no_earlier_bet(dataset, bets):
    """The backtest's whole worth is that it never looked ahead. Rewrite every
    result from a date on, and nothing bet before that date may move."""
    frame, cfg = bets
    cut = dataset["date"].iloc[220]
    changed = dataset.copy()
    later = changed["date"] >= cut
    changed.loc[later, ["home_goals", "away_goals"]] = (
        changed.loc[later, ["away_goals", "home_goals"]].to_numpy() + 3)
    again = backtest.run(changed, cfg)
    before = frame[frame["date"] < cut].reset_index(drop=True)
    after = again[again["date"] < cut].reset_index(drop=True)
    pd.testing.assert_frame_equal(before, after)
    # And the rewrite did reach the later ones, or the test proves nothing.
    assert not frame[frame["date"] >= cut]["won"].reset_index(drop=True).equals(
        again[again["date"] >= cut]["won"].reset_index(drop=True))


def test_filters_only_ever_remove_bets(bets):
    frame, _ = bets
    capped = backtest.apply_filters(frame, backtest.BacktestConfig(max_edge=0.05))
    assert len(capped) < len(frame) and (capped["edge"] < 0.05).all()
    one = backtest.apply_filters(frame, backtest.BacktestConfig(one_per_match="edge"))
    assert not one.duplicated(["date", "match"]).any()
