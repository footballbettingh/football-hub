"""The record of what the daily pick did.

A ledger's whole value is that it cannot be improved after the fact, so most of
these tests are about refusing to write: no second pick for a day already
recorded, no pick for a day that has gone, no rewriting a settled row. The rest
check that a graded bet is graded correctly and that money is only counted where
there was a price to take.
"""

import pandas as pd
import pytest

from hub import ledger


@pytest.fixture
def path(tmp_path):
    return tmp_path / "best_picks.csv"


def pick(**overrides):
    base = {
        "day": "2026-08-14", "competition": "AUT-BUNDESLI",
        "competition_name": "Bundesliga (Austria)", "home": "lask", "away": "ried",
        "match": "LASK v Ried", "key": "ou2.5_over", "group": "ou",
        "selection": "Over 2.5 goals", "prob": 0.624, "fair_odds": 1.60,
        "odds": 1.75, "hit_rate": 0.658, "hit_rate_n": 36366,
    }
    base.update(overrides)
    return base


def history(rows):
    """rows: (competition, home, away, date, home_goals, away_goals)."""
    return pd.DataFrame([{
        "competition": comp, "home": home, "away": away,
        "date": pd.Timestamp(date), "home_goals": hg, "away_goals": ag,
        "total_corners": 9.0,
    } for comp, home, away, date, hg, ag in rows])


# -- writing it down -------------------------------------------------------

def test_a_pick_is_recorded_once(path):
    assert ledger.record(pick(), path, today="2026-08-12")["match"] == "LASK v Ried"
    assert len(ledger.load(path)) == 1


def test_the_same_match_day_is_never_recorded_twice(path):
    """Refreshing the card ten times before kick-off must keep the first
    answer. A ledger that follows whichever pick currently looks best would
    show a flattering history and mean nothing."""
    ledger.record(pick(), path, today="2026-08-12")
    again = ledger.record(pick(match="Somebody v Else", prob=0.99), path,
                          today="2026-08-12")
    assert again is None

    frame = ledger.load(path)
    assert len(frame) == 1
    assert frame["match"].iloc[0] == "LASK v Ried"


def test_a_day_that_has_gone_is_not_recorded(path):
    """Otherwise a rebuild against a stale fixture file appends yesterday's
    matches as though they had been called in advance."""
    assert ledger.record(pick(day="2026-08-10"), path, today="2026-08-14") is None
    assert ledger.load(path).empty


def test_today_still_counts_as_recordable(path):
    assert ledger.record(pick(day="2026-08-14"), path, today="2026-08-14")


def test_nothing_to_record_is_not_an_error(path):
    assert ledger.record(None, path) is None
    assert ledger.load(path).empty


def test_two_bands_on_the_same_day_are_both_recorded(path):
    """The day alone is no longer the key — three bands share a match day, and
    keying on the day would silently drop two thirds of the record."""
    ledger.record(pick(band="safe", match="a v b"), path, today="2026-08-12")
    ledger.record(pick(band="main", match="c v d"), path, today="2026-08-12")
    ledger.record(pick(band="value", match="e v f"), path, today="2026-08-12")

    frame = ledger.load(path)
    assert list(frame["band"]) == ["safe", "main", "value"]
    assert ledger.record(pick(band="main", match="different"), path,
                         today="2026-08-12") is None


def test_a_row_written_before_bands_existed_counts_as_the_flagship(path):
    """The only real history there is came from the 1.60-2.20 range. Filing it
    anywhere else would misplace it; leaving it blank would let a duplicate in."""
    frame = ledger.load(path)
    frame.loc[0, :] = None
    frame.loc[0, "day"] = "2026-08-14"
    frame.loc[0, "match"] = "LASK v Ried"
    frame.loc[0, "outcome"] = "pending"
    ledger.save(frame, path)

    reloaded = ledger.load(path)
    assert reloaded["band"].iloc[0] == "main"
    assert ledger.record(pick(day="2026-08-14"), path, today="2026-08-12") is None


def test_the_whole_slate_is_recorded_in_one_call(path):
    slate = [pick(band=band, day=day, match=f"{day}-{band}")
             for day in ("2026-08-14", "2026-08-15")
             for band in ("safe", "main")]
    written = ledger.record_slate(slate, path, today="2026-08-12")
    assert len(written) == 4

    # Running it again the next day adds nothing and changes nothing.
    assert ledger.record_slate(slate, path, today="2026-08-13") == []
    assert len(ledger.load(path)) == 4


