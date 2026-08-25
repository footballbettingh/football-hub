"""The record of what the best pick of the day actually did.

An append-only ledger, kept deliberately dumb. A pick is written down before
the match with the price that was available at the time, and nothing rewrites
it afterwards — a record you can edit once you know the answer is not a record.
Settlement only ever fills in the empty columns.

One row per match day. Rebuilding the card ten times before kick-off records the
first answer and leaves it alone, because the alternative is a ledger that
quietly tracks whichever pick happened to look best last, which would show a
suspiciously good history and mean nothing.

P&L is flat stakes of one unit at the price the feed quoted. Where it quoted no
price the bet is still graded won or lost — that is the honest test of the
forecast — but it cannot contribute to profit, so it is counted separately
rather than settled at our own fair odds, which would return exactly zero by
construction and look like a result.
"""

import json
import math
from datetime import date, datetime

import numpy as np
import pandas as pd

from confidence.markets import corner_results, goal_results
from confidence.teams import build_resolver
from valuebets import config as vb_config

LEDGER_CSV = vb_config.DATA_DIR / "best_picks.csv"
ACCA_CSV = vb_config.DATA_DIR / "best_accas.csv"
STAKE = 1.0

# How far a fixture may move and still be recognised as the one we bet on.
POSTPONEMENT_DAYS = 7

COLUMNS = [
    "day", "band", "recorded_at", "competition", "competition_name",
    "home", "away", "match", "key", "group", "selection",
    "prob", "fair_odds", "odds", "hit_rate", "hit_rate_n",
    "played_on", "home_goals", "away_goals", "outcome", "pnl", "settled_at",
]

# What a row without a band is. Rows written before the slate existed were all
# from the flagship 1.60-2.20 range, so labelling them anything else would
# misfile the only real history there is.
DEFAULT_BAND = "main"

# Columns that hold text. An all-NaN column arrives as float64, and writing a
# date string into one is deprecated in pandas and an error in a future
# version — so they are pinned to object up front.
TEXT_COLUMNS = ("day", "band", "recorded_at", "competition", "competition_name",
                "home", "away", "match", "key", "group", "selection",
                "played_on", "outcome", "settled_at")


def load(path=LEDGER_CSV):
    if path.exists():
        frame = pd.read_csv(path, dtype={"day": str, "outcome": str})
    else:
        frame = pd.DataFrame(columns=COLUMNS)
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan
    frame = frame[COLUMNS]
    for column in TEXT_COLUMNS:
        frame[column] = frame[column].astype(object)
    frame["band"] = frame["band"].where(frame["band"].notna(), DEFAULT_BAND)
    return frame


def save(frame, path=LEDGER_CSV):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame[COLUMNS].to_csv(path, index=False, float_format="%.5f")


def record(pick, path=LEDGER_CSV, today=None):
    """Write down one pick. Returns the row, or None if nothing was written.

    Refuses two things: a second pick for a match day and price band already in
    the ledger, and a pick for a day that has already gone. The second guard
    matters — without it, rebuilding the card against a stale fixture file would
    append yesterday's fixtures as though they had been called in advance.
    """
    if not pick:
        return None
    frame = load(path)
    day = str(pick["day"])[:10]
    band = pick.get("band") or DEFAULT_BAND
    taken = (frame["day"].astype(str) == day) & (frame["band"].astype(str) == band)
    if taken.any():
        return None
    today = pd.to_datetime(today or date.today()).strftime("%Y-%m-%d")
    if day < today:
        return None

    row = {
        "day": day,
        "band": band,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "competition": pick.get("competition"),
        "competition_name": pick.get("competition_name") or pick.get("competition"),
        "home": pick.get("home"), "away": pick.get("away"),
        "match": pick.get("match"), "key": pick.get("key"),
        "group": pick.get("group"), "selection": pick.get("selection"),
        "prob": pick.get("prob"), "fair_odds": pick.get("fair_odds"),
        "odds": pick.get("odds"),
        "hit_rate": pick.get("hit_rate"), "hit_rate_n": pick.get("hit_rate_n"),
        "played_on": None, "home_goals": None, "away_goals": None,
        "outcome": "pending", "pnl": None, "settled_at": None,
    }
    # Rebuilt from records rather than concatenated or assigned in place. A
    # ledger row is mostly empty until it settles, and every pandas route that
    # merges an all-NA frame with a new row warns about future dtype inference
    # — which is irrelevant here, because the next thing that happens is a
    # write to CSV. Building the frame from dicts skips the question entirely,
    # and at a few rows a day the cost never matters.
    save(pd.DataFrame(frame.to_dict("records") + [row], columns=COLUMNS), path)
    return row


