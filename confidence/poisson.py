"""Poisson attack/defence model with a Dixon-Coles low-score correction.

Adapted from the sibling project's `valuebets/model.py`, with three changes
that this project needs and that one didn't:

1. **Unknown teams are league-average, not an error.** A promoted side has no
   history in its new division. The value-betting project skipped those
   fixtures; here the closing line carries most of the forecast anyway, so a
   neutral prior plus a `new_team` flag is more useful than a hole in August's
   card — which is exactly when the fixture list is full of them.
2. **The counts are a parameter.** Goals, shots on target and corners are the
   same estimation problem, so corners get a real model instead of a heuristic.
3. **Ratio estimator dropped.** The joint MLE won on a paired test there
   (p = 0.020) and costs the same warm-started; keeping one path keeps the
   score matrix honest.

    log lambda_home = base + home_adv + attack[h] - defence[a]
    log lambda_away = base            + attack[a] - defence[h]
"""

import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar
from scipy.special import gammaln

_DC_MAX = 1          # Dixon-Coles only touches scorelines with 0 or 1 goals

# log(k!) for the goal grid. The market-implied fit builds a score matrix a few
# dozen times per match over 64k matches, and scipy.stats.poisson.pmf spends
# most of its time on argument validation — this turns 8ms per match into 1.
_LOG_FACT = gammaln(np.arange(64) + 1.0)


def poisson_pmf(lam, n):
    """P(X = 0..n) for a Poisson mean, without the scipy.stats overhead."""
    k = np.arange(n + 1)
    lam = max(float(lam), 1e-12)
    return np.exp(-lam + k * np.log(lam) - _LOG_FACT[:n + 1])


def dc_tau(hg, ag, lam, mu, rho):
    """Dixon-Coles multiplier on the four lowest scorelines."""
    tau = np.ones_like(np.asarray(lam, dtype=float))
    tau = np.where((hg == 0) & (ag == 0), 1.0 - lam * mu * rho, tau)
    tau = np.where((hg == 0) & (ag == 1), 1.0 + lam * rho, tau)
    tau = np.where((hg == 1) & (ag == 0), 1.0 + mu * rho, tau)
    tau = np.where((hg == 1) & (ag == 1), 1.0 - rho, tau)
    return tau


def score_matrix(lam, mu, rho=0.0, max_goals=12):
    """P(home scores i, away scores j) as a (max_goals+1)^2 grid."""
    matrix = np.outer(poisson_pmf(lam, max_goals), poisson_pmf(mu, max_goals))
    if rho:
        i, j = np.meshgrid([0, 1], [0, 1], indexing="ij")
        adjust = dc_tau(i.ravel(), j.ravel(),
                        np.full(4, lam), np.full(4, mu), rho).reshape(2, 2)
        matrix[:_DC_MAX + 1, :_DC_MAX + 1] *= adjust
        matrix = np.clip(matrix, 0.0, None)
    return matrix / matrix.sum()


