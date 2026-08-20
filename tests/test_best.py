"""Best pick of the day, and the accumulator pick.

Both answer "which bet" rather than "how likely", so both can be wrong in ways
a probability cannot: picking a bet outside the price range it promised, ranking
on a claim the record does not support, or building an accumulator out of two
legs from the same match.
"""

import pandas as pd
import pytest

from confidence import config, picks as picks_mod


def card(rows):
    """rows: dicts overriding a sane default selection."""
    base = {
        "date": "2026-08-14", "competition": "PL", "competition_name": "Premier League",
        "match": "a v b", "selection": "Home win", "key": "1x2_home", "group": "1x2",
        "prob": 0.55, "fair_odds": 1 / 0.55, "odds": None, "edge": None,
        "hit_rate": None, "hit_rate_predicted": None, "hit_rate_n": 0,
        "new_team": False, "validated": True, "implied_resid": 0.0,
    }
    out = []
    for row in rows:
        merged = dict(base)
        merged.update(row)
        # Team keys travel with every pick so an accumulator leg can be settled
        # later; derived from the match name unless a test sets them.
        parts = str(merged["match"]).split(" v ")
        merged.setdefault("home", parts[0])
        merged.setdefault("away", parts[-1])
        # Keep the two consistent unless a test is deliberately setting both.
        if "fair_odds" not in row:
            merged["fair_odds"] = 1 / merged["prob"]
        out.append(merged)
    return pd.DataFrame(out)


# -- best pick of the day --------------------------------------------------

def test_the_best_pick_sits_inside_the_price_range():
    """Without the range the answer is always a 99% handicap paying 1.01 —
    true, and not what anyone means by a best pick."""
    table = card([
        {"match": "certain v thing", "prob": 0.98},          # fair 1.02
        {"match": "a v b", "prob": 0.60},                    # fair 1.67
        {"match": "c v d", "prob": 0.40},                    # fair 2.50
    ])
    best = picks_mod.best_of_day(table)
    assert best["match"] == "a v b"
    assert config.BEST_ODDS_MIN <= best["fair_odds"] <= config.BEST_ODDS_MAX


def test_the_band_record_can_outrank_a_bigger_claim():
    """A 61% claim from a band that came up 15% short loses to a 58% claim from
    a band that held. Ranking on the claim alone is how a model talks itself
    into its own worst errors."""
    table = card([
        {"match": "loud v claim", "prob": 0.61, "hit_rate": 0.51,
         "hit_rate_predicted": 0.60, "hit_rate_n": 5000},
        {"match": "quiet v honest", "prob": 0.58, "hit_rate": 0.58,
         "hit_rate_predicted": 0.58, "hit_rate_n": 5000},
    ])
    assert picks_mod.best_of_day(table)["match"] == "quiet v honest"


def test_the_discount_keeps_the_order_inside_a_band():
    """A hit rate is shared by thousands of selections. Ranking on it directly
    would collapse them all to one score and make the order arbitrary; a factor
    scales them and leaves the ordering intact."""
    table = card([
        {"match": "a v b", "prob": 0.62, "hit_rate": 0.60,
         "hit_rate_predicted": 0.62, "hit_rate_n": 9000},
        {"match": "c v d", "prob": 0.59, "hit_rate": 0.60,
         "hit_rate_predicted": 0.62, "hit_rate_n": 9000},
    ])
    assert picks_mod.best_of_day(table)["match"] == "a v b"


def test_a_band_that_beat_its_claim_gets_no_bonus():
    """Landing more often than promised is modesty, not evidence of an edge —
    inflating a claim on the back of it is the mistake in the other direction."""
    modest = {"prob": 0.60, "hit_rate": 0.70, "hit_rate_predicted": 0.60,
              "hit_rate_n": 9000}
    assert picks_mod.confidence_score(modest) == pytest.approx(0.60)


def test_a_thin_band_record_does_not_override_the_model():
    """Nine historical bets say nothing; letting them drag a pick down would be
    fitting to noise in the other direction."""
    table = card([
        {"match": "a v b", "prob": 0.61, "hit_rate": 0.10,
         "hit_rate_predicted": 0.61, "hit_rate_n": 9},
        {"match": "c v d", "prob": 0.58, "hit_rate": 0.58,
         "hit_rate_predicted": 0.58, "hit_rate_n": 5000},
    ])
    assert picks_mod.best_of_day(table)["match"] == "a v b"


def test_only_the_first_day_is_considered():
    table = card([
        {"date": "2026-08-14", "match": "today v now", "prob": 0.50},
        {"date": "2026-08-20", "match": "later v then", "prob": 0.62},
    ])
    best = picks_mod.best_of_day(table)
    assert best["match"] == "today v now"
    assert best["day"] == "2026-08-14"


