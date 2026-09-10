"""Loading the two datasets this project consumes.

`history.csv`   38,749 played matches, 22 divisions, 2021-22 to 2025-26, each
                with closing 1X2 and Over/Under 2.5 prices (best and consensus)
                plus shots, shots on target and corners.
`odds_*.csv`    upcoming fixtures with consensus 1X2 prices, no result yet.

Both come from the sibling project and are read, never written.
"""

from pathlib import Path

import pandas as pd

from . import config
from .teams import normalize

# Marks a row that was typed in rather than fetched. See `_with_manual_results`.
MANUAL = "manual"


def parse_dates(series, source=""):
    """Parse a date column that may hold two spellings of the same day.

    Appending to a CSV mixes them: the rows already on disk come back as the
    strings `2026-08-21`, while freshly fetched rows are Timestamps and get
    written as `2026-08-21 00:00:00`. pandas infers the format from the first
    value and then throws on the first row that disagrees — so a file that is
    perfectly readable dies with a message about position 10.

    Every source here writes ISO, so the day is the first ten characters. That
    is deterministic, needs no inference, and cannot be tripped by a mixture.
    """
    text = series.astype(str).str.strip().str.slice(0, 10)
    parsed = pd.to_datetime(text, format="%Y-%m-%d", errors="coerce")
    if parsed.isna().any():
        bad = sorted(set(series[parsed.isna()].astype(str)))[:3]
        raise ValueError(
            f"unparseable dates in {source or 'the data'}: {bad}. "
            "Expected ISO (YYYY-MM-DD, optionally with a time after it).")
    return parsed

HISTORY_COLUMNS = [
    "date", "competition", "season", "home", "away", "home_goals", "away_goals",
    "home_odds", "draw_odds", "away_odds",
    "home_odds_cons", "draw_odds_cons", "away_odds_cons",
    "over25_odds", "under25_odds", "over25_odds_cons", "under25_odds_cons",
    "home_sot", "away_sot", "home_corners", "away_corners",
]


def load_history(path=None) -> pd.DataFrame:
    """Played matches, oldest first, with team keys in `home` / `away`.

    The guard only applies to the default source. Checked unconditionally it
    asked about a file the caller had not named — which passes silently on any
    machine that happens to have fetched history, and fails on one that has
    not, so a test reading its own CSV depended on the developer's data folder.
    """
    source = path or config.HISTORY_CSV
    if path is None:
        config.require_source()
    elif not Path(source).exists():
        raise SystemExit(f"Historical data not found at {source}.")
    df = pd.read_csv(source)

    df["date"] = parse_dates(df["date"], str(source))
    # Keyed from the provider's own name where there is one, not from the key
    # stored beside it. The stored key was normalised by whatever version of
    # the rules was installed on the day the file was fetched, and normalising
    # is lossy: a fix to those rules cannot recover "Wisła Płock" from the
    # "wis a p ock" an older build wrote down. Falling back to the stored key
    # keeps a hand-made CSV working.
    df["home"] = (df["home_team"] if "home_team" in df else df["home_key"]).map(normalize)
    df["away"] = (df["away_team"] if "away_team" in df else df["away_key"]).map(normalize)

    for col in HISTORY_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    df = df.dropna(subset=["home_goals", "away_goals"])
    df[MANUAL] = False
    # Alongside whichever history was actually read, not alongside the
    # configured one. A caller that names its own CSV — every test that builds
    # a two-row history does — must not silently inherit the results typed in
    # on the developer's machine.
    df = _with_manual_results(df, Path(source).with_name(
        config.MANUAL_RESULTS_CSV.name))

    df["home_goals"] = df["home_goals"].astype(int)
    df["away_goals"] = df["away_goals"].astype(int)
    df["total_goals"] = df["home_goals"] + df["away_goals"]
    df["total_corners"] = pd.to_numeric(df["home_corners"], errors="coerce") + \
        pd.to_numeric(df["away_corners"], errors="coerce")

    df = df.sort_values(["date", "competition", "home"]).reset_index(drop=True)
    return df


