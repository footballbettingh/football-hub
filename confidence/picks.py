"""Pricing the fixtures that have not been played yet.

Same code path as the backtest, deliberately. The model is fitted on every
finished match in the competition, the closing-line anchor comes from the same
de-vig, the fusion weight is the same number, and the calibrators are the ones
the evaluation validated. If the shortlist were assembled by a second,
"live-only" path, nothing measured on history would say anything about it.

Two things are attached to every pick that a raw probability does not give you:

* `hit_rate` — what selections in this confidence band ACTUALLY did over the
  historical sample. That is the number to read when the model says 88%.
* `edge` — where a price exists. High probability and good value are different
  questions, and short-priced favourites are usually the market's best-priced
  bets, not its worst.
"""

import numpy as np
import pandas as pd

from . import config, evaluate
from .implied import devig, implied_lambdas
from .teams import build_resolver
from .markets import group_of, label
from .poisson import BlendedGoalsModel, PoissonModel
from .predict import CORNER_MAX, fuse, match_probabilities

FIXTURE_1X2_CONS = ("home_odds_cons", "draw_odds_cons", "away_odds_cons")
FIXTURE_1X2_BEST = ("home_odds", "draw_odds", "away_odds")

# Selections whose price the fixture feed actually carries, so an edge can be
# computed. Everything else is priced by us alone and shows fair odds only.
#
# The totals entries are live only when the feed was fetched with the `totals`
# market — `getattr` returns NaN for a column that is not there. Prices for
# 1X2 alone left the Edge column empty on almost every row of the card, because
# the selections that reach high confidence are handicaps and totals, never a
# match result.
PRICED = {
    "1x2_home": "home_odds", "1x2_draw": "draw_odds", "1x2_away": "away_odds",
    "ou2.5_over": "over25_odds", "ou2.5_under": "under25_odds",
    "ou1.5_over": "over15_odds", "ou1.5_under": "under15_odds",
    "ou3.5_over": "over35_odds", "ou3.5_under": "under35_odds",
}


def _fixture_market(row, method):
    prices = [getattr(row, c, None) for c in FIXTURE_1X2_CONS]
    if any(p is None or p != p or float(p) <= 1.0 for p in prices):
        prices = [getattr(row, c, None) for c in FIXTURE_1X2_BEST]
        if any(p is None or p != p or float(p) <= 1.0 for p in prices):
            return None
    return devig([float(p) for p in prices], method)


def price_fixtures(history, fixtures, calibrators=None, weight=None,
                   devig_method=None, half_life_days=None, ridge=None,
                   signal_weight=0.5, min_train=None):
    """One row per (fixture, selection), with raw and calibrated probability."""
    weight = config.MARKET_WEIGHT if weight is None else weight
    devig_method = devig_method or config.DEVIG
    half_life_days = config.HALF_LIFE_DAYS if half_life_days is None else half_life_days
    ridge = config.RIDGE if ridge is None else ridge
    min_train = config.MIN_TRAIN_MATCHES if min_train is None else min_train

    rows = []
    for competition, block in fixtures.groupby("competition"):
        train = history[history["competition"] == competition]
        if len(train) < min_train:
            continue
        as_of = block["date"].min()

        model = BlendedGoalsModel(weight=signal_weight, half_life_days=half_life_days,
                                  ridge=ridge, max_goals=12).fit(train, as_of=as_of)
        corners = None
        corner_train = train.dropna(subset=["home_corners", "away_corners"])
        if len(corner_train) >= min_train:
            corners = PoissonModel(("home_corners", "away_corners"),
                                   dixon_coles=False, half_life_days=half_life_days,
                                   ridge=ridge, max_goals=CORNER_MAX)
            corners.fit(corner_train, as_of=as_of)

        # The two providers spell half the clubs differently — the price feed
        # says "Mansfield Town" where the results file says "Mansfield". Left
        # unresolved the team is unknown to the model (priced at league average
        # and flagged new) AND the pick can never be matched to its result, so
        # it sits pending forever with nothing to say why.
        resolver = build_resolver(set(train["home"]) | set(train["away"]))

        for fixture in block.itertuples():
            rows.extend(_price_one(fixture, model, corners, weight, devig_method,
                                   competition, resolver))

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame

    if calibrators is not None:
        frame["prob"] = _calibrate_column(frame, calibrators)
    else:
        frame["prob"] = frame["prob_raw"]

    frame["fair_odds"] = 1.0 / frame["prob"].clip(lower=1e-6)
    frame["edge"] = np.where(frame["odds"].notna(),
                             frame["prob"] * frame["odds"] - 1.0, np.nan)
    return frame.sort_values("prob", ascending=False).reset_index(drop=True)


