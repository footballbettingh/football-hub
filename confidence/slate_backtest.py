"""The slate, chosen again over the whole history, the way it would have been.

The ledger is the only record of the picks themselves, and it grows by nine a
day: at that pace a claim about how the slate behaves — that the tie-break is
worth a point and a quarter, that the value band is as honest as the safe one
— takes years to settle on it. The history can settle it now, provided the
replay keeps to time as strictly as the ledger does.

So it goes a month at a time. For each month, everything the live card leans
on is rebuilt from the months before it and nothing else: the calibrators from
their raw forecasts, and the band records, market ceilings and tie-break
factors from their calibrated ones — which were themselves calibrated only on
what came before them. Then the month's matches are calibrated and handed, a
day at a time, to the very functions that choose the live slate. A month's
results cannot reach its own picks, and a test holds it to that.

What it cannot replay is the price. The history carries closing prices and
the card is priced days earlier, so this measures the selection on the best
information there was; how much the earlier price costs is a separate
question, and the ledger's to answer.
"""

import numpy as np
import pandas as pd

from . import config, evaluate, picks, predict
from .calibrate import Calibrators
from .markets import group_of, label

# Months of history kept back before anything is calibrated, as a share of all
# matches — the same fifth the live walk-forward leaves unscored.
WARMUP = 0.2

# Out-of-sample matches a month needs behind it before its picks are chosen.
# Below this the band records are thin enough that most bands fall under the
# 200 bets `picks.MIN_BAND_SAMPLE` asks for, and the replay would be choosing
# on a rule the live card never runs.
MIN_SCORED = 5000


def replay(predictions, weight=None, tiebreak=None, competitions=None,
           warmup=WARMUP, min_scored=MIN_SCORED, progress=print):
    """Every day's slate, chosen out of sample. One row per pick.

    `predictions` is predictions.csv. `tiebreak` defaults to what the live
    card does (`config.PICK_TIEBREAK`); set, it chooses with or without the
    per-selection price record, so the two can be compared on the same days.
    `competitions` limits which leagues' matches are candidates — the live
    card can only price the ones a feed quotes — while the calibration and
    the records behind it are built on every league, as they are live.
    """
    weight = config.MARKET_WEIGHT if weight is None else weight
    tiebreak = config.PICK_TIEBREAK if tiebreak is None else tiebreak
    predictions = predictions.reset_index(drop=True)
    keys, probs, results = predict.build_arrays(predictions, weight)
    dates = pd.to_datetime(predictions["date"])
    months = dates.dt.to_period("M").to_numpy()
    eligible = (predictions["competition"].isin(competitions).to_numpy()
                if competitions is not None else np.ones(len(predictions), bool))

    calibrated = np.full(probs.shape, np.nan)
    scored = np.zeros(len(predictions), dtype=bool)
    chosen = []
    for month in sorted(set(months)):
        rows = months == month
        earlier = dates.to_numpy() < month.start_time.to_datetime64()
        if earlier.sum() < warmup * len(predictions):
            continue
        calibrators = Calibrators.fit(keys, probs, results, earlier)
        calibrated[rows] = calibrators.apply(keys, probs[rows])

        behind = scored & earlier
        if behind.sum() >= min_scored:
            reliability = evaluate.reliability_by_scope(keys, calibrated, results, behind)
            factors = (evaluate.key_price_factors(
                keys, calibrated, results, behind,
                step=config.PICK_FACTOR_STEP, min_n=config.PICK_FACTOR_MIN_N)
                if tiebreak else None)
            month_picks = _choose(predictions, keys, calibrated, results,
                                  rows & eligible, reliability, factors)
            chosen.extend(month_picks)
            if progress:
                progress(f"  {month}: {len(month_picks):>3} picks")
        scored |= rows

    columns = ["date", "band", "competition", "match", "key", "group", "selection",
               "prob", "fair_odds", "hit_rate", "result"]
    return pd.DataFrame(chosen, columns=columns)


def _choose(predictions, keys, calibrated, results, rows, reliability, factors):
    """One month's picks, chosen a day at a time by the live code."""
    index = np.flatnonzero(rows)
    if len(index) == 0:
        return []
    probs, outcomes = calibrated[index], results[index]
    low = min(lo for lo, _ in config.PICK_BANDS.values())
    high = max(hi for _, hi in config.PICK_BANDS.values())
    # Only what a band could take: the rest can never be chosen, and leaving
    # it in only slows every ranking down.
    with np.errstate(invalid="ignore"):
        inside = np.isfinite(probs) & (probs >= 1.0 / high) & (probs <= 1.0 / low)
    match_no, key_no = np.nonzero(inside)
    if len(match_no) == 0:
        return []

    sub = predictions.iloc[index[match_no]]
    key_names = np.asarray(keys)[key_no]
    prob = probs[match_no, key_no]
    frame = pd.DataFrame({
        "date": pd.to_datetime(sub["date"]).to_numpy(),
        "competition": sub["competition"].to_numpy(),
        "home": sub["home"].to_numpy(),
        "away": sub["away"].to_numpy(),
        "match": (sub["home"].astype(str) + " v " + sub["away"].astype(str)).to_numpy(),
        "key": key_names,
        "group": [group_of(k) for k in key_names],
        "selection": [label(k) for k in key_names],
        "prob": prob,
        "fair_odds": 1.0 / np.clip(prob, 1e-6, None),
        "odds": np.nan,
        "implied_resid": (pd.to_numeric(sub["implied_resid"], errors="coerce").to_numpy()
                          if "implied_resid" in sub.columns else np.nan),
        "result": outcomes[match_no, key_no],
    })
    frame = picks.attach_hit_rates(frame, reliability, factors)

    out = []
    for _, day in frame.groupby("date", sort=True):
        result_of = {(m, k): r for m, k, r in zip(day["match"], day["key"], day["result"])}
        for pick in picks.daily_slate(day, days=1):
            out.append({
                "date": pd.Timestamp(pick["date"]).strftime("%Y-%m-%d"),
                "band": pick["band"], "competition": pick["competition"],
                "match": pick["match"], "key": pick["key"], "group": pick["group"],
                "selection": pick["selection"], "prob": float(pick["prob"]),
                "fair_odds": float(pick["fair_odds"]), "hit_rate": pick.get("hit_rate"),
                "result": int(result_of[(pick["match"], pick["key"])]),
            })
    return out


def summary(chosen, by="band"):
    """Claimed against landed, for each value of `by` and for all of them.

    `result` is 1 won, 0 lost, -1 void — a draw-no-bet on a draw — and a void
    is out of the record, as it is in the ledger.
    """
    rows = []
    present = set(chosen[by]) if len(chosen) else set()
    names = ([b for b in config.BAND_ORDER if b in present] if by == "band"
             else sorted(present))
    for name in names + ["all"]:
        block = chosen if name == "all" else chosen[chosen[by] == name]
        decided = block[block["result"] >= 0]
        if decided.empty:
            continue
        claimed, landed = decided["prob"].to_numpy(), decided["result"].to_numpy(float)
        low, high = evaluate.wilson(landed.sum(), len(landed))
        rows.append({
            by: name, "picks": len(decided), "void": int((block["result"] < 0).sum()),
            "fair_odds": float(decided["fair_odds"].mean()),
            "claimed": float(claimed.mean()), "landed": float(landed.mean()),
            "gap_pp": float((landed.mean() - claimed.mean()) * 100),
            "ci_low": low, "ci_high": high,
            "z": evaluate.calibration_z(claimed, landed),
            "brier": evaluate.brier(claimed, landed),
        })
    return pd.DataFrame(rows)
