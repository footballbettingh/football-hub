"""End to end on a synthetic league: no lookahead, no silent row loss, and a
card that respects its own rules.
"""

import numpy as np
import pandas as pd
import pytest

from confidence import markets, picks as picks_mod, predict, walkforward
from confidence.calibrate import Calibrators
from confidence.poisson import BlendedGoalsModel, score_matrix


def fake_history(n_teams=14, rounds=8, seed=11):
    """A competition with results AND prices, priced off the true lambdas."""
    rng = np.random.default_rng(seed)
    teams = [f"team{i:02d}" for i in range(n_teams)]
    attack, defence = rng.normal(0, 0.3, n_teams), rng.normal(0, 0.2, n_teams)

    rows, day = [], pd.Timestamp("2022-01-01")
    for _ in range(rounds):
        for i in range(n_teams):
            j = int(rng.integers(n_teams))
            if i == j:
                continue
            lam = float(np.exp(0.2 + 0.25 + attack[i] - defence[j]))
            mu = float(np.exp(0.2 + attack[j] - defence[i]))
            matrix = score_matrix(lam, mu, 0.0, 12)
            p_home = float(np.tril(matrix, -1).sum())
            p_draw = float(np.trace(matrix))
            p_away = 1 - p_home - p_draw
            totals = np.add.outer(np.arange(13), np.arange(13))
            p_over = float(matrix[totals > 2.5].sum())
            margin = 1.06
            rows.append({
                "date": day, "competition": "TEST", "season": 2022,
                "home": teams[i], "away": teams[j],
                "home_team": teams[i], "away_team": teams[j],
                "home_goals": int(rng.poisson(lam)), "away_goals": int(rng.poisson(mu)),
                "home_sot": int(rng.poisson(lam * 3)), "away_sot": int(rng.poisson(mu * 3)),
                "home_corners": int(rng.poisson(5)), "away_corners": int(rng.poisson(4)),
                "home_odds_cons": 1 / (p_home * margin), "draw_odds_cons": 1 / (p_draw * margin),
                "away_odds_cons": 1 / (p_away * margin),
                "over25_odds_cons": 1 / (p_over * margin),
                "under25_odds_cons": 1 / ((1 - p_over) * margin),
                "home_odds": 1 / p_home, "draw_odds": 1 / p_draw, "away_odds": 1 / p_away,
                "over25_odds": 1 / p_over, "under25_odds": 1 / (1 - p_over),
            })
            day += pd.Timedelta(days=1)
    frame = pd.DataFrame(rows)
    frame["total_corners"] = frame["home_corners"] + frame["away_corners"]
    return frame, teams


def test_walk_forward_never_trains_on_the_present(monkeypatch):
    """The single most valuable test here. A fit that includes same-day results
    makes every downstream number optimistic and nothing crashes to say so."""
    seen = []

    class Spy(BlendedGoalsModel):
        def fit(self, matches, as_of=None):
            seen.append((matches["date"].max(), as_of))
            return super().fit(matches, as_of)

    monkeypatch.setattr(walkforward, "BlendedGoalsModel", Spy)
    history, _ = fake_history()
    walkforward.run(history, refit_days=3, min_train=60, progress=None)

    assert seen, "the model was never fitted"
    for latest_training_match, fitted_for in seen:
        assert latest_training_match < fitted_for


def test_every_match_after_burn_in_is_priced_once():
    history, _ = fake_history()
    out = walkforward.run(history, refit_days=3, min_train=60, progress=None)
    burn_in = history.sort_values("date").iloc[:60]["date"].max()
    expected = (history["date"] > burn_in).sum()
    assert len(out) == pytest.approx(expected, rel=0.05)
    assert not out.duplicated(subset=["date", "home", "away"]).any()
    assert out["implied_resid"].max() < 1e-4


def test_prices_are_reproduced_by_the_implied_matrix():
    """If the anchor does not reprice the line, the derived markets are not
    the market's opinion — they are the fitter's."""
    history, _ = fake_history()
    out = walkforward.run(history, refit_days=3, min_train=60, progress=None)
    keys, probs, results = predict.build_arrays(out, weight=1.0)
    home = probs[:, keys.index("1x2_home")]
    assert home == pytest.approx(out["q_home"].to_numpy(), abs=1e-3)


def test_fusion_endpoints_are_exact():
    assert predict.fuse(1.2, 0.9, -0.05, 2.0, 1.5, 0.01, 0.0)[:2] == pytest.approx((1.2, 0.9))
    assert predict.fuse(1.2, 0.9, -0.05, 2.0, 1.5, 0.01, 1.0)[:2] == pytest.approx((2.0, 1.5))