def _with_manual_results(history, path=None):
    """History plus any result entered by hand, marked as such.

    A last resort, for the match the feed is never going to publish. The score
    is the one thing on this site a reader cannot check for themselves, so it
    does not get to look like the rest: the row carries `manual`, the feed wins
    any fixture both of them have, and the code that judges whether a league's
    results are still arriving ignores these entirely — one row typed in by
    hand says nothing about whether the feed has come back to life.
    """
    source = path or config.MANUAL_RESULTS_CSV
    if not Path(source).exists():
        return history
    try:
        extra = pd.read_csv(source)
    except pd.errors.EmptyDataError:
        # A file someone created and has not filled in yet. Nothing to add is
        # not an error — and this file is far too optional to be allowed to
        # take the whole site down. A malformed one still raises, because a
        # result quietly dropped is worse than a loud failure.
        return history
    if extra.empty:
        return history

    extra["date"] = parse_dates(extra["date"], str(source))
    extra["home"] = extra["home_team"].map(normalize)
    extra["away"] = extra["away_team"].map(normalize)
    extra[MANUAL] = True
    # Typed rather than pd.NA: a column of bare NA arrives as object, and
    # concatenating an all-NA column against a float one is deprecated in
    # pandas and warns on every single load. Taking the dtype from the frame it
    # is about to join gives the concat nothing left to infer.
    for column in history.columns:
        if column not in extra.columns:
            extra[column] = pd.Series(index=extra.index, dtype=history[column].dtype)

    joined = pd.concat([history, extra[history.columns]], ignore_index=True)
    # Feed first, so `keep="first"` drops the hand-typed row on the day the
    # feed finally publishes the match. That is the direction that must win.
    return joined.drop_duplicates(subset=["competition", "date", "home", "away"],
                                  keep="first")


def from_feed(history):
    """History minus anything entered by hand.

    For the two questions that are about the FEED rather than about football:
    has this league stopped publishing, and is a bet's result ever going to
    arrive. A row someone typed in must not answer either of them yes.
    """
    columns = getattr(history, "columns", ())
    if history is None or len(history) == 0 or MANUAL not in columns:
        return history
    return history[~history[MANUAL].fillna(False).astype(bool)]


def load_fixtures(source_dir=None, include_started=False, now=None) -> pd.DataFrame:
    """Upcoming fixtures, deduplicated to the most recent price per match.

    UPCOMING is the operative word. Price files are appended to, never pruned,
    so a match played last week is still sitting in them — and without this
    filter the card goes on offering it. That failure is quiet and gets worse
    with time: "best pick of the day" keeps naming a fixture that has already
    been played, the ledger refuses to record a day that has gone, and the
    history simply stops growing while every page still looks populated.

    Kick-off time decides it where the feed gives one, so a match at 20:00 is
    still on the card at lunchtime. Otherwise the date does, which is the right
    fallback: a fixture with no time attached is only known to the day.
    """
    source = source_dir or config.SOURCE_DATA
    files = sorted(p for p in source.glob(config.FIXTURE_GLOB) if p.is_file())
    if not files:
        raise SystemExit(
            f"No fixture files matching {config.FIXTURE_GLOB} in {source}.\n"
            "Run `python fb.py fetch odds`, or press Fetch new prices."
        )

    frames = []
    for path in files:
        frame = pd.read_csv(path)
        frame["date"] = parse_dates(frame["date"], path.name)
        frames.append(frame)
    df = pd.concat(frames, ignore_index=True)

    df["fetched_at"] = pd.to_datetime(df.get("fetched_at"), errors="coerce", utc=True)
    # As in `load_history`: the provider's name first, the key beside it only
    # as a fallback, so a stale price file cannot pin a fixture to a spelling
    # the rules have since learned to read.
    df["home"] = (df["home_team"] if "home_team" in df else df["home_key"]).map(normalize)
    df["away"] = (df["away_team"] if "away_team" in df else df["away_key"]).map(normalize)

    if not include_started:
        df = df[_not_started(df, now)]

    # One row per fixture: keep the freshest quote.
    df = (df.sort_values("fetched_at")
            .drop_duplicates(subset=["competition", "home", "away"], keep="last")
            .sort_values(["date", "competition"])
            .reset_index(drop=True))
    return df


def _not_started(df, now=None):
    """Mask of fixtures that have not kicked off yet."""
    moment = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now, tz="UTC")
    if "commence_time" in df.columns:
        kickoff = pd.to_datetime(df["commence_time"], errors="coerce", utc=True)
    else:
        # Typed explicitly: an all-NaT object column makes the fillna below
        # downcast and warn about it.
        kickoff = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
    # A date with no time is treated as the end of that day, so a fixture is
    # only dropped once the day itself is over.
    end_of_day = df["date"].dt.tz_localize("UTC") + pd.Timedelta(days=1)
    return kickoff.fillna(end_of_day) > moment


def history_before(history: pd.DataFrame, competition: str, when) -> pd.DataFrame:
    """Matches usable for a fit made on `when`.

    STRICTLY before, never same-day: on Saturday morning you do not know
    Saturday's other results, and letting them in is the single easiest way to
    make a backtest look clever.
    """
    mask = (history["competition"] == competition) & (history["date"] < when)
    return history.loc[mask]