def test_a_named_day_overrides_the_default():
    table = card([
        {"date": "2026-08-14", "match": "today v now", "prob": 0.50},
        {"date": "2026-08-20", "match": "later v then", "prob": 0.62},
    ])
    assert picks_mod.best_of_day(table, day="2026-08-20")["match"] == "later v then"


def test_unverified_and_unrepresentable_picks_are_never_the_best():
    table = card([
        {"match": "over v ceiling", "prob": 0.62, "validated": False},
        {"match": "bad v line", "prob": 0.61, "implied_resid": 0.22},
        {"match": "fine v pick", "prob": 0.55},
    ])
    assert picks_mod.best_of_day(table)["match"] == "fine v pick"


def test_nothing_in_the_range_returns_nothing():
    """Better an empty panel that says so than a bet dragged in from outside
    the range it advertised."""
    assert picks_mod.best_of_day(card([{"prob": 0.95}, {"prob": 0.20}])) is None
    assert picks_mod.best_of_day(card([])) is None


# -- the slate: several days, several bands --------------------------------

def _three_days():
    """A card with something in every band on each of three days."""
    rows = []
    for offset, day in enumerate(("2026-08-14", "2026-08-15", "2026-08-16")):
        for i, prob in enumerate((0.72, 0.55, 0.40, 0.20)):
            rows.append({"date": day, "match": f"{day} m{i}", "prob": prob})
        rows.append({"date": day, "match": f"{day} sure", "prob": 0.97})
    return card(rows)


def test_the_slate_covers_the_next_three_match_days():
    slate = picks_mod.daily_slate(_three_days())
    assert sorted({pick["day"] for pick in slate}) == [
        "2026-08-14", "2026-08-15", "2026-08-16"]


def test_each_day_gets_one_pick_per_band():
    slate = picks_mod.daily_slate(_three_days())
    for day in ("2026-08-14", "2026-08-15", "2026-08-16"):
        bands = [pick["band"] for pick in slate if pick["day"] == day]
        assert bands == ["safe", "main", "value"]


def test_bands_do_not_overlap_so_a_pick_cannot_appear_twice():
    slate = picks_mod.daily_slate(_three_days())
    seen = [(pick["day"], pick["match"], pick["selection"]) for pick in slate]
    assert len(seen) == len(set(seen))
    for pick in slate:
        assert pick["band_low"] <= pick["fair_odds"] <= pick["band_high"]


def test_a_band_with_nothing_in_it_is_absent_rather_than_filled():
    """Reaching outside the range for the nearest thing would quietly file a
    1.05 shot as a 'value' pick and poison the band's record."""
    table = card([{"date": "2026-08-14", "match": "a v b", "prob": 0.55}])
    slate = picks_mod.daily_slate(table)
    assert [pick["band"] for pick in slate] == ["main"]


def test_the_horizon_follows_match_days_not_the_calendar():
    """An international break should push the horizon out, not show two empty
    panels for tomorrow and the day after."""
    table = card([{"date": "2026-08-14", "match": "a v b", "prob": 0.55},
                  {"date": "2026-09-02", "match": "c v d", "prob": 0.55},
                  {"date": "2026-09-05", "match": "e v f", "prob": 0.55}])
    assert picks_mod.match_days(table) == ["2026-08-14", "2026-09-02", "2026-09-05"]


def test_the_horizon_is_configurable():
    assert len(picks_mod.match_days(_three_days(), days=2)) == 2


def test_unverified_picks_never_reach_the_slate():
    table = card([{"date": "2026-08-14", "match": "bad v pick", "prob": 0.55,
                   "validated": False},
                  {"date": "2026-08-14", "match": "ok v pick", "prob": 0.54}])
    slate = picks_mod.daily_slate(table)
    assert [pick["match"] for pick in slate] == ["ok v pick"]


# -- accumulator pick ------------------------------------------------------

def test_every_leg_clears_the_nth_root_of_the_target():
    # 3^(1/3) = 1.442, so a leg has to be at or below 69.3% to qualify — the
    # near-certainties at the top of the card are exactly what this excludes.
    table = card([{"match": f"{i} v x", "prob": p} for i, p in
                  enumerate([0.99, 0.95, 0.68, 0.66, 0.64, 0.60])])
    acca = picks_mod.best_accumulator(table, legs=3, target_odds=3.0)
    floor = 3.0 ** (1 / 3)
    assert len(acca["selections"]) == 3
    assert all(leg["fair_odds"] >= floor for leg in acca["selections"])
    assert [leg["prob"] for leg in acca["selections"]] == [0.68, 0.66, 0.64]
    assert acca["fair_odds"] >= 3.0


