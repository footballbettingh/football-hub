"""Turning closing prices into probabilities, and probabilities into a matrix.

Two steps, both of which decide how good the final numbers are:

**De-vigging.** A price carries the bookmaker's margin, so raw `1/odds`
over-counts by ~5-7% in total. Removing it proportionally is the usual choice
and is known to be wrong in a specific direction: the margin is not spread
evenly, it is loaded onto longshots. Since this project ranks bets by *how
likely* they are, that bias lands squarely on the favourites we care most
about, so all three standard methods are implemented and `python cf.py devig`
measures which one predicts real results best.

**The market-implied score matrix.** The closing line prices 1X2 and Over/Under
2.5 and nothing else — no BTTS, no Over 1.5, no team totals. But a pair of
Poisson means plus a Dixon-Coles rho generates the whole joint distribution, so
fitting (lambda_home, lambda_away, rho) to reproduce the prices you *do* have
recovers a market-quality view of every market you don't. Two 1X2 prices pin
two lambdas exactly; adding the totals price pins rho as well.

That is the trick this project runs on. The sibling project established that
the closing line out-forecasts the model on every division it tested; this
lets the closing line answer questions it was never asked.
"""

import numpy as np
from scipy.optimize import brentq, least_squares

from .poisson import poisson_pmf

DEVIG_METHODS = ("proportional", "power", "shin")


def devig(prices, method="power"):
    """Fair probabilities over one market's mutually exclusive outcomes.

    `prices` must be the full set: all three of 1X2, both sides of a total.
    De-vigging a partial market is meaningless, and de-vigging a *best*-price
    column is close to a no-op (shopping the top price already strips the
    margin) — feed it the consensus column.
    """
    raw = np.asarray([1.0 / float(p) for p in prices], dtype=float)
    total = raw.sum()

    if method == "proportional":
        return raw / total

    if method == "power":
        # p_i proportional to (1/o_i)^k. The margin is multiplicative in log
        # space, so this shifts more of it onto longshots than a flat scaling.
        if total <= 1.0:
            return raw / total
        # Each raw probability is below 1, so sum(raw**k) FALLS as k rises:
        # the exponent that removes a positive margin is above 1, not below.
        f = lambda k: np.sum(raw ** k) - 1.0
        try:
            k = brentq(f, 1.0, 20.0, xtol=1e-12)
        except ValueError:
            return raw / total
        out = raw ** k
        return out / out.sum()

    if method == "shin":
        # Shin's model: the margin is what the book charges for the risk of
        # insider bets. z is the implied share of inside money.
        if total <= 1.0:
            return raw / total

        def p_of(z):
            return (np.sqrt(z ** 2 + 4 * (1 - z) * raw ** 2 / total) - z) / (2 * (1 - z))

        try:
            z = brentq(lambda z: p_of(z).sum() - 1.0, 1e-9, 0.5, xtol=1e-12)
        except ValueError:
            return raw / total
        out = p_of(z)
        return out / out.sum()

    raise ValueError(f"unknown de-vig method {method!r}; known: {DEVIG_METHODS}")


def _outcome_probs(lam, mu, rho, max_goals):
    """(P_home, P_draw, P_away, P_over_2.5) from a pair of means and rho.

    The fit below evaluates this a few dozen times per match over 64k matches,
    so it works on the two marginal vectors instead of materialising the joint
    grid. Dixon-Coles only moves four cells, and all four have a total of 2 or
    fewer goals, so its effect on each figure is a handful of additions.
    """
    ph = poisson_pmf(lam, max_goals)
    pa = poisson_pmf(mu, max_goals)

    low = np.outer(ph[:2], pa[:2])
    if rho:
        tau = np.array([[1.0 - lam * mu * rho, 1.0 + lam * rho],
                        [1.0 + mu * rho, 1.0 - rho]])
        delta = np.clip(low * tau, 0.0, None) - low
    else:
        delta = np.zeros((2, 2))
    # Truncating the grid at max_goals loses a little mass; normalising by the
    # mass actually present is what score_matrix does, and skipping it would
    # dump the whole tail onto the away win.
    norm = float(ph.sum() * pa.sum()) + delta.sum()

    cum_home, cum_away = np.cumsum(ph), np.cumsum(pa)
    p_home = float(ph[1:] @ cum_away[:-1]) + delta[1, 0]
    p_away = float(pa[1:] @ cum_home[:-1]) + delta[0, 1]
    p_draw = float(ph @ pa) + delta[0, 0] + delta[1, 1]
    # total <= 2 covers (0,0) (0,1) (0,2) (1,0) (1,1) (2,0) — every DC cell
    under = (ph[0] * cum_away[2] + ph[1] * cum_away[1] + ph[2] * pa[0]
             + delta.sum())
    return p_home / norm, p_draw / norm, p_away / norm, 1.0 - under / norm


def implied_lambdas(q_home, q_draw, q_away, q_over=None, rho=0.0,
                    init=(1.45, 1.15), max_goals=12, fit_rho=None):
    """Fit (lambda_home, lambda_away[, rho]) so the matrix reprices the market.

    Returns (lambda_home, lambda_away, rho, residual). `residual` is the
    largest absolute probability error left over — an honest reading of how
    well a Dixon-Coles Poisson can represent this particular line. It is small
    (< 0.005) for essentially every real price, and large enough to notice on
    the few that are internally inconsistent.

    With only 1X2 supplied there are two free probabilities and two lambdas, so
    the fit is exact and rho stays at whatever the model estimated. With the
    totals price too there are three, and rho is freed to absorb the draw-heavy
    low-scoring structure that independent Poissons get wrong.
    """
    have_total = q_over is not None and q_over == q_over  # NaN-safe
    if fit_rho is None:
        fit_rho = have_total

    def residuals(theta):
        lam, mu = np.exp(theta[0]), np.exp(theta[1])
        r = theta[2] if fit_rho else rho
        p_home, p_draw, p_away, p_over = _outcome_probs(lam, mu, r, max_goals)
        out = [p_home - q_home, p_away - q_away, p_draw - q_draw]
        if have_total:
            out.append(p_over - q_over)
        return np.asarray(out)

    x0 = [np.log(init[0]), np.log(init[1])]
    lo, hi = [np.log(0.05), np.log(0.05)], [np.log(8.0), np.log(8.0)]
    if fit_rho:
        x0.append(float(np.clip(rho, -0.15, 0.15)))
        lo.append(-0.2)
        hi.append(0.2)

    fit = least_squares(residuals, x0, bounds=(lo, hi), xtol=1e-10, ftol=1e-10)
    lam, mu = float(np.exp(fit.x[0])), float(np.exp(fit.x[1]))
    out_rho = float(fit.x[2]) if fit_rho else float(rho)
    return lam, mu, out_rho, float(np.max(np.abs(fit.fun)))
