"""The daily walk-forward prices again only what new results can have changed.

Its whole claim is that it comes out as a full run would, so that is what the
first test holds it to; the rest hold the decisions around it — when the tail
is not enough, and what a match that arrives late or disappears does to it.
"""

import json

import numpy as np
import pandas as pd
import pytest

from confidence import config as cf_config
from confidence.walkforward import run
from hub import pipeline

TEAMS = [f"team{i}" for i in range(8)]


def _history(seasons=5, seed=3):
    """A league of eight, a double round robin a season, with closing prices."""
    rng = np.random.default_rng(seed)
    strength = dict(zip(TEAMS, rng.normal(0, 0.25, len(TEAMS))))
    rows, day = [], pd.Timestamp("2021-08-07")
    for season in range(seasons):
        for home in TEAMS:
            for away in TEAMS:
                if home == away:
                    continue
                lam = np.exp(0.3 + strength[home] - strength[away] * 0.5)
                mu = np.exp(0.05 + strength[away] - strength[home] * 0.5)
                p_home = 0.45 + 0.25 * np.tanh(strength[home] - strength[away])
                rows.append({
                    "date": day, "competition": "LGE", "season": 2021 + season,
                    "home": home, "away": away,
                    "home_goals": rng.poisson(lam), "away_goals": rng.poisson(mu),
                    "home_odds_cons": 1 / p_home * 0.95, "draw_odds_cons": 3.4,
                    "away_odds_cons": 1 / max(0.8 - p_home, 0.1) * 0.95,
                    "home_odds": np.nan, "draw_odds": np.nan, "away_odds": np.nan,
                    "home_sot": rng.poisson(lam * 3), "away_sot": rng.poisson(mu * 3),
                    "home_corners": rng.poisson(5.2), "away_corners": rng.poisson(4.4),
                })
                day += pd.Timedelta(days=2)
    frame = pd.DataFrame(rows)
    frame["total_corners"] = frame["home_corners"] + frame["away_corners"]
    return frame


@pytest.fixture(scope="module")
def history():
    return _history()


def test_the_tail_is_priced_as_the_full_run_prices_it(history):
    """The refit schedule is walked from the start either way, so the first
    match priced gets the fit a full run would have given it — made as of the
    same day, on the same matches. Only the optimiser's starting point differs."""
    full = run(history, progress=None)
    since = {"LGE": history["date"].max() - pd.Timedelta(days=60)}
    tail = run(history, progress=None, since=since)
    assert len(tail) and tail["date"].min() >= since["LGE"]
    both = full.merge(tail, on=["date", "competition", "home", "away"],
                      suffixes=("_full", "_tail"))
    assert len(both) == len(tail)
    for column in ("lam_model", "mu_model", "lam_market", "corner_lam"):
        assert np.allclose(both[column + "_full"], both[column + "_tail"],
                           rtol=1e-4, atol=1e-5, equal_nan=True), column


@pytest.fixture
def on_disk(tmp_path, monkeypatch, history):
    """A stored run of `history`, as the last daily run left it."""
    monkeypatch.setattr(cf_config, "PREDICTIONS_CSV", tmp_path / "predictions.csv")
    monkeypatch.setattr(pipeline, "PREDICTIONS_META", tmp_path / "predictions_meta.json")
    stored = run(history, progress=None)
    stored.to_csv(cf_config.PREDICTIONS_CSV, index=False)
    pipeline.PREDICTIONS_META.write_text(json.dumps({
        "fingerprint": pipeline._model_fingerprint(), "full_at": "2026-09-22"}))
    return stored


def test_a_recent_full_run_leaves_only_the_tail_to_price(history, on_disk):
    since, kept, _ = pipeline._incremental_plan(history, today="2026-09-25")
    assert since == {"LGE": history["date"].max() - pd.Timedelta(days=pipeline.TAIL_DAYS)}
    assert (kept["date"] < since["LGE"]).all() and len(kept) > 0


@pytest.mark.parametrize("change, why", [
    ({"fingerprint": "something older"}, "code or settings changed"),
    ({"full_at": "2026-09-17"}, "weekly rebuild"),
])
def test_what_the_tail_cannot_cover_walks_the_whole_history(history, on_disk,
                                                            change, why):
    meta = json.loads(pipeline.PREDICTIONS_META.read_text())
    pipeline.PREDICTIONS_META.write_text(json.dumps({**meta, **change}))
    since, kept, reason = pipeline._incremental_plan(history, today="2026-09-25")
    assert since is None and kept is None and why in reason


def test_no_earlier_run_means_a_full_one(history, on_disk):
    pipeline.PREDICTIONS_META.unlink()
    assert pipeline._incremental_plan(history)[0] is None


def test_a_match_that_arrives_late_moves_the_start_back_to_it(history, on_disk):
    """A result from before the tail that the stored run never saw changes the
    training data of everything after it."""
    early = history["date"].min() + pd.Timedelta(days=500)
    stored = on_disk[on_disk["date"] != early]
    stored.to_csv(cf_config.PREDICTIONS_CSV, index=False)
    since, _, _ = pipeline._incremental_plan(history, today="2026-09-25")
    assert since["LGE"] == early


def test_a_match_gone_from_the_history_is_dropped_and_results_are_refreshed(
        history, on_disk):
    first = on_disk.iloc[0]
    changed = history.copy()
    corrected = (changed["date"] == on_disk.iloc[1]["date"]) & \
                (changed["home"] == on_disk.iloc[1]["home"])
    changed.loc[corrected, "home_goals"] = 9
    changed = changed[~((changed["date"] == first["date"]) & (changed["home"] == first["home"]))]
    _, kept, _ = pipeline._incremental_plan(changed, today="2026-09-25")
    assert not ((kept["date"] == first["date"]) & (kept["home"] == first["home"])).any()
    second = on_disk.iloc[1]
    row = kept[(kept["date"] == second["date"]) & (kept["home"] == second["home"])]
    assert int(row["home_goals"].iloc[0]) == 9


def test_two_runs_a_day_apart_end_where_one_full_run_would(history, tmp_path,
                                                          monkeypatch):
    monkeypatch.setattr(cf_config, "PREDICTIONS_CSV", tmp_path / "predictions.csv")
    monkeypatch.setattr(pipeline, "PREDICTIONS_META", tmp_path / "predictions_meta.json")
    last_round = history["date"] > history["date"].max() - pd.Timedelta(days=6)
    monkeypatch.setattr(pipeline.cf_data, "load_history", lambda: history[~last_round])
    pipeline.rebuild_model(progress=lambda *_: None)            # full: nothing on file
    monkeypatch.setattr(pipeline.cf_data, "load_history", lambda: history)
    said = []
    pipeline.rebuild_model(progress=said.append)                # the tail
    assert any("Pricing again" in line for line in said)

    daily = pd.read_csv(cf_config.PREDICTIONS_CSV, parse_dates=["date"])
    full = run(history, progress=None)
    assert len(daily) == len(full)
    assert np.allclose(daily["lam_model"], full["lam_model"], rtol=1e-4, atol=1e-5)
    assert np.allclose(daily["corner_lam"], full["corner_lam"], rtol=1e-4, atol=1e-5,
                       equal_nan=True)
