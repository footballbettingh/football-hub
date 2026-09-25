"""One clock, in UTC, that says so.

The ledger's `recorded_at` held the laptop's local time from a hand-run build
and the runner's UTC from the daily one, with nothing to tell them apart, and
"today" was whichever day the machine running the step thought it was.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd

from hub import clock, ledger


def test_a_stamp_carries_its_offset_and_reads_back():
    written = clock.stamp()
    assert written.endswith("+00:00")
    # Readable by the standard library on every Python the project runs on:
    # fromisoformat only learned to read a trailing Z in 3.11.
    back = datetime.fromisoformat(written)
    assert back.utcoffset() == timedelta(0)
    assert abs(back - clock.now()) < timedelta(seconds=5)


def test_today_is_the_utc_date_shaped_like_a_match_date():
    today = clock.today()
    assert today == pd.Timestamp(datetime.now(timezone.utc).date())
    assert today.tz is None and today == today.normalize()


def test_an_old_stamp_with_no_offset_is_read_as_utc():
    assert clock.parse("2026-08-24T11:03:39") == datetime(2026, 8, 24, 11, 3, 39,
                                                          tzinfo=timezone.utc)
    assert clock.parse("2026-08-24T14:03:39+03:00") == datetime(2026, 8, 24, 11, 3, 39,
                                                                tzinfo=timezone.utc)


def test_nothing_or_nonsense_parses_to_none():
    for value in (None, "", "not a time", float("nan")):
        assert clock.parse(value) is None


def test_the_ledger_writes_utc(tmp_path):
    path = tmp_path / "best_picks.csv"
    pick = {"day": "2099-01-01", "band": "main", "match": "a v b", "key": "1x2_home",
            "selection": "Home win", "prob": 0.6, "fair_odds": 1.67,
            "competition": "PL", "home": "a", "away": "b"}
    row = ledger.record(pick, path=path)
    assert row["recorded_at"].endswith("+00:00")


def test_the_cards_badge_says_its_zone():
    from hub import pages
    assert pages._built_badge("2026-08-12T10:00:00") == "built 12 Aug 10:00 UTC"
    assert pages._built_badge("2026-08-12T13:00:00+03:00") == "built 12 Aug 10:00 UTC"
