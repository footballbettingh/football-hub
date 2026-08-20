"""Fusing the two forecasts into one distribution, and expanding it.

The walk-forward run stores *parameters* (a pair of lambdas and a rho from the
model, another pair from the closing line) rather than the ~45 probabilities
they generate. Two reasons: predictions.csv stays a 10MB file instead of a
300MB one, and re-fusing at a different market weight becomes a second of
arithmetic instead of an hour of refitting — which is what makes `cf.py sweep`
cheap enough to be honest with.
"""

import numpy as np

from . import markets
from .poisson import score_matrix

CORNER_MAX = 25       # corners run to the low 20s; goals do not


def fuse(lam_model, mu_model, rho_model, lam_market, mu_market, rho_market,
         weight):
    """Geometric blend of two lambda pairs. weight=1 is the market alone.

    Geometric, not arithmetic, because the model is multiplicative in
    log-lambda space: blending there keeps a blend of two Poissons a Poisson,
    and makes weight 0 and 1 reproduce the inputs exactly.
    """
    if lam_market is None or lam_market != lam_market:      # NaN-safe
        return float(lam_model), float(mu_model), float(rho_model)
    w = float(weight)
    lam = float(lam_model) ** (1 - w) * float(lam_market) ** w
    mu = float(mu_model) ** (1 - w) * float(mu_market) ** w
    rho = (1 - w) * float(rho_model) + w * float(rho_market)
    return lam, mu, rho


def match_probabilities(lam, mu, rho, max_goals=12,
                        corner_lam=None, corner_mu=None):
    """All selections for one match, as {key: probability}."""
    probs = markets.goal_probabilities(score_matrix(lam, mu, rho, max_goals))
    if corner_lam is not None and corner_lam == corner_lam:
        corner_matrix = score_matrix(corner_lam, corner_mu, 0.0, CORNER_MAX)
        probs.update(markets.corner_probabilities(corner_matrix))
    return probs


def build_arrays(predictions, weight, max_goals=12, keys=None):
    """Expand stored parameters into (keys, probs, results) arrays.

    probs   float32 [n_matches, n_keys], NaN where the selection is unavailable
            (corners in a division that has no corner data)
    results int8    [n_matches, n_keys], 1 won / 0 lost / -1 void or unknown

    Column-major arrays rather than a long DataFrame: 64k matches x 45
    selections is 3M rows, which pandas will happily turn into a gigabyte of
    object columns.
    """
    keys = list(keys or markets.ALL_KEYS)
    index = {k: i for i, k in enumerate(keys)}
    n = len(predictions)
    probs = np.full((n, len(keys)), np.nan, dtype=np.float32)
    results = np.full((n, len(keys)), -1, dtype=np.int8)

    has_goals = "home_goals" in predictions.columns
    for row_no, row in enumerate(predictions.itertuples()):
        lam, mu, rho = fuse(row.lam_model, row.mu_model, row.rho_model,
                            row.lam_market, row.mu_market, row.rho_market,
                            weight)
        row_probs = match_probabilities(
            lam, mu, rho, max_goals,
            getattr(row, "corner_lam", np.nan), getattr(row, "corner_mu", np.nan))
        for key, value in row_probs.items():
            slot = index.get(key)
            if slot is not None:
                probs[row_no, slot] = value

        if not has_goals or row.home_goals != row.home_goals:
            continue
        outcomes = markets.goal_results(row.home_goals, row.away_goals)
        outcomes.update(markets.corner_results(getattr(row, "total_corners", np.nan)))
        for key, won in outcomes.items():
            slot = index.get(key)
            if slot is not None and won is not None:
                results[row_no, slot] = int(bool(won))

    return keys, probs, results