def test_summary_splits_by_band(path):
    ledger.record(pick(band="safe", home="a", away="b", match="a v b", odds=1.4),
                  path, today="2026-08-12")
    ledger.record(pick(band="value", home="c", away="d", match="c v d", odds=2.5),
                  path, today="2026-08-12")
    ledger.settle(history([("AUT-BUNDESLI", "a", "b", "2026-08-14", 2, 1),
                           ("AUT-BUNDESLI", "c", "d", "2026-08-14", 0, 0)]), path)

    by_band = {row["band"]: row for row in ledger.summary_by_band(ledger.load(path))}
    assert by_band["safe"]["wins"] == 1
    assert by_band["value"]["losses"] == 1
    assert by_band["value"]["pnl"] == pytest.approx(-1.0)


def test_a_second_day_appends(path):
    ledger.record(pick(day="2026-08-14"), path, today="2026-08-12")
    ledger.record(pick(day="2026-08-15", match="A v B"), path, today="2026-08-12")
    assert list(ledger.load(path)["day"]) == ["2026-08-14", "2026-08-15"]


# -- grading it ------------------------------------------------------------

def test_a_won_bet_pays_the_price_it_was_taken_at(path):
    ledger.record(pick(), path, today="2026-08-12")
    settled = ledger.settle(history([("AUT-BUNDESLI", "lask", "ried",
                                      "2026-08-14", 2, 1)]), path)
    row = ledger.load(path).iloc[0]
    assert settled == 1
    assert row["outcome"] == "won"
    assert row["pnl"] == pytest.approx(0.75)
    assert row["home_goals"] == 2


def test_a_lost_bet_costs_the_stake(path):
    ledger.record(pick(), path, today="2026-08-12")
    ledger.settle(history([("AUT-BUNDESLI", "lask", "ried", "2026-08-14", 1, 0)]), path)
    row = ledger.load(path).iloc[0]
    assert row["outcome"] == "lost"
    assert row["pnl"] == pytest.approx(-1.0)


def test_a_void_bet_returns_the_stake(path):
    ledger.record(pick(key="dnb_home", group="dnb", selection="Home draw-no-bet"),
                  path, today="2026-08-12")
    ledger.settle(history([("AUT-BUNDESLI", "lask", "ried", "2026-08-14", 1, 1)]), path)
    row = ledger.load(path).iloc[0]
    assert row["outcome"] == "void"
    assert row["pnl"] == 0.0


def test_a_bet_with_no_quoted_price_is_graded_but_not_counted(path):
    """Settling it at our own fair odds would return zero by construction and
    look like a result."""
    ledger.record(pick(odds=None), path, today="2026-08-12")
    ledger.settle(history([("AUT-BUNDESLI", "lask", "ried", "2026-08-14", 2, 1)]), path)
    row = ledger.load(path).iloc[0]
    assert row["outcome"] == "won"
    assert pd.isna(row["pnl"])

    head = ledger.summary(ledger.load(path))
    assert head["wins"] == 1 and head["unpriced"] == 1
    assert head["pnl"] == 0.0 and head["priced"] == 0


def test_an_unplayed_match_stays_pending(path):
    ledger.record(pick(), path, today="2026-08-12")
    assert ledger.settle(history([]), path) == 0
    assert ledger.load(path).iloc[0]["outcome"] == "pending"


def test_a_postponed_match_is_still_the_same_bet(path):
    ledger.record(pick(), path, today="2026-08-12")
    ledger.settle(history([("AUT-BUNDESLI", "lask", "ried", "2026-08-17", 3, 0)]), path)
    row = ledger.load(path).iloc[0]
    assert row["outcome"] == "won"
    assert row["played_on"] == "2026-08-17"


def test_a_match_rearranged_months_later_is_not(path):
    """Beyond a week the "same" fixture is a different game in different
    circumstances, and the price we recorded no longer described it."""
    ledger.record(pick(), path, today="2026-08-12")
    ledger.settle(history([("AUT-BUNDESLI", "lask", "ried", "2026-11-20", 3, 0)]), path)
    assert ledger.load(path).iloc[0]["outcome"] == "pending"


