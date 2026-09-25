"""The pipeline steps, in one place, callable from both the CLI and the buttons.

Each function takes a `progress` callable and returns a short dict for the job
log. Nothing here prints directly to stdout on its own behalf — the job runner
captures whatever the libraries print anyway, and the summaries these return are
what ends up in status.json.
"""

import json
import time

import numpy as np
import pandas as pd

from confidence import config as cf_config, data as cf_data, evaluate, predict
from confidence.calibrate import Calibrators, walk_forward
from confidence.corners import CornerAnchor
from confidence.walkforward import run as walk_forward_run
from valuebets import config as vb_config, files
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
    files.write_csv(frame, out, index=False)
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

    # The full loader rather than the competition column alone: `quiet` below
    # needs the date of the last result, and needs it to be a result — a row
    # for a fixture that has not been played yet would make a dead feed look
    # current.
    history = cf_data.load_history()
    have_history = set(history["competition"].unique())
    silent = leagues.skipped(history, listed=leagues.listed_fixtures())
    tracked = set(sports_tracked())

    plan, missing, stopped = [], [], []
    for code, sport in sorted(leagues.SPORT_KEYS.items()):
        if code not in have_history:
            continue                     # nothing to price it against
        if code in silent:
            # In season as far as the API is concerned, and priced happily, but
            # nothing it sells could ever be graded. Buying it is worse than
            # useless: it costs credits and fills the ledger with bets that
            # stay pending for good.
            stopped.append((code, sport))
            continue
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
    if stopped:
        progress(f"Results have stopped arriving for {len(stopped)}, so they are "
                 "left out until they resume: "
                 + ", ".join(leagues.label(code) for code, _ in stopped))

    unknown = sorted(set(live) - set(leagues.BY_SPORT))
    if unknown:
        progress(f"{len(unknown)} in-season leagues have no history here, so they "
                 "cannot be priced: " + ", ".join(unknown[:8])
                 + ("…" if len(unknown) > 8 else ""))

    files.write_text(LEAGUE_PLAN, json.dumps(plan, indent=1))
    new = sum(1 for entry in plan if not entry["tracked"])
    full = len(plan) * odds_api.credits()
    progress(f"\n{len(plan)} leagues can be priced ({new} new). A full price "
             f"fetch would cost about {full} credits.")
    progress(f"Plan -> {LEAGUE_PLAN}")
    return {"available": len(plan), "new": new, "estimated_credits": full}


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


def _redraw_plan(progress):
    """Draw the league plan again from what is in season. Free.

    A plan drawn once goes stale both ways: it used to be drawn only on a
    machine that had none, so a league coming into season was never added, and
    a league whose results had stopped kept being bought for a card that then
    threw its fixtures away. If the redraw fails, the plan on file still stands.
    """
    from requests import RequestException
    try:
        discover_leagues(progress)
    except (SystemExit, RequestException) as exc:
        # The type and not the message: a failed request names its URL, and
        # this one carries the API key.
        progress(f"  could not redraw the league plan ({type(exc).__name__}); "
                 "following the one on file")


def fetch_odds(progress=print, sports=None, regions=None, markets=None):
    """Current prices for every league in the plan, now. COSTS Odds API credits.

    The deliberate, whole-plan fetch — `fb.py fetch odds`. An unattended run
    uses `fetch_odds_paced`, which buys only what is about to be played and
    only as much as the month can afford. Without a list of sports this
    follows the league plan, redrawn first.
    """
    _ensure()
    from valuebets.sources import odds_api
    regions, markets = regions or odds_api.REGIONS, markets or odds_api.MARKETS

    if not sports:
        _redraw_plan(progress)
    sports = list(sports or league_plan())
    if not sports:
        raise SystemExit("No leagues to fetch. Run “Check available leagues” first.")
    cost = odds_api.credits(regions, markets)
    progress(f"{len(sports)} leagues x {cost} credits = ~{len(sports) * cost} credits")

    total, failed = 0, []
    for sport in sports:
        rows = _fetch_one(sport, regions, markets, progress)
        if rows is None:
            failed.append(sport)
        else:
            total += rows
    return {"sports": len(sports) - len(failed), "rows": total,
            "skipped": len(failed)}


