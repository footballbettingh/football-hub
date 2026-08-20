"""Does an 80% pick win 80% of the time?

That is the only question this file answers, in a few different shapes. Rank
metrics (Brier, log loss) say whether the numbers are informative; the
reliability table says whether they are *true*, which is what a user reading
"87% confident" is actually relying on.

Every table here is computed on out-of-sample rows only. The interval on each
hit rate is Wilson, not normal-approximation, because the bands that matter
most are the ones near 0.95 where the normal interval runs past 1.0 and stops
meaning anything.
"""

import numpy as np
import pandas as pd

from .markets import GROUPS, group_of, label

# Finer at the top on purpose: a single 95-100% bucket averages a 95.1% pick
# with a 99.5% one, and the card is full of both. The band a pick lands in is
# what gets quoted back to the user as "what this confidence has been worth".
#
# They start at 0.30, not 0.50. Every selection has a complement, so measuring
# only the favoured half once covered everything — but the slate now singles
# out picks priced at 2.20-3.00, which are 33-45% shots, and clamping those
# into the 50-60% bucket quoted them a record belonging to bets twice as
# likely to land.
BANDS = (0.30, 0.40, 0.45, 0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95,
         0.97, 0.98, 0.99, 1.01)

# Ceilings are a statement about how high a market can be trusted, so the walk
# that finds them ignores the bands below this.
CEILING_FLOOR = 0.50


def brier(probs, outcomes):
    return float(np.mean((np.asarray(probs) - np.asarray(outcomes)) ** 2))


