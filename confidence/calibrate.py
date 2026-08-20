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

One calibrator per market group, not per selection. `ou0.5_under` only ever
takes values near 0.07, so on its own it can only ever learn a curve for that
sliver; pooling the group covers the whole range and quadruples the sample.

Fitted on a market that is already well calibrated, isotonic does almost
nothing — which is the expected result for anything anchored to a closing
line, and worth checking rather than assuming.
"""

import json

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
        counts, mean_x, mean_y = counts[used], mean_x[used] / counts[used], mean_y[used] / counts[used]

        target = (counts * mean_y + shrink * mean_x) / (counts + shrink)
        fitted = _pava(target, counts + shrink)
        return cls(mean_x, np.clip(fitted, EPS, 1 - EPS), n=len(probs))

    def __call__(self, probs):
        probs = np.asarray(probs, dtype=float)
        if self.is_identity:
            return probs
        out = np.interp(probs, self.x, self.y)
        # np.interp clamps outside the fitted range, which would flatten the
        # very top of the card — exactly where the picks live. Extend the last
        # segment's behaviour instead by keeping the raw value's excess.
        below = probs < self.x[0]
        above = probs > self.x[-1]
        out = np.where(below, np.minimum(probs, self.y[0]), out)
        out = np.where(above, np.maximum(probs, self.y[-1]), out)
        return np.clip(out, EPS, 1 - EPS)

    def to_dict(self):
        return {"x": [round(v, 6) for v in self.x],
                "y": [round(v, 6) for v in self.y], "n": self.n}

    @classmethod
    def from_dict(cls, blob):
        return cls(blob["x"], blob["y"], blob.get("n", 0))


class Calibrators:
    """One isotonic map per market group, plus a global fallback."""

    def __init__(self, by_group=None, meta=None):
        self.by_group = by_group or {}
        self.meta = meta or {}

    @classmethod
    def fit(cls, keys, probs, results, mask=None, n_bins=DEFAULT_BINS,
            min_samples=MIN_SAMPLES, meta=None):
        """`probs` and `results` are the [match, selection] arrays; `mask`
        restricts the fit to rows whose result was known in time."""
        groups = {}
        rows = np.ones(len(probs), dtype=bool) if mask is None else np.asarray(mask)
        for group in sorted({group_of(k) for k in keys}):
            columns = [i for i, k in enumerate(keys) if group_of(k) == group]
            p = probs[np.ix_(rows, columns)].ravel()
            r = results[np.ix_(rows, columns)].ravel().astype(float)
            valid = (r >= 0) & np.isfinite(p)
            groups[group] = Isotonic.fit(p[valid], r[valid], n_bins, min_samples)
        return cls(groups, meta)

    def apply(self, keys, probs):
        out = np.array(probs, dtype=np.float64, copy=True)
        for group, calibrator in self.by_group.items():
            columns = [i for i, k in enumerate(keys) if group_of(k) == group]
            if not columns:
                continue
            block = out[:, columns]
            finite = np.isfinite(block)
            block[finite] = calibrator(block[finite])
            out[:, columns] = block
        return out

    def save(self, path):
        blob = {"meta": self.meta,
                "groups": {g: c.to_dict() for g, c in self.by_group.items()}}
        path.write_text(json.dumps(blob, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path):
        blob = json.loads(path.read_text(encoding="utf-8"))
        return cls({g: Isotonic.from_dict(c) for g, c in blob["groups"].items()},
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
