"""Poisson goals model with two estimators, Dixon-Coles, time decay and shrinkage.

ESTIMATOR COMPARISON (11,668 matches, 6 leagues, walk-forward, same fixtures):

    metric                  ratio      MLE      market close
    Brier (lower better)    0.6088     0.6066   0.5867
    log loss                1.0166     1.0140   0.9839
    gap to market           +3.77%     +3.40%   --
    bets flagged            7,498      5,310    --
    ROI                     -0.41%     -2.28%   --

MLE is genuinely the better forecaster: a PAIRED t-test over the same 10,699
matches gives a mean Brier improvement of 0.00218, t = -2.32, p = 0.020. The
theory held — accounting for schedule strength does help.

It is also nowhere near enough. The gap to the closing line is 0.0199, which is
NINE TIMES the 0.0022 that switching estimators bought. Closing it would take
another eight improvements of the same size. The bottleneck was never the
estimator.

The ROI difference (-0.41% -> -2.28%) is NOT significant: bootstrapping both
gives P(MLE better) = 0.155, and the intervals overlap heavily. Do not read it
as MLE being worse at making money — it is noise. MLE is the default because it
wins on the metric that has a tight p-value and a causal story behind it, and
because it flags 29% fewer bets for the same information, which is what a
better-calibrated model should do.

Note the two estimators take DIFFERENT regularisation parameters:
`shrinkage_games` applies to "ratio", `ridge` applies to "mle". Setting the
wrong one is silently ignored.


Baseline idea (unchanged): each team gets attack and defense strengths
relative to the league average, split by home/away. Expected goals for a
matchup = attacker's strength x defender's weakness x league average. Goals
are Poisson, so convolving the two distributions gives P(home/draw/away).

Three corrections on top of that baseline, each fixing a way the plain model
misprices real football:

1. Dixon-Coles low-score correction (`rho`)
   Independent Poissons systematically underprice 0-0 and 1-1 and overprice
   1-0 / 0-1. Since draws are exactly where the 1.70-2.00 odds band lives,
   this bias lands right on the bets this project cares about. `rho` is fit
   by maximum likelihood on the training data, not guessed.

   How strong is the evidence for it here? A parametric bootstrap (simulate
   goals independently from the fitted lambdas, refit, 150x per league) puts
   the null sd of rho at 0.027-0.035 for a single league's 1500-2800 matches.
   Measured against that:

       league  n      rho       p
       PL      1900   -0.0286   0.280
       SA      1900   -0.0484   0.173
       PD      1900   -0.0091   0.787
       BL1     1530   -0.1120   0.000
       FL1     1678   -0.0566   0.093
       ELC     2760   -0.0048   0.893

   So a SINGLE league's rho is mostly indistinguishable from estimation noise
   — only the Bundesliga's is individually significant. But all six are
   negative (sign test p = 0.031) and combining them gives Stouffer z = -3.15,
   p = 0.0016. The effect is real and in the direction the literature reports;
   it is simply too small to pin down one league at a time.

   Practically it moves a draw probability by well under a percentage point,
   and turning it off changes the backtest from +0.32% to -0.26% ROI — inside
   the noise either way. Do not read a single fitted rho as a finding.

2. Time decay (`half_life_days`)
   A result from three seasons ago says less about a team than last month's.
   Matches are weighted 0.5 ** (age / half_life). Set to None to disable.

3. Shrinkage (`shrinkage_games`)
   A team with two home games and a 4-0 win gets attack strength 2.9 under
   the plain ratio, and the model then "finds value" on a number that's pure
   noise. Strengths are pulled toward league average 1.0 with weight
   k / (n + k). This is the single biggest source of fake edge in a naive
   backtest.

Input: DataFrame with columns date, home_team, away_team, home_goals, away_goals
(`date` optional — required only for time decay).
"""

import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import poisson

# Beyond this, tau() is 1 and the correction has no effect.
_DC_MAX = 1


