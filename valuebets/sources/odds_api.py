"""The Odds API — current and upcoming odds.

Free tier: 500 credits/month. /sports and /events cost ZERO credits, so this
module uses them for discovery and validates a sport key before spending
anything. /odds costs [markets] x [regions]. Historical endpoints need a paid
plan and cost 10x, which is why backtesting uses football_data_uk instead.

Every response carries x-requests-remaining / x-requests-used / x-requests-last,
so cost is tracked exactly rather than estimated.
"""

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

from .. import config
from ..teams import normalize
from .football_data_org import SPORT_TO_COMPETITION

USER_AGENT = "value-bets-mvp/0.3"


class Client:
    def __init__(self, key=None, session=None):
        self.key = key or config.ODDS_API_KEY
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.remaining = None
        self.used = None
        self.last_cost = 0

    @staticmethod
    def _is_free(path):
        """Only /sports and the live /events listing are free.

        Matching loosely here (e.g. `"/sports" in path`) would treat
        /sports/{sport}/odds as free and skip the credit floor entirely, which
        is exactly the call the floor exists to guard.
        """
        if path.startswith("/historical"):
            return False  # historical/events costs 1 credit
        return path == "/sports" or path.endswith("/events")

    def get(self, path, params=None):
        if (not self._is_free(path) and self.remaining is not None
                and self.remaining < config.ODDS_API_MIN_CREDITS):
            raise SystemExit(
                f"Odds API credits ({self.remaining}) below floor "
                f"({config.ODDS_API_MIN_CREDITS}). Raise ODDS_API_MIN_CREDITS in .env "
                f"to override.")

        params = dict(params or {}, apiKey=self.key)
        resp = self.session.get(f"{config.ODDS_API_BASE}{path}", params=params, timeout=30)

        for attr, header in (("remaining", "x-requests-remaining"), ("used", "x-requests-used")):
            value = resp.headers.get(header)
            if value is not None:
                setattr(self, attr, int(float(value)))
        self.last_cost = int(float(resp.headers.get("x-requests-last", 0)))

        if resp.status_code == 401:
            raise SystemExit("Odds API rejected the key (401). Check ODDS_API_KEY in .env.")
        if resp.status_code == 422:
            raise SystemExit(f"Odds API rejected the request (422): {resp.text[:200]}")
        resp.raise_for_status()
        return resp.json()

    def report(self):
        return f"credits used {self.used}, remaining {self.remaining}"


def consensus_prices(event, method="best"):
    """Collapse many bookmakers' h2h prices into one set.

    "best"   highest price per outcome (what you'd get, shopping around)
    "median" robust consensus, closer to the true market price

    These are NOT interchangeable. Measured on 39 EPL bookmakers, best-price
    overround is ~0.998 (shopping strips the margin, and can even imply an arb)
    while the median is ~1.065 — the real ~6.5% margin. De-vig the consensus to
    get a fair probability; settle at the best price.
    """
    per_outcome = {}
    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes", []):
                per_outcome.setdefault(outcome["name"], []).append(outcome["price"])

    if not per_outcome:
        return None, 0
    if method == "median":
        prices = {k: float(np.median(v)) for k, v in per_outcome.items()}
    else:
        prices = {k: max(v) for k, v in per_outcome.items()}
    return prices, len(event.get("bookmakers", []))


def totals_prices(event, line, method="best"):
    """Over/Under prices at a specific line, across bookmakers.

    Books quote several lines; only quotes at exactly `line` are comparable, so
    the rest are ignored rather than blended. Returns (over, under) or None.
    """
    over, under = [], []
    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            if market.get("key") not in ("totals", "alternate_totals"):
                continue
            for outcome in market.get("outcomes", []):
                if outcome.get("point") is None or abs(float(outcome["point"]) - line) > 1e-9:
                    continue
                (over if outcome["name"].lower() == "over" else under).append(outcome["price"])

    if not over or not under:
        return None
    pick = (lambda v: float(np.median(v))) if method == "median" else max
    return pick(over), pick(under)


def _overround(prices, home, away):
    try:
        return round(1 / prices[home] + 1 / prices["Draw"] + 1 / prices[away], 4)
    except (KeyError, TypeError, ZeroDivisionError):
        return None


def list_sports(client=None):
    client = client or Client()
    return client.get("/sports")  # free


