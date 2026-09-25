"""The slate replayed over history has to keep to time as strictly as the ledger.

Its one job is to say how the live picker would have done, and the easiest way
for it to lie is to let a month's own results reach the records that month's
picks were ranked on. The first test here makes that impossible to miss.
"""

import numpy as np
import pandas as pd
import pytest

from confidence import config, slate_backtest

WEEKS = 90


def _history(seed=4):
    """A few seasons of made-up matches, in the shape predictions.csv has."""
    rng = np.random.default_rng(seed)
    rows = []
    start = pd.Timestamp("2022-01-03")
    for week in range(WEEKS):
        for comp in ("AAA", "BBB", "CCC"):
            for game in range(6):
                lam, mu = rng.uniform(0.8, 2.2), rng.uniform(0.6, 1.8)
                clam, cmu = rng.uniform(4.0, 6.5), rng.uniform(3.5, 5.5)
                rows.append({
                    "date": start + pd.Timedelta(days=7 * week + game % 3),
                    "competition": comp, "home": f"{comp}{game}h", "away": f"{comp}{game}a",
                    "home_goals": rng.poisson(lam), "away_goals": rng.poisson(mu),
                    "total_corners": rng.poisson(clam) + rng.poisson(cmu),
                    "lam_model": lam, "mu_model": mu, "rho_model": -0.05,
                    "lam_market": lam, "mu_market": mu, "rho_market": -0.05,
                    "corner_lam": clam, "corner_mu": cmu, "implied_resid": 0.001,
                })
    return pd.DataFrame(rows)


def _replay(history, **kwargs):
    kwargs.setdefault("min_scored", 400)
    return slate_backtest.replay(history, progress=None, **kwargs)


@pytest.fixture(scope="module")
def history():
    return _history()


@pytest.fixture(scope="module")
def chosen(history):
    return _replay(history)


def test_a_months_results_never_reach_its_own_picks(history, chosen):
    """Rewrite every score in one month mid-way. That month's picks, and every
    month's before it, must not move: they were chosen on records built from
    earlier months only. The months after it must move — or the rewrite
    reached nothing, and the first half of this would prove nothing either."""
    months = history["date"].dt.to_period("M")
    picked = sorted(set(chosen["date"].str[:7]))
    middle = pd.Period(picked[len(picked) // 2], "M")
    rewritten = history.copy()
    rows = months == middle
    rewritten.loc[rows, ["home_goals", "away_goals"]] = \
        rewritten.loc[rows, ["away_goals", "home_goals"]].to_numpy() + 1
    again = _replay(rewritten)

    cut = str((middle + 1).start_time.date())
    columns = ["date", "band", "match", "key", "prob"]

    def through(frame):
        return frame.loc[frame["date"] < cut, columns].reset_index(drop=True)

    def after(frame):
        return frame.loc[frame["date"] >= cut, columns].reset_index(drop=True)

    assert (chosen["date"].str[:7] == str(middle)).any()
    pd.testing.assert_frame_equal(through(chosen), through(again))
    assert not after(chosen).equals(after(again))


def test_nothing_is_chosen_until_there_is_a_record_to_choose_on(history, chosen):
    first = pd.Timestamp(chosen["date"].min())
    assert first > history["date"].min() + pd.Timedelta(days=120)


def test_every_pick_sits_in_its_band_and_one_bet_to_a_match(chosen):
    for band, (low, high) in config.PICK_BANDS.items():
        odds = chosen.loc[chosen["band"] == band, "fair_odds"]
        assert len(odds) and odds.between(low, high).all(), band
    assert not chosen.duplicated(["date", "match"]).any()
    assert set(chosen["result"]) <= {-1, 0, 1}


def test_the_tiebreak_changes_which_bet_but_not_which_days_have_one(history):
    """The comparison `--compare-tiebreak` prints is day for day, so both
    replays have to fill the same days and bands."""
    on, off = _replay(history, tiebreak=True), _replay(history, tiebreak=False)
    assert set(zip(on["date"], on["band"])) == set(zip(off["date"], off["band"]))


def test_the_summary_grades_claims_against_what_landed():
    picks = pd.DataFrame({
        "band": ["main"] * 4 + ["safe"] * 2,
        "prob": [0.6, 0.6, 0.6, 0.6, 0.8, 0.8],
        "fair_odds": [1 / 0.6] * 4 + [1.25] * 2,
        "result": [1, 1, 0, -1, 1, 1],
    })
    table = slate_backtest.summary(picks).set_index("band")
    assert list(table.index) == ["safe", "main", "all"]
    main = table.loc["main"]
    assert (main["picks"], main["void"]) == (3, 1)          # the void is out
    assert main["landed"] == pytest.approx(2 / 3)
    assert main["gap_pp"] == pytest.approx((2 / 3 - 0.6) * 100)
    assert main["z"] == pytest.approx((2 - 1.8) / np.sqrt(3 * 0.24))
    assert table.loc["all", "picks"] == 5