def record_slate(slate, path=LEDGER_CSV, today=None):
    """Write down every pick on the slate that is not already recorded.

    Idempotent, one row per (match day, band). Rebuilding the card three days
    running therefore adds the newly reachable day and leaves the two already
    written alone — which is the whole point of looking three days ahead: the
    pick for Saturday is recorded on Thursday, at Thursday's price, and never
    revised.
    """
    return [row for row in (record(pick, path, today) for pick in slate or [])
            if row]


def _resolvers(history):
    """One team-name resolver per competition, built once per settlement run."""
    cache = {}

    def get(competition):
        if competition not in cache:
            sub = history[history["competition"] == competition]
            cache[competition] = build_resolver(set(sub["home"]) | set(sub["away"]))
        return cache[competition]

    return get


def _result_for(row, history, resolvers=None):
    """The finished match this pick was made on, if it has been played.

    Matched on competition and both team keys rather than on the date, because
    fixtures move. A postponement of more than a week is treated as a different
    fixture — beyond that the "same" match is really a rearranged one played in
    different circumstances.

    Team keys are resolved rather than compared: the price feed says "Mansfield
    Town" and the results file says "Mansfield", and an exact comparison leaves
    the bet pending forever with nothing to say why. Resolution only accepts a
    single unambiguous candidate, so a name that could be two clubs stays
    unmatched rather than being graded against the wrong match.
    """
    if history is None or history.empty:
        return None
    in_comp = history[history["competition"] == row["competition"]]
    if in_comp.empty:
        return None

    home, away = row["home"], row["away"]
    keys = set(in_comp["home"]) | set(in_comp["away"])
    if home not in keys or away not in keys:
        resolve = (resolvers or _resolvers(history))(row["competition"])
        home = resolve(home) or home
        away = resolve(away) or away

    candidates = in_comp[(in_comp["home"] == home) & (in_comp["away"] == away)]
    if candidates.empty:
        return None
    day = pd.to_datetime(row["day"])
    drift = (candidates["date"] - day).abs()
    within = candidates[drift <= pd.Timedelta(days=POSTPONEMENT_DAYS)]
    if within.empty:
        return None
    return within.loc[(within["date"] - day).abs().idxmin()]


def _grade(key, match):
    """won / lost / void for one selection on one finished match."""
    outcomes = goal_results(match["home_goals"], match["away_goals"])
    outcomes.update(corner_results(match.get("total_corners", np.nan)))
    if key not in outcomes:
        return None
    won = outcomes[key]
    if won is None:
        return "void"
    return "won" if won else "lost"


def profit(outcome, odds, stake=STAKE):
    """Flat stakes at the quoted price. NaN when there was no price to take."""
    if odds is None or odds != odds:
        return np.nan
    if outcome == "won":
        return stake * (float(odds) - 1.0)
    if outcome == "lost":
        return -stake
    if outcome == "void":
        return 0.0
    return np.nan


def settle(history, path=LEDGER_CSV):
    """Fill in results for any pending pick whose match has been played."""
    frame = load(path)
    pending = frame["outcome"].fillna("pending") == "pending"
    if not pending.any():
        return 0

    settled = 0
    resolvers = _resolvers(history) if history is not None and len(history) else None
    for index in frame.index[pending]:
        row = frame.loc[index]
        match = _result_for(row, history, resolvers)
        if match is None:
            continue
        outcome = _grade(row["key"], match)
        if outcome is None:
            continue
        frame.loc[index, "played_on"] = match["date"].strftime("%Y-%m-%d")
        frame.loc[index, "home_goals"] = int(match["home_goals"])
        frame.loc[index, "away_goals"] = int(match["away_goals"])
        frame.loc[index, "outcome"] = outcome
        frame.loc[index, "pnl"] = profit(outcome, row["odds"])
        frame.loc[index, "settled_at"] = datetime.now().isoformat(timespec="seconds")
        settled += 1

    if settled:
        save(frame, path)
    return settled


# -- reading it back -------------------------------------------------------

