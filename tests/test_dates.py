"""Dates that arrive in two spellings from the same file.

Appending fresh rows to an existing odds CSV writes Timestamps as
`2026-08-21 00:00:00` next to strings already on disk as `2026-08-21`. pandas
infers the format from the first value and raises on the first row that
disagrees, so the whole card build dies with a message about "position 10" and
nothing about which file or why. This happened in the field.
"""

import pandas as pd
import pytest

from confidence import data as cf_data

def day(offset):
    """A date relative to now, so these tests do not expire.

    Fixture loading drops anything already kicked off, so hard-coded dates
    would quietly start failing the week they went past.
    """
    return (pd.Timestamp.today().normalize()
            + pd.Timedelta(days=offset)).strftime("%Y-%m-%d")


def quotes(rows, competition="PL"):
    """rows: (fetched_at, home, away, date, home_odds)."""
    return pd.DataFrame([{
        "fetched_at": fetched, "competition": competition,
        "home_team": home, "away_team": away,
        "home_key": home.lower(), "away_key": away.lower(),
        "date": date,
        "home_odds": price, "draw_odds": 3.4, "away_odds": 3.6,
        "home_odds_cons": price + 0.1, "draw_odds_cons": 3.5,
        "away_odds_cons": 3.7,
    } for fetched, home, away, date, price in rows])


def test_a_mixed_date_column_still_parses():
    series = pd.Series(["2026-08-21", "2026-08-21 00:00:00",
                        "2026-08-22T18:30:00Z"])
    parsed = cf_data.parse_dates(series)
    assert list(parsed) == [pd.Timestamp("2026-08-21"), pd.Timestamp("2026-08-21"),
                            pd.Timestamp("2026-08-22")]


def test_a_time_of_day_is_dropped_not_kept():
    """Fixtures are matched to history by day. A stray 18:30 would make
    `date < when` compare a kick-off time against a date and silently shift
    which matches count as "before"."""
    parsed = cf_data.parse_dates(pd.Series(["2026-08-21 18:30:00"]))
    assert parsed.iloc[0] == pd.Timestamp("2026-08-21")


def test_an_unparseable_date_names_the_file_and_the_value():
    with pytest.raises(ValueError) as caught:
        cf_data.parse_dates(pd.Series(["2026-08-21", "next tuesday"]),
                            "odds_soccer_epl.csv")
    message = str(caught.value)
    assert "odds_soccer_epl.csv" in message
    assert "next tuesday" in message


def test_load_fixtures_reads_a_file_written_by_two_different_runs(tmp_path):
    """The exact shape on disk after an append: yesterday's rows as bare dates,
    today's with a midnight time."""
    quotes([("2026-08-12T14:03:58+00:00", "Arsenal", "Chelsea", day(9), 2.0),
            ("2026-08-13T09:00:00+00:00", "Arsenal", "Chelsea", day(9) + " 00:00:00", 2.2)]
           ).to_csv(tmp_path / "odds_soccer_epl.csv", index=False)

    fixtures = cf_data.load_fixtures(tmp_path)
    assert len(fixtures) == 1                      # deduped to the freshest quote
    assert fixtures["date"].iloc[0] == pd.Timestamp(day(9))
    assert fixtures["home_odds"].iloc[0] == 2.2    # the later fetch won


def test_load_fixtures_parses_each_file_on_its_own_terms(tmp_path):
    """One league written with times must not decide how another league's file
    is read — which is what a single concat-then-parse would do."""
    quotes([("2026-08-12T14:00:00+00:00", "Arsenal", "Chelsea", day(9) + " 00:00:00", 2.0)]
           ).to_csv(tmp_path / "odds_soccer_epl.csv", index=False)
    quotes([("2026-08-12T14:00:00+00:00", "Inter", "Monza", day(11), 1.4)],
           competition="SA").to_csv(tmp_path / "odds_soccer_italy_serie_a.csv", index=False)

    fixtures = cf_data.load_fixtures(tmp_path)
    assert len(fixtures) == 2
    assert set(fixtures["competition"]) == {"PL", "SA"}
    assert fixtures["date"].dt.time.nunique() == 1     # all midnight