def _fetch_one(sport, regions, markets, progress, client=None):
    """Buy one league's prices and append them to its file.

    Returns the rows now in the file, 0 when the feed had nothing for it, or
    None when it was skipped.
    """
    from valuebets.sources import odds_api
    try:
        frame = odds_api.fetch_odds(sport, regions, markets, client=client)
    except SystemExit as exc:
        # One league being out of season must not abandon the other thirty.
        progress(f"  {sport}: skipped ({exc})")
        return None
    if frame.empty:
        progress(f"  {sport}: nothing returned (between seasons?)")
        return 0

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
    files.write_csv(frame, out, index=False)
    progress(f"  {sport}: {len(frame)} rows")
    return len(frame)


# -- paying for prices at the pace the quota allows ---------------------------

QUOTA_STATE = vb_config.DATA_DIR / "odds_quota.json"

# A league is worth paying for when it has a match inside this window. The
# slate reaches three match days ahead and a pick is written down when its day
# first comes into that reach — two days out, most weeks — so a price bought
# any earlier has only aged by the time it is used.
HORIZON_DAYS = 3

# Never buy the same league twice inside this. With budget to spare the
# allowance would otherwise go on re-buying prices a few hours old.
MIN_REFRESH_HOURS = 12


def fetch_odds_paced(progress=print, horizon_days=HORIZON_DAYS, now=None):
    """Prices for the leagues that play soon, as many as the quota affords today.

    The rule this replaces bought every league at once whenever the newest
    price on file was eight days old, so a pick was written down on prices 3.7
    days old on average and its match kicked off on prices older still. This
    asks the free events endpoint which leagues have a match within
    `horizon_days`, and buys those — stalest first — up to today's share of
    what is left of the month.

    Simulated against seven months of fixtures from the history: 374 to 450
    credits a month where the old rule spent 308 to 512, and prices 1.2 days
    old when a pick is written down instead of 3.7. The share comes from the
    API's own count of what is left, read off a free call, so it cannot
    overspend however the cache fares or whoever else is using the key.
    """
    _ensure()
    from requests import RequestException
    from valuebets.sources import odds_api

    _redraw_plan(progress)
    sports = league_plan()
    if not sports:
        raise SystemExit("No leagues to fetch. Run “Check available leagues” first.")

    client = odds_api.Client()
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    until = now + pd.Timedelta(days=horizon_days)
    soon = {}
    for sport in sports:
        try:
            events = odds_api.list_events(sport, client)
        except (SystemExit, RequestException) as exc:
            progress(f"  {sport}: could not list its fixtures ({type(exc).__name__})")
            continue
        kickoffs = pd.to_datetime([e.get("commence_time") for e in events],
                                  utc=True, errors="coerce")
        ahead = [k for k in kickoffs if pd.notna(k) and now < k <= until]
        if ahead:
            soon[sport] = min(ahead)

    if client.remaining is None:
        progress("The API did not say how much of the quota is left, so nothing "
                 "is bought blind.")
        return {"due": len(soon), "fetched": 0}

    days_left = days_to_reset(client.used, now.date())
    allowance = daily_allowance(client.remaining, days_left)
    cost = odds_api.credits(odds_api.REGIONS, odds_api.MARKETS)
    order = paced_order(soon, quote_ages(now), allowance, cost)
    progress(f"{len(soon)} of {len(sports)} leagues play in the next {horizon_days} "
             f"days. {client.remaining} credits left, {days_left} day(s) to the "
             f"reset: {allowance:.1f} to spend today, {cost} a league.")
    later = [sport for sport in sorted(soon) if sport not in order]
    if later:
        progress(f"  left for another day: {', '.join(later)}")

    bought = 0
    for sport in order:
        if _fetch_one(sport, odds_api.REGIONS, odds_api.MARKETS, progress,
                      client=client) is not None:
            bought += 1
    return {"due": len(soon), "fetched": bought, "remaining": client.remaining,
            "days_to_reset": days_left}


