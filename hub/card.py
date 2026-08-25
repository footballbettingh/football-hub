"""Pricing the upcoming fixtures into picks.json.

Thin wrapper around `confidence.picks`: the modelling all lives there, this
decides what the page needs and rounds it to something sane to embed. The whole
card is shipped to the browser so that filtering by league, market, confidence
and price is instant and works identically on a static export, where there is
no server to ask.
"""

import json
from datetime import datetime

import numpy as np
import pandas as pd

from confidence import config as cf_config, data as cf_data, picks as picks_mod
from confidence.calibrate import Calibrators
from confidence.markets import GROUPS

from . import leagues, ledger
from .artifacts import PICKS_JSON

# Columns the page uses. Anything else stays in picks.csv for analysis.
COLUMNS = ["date", "competition", "match", "home_team", "away_team", "key", "group",
           "selection", "prob", "fair_odds", "odds", "edge", "hit_rate", "hit_rate_n",
           "new_team", "validated", "implied_resid"]


def build(progress=print, weight=None, devig_method=None):
    history = cf_data.load_history()
    fixtures = cf_data.load_fixtures()
    if fixtures.empty:
        # Distinct from "nothing could be priced": every fixture on file has
        # already been played, which is what an off-season looks like.
        raise SystemExit("Every fixture on file has already kicked off. "
                         "Fetch new prices to get the next round.")
    progress(f"{len(fixtures)} upcoming fixtures, "
             f"{fixtures['competition'].nunique()} competitions")

    calibrators = None
    if cf_config.CALIBRATION_JSON.exists():
        calibrators = Calibrators.load(cf_config.CALIBRATION_JSON)
    else:
        progress("! No calibrators yet — probabilities will be uncalibrated. "
                 "Run Recalibrate.")

    table = picks_mod.price_fixtures(history, fixtures, calibrators,
                                     weight=weight, devig_method=devig_method)
    if table.empty:
        raise SystemExit("No fixtures could be priced.")

    reliability = (pd.read_csv(cf_config.RELIABILITY_CSV)
                   if cf_config.RELIABILITY_CSV.exists() else None)
    table = picks_mod.attach_hit_rates(table, reliability)
    table.to_csv(cf_config.PICKS_CSV, index=False, float_format="%.5f")

    payload = to_payload(table, fixtures, reliability, calibrators)

    # Write the day's pick down before the match, and grade any earlier ones
    # the results have caught up with. Both are no-ops when there is nothing
    # new, so pressing the button repeatedly changes nothing.
    written = ledger.record_slate(payload.get("slate"))
    for row in written:
        progress(f"Recorded {row['band']} pick for {row['day']}: "
                 f"{row['match']} — {row['selection']} @ "
                 f"{row['fair_odds']:.2f}")
    if not written:
        progress("No new picks to record — every day and band on the slate is "
                 "already in the ledger")

    slip = ledger.record_acca(
        (payload.get("accumulators") or {}).get(payload.get("acca_default")))
    if slip:
        progress(f"Recorded today's {slip['legs']}-leg accumulator: "
                 f"{slip['probability']:.1%} at {slip['fair_odds']:.2f}")

    # And then show what the ledger says, not what was just priced. Everything
    # above is a fresh opinion about days that mostly already have one.
    for changed in _apply_ledger(payload, table):
        progress(f"Showing the recorded {changed['band']} pick for "
                 f"{changed['day']} ({changed['recorded']}) instead of today's "
                 f"re-priced {changed['repriced']}")
    swapped = _apply_acca_ledger(payload)
    if swapped:
        progress(f"Showing the accumulator recorded at {swapped['at']} rather "
                 f"than today's re-priced one")
        progress(f"  recorded:  {swapped['recorded']}")
        progress(f"  re-priced: {swapped['repriced']}")

    PICKS_JSON.parent.mkdir(parents=True, exist_ok=True)
    PICKS_JSON.write_text(json.dumps(payload), encoding="utf-8")
    progress(f"Priced {len(table):,} selections -> {PICKS_JSON}")

    graded = ledger.settle(history) + ledger.settle_accas(history)
    if graded:
        progress(f"Settled {graded} earlier bet(s) against new results")
    return payload


def _new_team_flags(table):
    """Which fixtures on the card involve a team the model does not know.

    Deliberately not a ledger column: it is a fact about today's fit, not about
    the bet, and a club that was new in August is not new in March. So it is
    looked up again here rather than frozen with the rest of the row.
    """
    if table is None or getattr(table, "empty", True) or "new_team" not in table:
        return {}
    rows = table.drop_duplicates(subset=["date", "home", "away"])
    return {(str(row.date)[:10], row.home, row.away): bool(row.new_team)
            for row in rows.itertuples()}


