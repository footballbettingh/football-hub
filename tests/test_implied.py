"""De-vigging and the market-implied score matrix.

The repricing test is the important one: if the fit does not reproduce the
prices it was given, every market derived from it (BTTS, Over 1.5, team
totals) is quietly built on a different opinion than the one the market held.
"""

import numpy as np
import pytest

from confidence.implied import DEVIG_METHODS, devig, implied_lambdas
from confidence.poisson import score_matrix


@pytest.mark.parametrize("method", DEVIG_METHODS)
def test_devig_returns_a_distribution(method):
    probs = devig([2.10, 3.40, 3.60], method)
    assert probs.sum() == pytest.approx(1.0)
    assert (probs > 0).all()


@pytest.mark.parametrize("method", DEVIG_METHODS)
def test_devig_is_a_no_op_on_fair_prices(method):
    """Prices that already sum to 1 must come back unchanged, or the
    correction is inventing a margin that was never charged."""
    fair = [1 / 0.5, 1 / 0.3, 1 / 0.2]
    assert devig(fair, method) == pytest.approx([0.5, 0.3, 0.2], abs=1e-9)


def test_power_and_shin_take_more_margin_off_the_longshot():
    """The favourite-longshot bias is the whole reason to prefer them: a flat
    scaling leaves the longshot overstated, which is where a confidence
    project's false positives would come from."""
    prices = [1.30, 5.50, 11.0]
    flat = devig(prices, "proportional")
    for method in ("power", "shin"):
        adjusted = devig(prices, method)
        assert adjusted[0] > flat[0]
        assert adjusted[-1] < flat[-1]


def test_devig_rejects_an_unknown_method():
    with pytest.raises(ValueError):
        devig([2.0, 4.0, 4.0], "magic")


def _market_from(lam, mu, rho):
    matrix = score_matrix(lam, mu, rho, 12)
    totals = np.add.outer(np.arange(13), np.arange(13))
    return (float(np.tril(matrix, -1).sum()), float(np.trace(matrix)),
            float(np.triu(matrix, 1).sum()), float(matrix[totals > 2.5].sum()))


@pytest.mark.parametrize("lam,mu,rho", [(1.6, 1.1, -0.03), (0.9, 2.2, 0.05),
                                        (2.4, 0.6, 0.0)])
def test_implied_lambdas_recover_the_generating_parameters(lam, mu, rho):
    q_home, q_draw, q_away, q_over = _market_from(lam, mu, rho)
    got_lam, got_mu, got_rho, resid = implied_lambdas(q_home, q_draw, q_away, q_over,
                                                      rho=0.0)
    assert resid < 1e-6
    assert got_lam == pytest.approx(lam, abs=0.02)
    assert got_mu == pytest.approx(mu, abs=0.02)
    assert got_rho == pytest.approx(rho, abs=0.03)


def test_1x2_alone_still_pins_both_lambdas():
    """Two free probabilities, two lambdas — exactly identified, which is what
    lets a fixture feed that quotes only 1X2 produce a BTTS number."""
    q_home, q_draw, q_away, _ = _market_from(1.7, 1.0, -0.04)
    lam, mu, rho, resid = implied_lambdas(q_home, q_draw, q_away, None, rho=-0.04)
    assert resid < 1e-6
    assert lam == pytest.approx(1.7, abs=0.02)
    assert mu == pytest.approx(1.0, abs=0.02)
    assert rho == -0.04          # untouched when there is nothing to fit it to


def test_repricing_a_real_looking_line():
    q = devig([2.20, 3.40, 3.30], "power")
    q_over = devig([1.90, 1.95], "power")[0]
    lam, mu, rho, resid = implied_lambdas(q[0], q[1], q[2], q_over, rho=-0.03)
    assert resid < 1e-4
    assert 0.5 < lam < 3.0 and 0.5 < mu < 3.0