def test_fusion_falls_back_to_the_model_without_a_price():
    lam, mu, rho = predict.fuse(1.2, 0.9, -0.05, np.nan, np.nan, np.nan, 0.9)
    assert (lam, mu, rho) == pytest.approx((1.2, 0.9, -0.05))


def test_build_arrays_agrees_with_a_single_match():
    history, _ = fake_history()
    out = walkforward.run(history, refit_days=3, min_train=60, progress=None)
    keys, probs, _ = predict.build_arrays(out, weight=0.9)
    row = out.iloc[0]
    lam, mu, rho = predict.fuse(row.lam_model, row.mu_model, row.rho_model,
                                row.lam_market, row.mu_market, row.rho_market, 0.9)
    direct = predict.match_probabilities(lam, mu, rho, 12, row.corner_lam, row.corner_mu)
    for key, value in direct.items():
        assert probs[0, keys.index(key)] == pytest.approx(value, abs=1e-6)


def test_voids_are_not_counted_as_losses():
    history, _ = fake_history()
    out = walkforward.run(history, refit_days=3, min_train=60, progress=None)
    keys, probs, results = predict.build_arrays(out, weight=0.9)
    column = keys.index("dnb_home")
    drawn = (out["home_goals"] == out["away_goals"]).to_numpy()
    assert (results[drawn, column] == -1).all()
    assert (results[~drawn, column] >= 0).all()


# -- the card --------------------------------------------------------------

def _fake_card():
    return pd.DataFrame({
        "match": ["a v b", "a v b", "c v d", "e v f"],
        "selection": ["Over 0.5 goals", "Home +1.5", "Over 1.5 goals", "Home or draw (1X)"],
        "group": ["ou", "hcp", "ou", "dc"],
        "prob": [0.94, 0.88, 0.81, 0.72],
        "odds": [1.05, 1.10, 1.18, np.nan],
        "date": pd.to_datetime(["2026-08-14"] * 4),
    })


def test_shortlist_caps_correlated_legs_from_one_fixture():
    card = picks_mod.shortlist(_fake_card(), min_confidence=0.7, per_match=1)
    assert len(card) == 3
    assert card["match"].is_unique
    assert card.iloc[0]["prob"] == 0.94        # the strongest survives


def test_shortlist_respects_the_confidence_floor():
    card = picks_mod.shortlist(_fake_card(), min_confidence=0.85, per_match=0)
    assert set(card["prob"]) == {0.94, 0.88}


def test_accumulators_multiply_across_fixtures_only():
    card = picks_mod.shortlist(_fake_card(), min_confidence=0.7, per_match=1)
    accas = picks_mod.accumulators(card, sizes=(2, 3))
    assert accas.iloc[0]["probability"] == pytest.approx(0.94 * 0.81)
    assert accas.iloc[1]["probability"] == pytest.approx(0.94 * 0.81 * 0.72)
    assert accas.iloc[0]["fair_odds"] == pytest.approx(1 / (0.94 * 0.81))


def test_shortlist_drops_lines_the_matrix_could_not_reproduce():
    """If the anchor missed the price by 22 percentage points, every market
    derived from it is fiction — and it will still look confident."""
    card = _fake_card().assign(implied_resid=[0.0, 0.22, np.nan, 0.001])
    out = picks_mod.shortlist(card, min_confidence=0.7, per_match=0)
    assert list(out["prob"]) == [0.94, 0.81, 0.72]


def _reliability(rows):
    return pd.DataFrame(rows, columns=["scope", "band_low", "band_high", "predicted",
                                       "actual", "n"])


def test_hit_rates_come_from_the_matching_band():
    reliability = _reliability([
        ("all", 0.70, 0.80, 0.74, 0.735, 1000),
        ("all", 0.80, 0.90, 0.85, 0.851, 2000),
        ("all", 0.90, 1.00, 0.93, 0.928, 3000),
    ])
    card = picks_mod.attach_hit_rates(_fake_card(), reliability)
    assert list(card["hit_rate"]) == [0.928, 0.851, 0.851, 0.735]
    assert list(card["hit_rate_n"]) == [3000, 2000, 2000, 1000]


def test_hit_rate_prefers_the_market_it_is_in_when_the_evidence_is_there():
    reliability = _reliability([
        ("all", 0.90, 1.00, 0.93, 0.928, 3000),
        ("ou", 0.90, 1.00, 0.93, 0.902, 900),      # enough to speak for itself
        ("hcp", 0.80, 0.90, 0.88, 0.500, 12),      # far too thin to quote
    ])
    card = picks_mod.attach_hit_rates(_fake_card(), reliability)
    over_05 = card[card["selection"] == "Over 0.5 goals"].iloc[0]
    home_15 = card[card["selection"] == "Home +1.5"].iloc[0]
    assert over_05["hit_rate"] == 0.902 and over_05["hit_rate_n"] == 900
    assert home_15["hit_rate"] == 0.928        # fell back to all-markets