def daily_allowance(remaining, days_left, floor=None):
    """Today's share of the credits left above the floor."""
    floor = vb_config.ODDS_API_MIN_CREDITS if floor is None else floor
    return max(0.0, (remaining - floor) / max(int(days_left), 1))


def paced_order(soon, ages, allowance, cost, min_age_hours=MIN_REFRESH_HOURS):
    """Which of the leagues that play soon to buy today, in the order to buy them.

    `soon` maps a league to its next kick-off, `ages` to how many days old its
    newest price is — absent where it has none, which puts it first. Stalest
    first, then the one that plays soonest, and no more than the allowance
    pays for.
    """
    never = float("inf")
    due = [sport for sport in soon
           if ages.get(sport, never) * 24 >= min_age_hours]
    due.sort(key=lambda sport: (-ages.get(sport, never), soon[sport]))
    return due[:int((allowance + 1e-9) // cost)]


def quote_ages(now):
    """How many days old each league's newest price is, from `fetched_at`."""
    out = {}
    for sport in sports_tracked():
        path = vb_config.DATA_DIR / f"odds_{sport}.csv"
        try:
            stamps = pd.to_datetime(pd.read_csv(path, usecols=["fetched_at"])
                                    ["fetched_at"], errors="coerce", utc=True)
        except (ValueError, KeyError, OSError):
            continue
        if pd.notna(stamps.max()):
            out[sport] = (now - stamps.max()) / pd.Timedelta(days=1)
    return out


def days_to_reset(used, today, path=None):
    """Days until the monthly quota comes back, and at least one.

    The API says how much has been used since the last reset, never when the
    next one is. So the reset is watched for: `used` falling between two runs
    is one, and its day of the month is remembered in `odds_quota.json`. Until
    one has been seen the first of the month is assumed. A wrong guess is safe
    either way: too late and the month ends with credits unspent; too early and
    the spending reaches the floor, where the allowance holds it until the
    real reset comes.
    """
    path = path or QUOTA_STATE
    state = {}
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            state = {}
    reset_day = state.get("reset_day")
    if used is not None and state.get("used") is not None and used < state["used"]:
        reset_day = today.day
    if used is not None:
        files.write_text(path, json.dumps({"reset_day": reset_day, "used": used,
                                           "seen": today.isoformat()}))
    return _days_until(today, reset_day or 1)


def _days_until(today, day):
    """Days from `today` to the next date falling on this day of the month.

    A day past the end of a month lands on its last day, so a reset seen on
    the 31st still comes round in February.
    """
    import calendar
    from datetime import date

    def on(year, month):
        return date(year, month, min(day, calendar.monthrange(year, month)[1]))

    upcoming = on(today.year, today.month)
    if upcoming <= today:
        year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        upcoming = on(year, month)
    return (upcoming - today).days


# -- the confidence model --------------------------------------------------

PREDICTIONS_META = cf_config.PREDICTIONS_CSV.with_name("predictions_meta.json")

# How much of each league the daily run prices again: the last five refit
# periods. New results only change what comes after them, and a league's
# results arrive within about ten days.
TAIL_DAYS = 35

# And the whole history, from scratch, this often — for what the tail cannot
# see: a score corrected, or a closing price revised, weeks back.
FULL_EVERY_DAYS = 7

# What the stored predictions are a function of. A change to any of these and
# the tail is not enough: every row was priced by something that no longer
# exists.
_MODEL_SETTINGS = ("REFIT_DAYS", "MIN_TRAIN_MATCHES", "HALF_LIFE_DAYS", "RIDGE",
                   "CORNER_SHRINK", "DEVIG")

_KEY = ["competition", "date", "home", "away"]
_RESULTS = ["home_goals", "away_goals", "total_corners"]


def _model_fingerprint():
    """A hash of the code and the settings the walk-forward's output depends on."""
    import hashlib
    from pathlib import Path

    from confidence import data, implied, poisson, teams, walkforward

    digest = hashlib.sha256()
    for module in (poisson, walkforward, implied, data, teams):
        digest.update(Path(module.__file__).read_bytes())
    for name in _MODEL_SETTINGS:
        digest.update(f"{name}={getattr(cf_config, name)!r};".encode())
    return digest.hexdigest()[:16]


def _incremental_plan(history, today=None):
    """(since, kept, why) — or (None, None, why) where the run must be full.

    `since` is, per league, the first date to price again: the tail, or
    earlier if a match turned up that the stored run never priced. `kept` is
    every stored row before it whose match is still in the history, with its
    result refreshed from there.
    """
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    try:
        meta = json.loads(PREDICTIONS_META.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None, "no earlier run on file"
    if meta.get("fingerprint") != _model_fingerprint():
        return None, None, "the model's code or settings changed"
    full_at = pd.to_datetime(meta.get("full_at"), errors="coerce")
    if pd.isna(full_at) or (today - full_at).days >= FULL_EVERY_DAYS:
        return None, None, f"the weekly rebuild (last full {meta.get('full_at')})"
    if not cf_config.PREDICTIONS_CSV.exists():
        return None, None, "no earlier predictions on file"

    stored = pd.read_csv(cf_config.PREDICTIONS_CSV, parse_dates=["date"])
    current = history[_KEY + _RESULTS]
    stored = (stored.drop(columns=_RESULTS)
                    .merge(current, on=_KEY, how="inner")[stored.columns])

    since = {}
    for competition, sub in history.groupby("competition"):
        mine = stored[stored["competition"] == competition]
        if mine.empty:
            continue                          # never priced: this league runs in full
        start = sub["date"].max() - pd.Timedelta(days=TAIL_DAYS)
        priced = set(zip(mine["date"], mine["home"], mine["away"]))
        unpriced = np.array([(d, h, a) not in priced
                             for d, h, a in zip(sub["date"], sub["home"], sub["away"])],
                            dtype=bool)
        late = sub[(sub["date"] >= mine["date"].min()).to_numpy() & unpriced]
        if len(late):
            start = min(start, late["date"].min())
        since[competition] = start

    keep = stored[[date < since.get(comp, pd.Timestamp.min)
                   for comp, date in zip(stored["competition"], stored["date"])]]
    return since, keep, f"the last {TAIL_DAYS} days of each league"


def rebuild_model(progress=print, refit_days=None, competitions=None, full=False):
    """Walk-forward over every finished match, or over what has changed.

    A full walk takes six to eight minutes, and new results only change what
    comes after them, so a daily run prices again only each league's last
    five weeks — and any match that arrived late — and keeps the rest. It is
    the same computation: the refit schedule is walked from the start either
    way, so the tail comes out as a full run would price it, to the fourth
    decimal of a lambda. Full when asked, weekly, whenever the model's code or
    settings change, and for a subset or a non-default refit.
    """
    _ensure()
    history = cf_data.load_history()
    progress(f"History: {len(history):,} matches, "
             f"{history['competition'].nunique()} competitions, "
             f"{history['date'].min():%Y-%m-%d} to {history['date'].max():%Y-%m-%d}")

    started = time.time()
    if full or competitions or refit_days is not None:
        since, kept, why = None, None, "asked for"
    else:
        since, kept, why = _incremental_plan(history)

    if since is None:
        progress(f"Walking the whole history ({why})")
        predictions = walk_forward_run(history, refit_days=refit_days,
                                       competitions=competitions, progress=progress)
    else:
        progress(f"Pricing again {why}; keeping {len(kept):,} matches as they were")
        fresh = walk_forward_run(history, progress=progress, since=since)
        predictions = (pd.concat([kept, fresh], ignore_index=True)
                       .sort_values(["date", "competition", "home"])
                       .reset_index(drop=True))
    files.write_csv(predictions, cf_config.PREDICTIONS_CSV, index=False,
                    float_format="%.6f")
    if competitions:
        # A file with some leagues in it is no base for the next run's tail.
        PREDICTIONS_META.unlink(missing_ok=True)
    else:
        previous = {}
        if since is not None:
            previous = json.loads(PREDICTIONS_META.read_text(encoding="utf-8"))
        files.write_text(PREDICTIONS_META, json.dumps({
            "fingerprint": _model_fingerprint(),
            "full_at": (previous.get("full_at") if since is not None
                        else pd.Timestamp.today().strftime("%Y-%m-%d")),
            "built": time.strftime("%Y-%m-%d %H:%M")}))
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
    keys, probs, results = predict.out_of_sample_arrays(predictions, weight, folds)
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

    # The live card anchors corners on everything there is, so the live
    # calibrators are fitted on probabilities anchored the same way.
    anchor = CornerAnchor.fit(predictions)
    _, live, _ = predict.build_arrays(predictions, weight, anchor=anchor)
    progress(f"  corner anchor: {anchor.coefficients.round(3).tolist()} "
             f"on {anchor.n:,} matches")
    production = Calibrators.fit(keys, live, results, meta={
        "weight": weight, "folds": folds, "matches": int(len(predictions)),
        "built": time.strftime("%Y-%m-%d %H:%M"),
        "corner_anchor": anchor.to_dict()})
    files.write_text(cf_config.CALIBRATION_JSON, production.to_json())
    write_reliability(keys, calibrated, results, scored)
    factors = write_pick_factors(keys, calibrated, results, scored)
    progress(f"Calibrators -> {cf_config.CALIBRATION_JSON}")
    progress(f"Reliability -> {cf_config.RELIABILITY_CSV}")
    progress(f"Pick factors -> {cf_config.PICK_FACTORS_CSV} "
             f"({len(factors):,} selection/price cells)")
    return {k: round(v, 5) for k, v in scores.items()}


def write_reliability(keys, calibrated, results, scored):
    """One table: all markets pooled, then each market group on its own."""
    table = evaluate.reliability_by_scope(keys, calibrated, results, scored)
    files.write_csv(table, cf_config.RELIABILITY_CSV, index=False, float_format="%.5f")
    return table


def write_pick_factors(keys, calibrated, results, scored):
    """What each selection has done at each price, for the slate's tie-break.

    Its own file rather than more rows in reliability.csv. That table is a
    public answer to "does an 80% pick win 80% of the time" and the Reliability
    page reads every scope in it into a market dropdown; forty-six selections
    times a few dozen price cells would bury it, and `group_ceilings` walks the
    same rows expecting each scope to be a market group.
    """
    table = evaluate.key_price_factors(
        keys, calibrated, results, scored,
        step=cf_config.PICK_FACTOR_STEP, min_n=cf_config.PICK_FACTOR_MIN_N)
    files.write_csv(table, cf_config.PICK_FACTORS_CSV, index=False, float_format="%.5f")
    return table


def sweep_market_weight(progress=print, weights=(0.0, 0.25, 0.5, 0.75, 0.9, 1.0), folds=5):
    """How much the closing line deserves against the model."""
    predictions = pd.read_csv(cf_config.PREDICTIONS_CSV, parse_dates=["date"])
    dates = predictions["date"].to_numpy()
    rows = []
    for weight in weights:
        keys, probs, results = predict.out_of_sample_arrays(predictions, weight, folds)
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
    keys, probs, results = predict.out_of_sample_arrays(predictions, weight, folds)
    calibrated, scored = walk_forward(keys, probs, results,
                                      predictions["date"].to_numpy(), n_folds=folds)
    return {
        "reliability": evaluate.reliability(keys, calibrated, results, scored),
        "groups": evaluate.group_summary(keys, calibrated, results, scored, threshold),
        "selections": evaluate.per_selection(keys, calibrated, results, scored, threshold),
        "versus_market": evaluate.versus_market(predictions, keys, calibrated,
                                                results, scored),
        "coherence": evaluate.coherence(keys, calibrated),
        "rps": evaluate.rps_1x2(predictions, keys, calibrated, results, scored),
    }