class PoissonModel:
    """Poisson goals model with two interchangeable estimators.

    `method="ratio"` (the original) computes each team's strength as a ratio of
    its own weighted averages. Cheap, and wrong in a specific way: it never sees
    WHO a team played. Measured mid-season in the Premier League, opponent
    quality varied 0.88x to 1.27x, which misranks teams by up to five places.

    `method="mle"` fits all team parameters jointly by maximum likelihood — the
    Maher/Dixon-Coles formulation. Because every lambda depends on both teams,
    the optimiser cannot inflate one attack without adjusting the defences it
    faced, so schedule strength is handled by construction rather than by a
    correction term.

        log lambda_home = base + home_adv + attack[h] - defence[a]
        log lambda_away = base            + attack[a] - defence[h]

    Everything downstream (predict_probabilities, over/under, the markets layer)
    is unchanged — only how the strengths are arrived at differs.
    """

    def __init__(self, half_life_days=180, shrinkage_games=5.0,
                 dixon_coles=True, max_goals=10, method="mle", ridge=0.05,
                 signal="blend", signal_weight=0.5):
        self.half_life_days = half_life_days
        self.shrinkage_games = shrinkage_games
        self.dixon_coles = dixon_coles
        self.max_goals = max_goals
        self.method = method
        # Ridge on the log-strengths. Plays the role shrinkage plays in the
        # ratio estimator: pulls thin-sample teams toward average, and makes an
        # otherwise degenerate problem strictly convex.
        self.ridge = ridge
        # Which signal the team strengths are estimated FROM. Predictions are
        # always of GOALS — only the evidence used to rate teams changes.
        #   "goals"  a scoreline is the outcome, but a noisy measure of quality
        #   "sot"    shots on target: ~4.7 per team-match vs 1.5 goals, so a far
        #            larger sample of the same underlying attacking process
        #   "blend"  signal_weight * sot + (1 - signal_weight) * goals
        # Strengths fitted on shots are rescaled by the league's conversion rate
        # so the lambdas still come out in goals.
        self.signal = signal
        self.signal_weight = signal_weight
        self.scale_home = self.scale_away = 1.0
        self.attack = self.defence = None
        self.home_adv = self.base = None
        self._warm_start = None

        self.teams = None
        self.home_attack, self.home_defense = {}, {}
        self.away_attack, self.away_defense = {}, {}
        self.avg_home_goals = self.avg_away_goals = None
        self.rho = 0.0
        self.n_matches = 0

    # -- fitting ----------------------------------------------------------

    def _weights(self, matches, as_of=None):
        """Exponential decay by match age. Uniform if decay is disabled."""
        if not self.half_life_days or "date" not in matches.columns:
            return np.ones(len(matches))

        dates = pd.to_datetime(matches["date"])
        reference = pd.to_datetime(as_of) if as_of is not None else dates.max()
        age_days = (reference - dates).dt.total_seconds().to_numpy() / 86400.0
        age_days = np.clip(age_days, 0, None)
        return 0.5 ** (age_days / self.half_life_days)

    def _signal_series(self, matches, goals_h, goals_a, w):
        """Return the series the strengths are fitted on, plus the rescaling
        needed to turn its lambdas back into goals.

        Falls back to goals, loudly via `self.signal_fallback`, when the shot
        columns are absent — a dataset without them should not silently produce
        a different model than the caller asked for.
        """
        self.signal_fallback = False
        if self.signal == "goals":
            self.scale_home = self.scale_away = 1.0
            return goals_h, goals_a

        needed = ("home_sot", "away_sot")
        if not all(c in matches.columns for c in needed):
            self.signal_fallback = True
            self.scale_home = self.scale_away = 1.0
            return goals_h, goals_a

        sot_h = pd.to_numeric(matches["home_sot"], errors="coerce").to_numpy(dtype=float)
        sot_a = pd.to_numeric(matches["away_sot"], errors="coerce").to_numpy(dtype=float)
        if np.isnan(sot_h).any() or np.isnan(sot_a).any():
            sot_h = np.where(np.isnan(sot_h), goals_h, sot_h)
            sot_a = np.where(np.isnan(sot_a), goals_a, sot_a)

        # "blend" is handled by fitting two whole models and combining their
        # lambdas (see fit); it never reaches this function.
        series_h, series_a = sot_h, sot_a

        # Rescale so predicted lambdas are goals, not shots.
        self.scale_home = float((w * goals_h).sum() / max((w * series_h).sum(), 1e-9))
        self.scale_away = float((w * goals_a).sum() / max((w * series_a).sum(), 1e-9))
        return series_h, series_a

    def _shrink(self, ratio, n_eff):
        """Pull a strength ratio toward 1.0 when the sample is thin."""
        if not self.shrinkage_games:
            return ratio
        k = self.shrinkage_games
        return (n_eff * ratio + k * 1.0) / (n_eff + k)

    def fit(self, matches: pd.DataFrame, as_of=None):
        if self.signal == "blend":
            return self._fit_blend(matches, as_of)
        return self._fit_single(matches, as_of)

    def _fit_blend(self, matches, as_of):
        """Fit goals and shots separately, then average their lambdas.

        Blending the input SERIES before fitting is wrong, and measurably so:
        the Poisson likelihood is not scale-invariant, so rescaling shots into
        goal-units before the fit discards the precision that makes shots worth
        using. A first version did that and produced a discontinuous curve —
        Brier got monotonically WORSE from weight 0 to 0.75, then jumped better
        at 1.0, because weight 1.0 took a different code path.

        Combining in log-lambda space instead keeps each fit on its own natural
        scale and makes weight 0 and 1 exactly reproduce the pure models.
        """
        k = float(self.signal_weight)
        shared = dict(half_life_days=self.half_life_days,
                      shrinkage_games=self.shrinkage_games,
                      dixon_coles=self.dixon_coles, max_goals=self.max_goals,
                      method=self.method, ridge=self.ridge)
        self._goals_model = PoissonModel(signal="goals", **shared)._fit_single(matches, as_of)
        self._sot_model = PoissonModel(signal="sot", **shared)._fit_single(matches, as_of)

        base = self._sot_model if k >= 0.5 else self._goals_model
        self.teams = base.teams
        self.n_matches = base.n_matches
        self.home_attack, self.away_attack = base.home_attack, base.away_attack
        self.home_defense, self.away_defense = base.home_defense, base.away_defense
        self.avg_home_goals, self.avg_away_goals = base.avg_home_goals, base.avg_away_goals
        self.rho = (1 - k) * self._goals_model.rho + k * self._sot_model.rho
        # Surface the estimator attributes from the dominant sub-fit, so a blend
        # model is not silently missing fields the pure models have.
        self.attack, self.defence = base.attack, base.defence
        self.home_adv, self.base = base.home_adv, base.base
        self.mle_converged = getattr(base, "mle_converged", None)
        self.signal_fallback = (self._goals_model.signal_fallback
                                or self._sot_model.signal_fallback)
        self._blend_k = k
        return self

    def _fit_single(self, matches: pd.DataFrame, as_of=None):
        required = {"home_team", "away_team", "home_goals", "away_goals"}
        missing = required - set(matches.columns)
        if missing:
            raise ValueError(f"matches df missing columns: {missing}")
        if matches.empty:
            raise ValueError("cannot fit on an empty match set")

        home = matches.home_team.to_numpy()
        away = matches.away_team.to_numpy()
        goals_h = matches.home_goals.to_numpy(dtype=float)
        goals_a = matches.away_goals.to_numpy(dtype=float)
        w = self._weights(matches, as_of)

        hg, ag = self._signal_series(matches, goals_h, goals_a, w)

        self.teams = sorted(set(home) | set(away))
        self.n_matches = len(matches)
        index = {t: i for i, t in enumerate(self.teams)}
        hi = np.fromiter((index[t] for t in home), dtype=int, count=len(home))
        ai = np.fromiter((index[t] for t in away), dtype=int, count=len(away))
        n = len(self.teams)

        total_w = w.sum()
        self.avg_home_goals = float((w * hg).sum() / total_w)
        self.avg_away_goals = float((w * ag).sum() / total_w)
        if self.avg_home_goals <= 0 or self.avg_away_goals <= 0:
            raise ValueError("league average goals is zero — not enough data to fit")

        # Weighted per-team aggregates in one pass, rather than filtering the
        # DataFrame once per team (which made walk-forward backtests O(n^2)).
        games_home = np.bincount(hi, weights=w, minlength=n)
        scored_home = np.bincount(hi, weights=w * hg, minlength=n)
        conceded_home = np.bincount(hi, weights=w * ag, minlength=n)
        games_away = np.bincount(ai, weights=w, minlength=n)
        scored_away = np.bincount(ai, weights=w * ag, minlength=n)
        conceded_away = np.bincount(ai, weights=w * hg, minlength=n)

        def strength(totals, counts, league_avg):
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(counts > 0, totals / np.maximum(counts, 1e-12) / league_avg, 1.0)
            return self._shrink(ratio, counts)

        ha = strength(scored_home, games_home, self.avg_home_goals)
        hd = strength(conceded_home, games_home, self.avg_away_goals)
        aa = strength(scored_away, games_away, self.avg_away_goals)
        ad = strength(conceded_away, games_away, self.avg_home_goals)

        self.home_attack = dict(zip(self.teams, ha))
        self.home_defense = dict(zip(self.teams, hd))
        self.away_attack = dict(zip(self.teams, aa))
        self.away_defense = dict(zip(self.teams, ad))

        if self.method == "mle":
            self._fit_mle(hi, ai, hg, ag, w, n)
            lam, mu = self._lambdas_mle(hi, ai)
            lam, mu = lam * self.scale_home, mu * self.scale_away
            if self.dixon_coles:
                # rho is a property of SCORELINES, so it is always fitted
                # against real goals even when strengths came from shots.
                self.rho = self._fit_rho(goals_h, goals_a, lam, mu, w)
            else:
                self.rho = 0.0
            return self

        if self.dixon_coles:
            lam = ha[hi] * ad[ai] * self.avg_home_goals
            mu = aa[ai] * hd[hi] * self.avg_away_goals
            self.rho = self._fit_rho(hg, ag, lam, mu, w)
        else:
            self.rho = 0.0

        return self

    # -- maximum likelihood estimator -------------------------------------

    def _unpack(self, theta, n):
        return theta[:n], theta[n:2 * n], theta[2 * n], theta[2 * n + 1]

    def _lambdas_mle(self, hi, ai, theta=None, n=None):
        if theta is None:
            attack, defence = self.attack, self.defence
            home_adv, base = self.home_adv, self.base
        else:
            attack, defence, home_adv, base = self._unpack(theta, n)
        lam = np.exp(base + home_adv + attack[hi] - defence[ai])
        mu = np.exp(base + attack[ai] - defence[hi])
        return lam, mu

    def _fit_mle(self, hi, ai, hg, ag, w, n):
        """Joint MLE over attack, defence, home advantage and a base rate.

        Analytic gradients matter here: the walk-forward backtest refits a few
        thousand times, and a numerical gradient over 2n+2 parameters would
        make that hours instead of minutes.

        For Poisson, d/dtheta of the log-likelihood reduces to
        (observed - expected) x dloglambda/dtheta, and every dloglambda/dtheta
        is 0/±1, so each partial is just a weighted bincount of residuals.
        """
        total_w = w.sum()

        def objective(theta):
            attack, defence, home_adv, base = self._unpack(theta, n)
            lam = np.exp(base + home_adv + attack[hi] - defence[ai])
            mu = np.exp(base + attack[ai] - defence[hi])

            # constant log(y!) terms dropped — they don't depend on theta
            nll = -float((w * (hg * np.log(lam) - lam + ag * np.log(mu) - mu)).sum())
            nll += self.ridge * total_w * float(attack @ attack + defence @ defence)

            rh = w * (hg - lam)          # home residuals
            ra = w * (ag - mu)           # away residuals
            g_attack = -(np.bincount(hi, rh, n) + np.bincount(ai, ra, n))
            g_defence = np.bincount(ai, rh, n) + np.bincount(hi, ra, n)
            g_attack += 2 * self.ridge * total_w * attack
            g_defence += 2 * self.ridge * total_w * defence
            g_home = -float(rh.sum())
            g_base = -float(rh.sum() + ra.sum())
            return nll, np.concatenate([g_attack, g_defence, [g_home], [g_base]])

        if self._warm_start is not None and len(self._warm_start) == 2 * n + 2:
            x0 = self._warm_start
        else:
            x0 = np.zeros(2 * n + 2)
            x0[2 * n] = 0.25                                     # home advantage
            x0[2 * n + 1] = np.log(max((w * (hg + ag)).sum() / (2 * total_w), 0.1))

        result = minimize(objective, x0, jac=True, method="L-BFGS-B",
                          options={"maxiter": 250, "ftol": 1e-10})
        theta = result.x
        self._warm_start = theta

        attack, defence, home_adv, base = self._unpack(theta, n)
        # Ridge already centres these, but pin it exactly so the parameters are
        # comparable across refits and readable on their own.
        attack = attack - attack.mean()
        defence = defence - defence.mean()
        self.attack, self.defence = attack, defence
        self.home_adv, self.base = float(home_adv), float(base)
        self.mle_converged = bool(result.success)
        self.mle_iterations = int(result.nit)

        # Expose the same dict interface the ratio estimator provides, so
        # callers that check membership ("is this team known?") keep working.
        teams = self.teams
        self.home_attack = {t: float(np.exp(attack[i])) for i, t in enumerate(teams)}
        self.away_attack = dict(self.home_attack)
        self.home_defense = {t: float(np.exp(-defence[i])) for i, t in enumerate(teams)}
        self.away_defense = dict(self.home_defense)
        self._index = {t: i for i, t in enumerate(teams)}
        return self

    @staticmethod
    def _tau(hg, ag, lam, mu, rho):
        """Dixon-Coles adjustment to the four lowest scorelines."""
        tau = np.ones_like(lam, dtype=float)
        tau = np.where((hg == 0) & (ag == 0), 1.0 - lam * mu * rho, tau)
        tau = np.where((hg == 0) & (ag == 1), 1.0 + lam * rho, tau)
        tau = np.where((hg == 1) & (ag == 0), 1.0 + mu * rho, tau)
        tau = np.where((hg == 1) & (ag == 1), 1.0 - rho, tau)
        return tau

    def _fit_rho(self, hg, ag, lam, mu, w):
        """MLE for rho. The Poisson part doesn't depend on rho, so only the
        tau term enters the likelihood — a cheap 1-D optimisation."""
        def neg_log_lik(rho):
            tau = self._tau(hg, ag, lam, mu, rho)
            if np.any(tau <= 0):
                return 1e6  # rho this extreme implies negative probabilities
            return -float((w * np.log(tau)).sum())

        result = minimize_scalar(neg_log_lik, bounds=(-0.2, 0.2), method="bounded")
        return float(result.x) if result.success else 0.0

    # -- prediction -------------------------------------------------------

    def expected_goals(self, home_team, away_team):
        if self.teams is None:
            raise ValueError("model is not fitted")

        if self.signal == "blend":
            k = self._blend_k
            gh, ga = self._goals_model.expected_goals(home_team, away_team)
            sh, sa = self._sot_model.expected_goals(home_team, away_team)
            # geometric mean: the models are multiplicative in log-lambda space
            return (float(gh ** (1 - k) * sh ** k),
                    float(ga ** (1 - k) * sa ** k))
        if home_team not in self.home_attack or away_team not in self.away_attack:
            raise ValueError(f"unknown team, not in training data: {home_team!r} / {away_team!r}")

        if self.method == "mle":
            h, a = self._index[home_team], self._index[away_team]
            exp_home = np.exp(self.base + self.home_adv + self.attack[h] - self.defence[a])
            exp_away = np.exp(self.base + self.attack[a] - self.defence[h])
            return float(exp_home * self.scale_home), float(exp_away * self.scale_away)

        exp_home = self.home_attack[home_team] * self.away_defense[away_team] * self.avg_home_goals
        exp_away = self.away_attack[away_team] * self.home_defense[home_team] * self.avg_away_goals
        return float(exp_home * self.scale_home), float(exp_away * self.scale_away)

    def score_matrix(self, home_team, away_team):
        """P(home scores i, away scores j) over the truncated goal grid."""
        exp_home, exp_away = self.expected_goals(home_team, away_team)
        goals = np.arange(0, self.max_goals + 1)
        matrix = np.outer(poisson.pmf(goals, exp_home), poisson.pmf(goals, exp_away))

        if self.dixon_coles and self.rho:
            block = matrix[: _DC_MAX + 1, : _DC_MAX + 1]
            i, j = np.meshgrid([0, 1], [0, 1], indexing="ij")
            adjust = self._tau(
                i.ravel(), j.ravel(),
                np.full(4, exp_home), np.full(4, exp_away), self.rho,
            ).reshape(2, 2)
            matrix[: _DC_MAX + 1, : _DC_MAX + 1] = block * adjust

        return matrix / matrix.sum()

    def predict_probabilities(self, home_team, away_team):
        """Returns (P_home_win, P_draw, P_away_win)."""
        matrix = self.score_matrix(home_team, away_team)
        p_home = float(np.tril(matrix, -1).sum())   # home goals > away goals
        p_draw = float(np.trace(matrix))
        p_away = float(np.triu(matrix, 1).sum())
        total = p_home + p_draw + p_away
        return p_home / total, p_draw / total, p_away / total

    def predict_over_under(self, home_team, away_team, line=2.5):
        """P(total goals > line). Useful once you add totals markets."""
        matrix = self.score_matrix(home_team, away_team)
        totals = np.add.outer(np.arange(matrix.shape[0]), np.arange(matrix.shape[1]))
        return float(matrix[totals > line].sum())

    def __repr__(self):
        extra = (f"home_adv={np.exp(self.home_adv):.3f}" if self.method == "mle"
                 else f"shrinkage={self.shrinkage_games}")
        return (f"PoissonModel({self.method}, teams={len(self.teams or [])}, "
                f"matches={self.n_matches}, rho={self.rho:+.4f}, "
                f"half_life={self.half_life_days}, {extra})")
