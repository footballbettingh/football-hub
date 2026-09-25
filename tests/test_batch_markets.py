"""The batched pricing is the per-match pricing, a match to a row.

`build_arrays` prices every match in the history at once rather than one at a
time. What it must not do is price them differently, so each piece is held to
the way it used to be done — the grid built one match at a time, and every
selection summed straight off it — rather than to itself.
"""

import numpy as np
import pandas as pd
import pytest

from confidence import markets, predict
from confidence.poisson import score_matrices, score_matrix

RNG = np.random.default_rng(11)


def _reference_goal_probabilities(matrix):
    """Every goals selection summed off the grid directly, as it was done
    before the regions: the independent account the batch is checked against."""
    n = matrix.shape[0]
    diff = np.subtract.outer(np.arange(n), np.arange(n))
    total = np.add.outer(np.arange(n), np.arange(n))
    home, draw, away = np.tril(matrix, -1).sum(), np.trace(matrix), np.triu(matrix, 1).sum()
    out = {
        "1x2_home": home, "1x2_draw": draw, "1x2_away": away,
        "dc_1x": home + draw, "dc_12": home + away, "dc_x2": draw + away,
        "btts_yes": matrix[1:, 1:].sum(),
        "btts_no": matrix[0, :].sum() + matrix[:, 0].sum() - matrix[0, 0],
        "hcp_home-1.5": matrix[diff >= 2].sum(),
        "hcp_home+1.5": 1 - matrix[diff <= -2].sum(),
        "hcp_away-1.5": matrix[diff <= -2].sum(),
        "hcp_away+1.5": 1 - matrix[diff >= 2].sum(),
        "dnb_home": home / (home + away), "dnb_away": away / (home + away),
    }
    for line in markets.TOTAL_LINES:
        under = matrix[total <= line].sum()
        out[f"ou{line:g}_over"], out[f"ou{line:g}_under"] = 1 - under, under
    for side, marginal in (("home", matrix.sum(1)), ("away", matrix.sum(0))):
        for line in markets.TEAM_LINES:
            under = marginal[:int(line) + 1].sum()
            out[f"tt{line:g}_{side}_over"], out[f"tt{line:g}_{side}_under"] = 1 - under, under
    return out


def _parameters(n=300):
    lam = RNG.uniform(0.2, 3.5, n)
    mu = RNG.uniform(0.2, 3.0, n)
    rho = np.where(RNG.random(n) < 0.3, 0.0, RNG.uniform(-0.15, 0.05, n))
    return lam, mu, rho


def test_a_stack_of_grids_is_each_grid_on_its_own():
    lam, mu, rho = _parameters()
    stacked = score_matrices(lam, mu, rho, 12)
    for k in range(len(lam)):
        assert np.array_equal(stacked[k], score_matrix(lam[k], mu[k], rho[k], 12))


def test_every_goals_selection_is_the_region_it_names():
    lam, mu, rho = _parameters()
    batch = markets.goal_probability_arrays(score_matrices(lam, mu, rho, 12))
    assert set(batch) == set(markets.goal_results(1, 1))
    for k in range(len(lam)):
        expected = _reference_goal_probabilities(score_matrix(lam[k], mu[k], rho[k], 12))
        for key, value in expected.items():
            assert batch[key][k] == pytest.approx(value, abs=1e-12), key


def test_corner_lines_are_the_totals_under_them():
    lam, mu = RNG.uniform(3, 7, 50), RNG.uniform(2.5, 6, 50)
    batch = markets.corner_probability_arrays(score_matrices(lam, mu, 0.0, 25))
    for k in range(len(lam)):
        grid = score_matrix(lam[k], mu[k], 0.0, 25)
        total = np.add.outer(np.arange(26), np.arange(26))
        for line in markets.CORNER_LINES:
            assert batch[f"corners{line:g}_under"][k] == pytest.approx(
                grid[total <= line].sum(), abs=1e-12)


@pytest.mark.parametrize("home, away", [(0, 0), (1, 0), (0, 1), (2, 2), (3, 1),
                                        (1, 3), (0, 4), (5, 0)])