def fetch_odds(sport_key, regions="eu,uk", markets="h2h,totals", price_method="best",
               totals_lines=(2.5, 1.5, 3.5), client=None):
    """Current odds for upcoming matches. Costs [markets] x [regions] credits.

    `totals_lines` costs nothing to extend. One `totals` request returns every
    line the bookmakers are quoting, and the price is paid per market rather
    than per line, so the only question is which of them we bother to read.
    Measured over 1,122 events of raw responses on disk: 2.5 is quoted on 88%
    of fixtures, 3.5 on 19.5%, 1.5 on 7.2%. The model prices 0.5, 1.5, 2.5,
    3.5 and 4.5; of those, 0.5 and 4.5 are never quoted, so these three are all
    there is to take. 3.5 was being discarded despite being quoted more than
    twice as often as the 1.5 line that was not.
    """
    config.require("ODDS_API_KEY")
    config.ensure_dirs()
    client = client or Client()

    sports = list_sports(client)  # free; validates the key before spending
    valid = {s["key"] for s in sports}
    if sport_key not in valid:
        soccer = sorted(s["key"] for s in sports if s.get("group") == "Soccer")
        raise SystemExit(f"Unknown sport '{sport_key}'. In-season soccer keys:\n  "
                         + "\n  ".join(soccer))

    expected = len(markets.split(",")) * len(regions.split(","))
    print(f"Fetching {sport_key} odds ({regions} / {markets}) - costs ~{expected} credits")

    events = client.get(f"/sports/{sport_key}/odds",
                        params={"regions": regions, "markets": markets, "oddsFormat": "decimal"})
    print(f"  cost {client.last_cost} credits, {client.report()}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (config.RAW_DIR / f"odds_{sport_key}_{stamp}.json").write_text(
        json.dumps(events), encoding="utf-8")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for event in events:
        best, n_books = consensus_prices(event, "best")
        consensus, _ = consensus_prices(event, "median")
        if not best or not consensus:
            continue
        home, away = event["home_team"], event["away_team"]
        if any(p.get(k) is None for p in (best, consensus) for k in (home, away, "Draw")):
            continue

        primary = consensus if price_method == "median" else best
        totals_cols = {}
        for line in totals_lines:
            tag = f"{line:g}".replace(".", "")
            tb = totals_prices(event, line, "best")
            tc = totals_prices(event, line, "median")
            if not tb or not tc:
                continue
            totals_cols[f"over{tag}_odds"] = tb[0]
            totals_cols[f"under{tag}_odds"] = tb[1]
            totals_cols[f"over{tag}_odds_cons"] = tc[0]
            totals_cols[f"under{tag}_odds_cons"] = tc[1]

        rows.append({
            **totals_cols,
            # Odds move; a backtest needs the observation time, not just the date.
            "fetched_at": now,
            "date": event["commence_time"][:10],
            "commence_time": event["commence_time"],
            "sport": sport_key,
            "competition": SPORT_TO_COMPETITION.get(sport_key),
            "home_team": home,
            "away_team": away,
            "home_odds": primary[home],
            "draw_odds": primary["Draw"],
            "away_odds": primary[away],
            "home_odds_cons": consensus[home],
            "draw_odds_cons": consensus["Draw"],
            "away_odds_cons": consensus[away],
            "n_bookmakers": n_books,
            "overround": _overround(primary, home, away),
            "overround_cons": _overround(consensus, home, away),
        })

    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["date"] = pd.to_datetime(frame["date"])
        frame["home_key"] = frame.home_team.map(normalize)
        frame["away_key"] = frame.away_team.map(normalize)
    return frame


def fetch_alternate_totals(sport_key, lines=(1.5,), max_events=None, client=None):
    """Per-event alternate Over/Under lines (1.5, 3.5, …).

    The bulk /odds endpoint only serves *featured* markets, and for soccer that
    means the 2.5 line and nothing else — verified: a totals pull returns 184
    quotes at 2.5 and zero at 1.5. Anything else lives on the per-event odds
    endpoint under `alternate_totals`.

    That is priced per event, so a full round of fixtures costs roughly
    2 credits x [events] — about 140 for a weekend across six leagues, i.e.
    28% of a free month. Hence opt-in, with the bill printed before it is run.

    Returns {(home, away): {column: price}}.
    """
    config.require("ODDS_API_KEY")
    client = client or Client()

    events = client.get(f"/sports/{sport_key}/events")  # free
    if max_events:
        events = events[:max_events]
    print(f"  {len(events)} events x ~2 credits = ~{len(events) * 2} credits")

    out = {}
    for event in events:
        try:
            detail = client.get(f"/sports/{sport_key}/events/{event['id']}/odds",
                                params={"regions": "eu,uk", "markets": "alternate_totals",
                                        "oddsFormat": "decimal"})
        except requests.HTTPError:
            continue
        cols = {}
        for line in lines:
            tag = f"{line:g}".replace(".", "")
            best = totals_prices(detail, line, "best")
            cons = totals_prices(detail, line, "median")
            if not best or not cons:
                continue
            cols[f"over{tag}_odds"], cols[f"under{tag}_odds"] = best
            cols[f"over{tag}_odds_cons"], cols[f"under{tag}_odds_cons"] = cons
        if cols:
            out[(event["home_team"], event["away_team"])] = cols
    print(f"  got alternate lines for {len(out)}/{len(events)} events, {client.report()}")
    return out


def check():
    """Cheap liveness probe for the CLI's `quota` command."""
    client = Client()
    sports = list_sports(client)
    active = sum(1 for s in sports if s.get("group") == "Soccer")
    return active, client.report()
