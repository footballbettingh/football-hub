"""Isotonic calibration — making "80%" mean 80%.

A probability that ranks bets correctly can still be wrong in level: a model
that says 85% whenever the truth is 78% sorts its picks perfectly and misleads
you about every one of them. Since the entire product here is a number the user
is meant to *trust*, level matters more than rank.

Isotonic regression is the right tool: it is the monotone step function that
best fits observed outcomes, so it can fix any systematic over- or
under-statement without being told what shape the error has, and it cannot
reorder the picks. The cost is that it can overfit, which is why:

* it is fitted on binned quantiles (a few hundred knots, not 60,000), and
* every number reported in the evaluation comes from a calibrator fitted on
  matches that finished BEFORE the ones it scores.

One calibrator per line, learned on one side of it — see `Calibrators` and
`calibration_scope`. It was one per market group, which pooled lines that err
in opposite directions. Out of sample over the walk-forward, the average gap
between a line's claim and its record fell from 0.57 to 0.17 points on the goal
totals, 0.68 to 0.20 on team totals, 0.54 to 0.21 on handicaps and 2.43 to 0.53
on both-teams-to-score, with the Brier score level or better everywhere.

Fitted on a market that is already well calibrated, isotonic does almost
nothing — which is the expected result for anything anchored to a closing
line, and worth checking rather than assuming.
"""

import json
import re

import numpy as np

from .markets import group_of

MIN_SAMPLES = 2000        # below this a group keeps the identity map
DEFAULT_BINS = 150
EPS = 1e-4

# Pseudo-observations pulling each knot back toward "the input was already
# right". Plausible — the market is close to calibrated, so the identity is a
# reasonable prior — and measured to be a bad trade. Fitting a distorted
# forecast and scoring on held-out draws:
#
#     n        shrink   calibration error   noise added to an
#                       left after fixing   already-calibrated input
#     200,000       0   0.0042              0.0051
#     200,000     200   0.0114              0.0046
#      20,000       0   0.0102              0.0117
#      20,000     200   0.0415              0.0057
#
# Shrinking costs three times more correction than it saves in noise at every
# sample size tried. Kept as a knob because the experiment is worth being able
# to repeat, and defaulted off because it lost.
SHRINK = 0


def _pava(y, w):
    """Pool-adjacent-violators: the weighted isotonic fit of y."""
    y = np.asarray(y, dtype=float).copy()
    w = np.asarray(w, dtype=float).copy()
    n = len(y)
    # Each block is (weighted mean, total weight, count of original points).
    values, weights, sizes = [], [], []
    for i in range(n):
        value, weight, size = y[i], w[i], 1
        while values and values[-1] > value:
            prev_v, prev_w, prev_s = values.pop(), weights.pop(), sizes.pop()
            total = prev_w + weight
            value = (prev_v * prev_w + value * weight) / total
            weight, size = total, prev_s + size
        values.append(value)
        weights.append(weight)
        sizes.append(size)
    return np.repeat(values, sizes)