def test_settling_twice_changes_nothing(path):
    played = history([("AUT-BUNDESLI", "lask", "ried", "2026-08-14", 2, 1)])
    ledger.record(pick(), path, today="2026-08-12")
    assert ledger.settle(played, path) == 1
    assert ledger.settle(played, path) == 0
    assert ledger.load(path).iloc[0]["pnl"] == pytest.approx(0.75)


def test_the_wrong_fixture_is_never_graded(path):
    ledger.record(pick(), path, today="2026-08-12")
    ledger.settle(history([("AUT-BUNDESLI", "rapid", "sturm", "2026-08-14", 5, 0)]), path)
    assert ledger.load(path).iloc[0]["outcome"] == "pending"


# -- the accumulator's own book --------------------------------------------

@pytest.fixture
def acca_path(tmp_path):
    return tmp_path / "best_accas.csv"


def acca(**overrides):
    base = {
        "legs": 2, "target_odds": 3.0, "min_leg_odds": 1.73,
        "probability": 0.33, "fair_odds": 3.0, "offered_odds": 3.2,
        "weakest_leg": 0.57, "first_day": "2026-08-14", "last_day": "2026-08-15",
        "selections": [
            {"date": "2026-08-14", "competition": "AUT-BUNDESLI", "home": "lask",
             "away": "ried", "match": "LASK v Ried", "key": "ou2.5_over",
             "selection": "Over 2.5 goals", "prob": 0.58, "odds": 1.75},
            {"date": "2026-08-15", "competition": "AUT-BUNDESLI", "home": "a",
             "away": "b", "match": "a v b", "key": "ou2.5_over",
             "selection": "Over 2.5 goals", "prob": 0.57, "odds": 1.80},
        ],
    }
    base.update(overrides)
    return base


def test_one_accumulator_per_day_it_was_issued(acca_path):
    assert ledger.record_acca(acca(), acca_path, today="2026-08-12")
    assert ledger.record_acca(acca(probability=0.9), acca_path,
                              today="2026-08-12") is None
    assert ledger.record_acca(acca(), acca_path, today="2026-08-13")
    assert list(ledger.load_accas(acca_path)["issued"]) == ["2026-08-12", "2026-08-13"]


def test_an_accumulator_wins_only_when_every_leg_does(acca_path):
    ledger.record_acca(acca(), acca_path, today="2026-08-12")
    ledger.settle_accas(history([("AUT-BUNDESLI", "lask", "ried", "2026-08-14", 2, 1),
                                 ("AUT-BUNDESLI", "a", "b", "2026-08-15", 3, 1)]),
                        acca_path)
    row = ledger.load_accas(acca_path).iloc[0]
    assert row["outcome"] == "won"
    assert row["legs_won"] == 2
    assert row["pnl"] == pytest.approx(1.75 * 1.80 - 1)


def test_one_losing_leg_loses_the_slip(acca_path):
    ledger.record_acca(acca(), acca_path, today="2026-08-12")
    ledger.settle_accas(history([("AUT-BUNDESLI", "lask", "ried", "2026-08-14", 2, 1),
                                 ("AUT-BUNDESLI", "a", "b", "2026-08-15", 0, 0)]),
                        acca_path)
    row = ledger.load_accas(acca_path).iloc[0]
    assert row["outcome"] == "lost"
    assert row["legs_won"] == 1
    assert row["pnl"] == pytest.approx(-1.0)


def test_a_slip_waits_for_its_last_leg(acca_path):
    ledger.record_acca(acca(), acca_path, today="2026-08-12")
    settled = ledger.settle_accas(
        history([("AUT-BUNDESLI", "lask", "ried", "2026-08-14", 2, 1)]), acca_path)
    assert settled == 0
    assert ledger.load_accas(acca_path).iloc[0]["outcome"] == "pending"


def test_a_void_leg_drops_out_and_the_slip_settles_on_the_rest(acca_path):
    """What a bookmaker does. Counting the void as a loss would be wrong, and
    voiding the whole slip would be wrong in the other direction."""
    legs = acca()["selections"]
    legs[1] = {**legs[1], "key": "dnb_home", "selection": "Home draw-no-bet"}
    ledger.record_acca(acca(selections=legs), acca_path, today="2026-08-12")
    ledger.settle_accas(history([("AUT-BUNDESLI", "lask", "ried", "2026-08-14", 2, 1),
                                 ("AUT-BUNDESLI", "a", "b", "2026-08-15", 1, 1)]),
                        acca_path)
    row = ledger.load_accas(acca_path).iloc[0]
    assert row["outcome"] == "won"
    assert row["legs_void"] == 1
    assert row["pnl"] == pytest.approx(0.75)      # the surviving leg alone