def _calibrate_column(frame, calibrators):
    """Apply the per-group calibrator to a long frame of picks."""
    out = frame["prob_raw"].to_numpy(dtype=float).copy()
    for group, calibrator in calibrators.by_group.items():
        mask = frame["group"].to_numpy() == group
        if mask.any():
            out[mask] = calibrator(out[mask])
    return out


def _price_one(fixture, model, corners, weight, devig_method, competition,
               resolver=None):
    # Fall back to the feed's own key when nothing resolves: a wrong team is far
    # worse than an unknown one, and `new_team` already says the model is not
    # contributing.
    home = (resolver(fixture.home) if resolver else None) or fixture.home
    away = (resolver(fixture.away) if resolver else None) or fixture.away
    lam_model, mu_model = model.expected_counts(home, away)
    q = _fixture_market(fixture, devig_method)

    if q is None:
        lam, mu, rho = lam_model, mu_model, model.rho
        lam_market = mu_market = resid = np.nan
    else:
        lam_market, mu_market, rho_market, resid = implied_lambdas(
            float(q[0]), float(q[1]), float(q[2]), None, rho=model.rho,
            init=(lam_model, mu_model))
        lam, mu, rho = fuse(lam_model, mu_model, model.rho,
                            lam_market, mu_market, rho_market, weight)

    corner_lam = corner_mu = np.nan
    if corners is not None:
        corner_lam, corner_mu = corners.expected_counts(home, away)

    probs = match_probabilities(lam, mu, rho, 12, corner_lam, corner_mu)
    new_team = not (model.knows(home) and model.knows(away))

    out = []
    for key, value in probs.items():
        price = getattr(fixture, PRICED[key], np.nan) if key in PRICED else np.nan
        out.append({
            "date": fixture.date,
            "competition": competition,
            # Display names for reading, normalised keys for joining back onto
            # results later — the two providers spell half the clubs differently.
            "home": home,
            "away": away,
            "home_team": getattr(fixture, "home_team", fixture.home),
            "away_team": getattr(fixture, "away_team", fixture.away),
            "match": f"{getattr(fixture, 'home_team', fixture.home)} v "
                     f"{getattr(fixture, 'away_team', fixture.away)}",
            "key": key,
            "group": group_of(key),
            "selection": label(key),
            "prob_raw": float(value),
            "odds": float(price) if price == price else np.nan,
            "lam": lam,
            "mu": mu,
            "new_team": bool(new_team),
            "implied_resid": float(resid) if resid == resid else np.nan,
        })
    return out


MIN_BAND_SAMPLE = 200      # below this a band has not been tested, only visited
MAX_OVERSTATEMENT = 0.02   # how far a band may overstate itself and still count


def group_ceilings(reliability, min_n=MIN_BAND_SAMPLE, max_gap=MAX_OVERSTATEMENT):
    """The highest confidence each market has actually justified.

    Walk a group's bands upward and stop at the first one that either has too
    few historical bets to have been tested, or overstated itself by more than
    `max_gap`. Everything above that point is a number the project cannot
    stand behind.

    The rule is deliberately one-sided. A band that lands MORE often than it
    claimed is a forecast being modest, which costs the user nothing; a band
    that lands less often is the failure this whole project exists to avoid.
    Corners fail at 85% and BTTS at 75% — both are model-only or near enough,
    and the tails are where an unanchored Poisson goes wrong.
    """
    out = {}
    if reliability is None or reliability.empty or "scope" not in reliability:
        return out
    floor = evaluate.CEILING_FLOOR
    for group, block in reliability[reliability["scope"] != "all"].groupby("scope"):
        # A ceiling is a statement about high confidence. Starting the walk in
        # the 30-40% band would let a thin bucket down there truncate a market
        # that is perfectly well behaved everywhere it matters.
        block = block[block["band_low"] >= floor].sort_values("band_low")
        ceiling, expected_low = 0.0, None
        for row in block.itertuples():
            gap_ok = row.actual >= row.predicted - max_gap
            contiguous = expected_low is None or row.band_low <= expected_low + 1e-9
            if row.n < min_n or not gap_ok or not contiguous:
                break
            ceiling, expected_low = row.band_high, row.band_high
        out[group] = ceiling
    return out