def wilson(successes, n, z=1.96):
    """Interval for a hit rate. The same one the reliability tables use, and
    it is chosen for the same reason: the normal approximation runs past 1.0
    exactly where the bands that matter sit."""
    if not n:
        return (None, None)
    p = successes / n
    denom = 1 + z ** 2 / n
    centre = (p + z ** 2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def summary(frame):
    """Headline numbers. Everything is out of sample by construction here —
    every row was written down before the match was played."""
    frame = frame.copy()
    frame["outcome"] = frame["outcome"].fillna("pending")
    done = frame[frame["outcome"].isin(("won", "lost", "void"))]
    decided = done[done["outcome"] != "void"]
    priced = done[done["pnl"].notna()]

    wins = int((decided["outcome"] == "won").sum())
    pnl = float(priced["pnl"].sum()) if len(priced) else 0.0
    staked = float(len(priced) * STAKE)

    # A pick still pending long after its match should have been played is not
    # patience, it is a join that never matched — a team key the results file
    # spells differently. Left unflagged it would sit there forever, quietly
    # keeping a loss out of the record.
    overdue = frame[(frame["outcome"] == "pending")
                    & (pd.to_datetime(frame["day"], errors="coerce")
                       < pd.Timestamp.today() - pd.Timedelta(days=POSTPONEMENT_DAYS + 7))]

    return {
        "recorded": int(len(frame)),
        "overdue": int(len(overdue)),
        "overdue_days": sorted(overdue["day"].astype(str).tolist()),
        "pending": int((frame["outcome"] == "pending").sum()),
        "settled": int(len(done)),
        "void": int((done["outcome"] == "void").sum()),
        "wins": wins,
        "losses": int(len(decided)) - wins,
        "hit_rate": float(wins / len(decided)) if len(decided) else None,
        # The interval around what happened. Without it a reader has no way to
        # tell a forecast that is wrong from a sample that is small, and at
        # this many picks it is almost always the second.
        "hit_ci": wilson(wins, len(decided)) if len(decided) else (None, None),
        # The forecast's own claim, to sit next to what happened. A run of ten
        # is noise; the comparison only starts meaning something in the dozens.
        "expected": float(decided["prob"].mean()) if len(decided) else None,
        "priced": int(len(priced)),
        "unpriced": int(len(done) - len(priced)),
        "pnl": pnl,
        "staked": staked,
        "roi": float(pnl / staked * 100) if staked else None,
        "average_odds": float(priced["odds"].mean()) if len(priced) else None,
    }


def summary_by_band(frame):
    """The same numbers, split by price band.

    This is the split that actually answers "is it as well calibrated at 40% as
    it is at 70%" — pooling the bands hides exactly the failure the ceilings on
    the Reliability page were built to catch.
    """
    if frame.empty:
        return []
    out = []
    for band in [b for b in ("safe", "main", "value")
                 if b in set(frame["band"].astype(str))]:
        head = summary(frame[frame["band"].astype(str) == band])
        out.append({"band": band, **head})
    return out


# -- the accumulator, kept in its own book ---------------------------------

ACCA_COLUMNS = [
    "issued", "recorded_at", "legs", "target_odds", "min_leg_odds",
    "probability", "fair_odds", "offered_odds", "weakest_leg",
    "first_day", "last_day", "selections",
    "legs_won", "legs_void", "outcome", "pnl", "settled_at",
]

ACCA_TEXT = ("issued", "recorded_at", "first_day", "last_day", "selections",
             "outcome", "settled_at")


def load_accas(path=ACCA_CSV):
    if path.exists():
        frame = pd.read_csv(path, dtype={"issued": str, "outcome": str})
    else:
        frame = pd.DataFrame(columns=ACCA_COLUMNS)
    for column in ACCA_COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan
    frame = frame[ACCA_COLUMNS]
    for column in ACCA_TEXT:
        frame[column] = frame[column].astype(object)
    return frame


def record_acca(acca, path=ACCA_CSV, today=None):
    """One accumulator per day it was issued.

    Keyed on the issue date rather than a match day, because a slip spans
    several: it is today's slip, placed today, at today's prices. Rebuilding
    the card again this afternoon keeps this morning's answer.
    """
    if not acca:
        return None
    frame = load_accas(path)
    issued = pd.to_datetime(today or date.today()).strftime("%Y-%m-%d")
    if (frame["issued"].astype(str) == issued).any():
        return None

    row = {
        "issued": issued,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "legs": acca["legs"], "target_odds": acca["target_odds"],
        "min_leg_odds": acca["min_leg_odds"],
        "probability": acca["probability"], "fair_odds": acca["fair_odds"],
        "offered_odds": acca.get("offered_odds"),
        "weakest_leg": acca["weakest_leg"],
        "first_day": acca.get("first_day"), "last_day": acca.get("last_day"),
        "selections": json.dumps(acca["selections"]),
        "legs_won": None, "legs_void": None,
        "outcome": "pending", "pnl": None, "settled_at": None,
    }
    save_accas(pd.DataFrame(frame.to_dict("records") + [row],
                            columns=ACCA_COLUMNS), path)
    return row


def save_accas(frame, path=ACCA_CSV):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame[ACCA_COLUMNS].to_csv(path, index=False, float_format="%.5f")


def settle_accas(history, path=ACCA_CSV):
    """Grade any slip whose every leg has now been played.

    Void legs drop out and the slip settles on what is left, which is what a
    bookmaker does. A slip is only priced if EVERY surviving leg had a quoted
    price — and if it is unpriced it stays out of the P&L whether it won or
    lost, because counting the losses and not the wins would be worse than
    counting neither.
    """
    frame = load_accas(path)
    pending = frame["outcome"].fillna("pending") == "pending"
    if not pending.any():
        return 0

    settled = 0
    resolvers = _resolvers(history) if history is not None and len(history) else None
    for index in frame.index[pending]:
        legs = json.loads(frame.loc[index, "selections"])
        graded = []
        for leg in legs:
            match = _result_for({"competition": leg["competition"],
                                 "home": leg["home"], "away": leg["away"],
                                 "day": leg["date"]}, history, resolvers)
            graded.append(None if match is None else _grade(leg["key"], match))
        if any(outcome is None for outcome in graded):
            continue                       # a leg is still to play

        alive = [(leg, outcome) for leg, outcome in zip(legs, graded)
                 if outcome != "void"]
        wins = sum(1 for _, outcome in alive if outcome == "won")
        if not alive:
            outcome, pnl = "void", 0.0
        elif wins == len(alive):
            priced = all(leg.get("odds") for leg, _ in alive)
            outcome = "won"
            pnl = (float(np.prod([leg["odds"] for leg, _ in alive])) - 1.0) * STAKE \
                if priced else np.nan
        else:
            priced = all(leg.get("odds") for leg, _ in alive)
            outcome = "lost"
            pnl = -STAKE if priced else np.nan

        frame.loc[index, "legs_won"] = wins
        frame.loc[index, "legs_void"] = len(legs) - len(alive)
        frame.loc[index, "outcome"] = outcome
        frame.loc[index, "pnl"] = pnl
        frame.loc[index, "settled_at"] = datetime.now().isoformat(timespec="seconds")
        settled += 1

    if settled:
        save_accas(frame, path)
    return settled


def acca_summary(frame):
    frame = frame.copy()
    frame["outcome"] = frame["outcome"].fillna("pending")
    done = frame[frame["outcome"].isin(("won", "lost", "void"))]
    decided = done[done["outcome"] != "void"]
    priced = done[done["pnl"].notna()]

    wins = int((decided["outcome"] == "won").sum())
    pnl = float(priced["pnl"].sum()) if len(priced) else 0.0
    return {
        "recorded": int(len(frame)),
        "pending": int((frame["outcome"] == "pending").sum()),
        "settled": int(len(done)),
        "wins": wins,
        "losses": int(len(decided)) - wins,
        "hit_rate": float(wins / len(decided)) if len(decided) else None,
        # The product of the legs' probabilities, which is what the slip claimed.
        "expected": float(decided["probability"].mean()) if len(decided) else None,
        "priced": int(len(priced)),
        "unpriced": int(len(done) - len(priced)),
        "pnl": pnl,
        "roi": float(pnl / (len(priced) * STAKE) * 100) if len(priced) else None,
        "average_legs_won": float(decided["legs_won"].mean()) if len(decided) else None,
        "average_legs": float(decided["legs"].mean()) if len(decided) else None,
    }


def acca_legs(row):
    """The stored legs of one accumulator, back as dicts."""
    try:
        return json.loads(row["selections"])
    except (TypeError, ValueError):
        return []


def equity(frame):
    """Cumulative P&L, in the shape the site's chart already draws."""
    priced = frame[frame["pnl"].notna()].copy()
    if priced.empty:
        return []
    priced = priced.sort_values(["played_on", "day"])
    cumulative = priced["pnl"].cumsum()
    return [{"date": str(row.played_on or row.day)[:10], "match": row.match,
             "outcome": row.selection, "odds": float(row.odds),
             "won": row.outcome == "won", "pnl": float(row.pnl),
             "cum": float(cumulative.iloc[i])}
            for i, row in enumerate(priced.itertuples())]
