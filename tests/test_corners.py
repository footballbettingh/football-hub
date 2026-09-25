"""The corner anchor: what the market line says about a match's corners.

It is the one place a market price reaches the corner forecast, so the tests
hold it to three things: it finds a relationship that is there and invents
none that is not, it leaves the league's level alone, and it never learns from
the matches it is then used to price.
"""

from collections import namedtuple

import numpy as np
import pandas as pd
import pytest

from confidence import markets, picks as picks_mod, predict
from confidence.corners import CornerAnchor

TRUE = (0.10, 0.12)


def _walk_forward(n=12000, seed=2, coefficients=TRUE):
    """Walk-forward rows whose corners follow the market as `coefficients` say."""
    rng = np.random.default_rng(seed)
    comp = rng.choice(["AAA", "BBB", "CCC"], n)
    level = pd.Series(comp).map({"AAA": 2.4, "BBB": 2.7, "CCC": 3.0}).to_numpy()
    goals_model = level * np.exp(rng.normal(0, 0.15, n))
    goals_market = goals_model * np.exp(rng.normal(0, 0.08, n))
    share = rng.uniform(0.4, 0.6, n)
    corners = rng.uniform(8.5, 11.0, n)
    frame = pd.DataFrame({
        "date": pd.Timestamp("2022-01-01") + pd.to_timedelta(np.arange(n) // 20, "D"),
        "competition": comp, "home": "h", "away": "a",
        "home_goals": rng.poisson(goals_market * share),
        "away_goals": rng.poisson(goals_market * (1 - share)),
        "lam_model": goals_model * share, "mu_model": goals_model * (1 - share),
        "rho_model": -0.05,
        "lam_market": goals_market * share, "mu_market": goals_market * (1 - share),
        "rho_market": -0.05,
        "corner_lam": corners * 0.55, "corner_mu": corners * 0.45,
    })
    usual = pd.Series(np.log(goals_market)).groupby(comp).transform("mean").to_numpy()
    truth = corners * np.exp(coefficients[0] * (np.log(goals_market) - usual)
                             + coefficients[1] * (np.log(goals_market) - np.log(goals_model)))
    frame["total_corners"] = rng.poisson(truth)
    return frame


# 40,000 matches: the second feature varies little (sd 0.08), so its
# coefficient's standard error is about 0.02 here — the tolerances are three.
def test_the_anchor_finds_what_the_market_says_about_corners():
    anchor = CornerAnchor.fit(_walk_forward(n=40000))
    assert anchor.coefficients == pytest.approx(TRUE, abs=0.06)


def test_it_invents_nothing_where_the_market_says_nothing():
    anchor = CornerAnchor.fit(_walk_forward(n=40000, coefficients=(0.0, 0.0)))
    assert np.abs(anchor.coefficients).max() < 0.06


def test_it_leaves_each_leagues_level_where_it_was():
    """The corner model's level is refitted to what was played; the anchor
    only redistributes around it, league by league."""
    frame = _walk_forward()
    anchor = CornerAnchor.fit(frame)
    log_factor = np.log(anchor.factors(frame))
    for _, block in pd.Series(log_factor).groupby(frame["competition"].to_numpy()):
        assert abs(block.mean()) < 0.01


def test_too_little_history_or_no_line_means_no_adjustment():
    frame = _walk_forward(n=500)
    assert CornerAnchor.fit(frame).is_identity
    anchor = CornerAnchor.fit(_walk_forward())
    frame.loc[0, "lam_market"] = np.nan
    assert anchor.factors(frame.head(1))[0] == 1.0


def test_it_survives_the_trip_through_the_calibration_file():
    anchor = CornerAnchor.fit(_walk_forward())
    again = CornerAnchor.from_dict(anchor.to_dict())
    frame = _walk_forward(n=50, seed=9)
    assert again.factors(frame) == pytest.approx(anchor.factors(frame), rel=1e-5)
    assert CornerAnchor.from_dict(None).is_identity


def test_only_the_corners_move():
    frame = _walk_forward(n=300)
    anchor = CornerAnchor((0.3, 0.3), {"AAA": 1.0, "BBB": 1.0, "CCC": 1.0})
    keys, plain, _ = predict.build_arrays(frame, 0.9)
    _, anchored, _ = predict.build_arrays(frame, 0.9, anchor=anchor)
    corner = np.array([markets.group_of(k) == "corners" for k in keys])
    assert np.array_equal(plain[:, ~corner], anchored[:, ~corner])
    assert not np.allclose(plain[:, corner], anchored[:, corner])


def test_a_fold_is_never_priced_with_an_anchor_that_saw_it():
    """Rewrite the corner counts of the middle of the history. Everything up to
    it must price exactly as before; what comes after it must not."""
    frame = _walk_forward(n=10000)
    keys, before, _ = predict.out_of_sample_arrays(frame, 0.9, folds=5)
    order = np.argsort(frame["date"].to_numpy(), kind="stable")
    parts = np.array_split(order, 5)
    rewritten = frame.copy()
    rewritten.loc[parts[2], "total_corners"] = rewritten.loc[parts[2], "total_corners"] + 4
    _, after, _ = predict.out_of_sample_arrays(rewritten, 0.9, folds=5)

    upto = np.concatenate(parts[:3])
    later = np.concatenate(parts[3:])
    assert np.array_equal(before[upto], after[upto], equal_nan=True)
    assert not np.array_equal(before[later], after[later], equal_nan=True)


def test_the_card_prices_corners_through_the_anchor():
    Fixture = namedtuple("Fixture", "home away date home_odds_cons draw_odds_cons "
                                    "away_odds_cons home_team away_team")
    fixture = Fixture("h", "a", pd.Timestamp("2026-09-26"), 2.0, 3.5, 3.8, "H", "A")

    class Goals:
        rho = -0.05

        def expected_counts(self, home, away):
            return 1.4, 1.1

        def knows(self, team):
            return True

    class Corners:
        def expected_counts(self, home, away):
            return 5.4, 4.4

    def corner_line(anchor):
        rows = picks_mod._price_one(fixture, Goals(), Corners(), 0.9, "power", "AAA",
                                    anchor=anchor)
        return next(r["prob_raw"] for r in rows if r["key"] == "corners9.5_over")

    busier = CornerAnchor((0.0, 0.5), {"AAA": 0.0})
    plain = corner_line(None)
    assert corner_line(CornerAnchor()) == pytest.approx(plain)
    assert corner_line(busier) != pytest.approx(plain)