def test_an_unpriced_slip_is_graded_but_kept_out_of_the_money(acca_path):
    """Counting its losses and not its wins would be worse than counting
    neither."""
    legs = [{**leg, "odds": None} for leg in acca()["selections"]]
    ledger.record_acca(acca(selections=legs, offered_odds=None), acca_path,
                       today="2026-08-12")
    ledger.settle_accas(history([("AUT-BUNDESLI", "lask", "ried", "2026-08-14", 0, 0),
                                 ("AUT-BUNDESLI", "a", "b", "2026-08-15", 0, 0)]),
                        acca_path)
    row = ledger.load_accas(acca_path).iloc[0]
    assert row["outcome"] == "lost"
    assert pd.isna(row["pnl"])

    head = ledger.acca_summary(ledger.load_accas(acca_path))
    assert head["losses"] == 1 and head["unpriced"] == 1 and head["pnl"] == 0.0


def test_the_two_books_never_share_a_total(acca_path, path):
    """A four-leg slip at 33% and a single at 62% have nothing to say to each
    other; one hit rate over both would describe neither."""
    ledger.record(pick(), path, today="2026-08-12")
    ledger.record_acca(acca(), acca_path, today="2026-08-12")
    assert ledger.summary(ledger.load(path))["recorded"] == 1
    assert ledger.acca_summary(ledger.load_accas(acca_path))["recorded"] == 1


# -- reading it back -------------------------------------------------------

def _settled_ledger(path):
    for day, home, away, key, price, goals in [
        ("2026-08-14", "lask", "ried", "ou2.5_over", 1.75, (2, 1)),
        ("2026-08-15", "a", "b", "ou2.5_over", 2.00, (0, 0)),
        ("2026-08-16", "c", "d", "ou2.5_over", 1.80, (3, 1)),
    ]:
        ledger.record(pick(day=day, home=home, away=away, key=key, odds=price,
                           match=f"{home} v {away}"), path, today="2026-08-12")
    ledger.settle(history([("AUT-BUNDESLI", home, away, day, *goals)
                           for day, home, away, goals in [
                               ("2026-08-14", "lask", "ried", (2, 1)),
                               ("2026-08-15", "a", "b", (0, 0)),
                               ("2026-08-16", "c", "d", (3, 1))]]), path)
    return ledger.load(path)


def test_summary_counts_the_record_and_the_money(path):
    head = ledger.summary(_settled_ledger(path))
    assert head["wins"] == 2 and head["losses"] == 1
    assert head["hit_rate"] == pytest.approx(2 / 3)
    assert head["pnl"] == pytest.approx(0.75 - 1.0 + 0.80)
    assert head["roi"] == pytest.approx((0.75 - 1.0 + 0.80) / 3 * 100)


def test_a_pick_that_never_settles_is_flagged(path):
    """Pending forever means the result is not being found — a club the results
    file spells differently. Unflagged it sits there keeping a loss out of the
    record."""
    ledger.record(pick(day="2026-08-14"), path, today="2026-08-14")
    frame = ledger.load(path)
    # Pinned, not read off the wall clock. Left to the real one this asserted
    # "a pick for the 14th is not overdue" and became false on the 28th, which
    # is how it broke five nightly runs without a line of code changing.
    fresh = ledger.summary(frame, today="2026-08-14")
    assert fresh["overdue"] == 0

    frame.loc[0, "day"] = "2020-01-01"
    stale = ledger.summary(frame, today="2026-08-14")
    assert stale["overdue"] == 1
    assert stale["overdue_days"] == ["2020-01-01"]


def test_overdue_is_measured_against_the_clock_it_is_given(path):
    """The same ledger is overdue or not depending only on when you ask, so
    the clock has to be an argument rather than something read from the room."""
    ledger.record(pick(day="2026-08-14"), path, today="2026-08-14")
    frame = ledger.load(path)

    assert ledger.summary(frame, today="2026-08-27")["overdue"] == 0
    assert ledger.summary(frame, today="2026-08-29")["overdue"] == 1


def test_summary_of_an_empty_ledger_does_not_divide_by_zero(path):
    head = ledger.summary(ledger.load(path))
    assert head["recorded"] == 0
    assert head["hit_rate"] is None and head["roi"] is None
    assert head["pnl"] == 0.0


