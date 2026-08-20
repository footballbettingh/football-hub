"""The pipeline steps, in one place, callable from both the CLI and the buttons.

Each function takes a `progress` callable and returns a short dict for the job
log. Nothing here prints directly to stdout on its own behalf — the job runner
captures whatever the libraries print anyway, and the summaries these return are
what ends up in status.json.
"""

import json
import time

import pandas as pd

from confidence import config as cf_config, data as cf_data, evaluate, predict
from confidence.calibrate import Calibrators, walk_forward
from confidence.walkforward import run as walk_forward_run
from valuebets import config as vb_config
from valuebets.sources import football_data_uk, football_data_world

from . import leagues

LEAGUE_PLAN = vb_config.DATA_DIR / "leagues.json"

# How many completed seasons to keep behind the current one.
SEASONS_BACK = 5


def season_years(today=None, back=SEASONS_BACK):
    """European season start years to fetch, ending at the live one.

    A hard-coded list stops working in July. It did: the window ended at
    2025/26 while the 2026/27 season was already being played, so no file for
    it was ever downloaded and no result from it could ever arrive — every pick
    on a European league sat pending forever with nothing to say why.
    """
    stamp = pd.Timestamp(today) if today is not None else pd.Timestamp.today()
    current = stamp.year if stamp.month >= 7 else stamp.year - 1
    return tuple(range(current - back, current + 1))


def _ensure():
    cf_config.ensure_dirs()
    vb_config.ensure_dirs()


# -- fetching --------------------------------------------------------------

def _uk_season_files(seasons, use_cache, progress):
    """One call into the source, surviving a season that is not published yet."""
    try:
        return football_data_uk.fetch(football_data_uk.ALL_DIVISIONS,
                                      list(seasons), use_cache=use_cache)
    except SystemExit as exc:
        progress(f"  nothing for {list(seasons)}: {exc}")
        return None


def fetch_results(progress=print, seasons=None, include_world=True,
                  refresh_all=False):
    """Results + closing odds from football-data.co.uk. Free, no API key.

    The caching policy is the whole point of this function. A finished season's
    file never changes, so re-parsing it from `data/raw/` is right. The season
    being played changes every week, and the extra-country files change every
    day — reading those from cache means the job runs, reports thousands of
    matches, rewrites history.csv byte for byte, and brings back nothing. Which
    is exactly what it did.

    The two sources are concatenated rather than merged: the extra countries
    carry 1X2 only, no totals and no shots, and the model already treats a
    missing price as a market it cannot use.
    """
    _ensure()
    out = vb_config.DATA_DIR / "history.csv"
    seasons = list(seasons or season_years())
    past, current = seasons[:-1], seasons[-1]

    frames = []
    if past and not refresh_all:
        progress(f"Finished seasons {past[0]}-{past[-1]} (cached — they cannot change)")
        frames.append(_uk_season_files(past, True, progress))
    elif past:
        progress(f"Finished seasons {past[0]}-{past[-1]} (re-downloading all)")
        frames.append(_uk_season_files(past, False, progress))

    progress(f"Current season {current}/{(current + 1) % 100:02d} — downloading fresh")
    frames.append(_uk_season_files([current], False, progress))

    if include_world:
        progress("Extra countries — downloading fresh (calendar-year files)")
        frames.append(football_data_world.fetch(None, since=seasons[0],
                                                use_cache=False))

    frames = [f for f in frames if f is not None and len(f)]
    if not frames:
        raise SystemExit("Nothing fetched — check the connection.")
    frame = pd.concat(frames, ignore_index=True)

    frame = (frame.drop_duplicates(subset=["date", "home_key", "away_key"])
                  .sort_values("date"))
    frame.to_csv(out, index=False)
    progress(f"Saved {len(frame):,} matches to {out}")

    # New results are exactly what a pending pick is waiting for. Settling only
    # when the card is rebuilt would leave the record stale for anyone who
    # fetches results and reads the History page.
    from . import ledger
    history = cf_data.load_history()
    graded = ledger.settle(history) + ledger.settle_accas(history)
    if graded:
        progress(f"Settled {graded} pending bet(s) against the new results")

    return {"matches": int(len(frame)),
            "competitions": int(frame["competition"].nunique()),
            "last": str(frame["date"].max())[:10]}


def sports_tracked():
    """Which Odds API sports this installation already has a file for."""
    return sorted(p.stem[len("odds_"):] for p in vb_config.DATA_DIR.glob("odds_*.csv"))