def attach_hit_rates(picks, reliability):
    """What each pick's confidence band actually did, in its own market.

    Its own market where the evidence supports it: a band with fewer than
    `MIN_BAND_SAMPLE` bets in that group falls back to the all-markets row
    rather than quoting a hit rate off nine historical bets.
    """
    picks = picks.copy()
    if picks.empty or reliability is None or reliability.empty:
        picks["hit_rate"] = np.nan
        picks["hit_rate_predicted"] = np.nan
        picks["hit_rate_n"] = 0
        picks["validated"] = True
        return picks

    overall = reliability[reliability["scope"] == "all"].sort_values("band_low")
    probs = picks["prob"].to_numpy()

    def lookup(table):
        lows = table["band_low"].to_numpy()
        slot = np.clip(np.searchsorted(lows, probs, side="right") - 1, 0, len(table) - 1)
        return (table["actual"].to_numpy()[slot], table["predicted"].to_numpy()[slot],
                table["n"].to_numpy()[slot])

    hit_rate, predicted, sample = lookup(overall)
    for group, block in reliability[reliability["scope"] != "all"].groupby("scope"):
        block = block.sort_values("band_low")
        rate, said, n = lookup(block)
        use = (picks["group"].to_numpy() == group) & (n >= MIN_BAND_SAMPLE)
        hit_rate = np.where(use, rate, hit_rate)
        predicted = np.where(use, said, predicted)
        sample = np.where(use, n, sample)

    ceilings = group_ceilings(reliability)
    picks["hit_rate"] = hit_rate
    # What the band claimed, kept alongside what it did: the ratio of the two is
    # what `confidence_score` discounts by.
    picks["hit_rate_predicted"] = predicted
    picks["hit_rate_n"] = sample
    picks["validated"] = [
        prob <= ceilings.get(group, 1.0) + 1e-9
        for prob, group in zip(probs, picks["group"].to_numpy())
    ]
    return picks


def shortlist(picks, min_confidence=None, per_match=1, groups=None, limit=None,
              min_odds=None, validated_only=True):
    """The card: the strongest selections, at most `per_match` from a fixture.

    Capping per match is not a scoring rule, it is a correlation rule. Over 1.5
    and Home -1.5 in the same game are close to the same bet, and a list of
    twelve picks that is really four independent opinions will look far more
    reliable than it is.

    `min_odds` filters on FAIR odds, not offered ones, and exists because the
    honest answer to "what is most likely to happen?" is a wall of 99% picks
    paying 1.01. Asking for the most confident selection that still pays 1.25
    is a different and usually more useful question.
    """
    if picks.empty:
        return picks
    min_confidence = config.MIN_CONFIDENCE if min_confidence is None else min_confidence

    out = picks[picks["prob"] >= min_confidence]
    if validated_only and "validated" in out.columns:
        # A 93% corners pick is not a 93% pick — see group_ceilings.
        out = out[out["validated"]]
    if "implied_resid" in out.columns:
        # A line the score matrix could not reproduce is not a line we can
        # derive BTTS or a handicap from, whatever number came out.
        resid = out["implied_resid"].fillna(0.0)
        out = out[resid <= config.MAX_IMPLIED_RESID]
    if groups:
        out = out[out["group"].isin(groups)]
    if min_odds:
        out = out[out["fair_odds"] >= float(min_odds)]
    if per_match:
        out = (out.sort_values("prob", ascending=False)
                  .groupby("match", sort=False)
                  .head(per_match))
    out = out.sort_values("prob", ascending=False)
    return out.head(limit) if limit else out


# -- singling one out ------------------------------------------------------

MIN_BAND_FOR_SCORE = 200     # below this the band record is too thin to trust