def log_loss(probs, outcomes):
    p = np.clip(np.asarray(probs, dtype=float), 1e-9, 1 - 1e-9)
    y = np.asarray(outcomes, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def ranked_probability_score(probs, outcome_index):
    """Mean RPS over ordered categories. Lower is better; 0 is perfect.

    Scored on the cumulative distribution rather than the outright one, which
    is what makes it care about *how far* a forecast missed by. `probs` rows
    must sum to 1 and be in outcome order; `outcome_index` says which category
    actually happened.
    """
    probs = np.asarray(probs, dtype=float)
    n, r = probs.shape
    actual = np.zeros_like(probs)
    actual[np.arange(n), np.asarray(outcome_index, dtype=int)] = 1.0
    gap = np.cumsum(probs, axis=1)[:, :-1] - np.cumsum(actual, axis=1)[:, :-1]
    return float(np.mean(np.sum(gap ** 2, axis=1) / (r - 1)))


def rps_1x2(predictions, keys, probs, results, rows=None):
    """RPS on 1X2, against the closing line and against knowing nothing.

    Brier reads home/draw/away as three unrelated coin flips: calling a home
    win when the away side wins costs it exactly what calling one when the
    match is drawn costs. Home-draw-away is ordered, so those two misses are
    not equally wrong, and RPS is the standard scoring rule that says so.

    Only 1X2 gets this. Every other market on the card is binary, and for two
    ordered categories RPS is algebraically the Brier score — a second column
    of identical numbers.

    Calibrating each selection on its own breaks the sum to one, so the triple
    is renormalised here. `renorm` reports how much that moved it: it is the
    same violation `coherence` measures, restated as a cost to this metric.
    """
    mask = np.ones(len(predictions), dtype=bool) if rows is None else np.asarray(rows)
    ordered = ("1x2_home", "1x2_draw", "1x2_away")
    if any(k not in keys for k in ordered):
        return pd.DataFrame()

    columns = [keys.index(k) for k in ordered]
    ours = probs[mask][:, columns].astype(float)
    outcome = results[mask][:, columns]
    market = predictions[["q_home", "q_draw", "q_away"]].to_numpy(dtype=float)[mask]

    # One and only one of the three must have happened, or the row tells us
    # nothing about which way to score it.
    # Parenthesised because `&` binds tighter than `==`: without them this
    # reads as (all_present & count) == 1, which happens to give the right
    # answer for three outcomes and would stop doing so for four.
    settled = (outcome >= 0).all(axis=1) & ((outcome == 1).sum(axis=1) == 1)
    valid = settled & np.isfinite(ours).all(axis=1) & np.isfinite(market).all(axis=1)
    if valid.sum() == 0:
        return pd.DataFrame()

    ours, market, outcome = ours[valid], market[valid], outcome[valid]
    happened = np.argmax(outcome == 1, axis=1)

    totals = ours.sum(axis=1, keepdims=True)
    renorm = float(np.mean(np.abs(totals - 1.0)))
    ours = ours / totals
    market = market / market.sum(axis=1, keepdims=True)
    uniform = np.full_like(ours, 1.0 / 3.0)

    n = int(valid.sum())
    return pd.DataFrame([
        {"forecast": "Ours (calibrated)", "n": n,
         "rps": ranked_probability_score(ours, happened), "renorm": renorm},
        {"forecast": "Closing line", "n": n,
         "rps": ranked_probability_score(market, happened), "renorm": 0.0},
        {"forecast": "Knowing nothing", "n": n,
         "rps": ranked_probability_score(uniform, happened), "renorm": 0.0},
    ])


def wilson(successes, n, z=1.96):
    """Confidence interval for a hit rate. Behaves at n small and p near 1."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = successes / n
    denom = 1 + z ** 2 / n
    centre = (p + z ** 2 / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _flatten(keys, probs, results, rows=None, groups=None):
    """Every (probability, outcome) pair, dropping voids and missing prices."""
    columns = [i for i, k in enumerate(keys)
               if groups is None or group_of(k) in groups]
    if not columns:
        return np.array([]), np.array([])
    rows = slice(None) if rows is None else rows
    p = probs[rows][:, columns].ravel()
    r = results[rows][:, columns].ravel()
    valid = (r >= 0) & np.isfinite(p)
    return p[valid], r[valid].astype(float)


def reliability(keys, probs, results, rows=None, groups=None, bands=BANDS):
    """Predicted vs actual, bucketed by confidence."""
    p, y = _flatten(keys, probs, results, rows, groups)
    out = []
    for lo, hi in zip(bands, bands[1:]):
        pick = (p >= lo) & (p < hi)
        n = int(pick.sum())
        if n == 0:
            continue
        hits = float(y[pick].sum())
        low, high = wilson(hits, n)
        out.append({
            "band": f"{lo:.0%}-{min(hi, 1.0):.0%}",
            "band_low": lo,
            "band_high": min(hi, 1.0),
            "n": n,
            "predicted": float(p[pick].mean()),
            "actual": hits / n,
            "gap": hits / n - float(p[pick].mean()),
            "ci_low": low,
            "ci_high": high,
        })
    return pd.DataFrame(out)


def group_summary(keys, probs, results, rows=None, threshold=0.75):
    """Brier, log loss and top-band accuracy for every market group."""
    out = []
    for group in sorted({group_of(k) for k in keys}):
        p, y = _flatten(keys, probs, results, rows, [group])
        if len(p) == 0:
            continue
        top = p >= threshold
        hits = float(y[top].sum())
        low, high = wilson(hits, int(top.sum())) if top.any() else (np.nan, np.nan)
        out.append({
            "group": GROUPS.get(group, group),
            "n": len(p),
            "brier": brier(p, y),
            "log_loss": log_loss(p, y),
            "ece": expected_calibration_error(p, y),
            f"n>={threshold:.0%}": int(top.sum()),
            "predicted": float(p[top].mean()) if top.any() else np.nan,
            "actual": hits / max(int(top.sum()), 1) if top.any() else np.nan,
            "ci_low": low,
            "ci_high": high,
        })
    return pd.DataFrame(out)


def expected_calibration_error(probs, outcomes, n_bins=20):
    """Average |predicted - actual| over equal-count bins, weighted by size."""
    probs, outcomes = np.asarray(probs), np.asarray(outcomes)
    if len(probs) < n_bins * 5:
        return float("nan")
    edges = np.unique(np.quantile(probs, np.linspace(0, 1, n_bins + 1)))
    slot = np.clip(np.digitize(probs, edges[1:-1]), 0, len(edges) - 2)
    counts = np.bincount(slot, minlength=len(edges) - 1)
    sum_p = np.bincount(slot, probs, len(edges) - 1)
    sum_y = np.bincount(slot, outcomes, len(edges) - 1)
    used = counts > 0
    gaps = np.abs(sum_p[used] / counts[used] - sum_y[used] / counts[used])
    return float((gaps * counts[used]).sum() / counts.sum())


def per_selection(keys, probs, results, rows=None, threshold=0.75):
    """One row per selection type — which bets are worth showing at all."""
    rows = slice(None) if rows is None else rows
    out = []
    for i, key in enumerate(keys):
        p = probs[rows][:, i]
        r = results[rows][:, i]
        valid = (r >= 0) & np.isfinite(p)
        if valid.sum() == 0:
            continue
        p, y = p[valid], r[valid].astype(float)
        top = p >= threshold
        hits = float(y[top].sum())
        low, high = wilson(hits, int(top.sum())) if top.any() else (np.nan, np.nan)
        out.append({
            "key": key,
            "selection": label(key),
            "n": int(valid.sum()),
            "base_rate": float(y.mean()),
            "brier": brier(p, y),
            "n_top": int(top.sum()),
            "predicted": float(p[top].mean()) if top.any() else np.nan,
            "actual": hits / int(top.sum()) if top.any() else np.nan,
            "ci_low": low,
            "ci_high": high,
        })
    return pd.DataFrame(out)


def versus_market(predictions, keys, probs, results, rows=None):
    """Our number against the de-vigged closing line, on its own markets.

    The comparison is nearly circular by design — the closing line is the main
    input — so a large win here would be a bug, not a triumph. What it is
    really testing is whether fusing in the model and calibrating afterwards
    *damages* the market's own forecast.
    """
    mask = np.ones(len(predictions), dtype=bool) if rows is None else np.asarray(rows)
    pairs = [("1x2_home", "q_home"), ("1x2_draw", "q_draw"),
             ("1x2_away", "q_away"), ("ou2.5_over", "q_over")]
    out = []
    for key, column in pairs:
        i = keys.index(key)
        market = predictions[column].to_numpy(dtype=float)[mask]
        ours = probs[mask][:, i]
        outcome = results[mask][:, i].astype(float)
        valid = np.isfinite(market) & np.isfinite(ours) & (results[mask][:, i] >= 0)
        if valid.sum() == 0:
            continue
        out.append({
            "selection": label(key),
            "n": int(valid.sum()),
            "brier_ours": brier(ours[valid], outcome[valid]),
            "brier_market": brier(market[valid], outcome[valid]),
            "ece_ours": expected_calibration_error(ours[valid], outcome[valid]),
            "ece_market": expected_calibration_error(market[valid], outcome[valid]),
        })
    frame = pd.DataFrame(out)
    if not frame.empty:
        frame["delta"] = frame["brier_ours"] - frame["brier_market"]
    return frame


def coherence(keys, probs):
    """How far calibration pushes the card away from internal consistency.

    Everything starts from one score matrix, so P(1X) equals P(1) + P(X)
    exactly. Calibrating each group separately can break that. The violation
    should be tiny; if it ever is not, the calibrators are doing something
    other than fixing a level.
    """
    index = {k: i for i, k in enumerate(keys)}
    checks = {
        "1X = 1 + X": (["dc_1x"], ["1x2_home", "1x2_draw"]),
        "12 = 1 + 2": (["dc_12"], ["1x2_home", "1x2_away"]),
        "X2 = X + 2": (["dc_x2"], ["1x2_draw", "1x2_away"]),
        "1 + X + 2 = 1": (["1x2_home", "1x2_draw", "1x2_away"], []),
        "over/under 2.5 = 1": (["ou2.5_over", "ou2.5_under"], []),
    }
    out = []
    for name, (left, right) in checks.items():
        lhs = sum(probs[:, index[k]] for k in left)
        rhs = sum(probs[:, index[k]] for k in right) if right else 1.0
        gap = np.abs(lhs - rhs)
        gap = gap[np.isfinite(gap)]
        if len(gap) == 0:
            continue
        out.append({"identity": name, "mean_abs_gap": float(gap.mean()),
                    "p99": float(np.quantile(gap, 0.99)),
                    "max": float(gap.max())})
    return pd.DataFrame(out)
