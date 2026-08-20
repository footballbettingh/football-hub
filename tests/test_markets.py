"""The failures that don't raise: a key that exists on one side only, a market
that settles the wrong way round, probabilities that quietly stop summing to 1.
"""

import numpy as np
import pytest

from confidence import markets
from confidence.poisson import score_matrix


@pytest.fixture
def matrix():
    return score_matrix(1.6, 1.1, -0.03, 12)


def test_probability_and_settlement_keys_match(matrix):
    """A key priced but never settled is a bet graded against nothing."""
    priced = set(markets.goal_probabilities(matrix))
    settled = set(markets.goal_results(2, 1))
    assert priced == settled


def test_corner_keys_match():
    corner_matrix = score_matrix(5.4, 4.6, 0.0, 25)
    assert set(markets.corner_probabilities(corner_matrix)) == set(markets.corner_results(11))


def test_markets_are_exhaustive(matrix):
    probs = markets.goal_probabilities(matrix)
    assert probs["1x2_home"] + probs["1x2_draw"] + probs["1x2_away"] == pytest.approx(1.0)
    assert probs["btts_yes"] + probs["btts_no"] == pytest.approx(1.0)
    for line in markets.TOTAL_LINES:
        assert probs[f"ou{line:g}_over"] + probs[f"ou{line:g}_under"] == pytest.approx(1.0)
    assert probs["dnb_home"] + probs["dnb_away"] == pytest.approx(1.0)


def test_double_chance_is_the_sum_of_its_parts(matrix):
    probs = markets.goal_probabilities(matrix)
    assert probs["dc_1x"] == pytest.approx(probs["1x2_home"] + probs["1x2_draw"])
    assert probs["dc_12"] == pytest.approx(probs["1x2_home"] + probs["1x2_away"])
    assert probs["dc_x2"] == pytest.approx(probs["1x2_draw"] + probs["1x2_away"])


def test_totals_are_nested(matrix):
    """Over 0.5 cannot be less likely than Over 1.5."""
    probs = markets.goal_probabilities(matrix)
    overs = [probs[f"ou{line:g}_over"] for line in markets.TOTAL_LINES]
    assert overs == sorted(overs, reverse=True)


def test_dnb_is_conditional_not_marginal(matrix):
    probs = markets.goal_probabilities(matrix)
    assert probs["dnb_home"] > probs["1x2_home"]
    assert probs["dnb_home"] == pytest.approx(
        probs["1x2_home"] / (probs["1x2_home"] + probs["1x2_away"]))


@pytest.mark.parametrize("home,away,expected", [
    (2, 0, {"1x2_home": True, "btts_yes": False, "ou1.5_over": True,
            "ou2.5_over": False, "hcp_home-1.5": True, "dnb_home": True}),
    (1, 1, {"1x2_draw": True, "btts_yes": True, "ou1.5_over": True,
            "dnb_home": None, "dnb_away": None, "dc_1x": True, "dc_x2": True}),
    (0, 3, {"1x2_away": True, "btts_no": True, "hcp_away-1.5": True,
            "hcp_home+1.5": False, "tt0.5_home_over": False,
            "tt2.5_away_over": True}),
])
def test_settlement(home, away, expected):
    results = markets.goal_results(home, away)
    for key, want in expected.items():
        assert results[key] is want or results[key] == want, key


def test_draw_no_bet_voids_rather_than_loses():
    """None means the stake comes back. A False here would silently count
    every drawn match as a losing bet and understate the hit rate."""
    assert markets.goal_results(1, 1)["dnb_home"] is None
    assert markets.goal_results(1, 0)["dnb_home"] is True
    assert markets.goal_results(0, 1)["dnb_home"] is False


def test_corner_results_absent_when_data_is():
    assert markets.corner_results(np.nan) == {}
    assert markets.corner_results(None) == {}
    assert markets.corner_results(10)["corners9.5_over"] is True


def test_every_key_has_a_group_and_a_label():
    for key in markets.ALL_KEYS:
        assert markets.group_of(key) in markets.GROUPS
        assert markets.label(key) != key


def test_group_of_rejects_nonsense():
    with pytest.raises(KeyError):
        markets.group_of("handicap_asian_0.25")
