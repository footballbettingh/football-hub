"""Results typed in by hand, for the match the feed will never publish.

The mechanism is small; the thing it must not do is not. A hand-entered row is
the one number on this site a reader cannot check, so it has to stay
distinguishable from a fetched one forever — and in particular it must never be
mistaken for the feed having come back to life, which would put a league back
on the card on the strength of the very row that exists because it is not
covered.
"""

import pandas as pd
import pytest

from confidence import data as cf_data
from hub import ledger, leagues


def feed(rows):
    """rows: (competition, home, away, date, home_goals, away_goals)."""
    frame = pd.DataFrame([{
        "competition": comp, "home": home, "away": away,
        "date": pd.Timestamp(day), "home_goals": hg, "away_goals": ag,
        "home_team": home, "away_team": away,
    } for comp, home, away, day, hg, ag in rows])
    frame[cf_data.MANUAL] = False
    return frame


@pytest.fixture
def typed_in(tmp_path):
    def write(rows):
        """rows: (competition, home_team, away_team, date, hg, ag)."""
        path = tmp_path / "manual_results.csv"
        pd.DataFrame([{
            "date": day, "competition": comp,
            "home_team": home, "away_team": away,
            "home_goals": hg, "away_goals": ag, "source": "checked by hand",
        } for comp, home, away, day, hg, ag in rows]).to_csv(path, index=False)
        return path
    return write


def test_a_typed_result_joins_the_history_and_says_so(typed_in):
    history = feed([("PL", "arsenal", "chelsea", "2026-09-06", 1, 0)])
    path = typed_in([("RUS-PREMIERL", "Dynamo Moscow", "Spartak Moscow",
                      "2026-09-06", 2, 1)])

    joined = cf_data._with_manual_results(history, path)

    assert len(joined) == 2
    added = joined[joined[cf_data.MANUAL]]
    assert list(added["home"]) == ["dynamo moscow"]
    assert int(added.iloc[0]["away_goals"]) == 1


def test_the_feed_wins_a_fixture_both_of_them_have(typed_in):
    """The day the source finally publishes the match, its score is the one
    that counts — otherwise a guess typed in during the gap outlives the answer
    it was standing in for."""
    history = feed([("RUS-PREMIERL", "dynamo moscow", "spartak moscow",
                     "2026-09-06", 3, 3)])
    path = typed_in([("RUS-PREMIERL", "Dynamo Moscow", "Spartak Moscow",
                      "2026-09-06", 2, 1)])

    joined = cf_data._with_manual_results(history, path)

    assert len(joined) == 1
    assert int(joined.iloc[0]["home_goals"]) == 3
    assert not bool(joined.iloc[0][cf_data.MANUAL])


def test_a_file_with_nothing_in_it_cannot_take_the_site_down(typed_in, tmp_path):
    """Absent, blank, and header-only. This file is optional enough that none
    of the three may raise on the way to loading five years of history."""
    history = feed([("PL", "arsenal", "chelsea", "2026-09-06", 1, 0)])

    blank = tmp_path / "blank.csv"
    blank.write_text("", encoding="utf-8")
    header = tmp_path / "header.csv"
    header.write_text("date,competition,home_team,away_team,home_goals,away_goals",
                      encoding="utf-8")

    for path in (tmp_path / "absent.csv", blank, header, typed_in([])):
        assert len(cf_data._with_manual_results(history, path)) == 1


def test_the_file_is_read_from_beside_the_history_it_belongs_to(tmp_path):
    """Not from the configured data folder. A caller that names its own history
    CSV — every test in this suite that builds a two-row league — would
    otherwise silently inherit whatever is typed in on the machine running it,
    and pass or fail accordingly."""
    history = tmp_path / "history.csv"
    pd.DataFrame({"date": ["2026-09-06"], "competition": ["PL"], "season": [2026],
                  "home_team": ["arsenal"], "away_team": ["chelsea"],
                  "home_goals": [1], "away_goals": [0]}).to_csv(history, index=False)

    assert len(cf_data.load_history(history)) == 1

    pd.DataFrame({"date": ["2026-09-06"], "competition": ["RUS-PREMIERL"],
                  "home_team": ["Dynamo Moscow"], "away_team": ["Spartak Moscow"],
                  "home_goals": [2], "away_goals": [1]}).to_csv(
        tmp_path / "manual_results.csv", index=False)

    loaded = cf_data.load_history(history)
    assert len(loaded) == 2
    assert list(loaded[loaded[cf_data.MANUAL]]["away"]) == ["spartak moscow"]


def test_from_feed_drops_them_and_survives_a_frame_without_the_column():
    history = feed([("PL", "arsenal", "chelsea", "2026-09-06", 1, 0)])
    history.loc[0, cf_data.MANUAL] = True
    assert len(cf_data.from_feed(history)) == 0
    assert len(cf_data.from_feed(history.drop(columns=[cf_data.MANUAL]))) == 1


# -- what must NOT change because of a typed-in row -------------------------

def test_a_typed_result_does_not_bring_a_dead_league_back_onto_the_card():
    """The trap this whole separation exists for. One row entered by hand makes
    the league's newest result look four days old, and without the split it
    would be priced again the same afternoon — on a feed that is still dead."""
    history = feed([("RUS-PREMIERL", "orenburg", "lokomotiv moscow",
                     "2026-08-02", 1, 1),
                    ("RUS-PREMIERL", "dynamo moscow", "spartak moscow",
                     "2026-09-06", 2, 1)])
    history.loc[1, cf_data.MANUAL] = True

    assert leagues.quiet(history, today="2026-09-10") == {"RUS-PREMIERL"}


def test_a_typed_result_does_not_close_someone_elses_bet_as_having_none():
    """`_answer_is_in` asks whether the SOURCE has moved past a match. A row
    somebody typed for a different fixture is not an answer about this one."""
    history = feed([("RUS-PREMIERL", "orenburg", "lokomotiv moscow",
                     "2026-08-02", 1, 1),
                    ("RUS-PREMIERL", "dynamo moscow", "spartak moscow",
                     "2026-09-06", 2, 1)])
    history.loc[1, cf_data.MANUAL] = True

    assert not ledger._answer_is_in("RUS-PREMIERL", "2026-08-14", history)


def test_but_it_does_grade_its_own_match(tmp_path):
    """Grading uses every result there is; only the two questions about the
    feed's health are restricted to what the feed said."""
    path = tmp_path / "best_picks.csv"
    ledger.record(
        {"day": "2026-09-06", "competition": "RUS-PREMIERL",
         "competition_name": "Premier League (Russia)",
         "home": "dinamo moscow", "away": "spartak moscow",
         "match": "Dinamo Moscow v Spartak Moscow", "key": "ou3.5_under",
         "group": "ou", "selection": "Under 3.5 goals", "prob": 0.76,
         "fair_odds": 1.32, "odds": None},
        path, today="2026-09-04")
    history = feed([("RUS-PREMIERL", "orenburg", "lokomotiv moscow",
                     "2026-08-02", 1, 1),
                    ("RUS-PREMIERL", "dynamo moscow", "spartak moscow",
                     "2026-09-06", 2, 1)])
    history.loc[1, cf_data.MANUAL] = True
    history["total_corners"] = float("nan")

    assert ledger.settle(history, path) == 1
    assert ledger.load(path).iloc[0]["outcome"] == "won"