def confidence_score(row):
    """The claim, discounted by how much its band has historically overstated.

    Ranking on the raw probability lets a model talk itself into its own worst
    errors. Ranking on the band's hit rate instead does not work either: a hit
    rate is shared by every selection in the band, so thousands of rows collapse
    to one score and the ordering inside the band becomes arbitrary.

    So the band supplies a *factor*, actual / predicted, capped at 1. A band
    that delivered what it promised leaves the claim alone; one that came up two
    points short scales every claim in it down by the same proportion, which
    preserves the ordering within the band and still demotes the whole band
    against better-behaved ones. Bands with too few historical bets to have been
    tested are left at 1 rather than adjusted by noise.
    """
    prob = float(row["prob"])
    actual, predicted = row.get("hit_rate"), row.get("hit_rate_predicted")
    n = row.get("hit_rate_n") or 0
    if (actual is None or actual != actual or predicted is None
            or predicted != predicted or not predicted or n < MIN_BAND_FOR_SCORE):
        return prob
    return prob * min(1.0, float(actual) / float(predicted))


def _with_score(picks):
    out = picks.copy()
    out["score"] = [confidence_score(row) for _, row in out.iterrows()]
    return out


def _rank(picks):
    """Score first, then the weight of evidence behind the band.

    Whole blocks of the card share a score — the accumulator's legs sit exactly
    on the qualifying threshold by construction, so dozens of candidates tie.
    Breaking those ties by how many historical bets the band was measured on
    makes the choice reproducible and prefers the better-tested market.
    """
    columns = ["score"] + [c for c in ("hit_rate_n", "prob") if c in picks.columns]
    return _with_score(picks).sort_values(columns, ascending=False)


def best_of_day(picks, odds_min=None, odds_max=None, day=None,
                validated_only=True):
    """The single most reliable selection in a bettable price range.

    Ranked on `confidence_score`, not on the raw probability, and restricted to
    one day so that "of the day" means something. Without the price range the
    answer is always the same shape of bet — a 99% handicap paying 1.01 — which
    is true, useless, and not what anyone means by a best pick.
    """
    if picks.empty:
        return None
    odds_min = config.BEST_ODDS_MIN if odds_min is None else odds_min
    odds_max = config.BEST_ODDS_MAX if odds_max is None else odds_max

    out = picks
    if validated_only and "validated" in out.columns:
        out = out[out["validated"]]
    if "implied_resid" in out.columns:
        out = out[out["implied_resid"].fillna(0.0) <= config.MAX_IMPLIED_RESID]
    out = out[out["fair_odds"].between(odds_min, odds_max)]
    if out.empty:
        return None

    day = pd.to_datetime(day) if day is not None else pd.to_datetime(out["date"]).min()
    same_day = out[pd.to_datetime(out["date"]).dt.normalize() == day.normalize()]
    if same_day.empty:
        return None

    scored = _rank(same_day)
    best = scored.iloc[0]
    return {
        "day": day.strftime("%Y-%m-%d"),
        "candidates": int(len(scored)),
        **{key: (None if pd.isna(best[key]) else best[key])
           for key in ("date", "competition", "competition_name", "home", "away",
                       "match", "selection", "key", "group", "prob", "fair_odds",
                       "odds", "edge", "hit_rate", "hit_rate_n", "new_team")
           if key in best.index},
        "score": float(best["score"]),
    }


def match_days(picks, days=None, validated_only=True):
    """The next few days that actually have fixtures on the card.

    Match days rather than calendar days on purpose: during an international
    break the horizon should stretch to the next real fixtures instead of
    showing two empty panels for tomorrow and the day after.
    """
    if picks.empty:
        return []
    days = config.PICK_DAYS if days is None else int(days)
    out = picks
    if validated_only and "validated" in out.columns:
        out = out[out["validated"]]
    available = sorted(pd.to_datetime(out["date"]).dt.normalize().unique())
    return [pd.Timestamp(d).strftime("%Y-%m-%d") for d in available[:days]]


def daily_slate(picks, days=None, bands=None, validated_only=True):
    """The best pick in each price band, for each of the next few match days.

    One pick a day takes ten months to reach a sample anyone should read. Three
    bands across three days is nine measurements a day, at three different
    confidence levels — which is also what makes it possible to check whether
    the forecast is as well calibrated at 40% as it is at 70%.

    Bands are exclusive of each other by price, so the same selection can never
    appear twice; and a band with nothing in it is simply absent rather than
    filled with the nearest thing outside its range.
    """
    bands = bands or config.PICK_BANDS
    order = [b for b in config.BAND_ORDER if b in bands] + \
            [b for b in bands if b not in config.BAND_ORDER]

    slate = []
    for day in match_days(picks, days, validated_only):
        for band in order:
            low, high = bands[band]
            best = best_of_day(picks, odds_min=low, odds_max=high, day=day,
                               validated_only=validated_only)
            if best:
                slate.append({"band": band, "band_low": low, "band_high": high,
                              **best})
    return slate