class PoissonModel:
    """Joint-MLE team strengths over any pair of count columns."""

    def __init__(self, count_cols=("home_goals", "away_goals"),
                 half_life_days=180, ridge=0.05, dixon_coles=True,
                 max_goals=12):
        self.count_cols = count_cols
        self.half_life_days = half_life_days
        self.ridge = ridge
        self.dixon_coles = dixon_coles
        self.max_goals = max_goals

        self.teams = None
        self.index = {}
        self.attack = self.defence = None
        self.home_adv = self.base = None
        self.rho = 0.0
        self.n_matches = 0
        self.scale = (1.0, 1.0)     # count units -> goal units, for shot models
        self._warm_start = None

    # -- fitting ----------------------------------------------------------

    def _weights(self, matches, as_of):
        if not self.half_life_days or "date" not in matches.columns:
            return np.ones(len(matches))
        dates = pd.to_datetime(matches["date"])
        reference = pd.to_datetime(as_of) if as_of is not None else dates.max()
        age = (reference - dates).dt.total_seconds().to_numpy() / 86400.0
        return 0.5 ** (np.clip(age, 0, None) / self.half_life_days)

    def fit(self, matches: pd.DataFrame, as_of=None):
        hcol, acol = self.count_cols
        if matches.empty:
            raise ValueError("cannot fit on an empty match set")

        hg = pd.to_numeric(matches[hcol], errors="coerce").to_numpy(dtype=float)
        ag = pd.to_numeric(matches[acol], errors="coerce").to_numpy(dtype=float)
        ok = ~(np.isnan(hg) | np.isnan(ag))
        matches, hg, ag = matches[ok], hg[ok], ag[ok]
        if len(matches) == 0:
            raise ValueError(f"no rows with both {hcol} and {acol}")

        w = self._weights(matches, as_of)
        home = matches["home"].to_numpy()
        away = matches["away"].to_numpy()

        self.teams = sorted(set(home) | set(away))
        self.index = {t: i for i, t in enumerate(self.teams)}
        self.n_matches = len(matches)
        n = len(self.teams)
        hi = np.fromiter((self.index[t] for t in home), dtype=int, count=len(home))
        ai = np.fromiter((self.index[t] for t in away), dtype=int, count=len(away))

        self._fit_mle(hi, ai, hg, ag, w, n)

        if self.dixon_coles:
            lam, mu = self._lambdas(hi, ai)
            self.rho = self._fit_rho(hg, ag, lam, mu, w)
        return self

    def _fit_mle(self, hi, ai, hg, ag, w, n):
        """Joint MLE with analytic gradients.

        The walk-forward run refits a few thousand times; a numerical gradient
        over 2n+2 parameters would turn minutes into hours. For a Poisson the
        partials collapse to weighted bincounts of the residuals, because every
        dloglambda/dtheta is 0 or +/-1.
        """
        total_w = w.sum()

        def objective(theta):
            attack, defence = theta[:n], theta[n:2 * n]
            home_adv, base = theta[2 * n], theta[2 * n + 1]
            lam = np.exp(base + home_adv + attack[hi] - defence[ai])
            mu = np.exp(base + attack[ai] - defence[hi])

            nll = -float((w * (hg * np.log(lam) - lam + ag * np.log(mu) - mu)).sum())
            nll += self.ridge * total_w * float(attack @ attack + defence @ defence)

            rh, ra = w * (hg - lam), w * (ag - mu)
            g_attack = -(np.bincount(hi, rh, n) + np.bincount(ai, ra, n))
            g_defence = np.bincount(ai, rh, n) + np.bincount(hi, ra, n)
            g_attack += 2 * self.ridge * total_w * attack
            g_defence += 2 * self.ridge * total_w * defence
            grad = np.concatenate([g_attack, g_defence,
                                   [-float(rh.sum())], [-float(rh.sum() + ra.sum())]])
            return nll, grad

        if self._warm_start is not None and len(self._warm_start) == 2 * n + 2:
            x0 = self._warm_start
        else:
            x0 = np.zeros(2 * n + 2)
            x0[2 * n] = 0.25
            x0[2 * n + 1] = np.log(max((w * (hg + ag)).sum() / (2 * total_w), 0.1))

        result = minimize(objective, x0, jac=True, method="L-BFGS-B",
                          options={"maxiter": 250, "ftol": 1e-10})
        theta = self._warm_start = result.x
        attack, defence = theta[:n], theta[n:2 * n]
        # Ridge already centres these; pin it exactly so parameters stay
        # comparable between refits.
        self.attack = attack - attack.mean()
        self.defence = defence - defence.mean()
        self.home_adv, self.base = float(theta[2 * n]), float(theta[2 * n + 1])
        self.converged = bool(result.success)
        return self

    def _lambdas(self, hi, ai):
        lam = np.exp(self.base + self.home_adv + self.attack[hi] - self.defence[ai])
        mu = np.exp(self.base + self.attack[ai] - self.defence[hi])
        return lam, mu

    def _fit_rho(self, hg, ag, lam, mu, w):
        """1-D MLE for rho: the Poisson part does not depend on it."""
        def neg_log_lik(rho):
            tau = dc_tau(hg, ag, lam, mu, rho)
            if np.any(tau <= 0):
                return 1e6
            return -float((w * np.log(tau)).sum())

        result = minimize_scalar(neg_log_lik, bounds=(-0.2, 0.2), method="bounded")
        return float(result.x) if result.success else 0.0

    # -- prediction -------------------------------------------------------

    def knows(self, team) -> bool:
        return team in self.index

    def expected_counts(self, home, away):
        """(lambda_home, lambda_away) in the units the model was fitted on,
        rescaled by `self.scale`. Unknown teams get league-average strength."""
        if self.attack is None:
            raise ValueError("model is not fitted")
        h, a = self.index.get(home), self.index.get(away)
        att_h = self.attack[h] if h is not None else 0.0
        def_h = self.defence[h] if h is not None else 0.0
        att_a = self.attack[a] if a is not None else 0.0
        def_a = self.defence[a] if a is not None else 0.0
        lam = np.exp(self.base + self.home_adv + att_h - def_a) * self.scale[0]
        mu = np.exp(self.base + att_a - def_h) * self.scale[1]
        return float(lam), float(mu)

    def matrix(self, home, away):
        lam, mu = self.expected_counts(home, away)
        return score_matrix(lam, mu, self.rho if self.dixon_coles else 0.0,
                            self.max_goals)