def _apply_ledger(payload, table=None, path=None):
    """Replace every pick on the slate that has already been written down.

    The card looks three match days ahead and is rebuilt every morning, so all
    but the newest day on it was recorded one or two builds ago. Re-pricing
    those days is not wrong — new results and moved lines genuinely change the
    answer — but showing the new answer is, because the ledger keeps the old
    one and the ledger is what gets graded. Left alone, the front page named
    one bet and the History page named another for the same day, which is
    exactly the thing this site claims not to do.

    Returns the list of days where the two disagreed, for the job log.
    """
    slate = payload.get("slate") or []
    if not slate:
        return []

    days = list(dict.fromkeys(pick["day"] for pick in slate))
    recorded = ledger.recorded_slate(days, path or ledger.LEDGER_CSV)
    if not recorded:
        return []

    fresh = {(pick["day"], pick["band"]): pick for pick in slate}
    new_teams = _new_team_flags(table)
    # Config order first, then anything the ledger holds under a band this
    # installation no longer defines — a renamed band must not drop a recorded
    # pick off the page while it is still waiting to be graded.
    bands = list(cf_config.BAND_ORDER)
    bands += sorted({band for _, band in list(fresh) + list(recorded)} - set(bands))

    merged, changed = [], []
    for day in days:
        for band in bands:
            key = (day, band)
            pick = recorded.get(key)
            if pick is None:
                if key in fresh:
                    merged.append(fresh[key])
                continue
            if key in fresh:
                # How many qualified in this band is a fact about the day, not
                # about the pick, so today's count is the honest one to quote.
                # Absent where the band has nothing on the card any more: the
                # record still stands, the count does not.
                pick["candidates"] = fresh[key].get("candidates")
                repriced = (fresh[key].get("key"), fresh[key].get("match"))
                if repriced != (pick["key"], pick["match"]):
                    changed.append({
                        "day": day, "band": band,
                        "recorded": f"{pick['match']} — {pick['selection']}",
                        "repriced": f"{fresh[key]['match']} — "
                                    f"{fresh[key]['selection']}"})
            flag = new_teams.get((day, pick.get("home"), pick.get("away")))
            if flag is not None:
                pick["new_team"] = flag
            merged.append(pick)

    payload["slate"] = _plain(merged)
    payload["best_pick"] = next(
        (pick for pick in payload["slate"] if pick["band"] == "main"), None)
    return changed


def _acca_legs(acca):
    """What makes two slips the same bet: which legs are on it.

    Prices and probabilities move a little between builds and that is not a
    different accumulator. A leg swapped for another one is.
    """
    return sorted((leg.get("match"), leg.get("key"))
                  for leg in acca.get("selections") or [])


def _acca_line(acca):
    """One slip on one line, for the job log."""
    return " + ".join(f"{leg.get('match')}: {leg.get('selection')}"
                      for leg in acca.get("selections") or [])


def _apply_acca_ledger(payload, path=None):
    """Show the slip that was written down, where one already has been.

    Only the default leg count is ever recorded — `record_acca` is handed that
    one alone — so only that one is replaced. The other sizes stay live, and
    the page says so rather than letting them pass as part of the record.

    Returns what the two disagreed about, for the job log, or None.
    """
    accas = payload.get("accumulators") or {}
    recorded = ledger.recorded_acca(path=path or ledger.ACCA_CSV)
    if not recorded:
        return None

    # Filed under the leg count it was actually recorded at, and selected. If
    # ACCA_LEGS changed after a slip was written down, the recorded one is
    # still the bet that will be graded, so it is still the one to show.
    key = str(int(recorded["legs"]))
    fresh = accas.get(key)
    accas[key] = _plain(recorded)
    payload["accumulators"] = accas
    payload["acca_default"] = key

    if fresh and _acca_legs(fresh) != _acca_legs(recorded):
        return {"at": recorded.get("recorded_at"),
                "recorded": _acca_line(recorded),
                "repriced": _acca_line(fresh)}
    return None


def _plain(value, digits=5):
    """Recursively make a nested structure JSON-safe."""
    if isinstance(value, dict):
        return {k: _plain(v, digits) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v, digits) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return _clean(value, digits)


def _clean(value, digits=4):
    """JSON has no NaN. Round while we are here — nothing on the page needs
    more than four decimals, and it halves the embedded payload."""
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return None
    if isinstance(value, (np.floating, float)):
        return round(float(value), digits)
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def to_payload(table, fixtures=None, reliability=None, calibrators=None):
    codes = sorted(table["competition"].unique().tolist())
    names = leagues.labels_for(codes)
    # On the frame, not just on the rows below: the two headline picks are
    # chosen from the frame, and would otherwise report a league code.
    table = table.assign(competition_name=table["competition"].map(names))

    rows = []
    for row in table[COLUMNS].itertuples(index=False):
        record = {column: _clean(value) for column, value in zip(COLUMNS, row)}
        record["date"] = str(record["date"])[:10]
        # "Premier League" rather than "PL". The code stays as the filter value
        # and the join key; only the label changes.
        record["competition_name"] = names.get(record["competition"],
                                               record["competition"])
        rows.append(record)

    # Chosen here rather than in the browser, so the static export shows the
    # same bets the server does.
    slate = picks_mod.daily_slate(table)
    # The flagship: nearest day, middle band. Kept as its own field because it
    # is the one number the whole thing is judged on.
    best = next((pick for pick in slate if pick["band"] == "main"), None)
    accas = {str(legs): picks_mod.best_accumulator(table, legs=legs)
             for legs in range(2, cf_config.ACCA_MAX_LEGS + 1)}

    meta = (calibrators.meta if calibrators else {}) or {}
    return {
        "built": datetime.now().isoformat(timespec="seconds"),
        "best_pick": _plain(best),
        "slate": _plain(slate),
        "bands": {name: list(edges) for name, edges in cf_config.PICK_BANDS.items()},
        "accumulators": _plain({k: v for k, v in accas.items() if v}),
        "acca_default": str(cf_config.ACCA_LEGS),
        "acca_target": cf_config.ACCA_TARGET_ODDS,
        "best_band": [cf_config.BEST_ODDS_MIN, cf_config.BEST_ODDS_MAX],
        "n_fixtures": int(table["match"].nunique()),
        "n_selections": len(rows),
        "first_date": min((r["date"] for r in rows), default=None),
        "last_date": max((r["date"] for r in rows), default=None),
        "competitions": names,
        "groups": {key: GROUPS.get(key, key)
                   for key in sorted(table["group"].unique().tolist())},
        "ceilings": picks_mod.group_ceilings(reliability),
        "calibrated_on": int(meta.get("matches", 0)),
        "market_weight": meta.get("weight"),
        "selections": rows,
    }