def best_accumulator(picks, legs=None, target_odds=None, validated_only=True,
                     days=None):
    """The accumulator most likely to land, among those paying `target_odds`.

    Each leg has to clear the `legs`-th root of the target on its own, so the
    combined price clears the target by construction and no single long shot
    carries the slip. Within that constraint the legs are the highest-scoring
    available, one per fixture.

    One leg per fixture is not a preference. Two selections on the same match
    are correlated, and multiplying them overstates the accumulator — usually
    by a lot, since the pair that looks attractive together (Over 1.5 and Home
    -1.5, say) is close to the same bet twice.

    Legs are confined to the same short horizon as the slate. Left unbounded
    the search happily paired a match on the 14th with one on the 26th, which
    is a slip nobody would place: it cannot be settled for a fortnight, and the
    price on the far leg will have moved several times before it starts.
    """
    if picks.empty:
        return None
    legs = config.ACCA_LEGS if legs is None else int(legs)
    target_odds = config.ACCA_TARGET_ODDS if target_odds is None else float(target_odds)
    if legs < 2:
        raise ValueError("an accumulator needs at least two legs")

    per_leg = target_odds ** (1.0 / legs)
    horizon = match_days(picks, days, validated_only)
    out = picks[pd.to_datetime(picks["date"]).dt.strftime("%Y-%m-%d").isin(horizon)]
    if validated_only and "validated" in out.columns:
        out = out[out["validated"]]
    if "implied_resid" in out.columns:
        out = out[out["implied_resid"].fillna(0.0) <= config.MAX_IMPLIED_RESID]
    out = out[out["fair_odds"] >= per_leg]
    if out.empty:
        return None

    chosen = _rank(out).drop_duplicates("match").head(legs)
    if len(chosen) < legs:
        return None

    probability = float(chosen["prob"].prod())
    priced = bool(chosen["odds"].notna().all())
    days = sorted(pd.to_datetime(chosen["date"]).dt.strftime("%Y-%m-%d"))
    return {
        "legs": int(len(chosen)),
        "target_odds": target_odds,
        "min_leg_odds": per_leg,
        "probability": probability,
        "fair_odds": 1.0 / max(probability, 1e-12),
        "offered_odds": float(chosen["odds"].prod()) if priced else None,
        "weakest_leg": float(chosen["prob"].min()),
        "first_day": days[0],
        "last_day": days[-1],
        "selections": [
            {key: (None if pd.isna(row[key]) else row[key])
             # home/away keys travel with the leg so it can be settled later
             for key in ("date", "competition", "competition_name", "home",
                         "away", "match", "key", "selection", "prob",
                         "fair_odds", "odds", "hit_rate", "hit_rate_n")
             if key in chosen.columns}
            for _, row in chosen.iterrows()
        ],
    }


def accumulators(card, sizes=(2, 3, 4), pool=8):
    """Parlays built from the top picks, one leg per fixture.

    The joint probability is a straight product, which is only right because
    the legs are different matches. Two selections from one fixture are not
    independent and multiplying them would overstate the parlay — that is why
    `shortlist(per_match=1)` feeds this.
    """
    if card.empty:
        return pd.DataFrame()

    legs = card.drop_duplicates("match").head(pool)
    out = []
    for size in sizes:
        if len(legs) < size:
            continue
        chosen = legs.head(size)
        joint = float(np.prod(chosen["prob"].to_numpy()))
        priced = chosen["odds"].notna().all()
        out.append({
            "legs": size,
            "selections": " + ".join(f"{r.match}: {r.selection}"
                                     for r in chosen.itertuples()),
            "probability": joint,
            "fair_odds": 1.0 / max(joint, 1e-9),
            "offered_odds": float(np.prod(chosen["odds"].to_numpy())) if priced else np.nan,
        })
    return pd.DataFrame(out)
