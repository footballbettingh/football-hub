"""football-data.org — match results and scheduled fixtures.

Free tier, verified against the live API:
  - 13 competitions, 10 requests/minute
  - one request returns a whole 380-match season
  - the per-match `odds` field is NOT included (returns "Activate Odds-Package"),
    so odds come from other sources entirely

Pacing steers by the API's own X-Requests-Available-Minute and
X-RequestCounter-Reset headers rather than a fixed sleep, so it never waits
longer than needed and never overruns when responses are slow.
"""

import json
import sys
import time

import pandas as pd
import requests

from .. import config
from ..teams import normalize

USER_AGENT = "value-bets-mvp/0.3"

# Verified available on the free plan (GET /v4/competitions).
FREE_COMPETITIONS = ["PL", "ELC", "BL1", "SA", "PD", "FL1", "DED", "PPL", "BSA", "CL"]

# football-data.org code -> The Odds API sport key.
COMPETITION_TO_SPORT = {
    "PL": "soccer_epl",
    "ELC": "soccer_efl_champ",
    "BL1": "soccer_germany_bundesliga",
    "SA": "soccer_italy_serie_a",
    "PD": "soccer_spain_la_liga",
    "FL1": "soccer_france_ligue_one",
    "DED": "soccer_netherlands_eredivisie",
    "PPL": "soccer_portugal_primeira_liga",
    "BSA": "soccer_brazil_campeonato",
    "CL": "soccer_uefa_champs_league",
}
SPORT_TO_COMPETITION = {v: k for k, v in COMPETITION_TO_SPORT.items()}


class Client:
    def __init__(self, key=None, session=None):
        self.key = key or config.FOOTBALL_DATA_KEY
        self.session = session or requests.Session()
        self.session.headers.update({"X-Auth-Token": self.key, "User-Agent": USER_AGENT})
        self.available = config.FOOTBALL_DATA_RATE_LIMIT

    def get(self, path, params=None, max_retries=3):
        url = f"{config.FOOTBALL_DATA_BASE}{path}"

        for attempt in range(max_retries):
            if self.available <= 0:
                self._cool_off(60)

            resp = self.session.get(url, params=params, timeout=30)

            available = resp.headers.get("X-Requests-Available-Minute")
            self.available = int(available) if available is not None else self.available - 1

            if resp.status_code == 429:
                wait = int(resp.headers.get("X-RequestCounter-Reset", 60)) + 1
                print(f"  rate limited, waiting {wait}s", file=sys.stderr)
                self._cool_off(wait)
                continue

            if resp.status_code in (500, 502, 503, 504):
                backoff = 2 ** attempt
                print(f"  {resp.status_code} from server, retry in {backoff}s", file=sys.stderr)
                time.sleep(backoff)
                continue

            resp.raise_for_status()
            return resp.json()

        raise RuntimeError(f"giving up on {path} after {max_retries} attempts")

    def _cool_off(self, seconds):
        time.sleep(seconds)
        self.available = config.FOOTBALL_DATA_RATE_LIMIT


def parse_matches(payload, competition):
    rows = []
    for m in payload.get("matches", []):
        full_time = (m.get("score") or {}).get("fullTime") or {}
        if full_time.get("home") is None or full_time.get("away") is None:
            continue  # not played, postponed, or awarded without a score
        half_time = (m.get("score") or {}).get("halfTime") or {}
        rows.append({
            "date": m["utcDate"][:10],
            "competition": competition,
            "season": (m.get("season") or {}).get("startDate", "")[:4],
            "matchday": m.get("matchday"),
            "home_team": m["homeTeam"]["name"],
            "away_team": m["awayTeam"]["name"],
            "home_goals": full_time["home"],
            "away_goals": full_time["away"],
            "ht_home_goals": half_time.get("home"),
            "ht_away_goals": half_time.get("away"),
        })
    return rows


def fetch_matches(competitions, seasons, use_cache=True):
    config.require("FOOTBALL_DATA_KEY")
    config.ensure_dirs()
    client = Client()

    all_rows = []
    for comp in competitions:
        for season in seasons:
            cache = config.RAW_DIR / f"fd_{comp}_{season}.json"
            if use_cache and cache.exists():
                payload = json.loads(cache.read_text(encoding="utf-8"))
                print(f"{comp} {season}: cached")
            else:
                try:
                    payload = client.get(f"/competitions/{comp}/matches",
                                         params={"season": season, "status": "FINISHED"})
                except requests.HTTPError as exc:
                    code = exc.response.status_code
                    hint = {403: "not on your plan", 404: "no such competition/season"}.get(code, "")
                    print(f"{comp} {season}: skipped ({code} {hint})", file=sys.stderr)
                    continue
                cache.write_text(json.dumps(payload), encoding="utf-8")
                got = (payload.get("resultSet") or {}).get("count", 0)
                print(f"{comp} {season}: {got} matches (quota left this minute: {client.available})")
            all_rows.extend(parse_matches(payload, comp))

    if not all_rows:
        raise SystemExit("No matches fetched - check competitions/seasons and your plan.")

    frame = pd.DataFrame(all_rows)
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.drop_duplicates(subset=["date", "home_team", "away_team"])
    frame["home_key"] = frame.home_team.map(normalize)
    frame["away_key"] = frame.away_team.map(normalize)
    return frame.sort_values("date").reset_index(drop=True)


def check():
    """Cheap liveness probe for the CLI's `quota` command."""
    client = Client()
    payload = client.get("/competitions")
    return payload["count"], client.available