def test_a_thin_band_below_fifty_percent_cannot_truncate_a_market():
    """Ceilings are about high confidence. Starting the walk in the 30-40%
    bucket would let a sparse one down there kill a market that behaves
    perfectly everywhere it matters."""
    reliability = _reliability([
        ("ou", 0.30, 0.40, 0.35, 0.10, 12),        # thin and awful, and irrelevant
        ("ou", 0.50, 0.60, 0.55, 0.552, 40000),
        ("ou", 0.60, 0.70, 0.65, 0.651, 40000),
    ])
    assert picks_mod.group_ceilings(reliability)["ou"] == 0.70


def test_ceilings_stop_where_a_market_started_overstating_itself():
    reliability = _reliability([
        ("corners", 0.70, 0.75, 0.72, 0.723, 20000),
        ("corners", 0.75, 0.80, 0.76, 0.773, 15000),
        ("corners", 0.80, 0.85, 0.82, 0.813, 1900),
        ("corners", 0.90, 0.95, 0.93, 0.833, 520),     # 9 points short
        ("ou", 0.90, 0.95, 0.93, 0.931, 90000),
    ])
    ceilings = picks_mod.group_ceilings(reliability)
    assert ceilings["corners"] == 0.85
    assert ceilings["ou"] == 0.95


def test_a_modest_band_does_not_lower_the_ceiling():
    """Landing more often than claimed is a forecast being careful, not a
    failure — refusing to show those picks would punish the wrong direction."""
    reliability = _reliability([
        ("btts", 0.60, 0.70, 0.615, 0.638, 1751),
        ("btts", 0.70, 0.75, 0.727, 0.627, 1020),
    ])
    assert picks_mod.group_ceilings(reliability)["btts"] == 0.70


def test_ceilings_stop_at_an_untested_band():
    reliability = _reliability([
        ("corners", 0.80, 0.85, 0.82, 0.822, 5000),
        ("corners", 0.85, 0.90, 0.87, 0.900, 12),
        ("corners", 0.90, 0.95, 0.93, 0.930, 8000),
    ])
    assert picks_mod.group_ceilings(reliability)["corners"] == 0.85


def test_unvalidated_picks_are_off_the_card_by_default():
    reliability = _reliability([
        ("all", 0.90, 1.00, 0.93, 0.928, 3000),
        ("ou", 0.90, 1.00, 0.93, 0.700, 5000),     # totals overstate badly here
    ])
    card = picks_mod.attach_hit_rates(_fake_card(), reliability)
    assert not card.loc[card["selection"] == "Over 0.5 goals", "validated"].iloc[0]
    # The only totals band on record overstated itself, so the ceiling for that
    # group is nothing at all and every totals pick goes — including the 81% one.
    assert picks_mod.shortlist(card, 0.7, per_match=0)["selection"].tolist() == \
        ["Home +1.5", "Home or draw (1X)"]
    assert len(picks_mod.shortlist(card, 0.7, per_match=0, validated_only=False)) == 4


def test_calibrated_picks_stay_ordered_within_a_group():
    keys = ["ou0.5_over", "ou1.5_over", "ou2.5_over"]
    probs = np.array([[0.93, 0.78, 0.52]])
    results = np.array([[1, 1, 0]], dtype=np.int8)
    calibrators = Calibrators.fit(keys, probs, results, min_samples=10 ** 9)
    frame = pd.DataFrame({"group": ["ou"] * 3, "prob_raw": probs[0]})
    out = picks_mod._calibrate_column(frame, calibrators)
    assert list(out) == sorted(out, reverse=True)


def test_every_priced_selection_is_a_known_key():
    history, teams = fake_history()
    fixtures = pd.DataFrame({
        "date": [pd.Timestamp("2022-06-01")], "competition": ["TEST"],
        "home": [teams[0]], "away": [teams[1]],
        "home_team": [teams[0]], "away_team": [teams[1]],
        "home_odds_cons": [2.2], "draw_odds_cons": [3.4], "away_odds_cons": [3.3],
        "home_odds": [2.3], "draw_odds": [3.5], "away_odds": [3.4],
    })
    table = picks_mod.price_fixtures(history, fixtures, calibrators=None,
                                     weight=0.9, min_train=60)
    assert set(table["key"]) <= set(markets.ALL_KEYS)
    assert table["prob"].between(0, 1).all()
    # 1X2 carries a price, so an edge exists there and nowhere else
    assert table.loc[table["key"] == "1x2_home", "edge"].notna().all()
    assert table.loc[table["key"] == "btts_yes", "edge"].isna().all()
