"""football-data.co.uk — free historical results WITH closing odds.

This is the source that makes an honest backtest possible. The Odds API's
historical endpoints need a paid plan; these CSVs are free, need no key, and go
back decades for the major European leagues.

Crucially they carry *closing* odds — the last price before kickoff, which is
the sharpest number a bookmaker publishes and the correct benchmark for a
backtest. Opening prices flatter a model that is really just slower than the
market.

Two column families matter, and they map onto the same best/consensus split the
live Odds API path uses:

    MaxC{H,D,A}   best closing price across bookmakers -> what you'd be PAID
    AvgC{H,D,A}   average closing price               -> de-vig this for FAIR probability

Falling back to the non-closing Max/Avg columns is supported for older seasons
that predate them, but the fetcher records which it used, because mixing
opening and closing prices in one backtest quietly biases the result.

    https://www.football-data.co.uk/data.php
"""

import io
import sys
import time

import numpy as np
import pandas as pd
import requests

from .. import config
from ..teams import normalize

BASE = "https://www.football-data.co.uk/mmz4281"

# football-data.co.uk division code -> football-data.org competition code, so
# the two sources land in the same namespace.
DIVISIONS = {
    "E0": "PL",    # Premier League
    "E1": "ELC",   # Championship
    "D1": "BL1",   # Bundesliga
    "I1": "SA",    # Serie A
    "SP1": "PD",   # La Liga
    "F1": "FL1",   # Ligue 1
    "N1": "DED",   # Eredivisie
    "P1": "PPL",   # Primeira Liga
    # Second tiers and lower divisions. Sharp money concentrates on the top
    # leagues, so these are where a market is most likely to be beatable.
    # (Measured: it isn't — the gap shrinks 13% but the margin rises more.)
    "E2": "EL1",   # League One
    "E3": "EL2",   # League Two
    "EC": "ENL",   # National League
    "D2": "BL2",   # 2. Bundesliga
    "I2": "SB",    # Serie B
    "SP2": "SD",   # Segunda Division
    "F2": "FL2",   # Ligue 2
    "SC0": "SPL",  # Scottish Premiership
    "SC1": "SCH",  # Scottish Championship
    "SC2": "SC2",  # Scottish League One
    "SC3": "SC3",  # Scottish League Two
    "B1": "BJL",   # Belgian Pro League
    "T1": "TSL",   # Turkish Super Lig
    "G1": "GSL",   # Greek Super League
}

# Every division in the site's main section. All 22 carry closing 1X2, closing
# O/U 2.5 and shot counts (EC is the one exception: no shots).
ALL_DIVISIONS = list(DIVISIONS)

REQUIRED = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]

# Match statistics carried by every season file. Shots on target is the closest
# thing to xG available without scraping: understat forbids automated access
# (robots.txt Disallow: /), FBref blocks it, and StatsBomb's open data barely
# overlaps the seasons we have closing odds for. Shot counts test the same
# hypothesis anyway — that goals are too noisy a signal of attacking quality.
STAT_COLUMNS = {
    "home_shots": "HS", "away_shots": "AS",
    "home_sot": "HST", "away_sot": "AST",
    "home_corners": "HC", "away_corners": "AC",
}


def season_code(start_year: int) -> str:
    """2024 -> '2425' (the 2024/25 season)."""
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def _pick_totals_columns(columns, line="2.5"):
    """Over/Under price columns, closing preferred.

    Only the 2.5 line is published here — checked across all 30 cached
    season-files, none carry 1.5 or 3.5. O/U 1.5 therefore cannot be
    backtested from this source at any sample size; see markets.py.
    """
    closing = (f"MaxC>{line}", f"MaxC<{line}", f"AvgC>{line}", f"AvgC<{line}")
    preclose = (f"Max>{line}", f"Max<{line}", f"Avg>{line}", f"Avg<{line}")
    single = (f"B365>{line}", f"B365<{line}", f"B365>{line}", f"B365<{line}")
    for group, kind in ((closing, "closing"), (preclose, "pre-close"), (single, "single-book")):
        if all(c in columns for c in group):
            return group, kind
    return None, None


def _pick_price_columns(columns):
    """Prefer closing prices; fall back to full-season averages if absent."""
    if all(c in columns for c in ("MaxCH", "MaxCD", "MaxCA", "AvgCH", "AvgCD", "AvgCA")):
        return ("MaxCH", "MaxCD", "MaxCA"), ("AvgCH", "AvgCD", "AvgCA"), "closing"
    if all(c in columns for c in ("MaxH", "MaxD", "MaxA", "AvgH", "AvgD", "AvgA")):
        return ("MaxH", "MaxD", "MaxA"), ("AvgH", "AvgD", "AvgA"), "pre-close"
    if all(c in columns for c in ("B365H", "B365D", "B365A")):
        # Single book: best and consensus collapse to the same number, so
        # de-vigging still works but you lose the shopping premium.
        return ("B365H", "B365D", "B365A"), ("B365H", "B365D", "B365A"), "single-book"
    return None, None, None