class Isotonic:
    """A monotone map from raw probability to calibrated probability."""

    def __init__(self, x=None, y=None, n=0):
        self.x = np.asarray([0.0, 1.0] if x is None else x, dtype=float)
        self.y = np.asarray([0.0, 1.0] if y is None else y, dtype=float)
        self.n = int(n)

    @property
    def is_identity(self):
        return len(self.x) == 2 and self.x[0] == 0.0 and self.y[0] == 0.0

    @classmethod
    def fit(cls, probs, outcomes, n_bins=DEFAULT_BINS, min_samples=MIN_SAMPLES,
            shrink=SHRINK):
        probs = np.asarray(probs, dtype=float)
        outcomes = np.asarray(outcomes, dtype=float)
        keep = np.isfinite(probs) & np.isfinite(outcomes)
        probs, outcomes = probs[keep], outcomes[keep]
        if len(probs) < min_samples:
            return cls(n=len(probs))

        # Quantile bins, so every knot rests on a comparable amount of
        # evidence instead of the busiest part of the range dominating.
        edges = np.unique(np.quantile(probs, np.linspace(0, 1, n_bins + 1)))
        if len(edges) < 3:
            return cls(n=len(probs))
        slot = np.clip(np.digitize(probs, edges[1:-1]), 0, len(edges) - 2)

        counts = np.bincount(slot, minlength=len(edges) - 1)
        mean_x = np.bincount(slot, probs, len(edges) - 1)
        mean_y = np.bincount(slot, outcomes, len(edges) - 1)
        used = counts > 0
        counts, mean_x, mean_y = (counts[used], mean_x[used] / counts[used],
                                  mean_y[used] / counts[used])

        target = (counts * mean_y + shrink * mean_x) / (counts + shrink)
        fitted = _pava(target, counts + shrink)
        return cls(mean_x, np.clip(fitted, EPS, 1 - EPS), n=len(probs))

    def __call__(self, probs):
        probs = np.asarray(probs, dtype=float)
        if self.is_identity:
            return probs
        out = np.interp(probs, self.x, self.y)
        # np.interp clamps outside the fitted range, which would flatten the
        # very top of the card — exactly where the picks live. Past the last
        # knot there is no evidence, so the rule is the one-sided one the
        # ceilings use: the curve may go on pulling an overstated claim in,
        # but never pushes a claim further out than both the raw value and the
        # last fitted level.
        #
        # Pulling in, it runs in a straight line from the last knot to (1, 1).
        # It used to hand the raw value straight back, which was a step: the
        # BTTS top knot takes 0.709 down to 0.644, so a raw 0.70 read as 64%
        # and a raw 0.72 as 72% — the correction switched off for exactly the
        # most extreme claims of the market that overstates itself most. Out
        # of sample, past the end knots, BTTS said 73.7% and landed 62.9%;
        # the line says 66.5%. Corners went from 88.5% said, 82.6% landed, to
        # 86.5%.
        #
        # Pushing out, it keeps what it did: the last level, then the raw
        # value once that is higher. The same line there would add a point and
        # a half the 1X2 favourites past the knot never earned — they landed
        # 89.8% against 89.8% claimed as it was. Mirrored below the first knot.
        x0, y0, x1, y1 = self.x[0], self.y[0], self.x[-1], self.y[-1]
        if x1 < 1.0:
            to_one = y1 + (probs - x1) * (1.0 - y1) / (1.0 - x1)
            out = np.where(probs > x1,
                           np.minimum(to_one, np.maximum(probs, y1)), out)
        if x0 > 0.0:
            to_zero = probs * y0 / x0
            out = np.where(probs < x0,
                           np.maximum(to_zero, np.minimum(probs, y0)), out)
        return np.clip(out, EPS, 1 - EPS)

    def to_dict(self):
        return {"x": [round(v, 6) for v in self.x],
                "y": [round(v, 6) for v in self.y], "n": self.n}

    @classmethod
    def from_dict(cls, blob):
        return cls(blob["x"], blob["y"], blob.get("n", 0))


# Selections whose probabilities always add to one with a partner's. Each pair
# gets one curve, learned on the first of the two; the second is one minus it.
_PAIRED = re.compile(r"(ou[\d.]+|tt[\d.]+_(?:home|away)|corners[\d.]+)_(over|under)")
_PAIRS = {
    "btts_yes": ("btts", False), "btts_no": ("btts", True),
    "dnb_home": ("dnb", False), "dnb_away": ("dnb", True),
    # -1.5 for one side is exactly the complement of +1.5 for the other.
    "hcp_home-1.5": ("hcp_home-1.5", False), "hcp_away+1.5": ("hcp_home-1.5", True),
    "hcp_away-1.5": ("hcp_away-1.5", False), "hcp_home+1.5": ("hcp_away-1.5", True),
}


def calibration_scope(key):
    """(scope, flipped): which curve calibrates a selection, and whether from
    the other side of it.

    A line and its complement share one curve: `ou2.5_under` is calibrated as
    one minus the curve of `ou2.5_over`. Selections with no single partner —
    the three results, the three double chances — share one curve per market,
    as every market did before.
    """
    match = _PAIRED.fullmatch(key)
    if match:
        return match.group(1), match.group(2) == "under"
    return _PAIRS.get(key, (group_of(key), False))