def discover_leagues(progress=print):
    """Which mapped leagues are in season right now. Costs ZERO credits.

    `/sports` is free, so the expensive question — "what would a full refresh
    cost?" — can be answered before spending anything. The plan lands in
    leagues.json and `fetch_odds` follows it.
    """
    _ensure()
    from valuebets.sources import odds_api

    live = {s["key"]: s for s in odds_api.list_sports() if s.get("group") == "Soccer"}
    progress(f"The Odds API has {len(live)} soccer leagues in season right now.")

    history = pd.read_csv(vb_config.DATA_DIR / "history.csv", usecols=["competition"])
    have_history = set(history["competition"].unique())
    tracked = set(sports_tracked())

    plan, missing = [], []
    for code, sport in sorted(leagues.SPORT_KEYS.items()):
        if code not in have_history:
            continue                     # nothing to price it against
        if sport not in live:
            missing.append((code, sport))
            continue
        plan.append({"code": code, "sport": sport, "name": leagues.label(code),
                     "tracked": sport in tracked})

    for entry in plan:
        mark = "tracked" if entry["tracked"] else "NEW"
        progress(f"  {entry['code']:14} {entry['name']:32} {mark}")
    if missing:
        progress(f"Out of season or unmapped ({len(missing)}): "
                 + ", ".join(code for code, _ in missing))

    unknown = sorted(set(live) - set(leagues.BY_SPORT))
    if unknown:
        progress(f"{len(unknown)} in-season leagues have no history here, so they "
                 "cannot be priced: " + ", ".join(unknown[:8])
                 + ("…" if len(unknown) > 8 else ""))

    LEAGUE_PLAN.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    new = sum(1 for entry in plan if not entry["tracked"])
    progress(f"\n{len(plan)} leagues can be priced ({new} new). A full price "
             f"fetch would cost about {len(plan) * 4} credits.")
    progress(f"Plan -> {LEAGUE_PLAN}")
    return {"available": len(plan), "new": new, "estimated_credits": len(plan) * 4}


def league_plan():
    """The leagues a price fetch should cover: the discovered plan if there is
    one, otherwise whatever files already exist."""
    if LEAGUE_PLAN.exists():
        try:
            plan = json.loads(LEAGUE_PLAN.read_text(encoding="utf-8"))
            if plan:
                return [entry["sport"] for entry in plan]
        except (json.JSONDecodeError, OSError, KeyError):
            pass
    return sports_tracked()


def fetch_odds(progress=print, sports=None, regions="eu,uk", markets="h2h,totals"):
    """Current prices for upcoming fixtures. COSTS Odds API credits."""
    _ensure()
    from valuebets.sources import odds_api

    sports = list(sports or league_plan())
    if not sports:
        raise SystemExit("No leagues to fetch. Run “Check available leagues” first.")
    cost = len(regions.split(",")) * len(markets.split(","))
    progress(f"{len(sports)} leagues x {cost} credits = ~{len(sports) * cost} credits")

    total, failed = 0, []
    for sport in sports:
        try:
            frame = odds_api.fetch_odds(sport, regions, markets)
        except SystemExit as exc:
            # One league being out of season must not abandon the other thirty.
            progress(f"  {sport}: skipped ({exc})")
            failed.append(sport)
            continue
        if frame.empty:
            progress(f"  {sport}: nothing returned (between seasons?)")
            continue

        # The source maps only ten sports onto competition codes; ours maps all
        # of them. Without this the new leagues arrive with competition=NaN and
        # silently fail to join onto any history.
        code = leagues.BY_SPORT.get(sport)
        if code:
            frame["competition"] = code

        out = vb_config.DATA_DIR / f"odds_{sport}.csv"
        if out.exists():
            # Append: daily runs build the odds history the free tier won't sell.
            prior = pd.read_csv(out)
            frame = pd.concat([prior, frame], ignore_index=True).drop_duplicates(
                subset=["fetched_at", "home_team", "away_team"])
        # Write the day as plain text. The fetched frame holds Timestamps and
        # the rows read back from the CSV hold strings, so concatenating them
        # and writing produces one file with two spellings of the same date —
        # which the next reader infers a format from and then chokes on.
        frame["date"] = cf_data.parse_dates(frame["date"], out.name).dt.strftime("%Y-%m-%d")
        frame.to_csv(out, index=False)
        progress(f"  {sport}: {len(frame)} rows")
        total += len(frame)
    return {"sports": len(sports) - len(failed), "rows": total,
            "skipped": len(failed)}


# -- the confidence model --------------------------------------------------

def rebuild_model(progress=print, refit_days=None, competitions=None):
    """Walk-forward over every finished match. The slow one."""
    _ensure()
    history = cf_data.load_history()
    progress(f"History: {len(history):,} matches, "
             f"{history['competition'].nunique()} competitions, "
             f"{history['date'].min():%Y-%m-%d} to {history['date'].max():%Y-%m-%d}")

    started = time.time()
    predictions = walk_forward_run(history, refit_days=refit_days,
                                   competitions=competitions, progress=progress)
    predictions.to_csv(cf_config.PREDICTIONS_CSV, index=False, float_format="%.6f")
    progress(f"Priced {len(predictions):,} matches out of sample in "
             f"{time.time() - started:.0f}s")
    return {"matches": int(len(predictions)),
            "with_totals": round(float(predictions["has_totals"].mean()), 4),
            "worst_residual": float(predictions["implied_resid"].max())}