def test_every_score_settles_as_the_rules_say(home, away):
    """One row of the batch against the rules written out for one score."""
    settled = markets.goal_result_arrays([home], [away])
    margin, total = home - away, home + away
    rules = {"1x2_home": margin > 0, "1x2_draw": margin == 0, "1x2_away": margin < 0,
             "dc_1x": margin >= 0, "dc_12": margin != 0, "dc_x2": margin <= 0,
             "btts_yes": home > 0 and away > 0, "btts_no": home == 0 or away == 0,
             "hcp_home-1.5": margin >= 2, "hcp_home+1.5": margin >= -1,
             "hcp_away-1.5": margin <= -2, "hcp_away+1.5": margin <= 1,
             "dnb_home": None if margin == 0 else margin > 0,
             "dnb_away": None if margin == 0 else margin < 0}
    rules.update({f"ou{line:g}_over": total > line for line in markets.TOTAL_LINES})
    rules.update({f"ou{line:g}_under": total < line for line in markets.TOTAL_LINES})
    for side, goals in (("home", home), ("away", away)):
        rules.update({f"tt{line:g}_{side}_over": goals > line for line in markets.TEAM_LINES})
        rules.update({f"tt{line:g}_{side}_under": goals < line for line in markets.TEAM_LINES})
    assert set(settled) == set(rules)
    for key, won in rules.items():
        assert settled[key][0] == (-1 if won is None else int(won)), key


def test_a_missing_score_still_refuses_to_settle():
    with pytest.raises(ValueError):
        markets.goal_results(float("nan"), 1)


def test_fusing_arrays_is_fusing_each_match():
    lam, mu, rho = _parameters(40)
    lam_m, mu_m = lam * RNG.uniform(0.8, 1.2, 40), mu * RNG.uniform(0.8, 1.2, 40)
    rho_m = np.full(40, -0.04)
    lam_m[::5] = np.nan                                # no closing line for these
    fused = predict.fuse_arrays(lam, mu, rho, lam_m, mu_m, rho_m, 0.9)
    for k in range(40):
        one = predict.fuse(lam[k], mu[k], rho[k], lam_m[k], mu_m[k], rho_m[k], 0.9)
        assert tuple(values[k] for values in fused) == pytest.approx(one, rel=1e-15)
    assert fused[0][0] == lam[0]                      # unpriced: the model's own
    assert predict.fuse(1.2, 1.0, -0.05, None, None, None, 0.9) == (1.2, 1.0, -0.05)


def test_build_arrays_is_the_single_match_pricing_row_by_row():
    """Across a block boundary, with corners on some rows and not others, and
    results on some and not others."""
    n = predict.BLOCK + 37
    lam, mu, rho = _parameters(n)
    frame = pd.DataFrame({
        "lam_model": lam, "mu_model": mu, "rho_model": rho,
        "lam_market": np.where(RNG.random(n) < 0.2, np.nan, lam * 1.05),
        "mu_market": mu * 0.95, "rho_market": -0.05,
        "corner_lam": np.where(RNG.random(n) < 0.3, np.nan, RNG.uniform(3, 7, n)),
        "corner_mu": RNG.uniform(3, 6, n),
        "home_goals": np.where(RNG.random(n) < 0.1, np.nan, RNG.poisson(1.4, n)),
        "away_goals": RNG.poisson(1.1, n).astype(float),
        "total_corners": np.where(RNG.random(n) < 0.3, np.nan, RNG.poisson(10, n)),
    })
    keys, probs, results = predict.build_arrays(frame, 0.9)
    column = {key: i for i, key in enumerate(keys)}
    for k in list(range(0, n, 97)) + [predict.BLOCK - 1, predict.BLOCK, n - 1]:
        row = frame.iloc[k]
        fused = predict.fuse(row.lam_model, row.mu_model, row.rho_model,
                             row.lam_market, row.mu_market, row.rho_market, 0.9)
        one = predict.match_probabilities(*fused, 12, row.corner_lam, row.corner_mu)
        for key in keys:
            if key in one:
                assert probs[k, column[key]] == np.float32(one[key]), (k, key)
            else:
                assert np.isnan(probs[k, column[key]]), (k, key)
        if np.isnan(row.home_goals):
            assert (results[k] == -1).all()
            continue
        settled = markets.goal_results(row.home_goals, row.away_goals)
        settled.update(markets.corner_results(row.total_corners))
        for key in keys:
            won = settled.get(key)
            assert results[k, column[key]] == (-1 if won is None else int(won)), (k, key)