def parse_csv(text, division, season):
    frame = pd.read_csv(io.StringIO(text), encoding_errors="replace")
    frame = frame.dropna(how="all", axis=1).dropna(how="all")

    missing = [c for c in REQUIRED if c not in frame.columns]
    if missing:
        raise ValueError(f"{division} {season}: missing columns {missing}")

    best, consensus, kind = _pick_price_columns(frame.columns)
    if best is None:
        raise ValueError(f"{division} {season}: no usable odds columns")

    frame = frame.dropna(subset=REQUIRED + list(best) + list(consensus))

    out = pd.DataFrame({
        # dayfirst: these CSVs are UK format (16/08/2024)
        "date": pd.to_datetime(frame["Date"], dayfirst=True, errors="coerce"),
        "competition": DIVISIONS.get(division, division),
        "season": season,
        "home_team": frame["HomeTeam"].astype(str).str.strip(),
        "away_team": frame["AwayTeam"].astype(str).str.strip(),
        "home_goals": pd.to_numeric(frame["FTHG"], errors="coerce"),
        "away_goals": pd.to_numeric(frame["FTAG"], errors="coerce"),
        "home_odds": pd.to_numeric(frame[best[0]], errors="coerce"),
        "draw_odds": pd.to_numeric(frame[best[1]], errors="coerce"),
        "away_odds": pd.to_numeric(frame[best[2]], errors="coerce"),
        "home_odds_cons": pd.to_numeric(frame[consensus[0]], errors="coerce"),
        "draw_odds_cons": pd.to_numeric(frame[consensus[1]], errors="coerce"),
        "away_odds_cons": pd.to_numeric(frame[consensus[2]], errors="coerce"),
        "price_kind": kind,
    })

    for name, column in STAT_COLUMNS.items():
        out[name] = (pd.to_numeric(frame[column], errors="coerce")
                     if column in frame.columns else np.nan)
    # Over/Under 2.5, same best/consensus split as 1X2. Kept optional: a season
    # without these columns still yields a usable 1X2 dataset rather than none.
    totals, totals_kind = _pick_totals_columns(frame.columns, "2.5")
    if totals:
        out["over25_odds"] = pd.to_numeric(frame[totals[0]], errors="coerce")
        out["under25_odds"] = pd.to_numeric(frame[totals[1]], errors="coerce")
        out["over25_odds_cons"] = pd.to_numeric(frame[totals[2]], errors="coerce")
        out["under25_odds_cons"] = pd.to_numeric(frame[totals[3]], errors="coerce")
        out["totals_kind"] = totals_kind

    out = out.dropna(subset=["date", "home_goals", "away_goals",
                             "home_odds", "draw_odds", "away_odds"])
    out["home_goals"] = out.home_goals.astype(int)
    out["away_goals"] = out.away_goals.astype(int)
    out["home_key"] = out.home_team.map(normalize)
    out["away_key"] = out.away_team.map(normalize)
    return out


def fetch(divisions, seasons, use_cache=True, pause=0.5):
    """Download and normalise season CSVs. Returns one combined DataFrame."""
    config.ensure_dirs()
    session = requests.Session()
    session.headers.update({"User-Agent": "value-bets-mvp/0.3"})

    frames, kinds = [], set()
    for div in divisions:
        for year in seasons:
            code = season_code(year)
            cache = config.RAW_DIR / f"fduk_{div}_{code}.csv"

            if use_cache and cache.exists():
                text = cache.read_text(encoding="utf-8", errors="replace")
                source = "cached"
            else:
                url = f"{BASE}/{code}/{div}.csv"
                try:
                    resp = session.get(url, timeout=30)
                    resp.raise_for_status()
                except requests.RequestException as exc:
                    print(f"{div} {year}: skipped ({exc})", file=sys.stderr)
                    continue
                # These files are Latin-1-ish; decode leniently rather than crash
                # on one stray byte in a referee name.
                text = resp.content.decode("utf-8-sig", errors="replace")
                source = "downloaded"
                time.sleep(pause)  # be polite to a free static host

            try:
                frame = parse_csv(text, div, year)
            except ValueError as exc:
                # A season the site has not published yet answers with an HTML
                # "300 Multiple Choices" page, and a 300 does not raise. Caching
                # that body under a .csv name poisons the cache: every later run
                # reads the HTML back and fails the same way, and the real file
                # is never picked up once it appears.
                print(f"  {div} {year}: {exc}", file=sys.stderr)
                continue

            if source == "downloaded":
                cache.write_text(text, encoding="utf-8")

            kinds.add(frame.price_kind.iloc[0] if len(frame) else "empty")
            frames.append(frame)
            print(f"{div} {year}/{(year + 1) % 100:02d}: {len(frame):3d} matches "
                  f"({frame.price_kind.iloc[0] if len(frame) else '-'} odds, {source})")

    if not frames:
        raise SystemExit("Nothing fetched - check divisions/seasons.")

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["date", "home_key", "away_key"])
    combined = combined.sort_values("date").reset_index(drop=True)

    if len(kinds) > 1:
        print(f"\n  [!] Mixed price types across seasons: {sorted(kinds)}. "
              f"Comparing closing to pre-close prices biases the backtest.",
              file=sys.stderr)
    return combined