def test_the_accumulator_takes_the_safest_qualifying_legs():
    table = card([{"match": f"{i} v x", "prob": p} for i, p in
                  enumerate([0.70, 0.65, 0.60, 0.55])])
    acca = picks_mod.best_accumulator(table, legs=2, target_odds=2.0)
    assert [leg["prob"] for leg in acca["selections"]] == [0.70, 0.65]
    assert acca["probability"] == pytest.approx(0.70 * 0.65)
    assert acca["weakest_leg"] == 0.65


def test_two_legs_never_come_from_the_same_fixture():
    """Over 1.5 and Home -1.5 in one match are close to the same bet.
    Multiplying them overstates the slip, usually badly."""
    table = card([
        {"match": "a v b", "selection": "Over 1.5 goals", "prob": 0.70},
        {"match": "a v b", "selection": "Home -1.5", "prob": 0.69},
        {"match": "c v d", "selection": "Home win", "prob": 0.60},
    ])
    acca = picks_mod.best_accumulator(table, legs=2, target_odds=2.0)
    assert {leg["match"] for leg in acca["selections"]} == {"a v b", "c v d"}


def test_accumulator_legs_stay_inside_the_horizon():
    """Unbounded, the search happily paired a match on the 14th with one on the
    26th — a slip nobody would place, and one that cannot settle for a
    fortnight."""
    # The far leg is the STRONGEST qualifying candidate, so an unbounded search
    # would take it first.
    table = card([{"date": "2026-08-14", "match": "a v b", "prob": 0.68},
                  {"date": "2026-08-15", "match": "c v d", "prob": 0.66},
                  {"date": "2026-08-16", "match": "e v f", "prob": 0.64},
                  {"date": "2026-08-26", "match": "far v away", "prob": 0.69}])
    acca = picks_mod.best_accumulator(table, legs=3, target_odds=3.0, days=3)
    days = {leg["date"] for leg in acca["selections"]}
    assert days <= {"2026-08-14", "2026-08-15", "2026-08-16"}
    assert "far v away" not in {leg["match"] for leg in acca["selections"]}


def test_the_slip_reports_the_span_it_covers():
    table = card([{"date": "2026-08-14", "match": "a v b", "prob": 0.70},
                  {"date": "2026-08-16", "match": "c v d", "prob": 0.68}])
    acca = picks_mod.best_accumulator(table, legs=2, target_odds=2.0)
    assert acca["first_day"] == "2026-08-14"
    assert acca["last_day"] == "2026-08-16"


def test_legs_carry_what_settlement_needs():
    table = card([{"date": "2026-08-14", "match": "a v b", "prob": 0.70},
                  {"date": "2026-08-15", "match": "c v d", "prob": 0.68}])
    acca = picks_mod.best_accumulator(table, legs=2, target_odds=2.0)
    for leg in acca["selections"]:
        assert {"home", "away", "competition", "key"} <= set(leg)


def test_too_few_qualifying_fixtures_returns_nothing():
    table = card([{"match": "a v b", "prob": 0.70}])
    assert picks_mod.best_accumulator(table, legs=4, target_odds=3.0) is None


def test_offered_odds_only_multiply_when_every_leg_has_one():
    both = card([{"match": "a v b", "prob": 0.70, "odds": 1.5},
                 {"match": "c v d", "prob": 0.65, "odds": 1.6}])
    assert picks_mod.best_accumulator(both, legs=2, target_odds=2.0)["offered_odds"] \
        == pytest.approx(1.5 * 1.6)

    partial = card([{"match": "a v b", "prob": 0.70, "odds": 1.5},
                    {"match": "c v d", "prob": 0.65, "odds": None}])
    assert picks_mod.best_accumulator(partial, legs=2,
                                      target_odds=2.0)["offered_odds"] is None


def test_a_one_leg_accumulator_is_refused():
    with pytest.raises(ValueError):
        picks_mod.best_accumulator(card([{"prob": 0.6}]), legs=1)


def test_the_score_never_exceeds_the_claim():
    overstating = {"prob": 0.60, "hit_rate": 0.54, "hit_rate_predicted": 0.60,
                   "hit_rate_n": 5000}
    assert picks_mod.confidence_score(overstating) == pytest.approx(0.60 * 0.9)
    assert picks_mod.confidence_score(
        {"prob": 0.61, "hit_rate": None, "hit_rate_predicted": None,
         "hit_rate_n": 0}) == 0.61