def test_the_equity_curve_accumulates_in_the_order_played(path):
    curve = ledger.equity(_settled_ledger(path))
    assert [round(point["cum"], 2) for point in curve] == [0.75, -0.25, 0.55]
    assert curve[0]["won"] is True and curve[1]["won"] is False


# -- providers that spell the same club differently ------------------------

def test_a_pick_settles_when_the_results_file_uses_a_shorter_name():
    """The price feed says "Mansfield Town", the results file says "Mansfield".
    An exact comparison left the bet pending forever with nothing to say why."""
    import pathlib, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "ledger.csv"
        ledger.record(pick(competition="EL1", home="mansfield town",
                           away="doncaster rovers", match="Mansfield v Doncaster",
                           key="ou2.5_over"), path, today="2026-08-12")
        settled = ledger.settle(
            history([("EL1", "mansfield", "doncaster", "2026-08-14", 2, 1)]), path)
        assert settled == 1
        assert ledger.load(path).iloc[0]["outcome"] == "won"


def test_an_ambiguous_name_is_left_pending_rather_than_guessed():
    """"Manchester" is a shorter form of two clubs. Grading against the wrong
    match is far worse than not grading at all."""
    import pathlib, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "ledger.csv"
        ledger.record(pick(competition="PL", home="manchester", away="arsenal",
                           key="ou2.5_over"), path, today="2026-08-12")
        ledger.settle(history([("PL", "manchester city", "arsenal", "2026-08-14", 3, 0),
                               ("PL", "manchester united", "arsenal", "2026-08-14", 0, 1)]),
                      path)
        assert ledger.load(path).iloc[0]["outcome"] == "pending"


# -- reading it back onto the card -----------------------------------------

def test_a_recorded_day_comes_back_in_the_shape_the_card_uses(path):
    ledger.record(pick(), path, today="2026-08-12")
    slate = ledger.recorded_slate(["2026-08-14"], path)

    entry = slate[("2026-08-14", "main")]
    assert entry["match"] == "LASK v Ried"
    assert entry["selection"] == "Over 2.5 goals"
    assert entry["day"] == entry["date"] == "2026-08-14"
    assert (entry["band_low"], entry["band_high"]) == (1.60, 2.20)
    # Both halves of the edge are stored, so it is recomputed rather than kept.
    assert entry["edge"] == pytest.approx(0.624 * 1.75 - 1.0)


def test_a_day_that_was_not_asked_for_is_not_returned(path):
    ledger.record(pick(), path, today="2026-08-12")
    assert ledger.recorded_slate(["2026-08-15"], path) == {}


def test_a_pick_with_no_price_has_no_edge(path):
    ledger.record(pick(odds=None), path, today="2026-08-12")
    entry = ledger.recorded_slate(["2026-08-14"], path)[("2026-08-14", "main")]
    assert entry["odds"] is None and entry["edge"] is None


def test_an_empty_league_name_falls_back_to_the_code(path):
    """NaN is truthy, so `name or code` returns the NaN and the page prints it."""
    ledger.record(pick(competition_name=None), path, today="2026-08-12")
    entry = ledger.recorded_slate(["2026-08-14"], path)[("2026-08-14", "main")]
    assert entry["competition_name"] == "AUT-BUNDESLI"


def test_a_recorded_slip_comes_back_in_the_shape_the_card_renders(acca_path):
    ledger.record_acca(acca(), acca_path, today="2026-08-12")
    slip = ledger.recorded_acca("2026-08-12", acca_path)

    assert slip["legs"] == 2
    assert slip["probability"] == pytest.approx(0.33)
    assert slip["offered_odds"] == pytest.approx(3.2)
    assert [leg["match"] for leg in slip["selections"]] == ["LASK v Ried", "a v b"]
    assert slip["recorded_at"]


def test_a_day_with_no_slip_recorded_has_nothing_to_show(acca_path):
    ledger.record_acca(acca(), acca_path, today="2026-08-12")
    assert ledger.recorded_acca("2026-08-13", acca_path) is None


def test_an_unpriced_slip_reads_back_with_no_offered_price(acca_path):
    ledger.record_acca(acca(offered_odds=None), acca_path, today="2026-08-12")
    slip = ledger.recorded_acca("2026-08-12", acca_path)
    assert slip["offered_odds"] is None
