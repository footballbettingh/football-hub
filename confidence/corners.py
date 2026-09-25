"""What the closing line knows about corners that the corner model does not.

Corners are the one market here nobody quotes, so the corner model — team
strengths for corners won and conceded, shrunk — is the whole forecast. But the
line does say something about a match that bears on its corners: how open it
is expected to be. A match the market prices for four goals is played at a
different pitch from one it prices for two, and the corner strengths, which
are an average over a team's season, cannot see that.

So the corner expectation is scaled by what the market says, and nothing else:

    corners x exp(a * (log market goals - the league's usual log market goals)
                  + b * (log market goals - log model goals))

The first term is how open this match is against its league; the second, how
far the market has moved away from the goals model on it. Both are centred —
the first on its league, the second on nothing, the two models sharing a level
— so the adjustment averages out within each league and the corner model's
own level, refitted in `PoissonModel._refit_level`, is left where it is.

Fitted as a Poisson regression of total corners with the model's expectation
as the offset, on earlier matches only. Tested out of sample on three
different splits (fitted before July 2023, 2024 and 2025): the Brier score over
the corner lines fell by 0.00049, 0.00057 and 0.00059, with coefficients of
0.06-0.12 and 0.09-0.15. Through the walk-forward's own folds, calibrated as
the card is, it fell by 0.00066, and no other market moved. For scale,
shrinking the corner strengths bought 0.002.

The price of it is a little calibration: the anchor is refitted fold by fold,
and the per-line curves learned on earlier folds fit its later versions a
little less closely — the average line's gap went from 0.56 to 0.71 points.
The Brier score counts that cost and still comes out ahead.
"""

import numpy as np
from scipy.optimize import minimize

# Matches with a corner count and a market line below which the fit is left
# at no adjustment at all: two coefficients do not need many, but a handful
# of leagues' first months would put noise where there was none.
MIN_MATCHES = 2000

_NEEDED = ("corner_lam", "corner_mu", "lam_market", "mu_market", "lam_model", "mu_model")


class CornerAnchor:
    """The scaling, and what it was centred on."""

    def __init__(self, coefficients=(0.0, 0.0), centres=None, n=0):
        self.coefficients = np.asarray(coefficients, dtype=float)
        self.centres = dict(centres or {})
        self.n = int(n)

    @property
    def is_identity(self):
        return not np.any(self.coefficients)

    @classmethod
    def fit(cls, predictions, min_matches=MIN_MATCHES):
        """Fit on walk-forward rows that have a corner count and a market line."""
        if predictions is None or len(predictions) == 0 or any(
                column not in predictions for column in _NEEDED + ("total_corners",)):
            return cls()
        usable = predictions.dropna(subset=list(_NEEDED) + ["total_corners"])
        if len(usable) < min_matches:
            return cls(n=len(usable))

        log_goals = np.log((usable["lam_market"] + usable["mu_market"]).to_numpy(float))
        centres = (usable.assign(_g=log_goals).groupby("competition")["_g"].mean()
                   .to_dict())
        anchor = cls(centres=centres, n=len(usable))
        X = anchor._features(usable)
        offset = np.log((usable["corner_lam"] + usable["corner_mu"]).to_numpy(float))
        y = usable["total_corners"].to_numpy(float)

        def nll(beta):
            eta = offset + X @ beta
            return float(np.sum(np.exp(eta) - y * eta)), X.T @ (np.exp(eta) - y)

        anchor.coefficients = minimize(nll, np.zeros(2), jac=True, method="BFGS").x
        return anchor

    def _features(self, frame):
        log_market = np.log((frame["lam_market"] + frame["mu_market"]).to_numpy(float))
        log_model = np.log((frame["lam_model"] + frame["mu_model"]).to_numpy(float))
        usual = np.mean(list(self.centres.values())) if self.centres else 0.0
        centre = np.array([self.centres.get(c, usual) for c in frame["competition"]])
        return np.column_stack([log_market - centre, log_market - log_model])

    def factors(self, frame):
        """The multiplier on each row's corner expectation; 1 where there is no
        market line to read, or nothing was fitted."""
        out = np.ones(len(frame))
        if self.is_identity or len(frame) == 0:
            return out
        known = frame[list(_NEEDED[2:])].notna().all(axis=1).to_numpy()
        if known.any():
            out[known] = np.exp(self._features(frame[known]) @ self.coefficients)
        return out

    def factor(self, competition, lam_market, mu_market, lam_model, mu_model):
        """`factors` for one match — the card prices a fixture at a time."""
        import pandas as pd
        return float(self.factors(pd.DataFrame([{
            "competition": competition, "lam_market": lam_market, "mu_market": mu_market,
            "lam_model": lam_model, "mu_model": mu_model}]))[0])

    def adjust(self, predictions):
        """`predictions` with both corner lambdas scaled — a copy."""
        if self.is_identity or "corner_lam" not in predictions:
            return predictions
        out = predictions.copy()
        factor = self.factors(out)
        out["corner_lam"] = out["corner_lam"] * factor
        out["corner_mu"] = out["corner_mu"] * factor
        return out

    def to_dict(self):
        return {"coefficients": [round(float(c), 6) for c in self.coefficients],
                "centres": {k: round(float(v), 6) for k, v in self.centres.items()},
                "n": self.n}

    @classmethod
    def from_dict(cls, blob):
        if not blob:
            return cls()
        return cls(blob.get("coefficients", (0.0, 0.0)), blob.get("centres"),
                   blob.get("n", 0))