class BlendedGoalsModel:
    """Goals and shots-on-target fitted separately, then blended in log-lambda.

    Rating teams on shots beats rating them on goals: teams take ~4.7 shots on
    target for every ~1.5 goals, so it is a far larger sample of the same
    attacking process. The sibling project measured the blend at Brier 0.6044
    vs 0.6066 for goals alone, paired p = 1.6e-07.

    Blending the *series* before fitting is wrong and measurably so — the
    Poisson likelihood is not scale-invariant, so squashing shots into
    goal-units before the fit throws away the precision that made shots worth
    using. Blending the fitted lambdas keeps each fit on its own scale and
    makes weight 0 and 1 reproduce the pure models exactly.
    """

    def __init__(self, weight=0.5, **kwargs):
        self.weight = float(weight)
        self.goals = PoissonModel(("home_goals", "away_goals"), **kwargs)
        self.sot = PoissonModel(("home_sot", "away_sot"), dixon_coles=False,
                                **{k: v for k, v in kwargs.items() if k != "dixon_coles"})
        self.max_goals = self.goals.max_goals
        self.rho = 0.0
        self.n_matches = 0
        self.has_sot = False

    def fit(self, matches, as_of=None):
        self.goals.fit(matches, as_of)
        self.n_matches = self.goals.n_matches
        self.rho = self.goals.rho

        usable = matches.dropna(subset=["home_sot", "away_sot"])
        # A division without shot data (the National League, here) silently
        # falling back to goals is fine; silently doing it *and* claiming to be
        # a blend is not, hence the flag.
        self.has_sot = len(usable) >= 100 and self.weight > 0
        if not self.has_sot:
            return self

        self.sot.fit(usable, as_of)
        w = self.sot._weights(usable, as_of)

        def conversion(goal_col, shot_col):
            """League conversion rate, so shot lambdas come out as goals."""
            goals = pd.to_numeric(usable[goal_col], errors="coerce").to_numpy(float)
            shots = pd.to_numeric(usable[shot_col], errors="coerce").to_numpy(float)
            return float((w * goals).sum() / max((w * shots).sum(), 1e-9))

        self.sot.scale = (conversion("home_goals", "home_sot"),
                          conversion("away_goals", "away_sot"))
        return self

    def knows(self, team):
        return self.goals.knows(team)

    def expected_counts(self, home, away):
        gh, ga = self.goals.expected_counts(home, away)
        if not self.has_sot:
            return gh, ga
        sh, sa = self.sot.expected_counts(home, away)
        k = self.weight
        # geometric mean: the models are multiplicative in log-lambda space
        return float(gh ** (1 - k) * sh ** k), float(ga ** (1 - k) * sa ** k)

    def matrix(self, home, away):
        lam, mu = self.expected_counts(home, away)
        return score_matrix(lam, mu, self.rho, self.max_goals)