def recalibrate(progress=print, weight=None, folds=5):
    """Fit the calibrators, and score the same recipe out of sample."""
    _ensure()
    if not cf_config.PREDICTIONS_CSV.exists():
        raise SystemExit("No predictions yet — rebuild the model first.")
    weight = cf_config.MARKET_WEIGHT if weight is None else weight

    predictions = pd.read_csv(cf_config.PREDICTIONS_CSV, parse_dates=["date"])
    keys, probs, results = predict.build_arrays(predictions, weight)
    dates = predictions["date"].to_numpy()

    calibrated, scored = walk_forward(keys, probs, results, dates, n_folds=folds)
    progress(f"Out-of-sample rows: {scored.sum():,} of {len(scored):,}")

    raw_p, raw_y = evaluate._flatten(keys, probs, results, scored)
    cal_p, cal_y = evaluate._flatten(keys, calibrated, results, scored)
    scores = {
        "brier_raw": evaluate.brier(raw_p, raw_y),
        "brier_calibrated": evaluate.brier(cal_p, cal_y),
        "ece_raw": evaluate.expected_calibration_error(raw_p, raw_y),
        "ece_calibrated": evaluate.expected_calibration_error(cal_p, cal_y),
    }
    progress("  brier {brier_raw:.5f} -> {brier_calibrated:.5f}, "
             "calibration error {ece_raw:.5f} -> {ece_calibrated:.5f}".format(**scores))

    production = Calibrators.fit(keys, probs, results, meta={
        "weight": weight, "folds": folds, "matches": int(len(predictions)),
        "built": time.strftime("%Y-%m-%d %H:%M")})
    production.save(cf_config.CALIBRATION_JSON)
    write_reliability(keys, calibrated, results, scored)
    progress(f"Calibrators -> {cf_config.CALIBRATION_JSON}")
    progress(f"Reliability -> {cf_config.RELIABILITY_CSV}")
    return {k: round(v, 5) for k, v in scores.items()}


def write_reliability(keys, calibrated, results, scored):
    """One table: all markets pooled, then each market group on its own."""
    frames = []
    overall = evaluate.reliability(keys, calibrated, results, scored)
    overall.insert(0, "scope", "all")
    frames.append(overall)
    for group in sorted({evaluate.group_of(k) for k in keys}):
        block = evaluate.reliability(keys, calibrated, results, scored, [group])
        if not block.empty:
            block.insert(0, "scope", group)
            frames.append(block)
    table = pd.concat(frames, ignore_index=True)
    table.to_csv(cf_config.RELIABILITY_CSV, index=False, float_format="%.5f")
    return table


def sweep_market_weight(progress=print, weights=(0.0, 0.25, 0.5, 0.75, 0.9, 1.0), folds=5):
    """How much the closing line deserves against the model."""
    predictions = pd.read_csv(cf_config.PREDICTIONS_CSV, parse_dates=["date"])
    dates = predictions["date"].to_numpy()
    rows = []
    for weight in weights:
        keys, probs, results = predict.build_arrays(predictions, weight)
        calibrated, scored = walk_forward(keys, probs, results, dates, n_folds=folds)
        p, y = evaluate._flatten(keys, calibrated, results, scored)
        rows.append({"market_weight": weight, "n": len(p),
                     "brier": evaluate.brier(p, y),
                     "log_loss": evaluate.log_loss(p, y),
                     "ece": evaluate.expected_calibration_error(p, y)})
        progress(f"  weight {weight:.2f}: brier {rows[-1]['brier']:.5f}")
    return pd.DataFrame(rows)


def evaluation_tables(weight=None, folds=5, threshold=None):
    """Everything `evaluate` can say, for the CLI and the Reliability page."""
    weight = cf_config.MARKET_WEIGHT if weight is None else weight
    threshold = cf_config.MIN_CONFIDENCE if threshold is None else threshold
    predictions = pd.read_csv(cf_config.PREDICTIONS_CSV, parse_dates=["date"])
    keys, probs, results = predict.build_arrays(predictions, weight)
    calibrated, scored = walk_forward(keys, probs, results,
                                      predictions["date"].to_numpy(), n_folds=folds)
    return {
        "reliability": evaluate.reliability(keys, calibrated, results, scored),
        "groups": evaluate.group_summary(keys, calibrated, results, scored, threshold),
        "selections": evaluate.per_selection(keys, calibrated, results, scored, threshold),
        "versus_market": evaluate.versus_market(predictions, keys, calibrated,
                                                results, scored),
        "coherence": evaluate.coherence(keys, calibrated),
    }