class Calibrators:
    """One isotonic map per line — or per market, where a market has no lines.

    It was one per market group, pooling every line and both sides of each:
    a 0.60 on over 8.5 corners and a 0.60 on under 10.5 went through the same
    curve. A monotone curve can only move a probability one way, so wherever
    the lines of a market err in opposite directions — corners do, being more
    spread out than a Poisson allows, which leaves the low lines' overs too
    high and the high lines' too low — the pooled curve can mend neither. One
    curve per line can, and learning it on one side and deriving the other as
    its complement keeps over and under summing to one, as the pooled curve
    did by being symmetric. A line has a probability on every match, so the
    sample is the whole history rather than a sliver of it.
    """

    def __init__(self, by_scope=None, meta=None):
        self.by_scope = by_scope or {}
        self.meta = meta or {}

    @classmethod
    def fit(cls, keys, probs, results, mask=None, n_bins=DEFAULT_BINS,
            min_samples=MIN_SAMPLES, meta=None):
        """`probs` and `results` are the [match, selection] arrays; `mask`
        restricts the fit to rows whose result was known in time."""
        rows = np.ones(len(probs), dtype=bool) if mask is None else np.asarray(mask)
        columns = {}
        for index, key in enumerate(keys):
            scope, flipped = calibration_scope(key)
            columns.setdefault(scope, [])
            if not flipped:
                columns[scope].append(index)
        scopes = {}
        for scope, own in sorted(columns.items()):
            p = probs[np.ix_(rows, own)].ravel()
            r = results[np.ix_(rows, own)].ravel().astype(float)
            valid = (r >= 0) & np.isfinite(p)
            scopes[scope] = Isotonic.fit(p[valid], r[valid], n_bins, min_samples)
        return cls(scopes, meta)

    def calibrate(self, key, probs):
        """Calibrated probabilities for one selection, or the raw ones where
        nothing was fitted for it."""
        probs = np.asarray(probs, dtype=float)
        scope, flipped = calibration_scope(key)
        curve = self.by_scope.get(scope)
        if curve is None:
            # A calibration file written before the curves went per line holds
            # one per market group, applied to each selection as it stands.
            curve = self.by_scope.get(group_of(key))
            return probs if curve is None else curve(probs)
        return 1.0 - curve(1.0 - probs) if flipped else curve(probs)

    def apply(self, keys, probs):
        out = np.array(probs, dtype=np.float64, copy=True)
        for index, key in enumerate(keys):
            column = out[:, index]
            finite = np.isfinite(column)
            if finite.any():
                column[finite] = self.calibrate(key, column[finite])
        return out

    def to_json(self):
        return json.dumps({"meta": self.meta,
                           "scopes": {s: c.to_dict() for s, c in self.by_scope.items()}},
                          indent=1)

    def save(self, path):
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path):
        blob = json.loads(path.read_text(encoding="utf-8"))
        stored = blob.get("scopes", blob.get("groups", {}))
        return cls({s: Isotonic.from_dict(c) for s, c in stored.items()},
                   blob.get("meta", {}))


def walk_forward(keys, probs, results, dates, n_folds=5, n_bins=DEFAULT_BINS,
                 min_samples=MIN_SAMPLES):
    """Calibrate each chronological fold using only earlier folds.

    Returns (calibrated_probs, scored_mask). The first fold has nothing to
    learn from, so it is left raw and excluded from `scored_mask` — reporting
    it would quietly credit the calibrator with matches it never saw.
    """
    dates = np.asarray(dates)
    order = np.argsort(dates, kind="stable")
    folds = np.array_split(order, n_folds)

    calibrated = np.array(probs, dtype=np.float64, copy=True)
    scored = np.zeros(len(probs), dtype=bool)
    for index in range(1, len(folds)):
        train = np.concatenate(folds[:index])
        mask = np.zeros(len(probs), dtype=bool)
        mask[train] = True
        fitted = Calibrators.fit(keys, probs, results, mask, n_bins, min_samples)
        block = fitted.apply(keys, probs[folds[index]])
        calibrated[folds[index]] = block
        scored[folds[index]] = True
    return calibrated, scored
