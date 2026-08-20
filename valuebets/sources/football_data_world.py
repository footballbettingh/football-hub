"""football-data.co.uk "extra" section — 16 more countries, one file each.

A different schema from the main section, and a poorer one:

    main section    Date, HomeTeam, AwayTeam, FTHG, FTAG, MaxC*, AvgC*,
                    MaxC>2.5, AvgC>2.5, HS/AS/HST/AST
    extra section   Country, League, Season, Date, Home, Away, HG, AG,
                    MaxC*, AvgC*                       <- and nothing else

So these matches support the **1X2 market only** (no Over/Under prices exist)
and only the **goals** signal (no shot counts). Both degrade gracefully: the
backtest skips markets whose price columns are absent, and the model reports
`signal_fallback` rather than silently rating teams on something else.

One file can hold several leagues (Denmark ships Superliga and its playoff as
separate `League` values), so competition codes are derived per row rather than
per file.
"""

import re
import sys
import time
import unicodedata

import numpy as np
import pandas as pd
import requests

from .. import config
from ..teams import normalize

BASE = "https://www.football-data.co.uk/new"

COUNTRIES = {
    "ARG": "Argentina", "AUT": "Austria", "BRA": "Brazil", "CHN": "China",
    "DNK": "Denmark", "FIN": "Finland", "IRL": "Ireland", "JPN": "Japan",
    "MEX": "Mexico", "NOR": "Norway", "POL": "Poland", "ROU": "Romania",
    "RUS": "Russia", "SWE": "Sweden", "SWZ": "Switzerland", "USA": "USA",
}

REQUIRED = ["Date", "Home", "Away", "HG", "AG", "MaxCH", "MaxCD", "MaxCA",
            "AvgCH", "AvgCD", "AvgCA"]


def competition_code(country, league):
    """Stable short code, e.g. Brazil/Serie A -> BRA-SERIEA.

    Derived rather than hand-mapped: these files add leagues over time, and a
    missing entry in a lookup table would silently drop matches.
    """
    text = unicodedata.normalize("NFKD", str(league or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).upper()
    text = re.sub(r"[^A-Z0-9]+", "", text)[:8] or "MAIN"
    return f"{country}-{text}"


def parse(text, code):
    frame = pd.read_csv(pd.io.common.StringIO(text), encoding_errors="replace")
    frame = frame.dropna(how="all", axis=1).dropna(how="all")

    missing = [c for c in REQUIRED if c not in frame.columns]
    if missing:
        raise ValueError(f"{code}: missing columns {missing}")
    frame = frame.dropna(subset=REQUIRED)

    country = COUNTRIES.get(code, code)
    out = pd.DataFrame({
        "date": pd.to_datetime(frame["Date"], dayfirst=True, errors="coerce"),
        "competition": [competition_code(code, lg) for lg in frame.get("League", "")],
        "season": frame.get("Season", ""),
        "country": country,
        "home_team": frame["Home"].astype(str).str.strip(),
        "away_team": frame["Away"].astype(str).str.strip(),
        "home_goals": pd.to_numeric(frame["HG"], errors="coerce"),
        "away_goals": pd.to_numeric(frame["AG"], errors="coerce"),
        "home_odds": pd.to_numeric(frame["MaxCH"], errors="coerce"),
        "draw_odds": pd.to_numeric(frame["MaxCD"], errors="coerce"),
        "away_odds": pd.to_numeric(frame["MaxCA"], errors="coerce"),
        "home_odds_cons": pd.to_numeric(frame["AvgCH"], errors="coerce"),
        "draw_odds_cons": pd.to_numeric(frame["AvgCD"], errors="coerce"),
        "away_odds_cons": pd.to_numeric(frame["AvgCA"], errors="coerce"),
        "price_kind": "closing",
    })
    out = out.dropna(subset=["date", "home_goals", "away_goals",
                             "home_odds", "draw_odds", "away_odds"])
    out["home_goals"] = out.home_goals.astype(int)
    out["away_goals"] = out.away_goals.astype(int)
    out["home_key"] = out.home_team.map(normalize)
    out["away_key"] = out.away_team.map(normalize)
    return out


def fetch(codes=None, since=2021, use_cache=True, pause=0.5):
    """Download and normalise the extra-section country files."""
    config.ensure_dirs()
    codes = [c.strip().upper() for c in (codes or COUNTRIES)]
    session = requests.Session()
    session.headers.update({"User-Agent": "value-bets-mvp/0.4"})

    frames = []
    for code in codes:
        cache = config.RAW_DIR / f"fdw_{code}.csv"
        if use_cache and cache.exists():
            text = cache.read_text(encoding="utf-8", errors="replace")
            source = "cached"
        else:
            try:
                resp = session.get(f"{BASE}/{code}.csv", timeout=45)
                resp.raise_for_status()
            except requests.RequestException as exc:
                print(f"{code}: skipped ({exc})", file=sys.stderr)
                continue
            text = resp.content.decode("utf-8-sig", errors="replace")
            cache.write_text(text, encoding="utf-8")
            source = "downloaded"
            time.sleep(pause)

        try:
            frame = parse(text, code)
        except ValueError as exc:
            print(f"  {exc}", file=sys.stderr)
            continue

        # Season labels vary ("2024" vs "2024/2025"); filter on the parsed date.
        frame = frame[frame.date.dt.year >= since]
        if frame.empty:
            continue
        frames.append(frame)
        print(f"{code} {COUNTRIES.get(code, code):12} {len(frame):5,} matches, "
              f"{frame.competition.nunique()} league(s)  ({source})")

    if not frames:
        raise SystemExit("Nothing fetched - check the country codes.")

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["date", "home_key", "away_key"])
    return combined.sort_values("date").reset_index(drop=True)
