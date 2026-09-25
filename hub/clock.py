"""The one clock the hub reads: UTC, always, and saying so.

Times were read naive, with `datetime.now()` and `date.today()`, which is
the clock of whatever machine ran the step. The daily run is on a runner set
to UTC and a hand-run one on a laptop set to the reader's own zone, so the
ledger's `recorded_at` held both, three hours apart and indistinguishable, and
"today" moved with the machine. Everything written now carries its offset;
everything compared uses the UTC date, which is also the date the price feed
gives each kick-off.
"""

from datetime import datetime, timezone

import pandas as pd


def now():
    """This moment, as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def stamp():
    """This moment as it is written into a file: ISO seconds and +00:00.

    `+00:00` rather than `Z`, because `datetime.fromisoformat` only learned to
    read the letter in Python 3.11.
    """
    return now().isoformat(timespec="seconds")


def today():
    """Today's UTC date, as a Timestamp at midnight with no zone — the shape
    every match date in the history has, so the two compare directly."""
    return pd.Timestamp(now().date())


def parse(text):
    """A written time as an aware UTC datetime, or None.

    One written before this carries no offset. It was the laptop's local time
    or the runner's UTC and nothing now says which; it is read as UTC, which
    is what every scheduled run wrote.
    """
    when = pd.to_datetime(text, errors="coerce", utc=True)
    return None if pd.isna(when) else when.to_pydatetime()