# -- fixtures that have already been played --------------------------------

def test_a_played_fixture_is_not_offered_as_upcoming(tmp_path):
    """Price files are appended to, never pruned. Without this the card keeps
    naming a match that was played last week, and the failure is silent."""
    quotes([("2026-08-12T14:00:00+00:00", "Old", "Match", day(-3), 2.0),
            ("2026-08-12T14:00:00+00:00", "New", "Match", day(+3), 2.0)]
           ).to_csv(tmp_path / "odds_soccer_epl.csv", index=False)

    fixtures = cf_data.load_fixtures(tmp_path)
    assert list(fixtures["home_team"]) == ["New"]
    assert len(cf_data.load_fixtures(tmp_path, include_started=True)) == 2


def test_a_fixture_later_today_is_still_upcoming(tmp_path):
    """Kick-off decides, not the date — otherwise the whole of today's card
    disappears at midnight rather than as the matches start."""
    frame = quotes([("2026-08-12T09:00:00+00:00", "Evening", "Kickoff", day(0), 2.0)])
    frame["commence_time"] = pd.Timestamp.now(tz="UTC") + pd.Timedelta(hours=6)
    frame.to_csv(tmp_path / "odds_soccer_epl.csv", index=False)

    assert len(cf_data.load_fixtures(tmp_path)) == 1


def test_a_fixture_that_kicked_off_an_hour_ago_is_gone(tmp_path):
    frame = quotes([("2026-08-12T09:00:00+00:00", "Started", "Already", day(0), 2.0)])
    frame["commence_time"] = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=1)
    frame.to_csv(tmp_path / "odds_soccer_epl.csv", index=False)

    assert cf_data.load_fixtures(tmp_path).empty


def test_a_dateless_fixture_survives_until_the_day_is_over(tmp_path):
    """With no kick-off time all we know is the day, so it stays on the card
    for the whole of it rather than vanishing at 00:01."""
    frame = quotes([("2026-08-12T09:00:00+00:00", "No", "Time", day(0), 2.0)])
    frame = frame.drop(columns=["commence_time"], errors="ignore")
    frame.to_csv(tmp_path / "odds_soccer_epl.csv", index=False)

    assert len(cf_data.load_fixtures(tmp_path)) == 1


def test_history_dates_go_through_the_same_parser(tmp_path):
    frame = pd.DataFrame({
        "date": ["2021-08-14", "2021-08-15 00:00:00"],
        "competition": ["PL", "PL"], "season": [2021, 2021],
        "home_team": ["a", "b"], "away_team": ["b", "a"],
        "home_goals": [1, 2], "away_goals": [0, 2],
        "home_key": ["a", "b"], "away_key": ["b", "a"],
    })
    path = tmp_path / "history.csv"
    frame.to_csv(path, index=False)

    history = cf_data.load_history(path)
    assert list(history["date"]) == [pd.Timestamp("2021-08-14"),
                                     pd.Timestamp("2021-08-15")]


# -- the season window -----------------------------------------------------

def test_the_season_window_ends_at_the_live_season():
    """A hard-coded list stops working in July, and did: the window ended at
    2025/26 while 2026/27 was being played, so no file for it was ever
    downloaded and no result from it could arrive."""
    from hub import pipeline
    assert pipeline.season_years("2026-08-20", back=5)[-1] == 2026
    assert pipeline.season_years("2026-06-30", back=5)[-1] == 2025
    assert pipeline.season_years("2027-01-15", back=5)[-1] == 2026


def test_the_season_window_covers_the_years_behind_it():
    from hub import pipeline
    assert pipeline.season_years("2026-08-20", back=5) == (
        2021, 2022, 2023, 2024, 2025, 2026)
