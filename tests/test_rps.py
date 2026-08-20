"""The ranked probability score.

RPS earns its place only if it says something Brier cannot, so that is what is
pinned here: that missing by two places costs more than missing by one, and
that on a binary market the two metrics agree exactly — which is the reason
only 1X2 gets scored this way.
"""

import numpy as np
import pandas as pd
import pytest

from confidence.evaluate import brier, ranked_probability_score, rps_1x2

KEYS = ["1x2_home", "1x2_draw", "1x2_away", "btts_yes"]


def frame(n):
    """The `predictions` columns rps_1x2 reads: a de-vigged closing line."""
    return pd.DataFrame({"q_home": [0.5] * n, "q_draw": [0.3] * n,
                         "q_away": [0.2] * n})


# -- the metric ------------------------------------------------------------

def test_a_perfect_forecast_scores_zero():
    assert ranked_probability_score([[1.0, 0.0, 0.0]], [0]) == 0.0


def test_missing_by_two_places_costs_more_than_missing_by_one():
    """The whole reason for the metric. Brier cannot tell these apart."""
    near = ranked_probability_score([[1.0, 0.0, 0.0]], [1])   # draw happened
    far = ranked_probability_score([[1.0, 0.0, 0.0]], [2])    # away won
    assert far > near
    assert (far, near) == (1.0, 0.5)

    # Brier, for contrast, charges the same for both.
    outright = np.array([1.0, 0.0, 0.0])
    assert brier(outright, [0, 1, 0]) == brier(outright, [0, 0, 1])


def test_on_two_categories_it_is_the_brier_score():
    """Which is why every binary market on the card is left alone: a second
    column of identical numbers is not a second opinion."""
    rng = np.random.default_rng(0)
    p = rng.uniform(0.05, 0.95, 500)
    happened = (rng.uniform(size=500) < p).astype(int)
    pairs = np.column_stack([1 - p, p])

    got = ranked_probability_score(pairs, happened)
    assert got == pytest.approx(brier(p, happened), abs=1e-12)


def test_a_confident_wrong_call_scores_worse_than_an_unsure_one():
    sure = ranked_probability_score([[0.9, 0.07, 0.03]], [2])
    unsure = ranked_probability_score([[0.4, 0.33, 0.27]], [2])
    assert sure > unsure


# -- the 1X2 table ---------------------------------------------------------

def _arrays(probs_rows, results_rows):
    return np.array(probs_rows, dtype=float), np.array(results_rows, dtype=float)


def test_the_table_scores_us_the_market_and_knowing_nothing():
    probs, results = _arrays(
        [[0.5, 0.3, 0.2, 0.6], [0.4, 0.3, 0.3, 0.5]],
        [[1, 0, 0, 1], [0, 0, 1, 0]])
    table = rps_1x2(frame(2), KEYS, probs, results)

    assert list(table["forecast"]) == ["Ours (calibrated)", "Closing line",
                                       "Knowing nothing"]
    assert (table["n"] == 2).all()
    # Knowing nothing is the anchor: any forecast worth having beats it.
    assert table.loc[0, "rps"] < table.loc[2, "rps"]


def test_a_triple_that_does_not_sum_to_one_is_renormalised_and_reported():
    """Calibrating each selection on its own breaks the sum. Scoring the raw
    numbers would quietly blame the model for the calibrator's arithmetic."""
    probs, results = _arrays([[0.6, 0.36, 0.24, 0.5]], [[1, 0, 0, 1]])
    table = rps_1x2(frame(1), KEYS, probs, results)

    assert table.loc[0, "renorm"] == pytest.approx(0.2, abs=1e-9)
    # 0.6/1.2, 0.36/1.2, 0.24/1.2 -> 0.5/0.3/0.2, home won.
    assert table.loc[0, "rps"] == pytest.approx(0.5 * (0.25 + 0.04), abs=1e-9)


def test_unsettled_and_void_rows_are_left_out():
    probs, results = _arrays(
        [[0.5, 0.3, 0.2, 0.6],      # settled
         [0.5, 0.3, 0.2, 0.6],      # void: no outcome recorded
         [0.5, 0.3, 0.2, 0.6]],     # impossible: two winners
        [[1, 0, 0, 1], [-1, -1, -1, 1], [1, 1, 0, 1]])
    assert rps_1x2(frame(3), KEYS, probs, results).loc[0, "n"] == 1


def test_a_market_without_1x2_returns_nothing_rather_than_guessing():
    probs, results = _arrays([[0.6]], [[1]])
    assert rps_1x2(frame(1), ["btts_yes"], probs, results).empty
