"""Everything the site reads, and how stale it is.

The web layer never computes. It loads files that a job produced, so a page
request is a few milliseconds of disk reads no matter how expensive the thing
being displayed was to work out. That is also what makes the later static
export trivial: the pages already only depend on files.

Each artifact declares which job rebuilds it and what it depends on, so the
status strip can say "picks are older than the odds they were priced from"
instead of leaving the user to notice.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from confidence import config as cf_config
from valuebets import config as vb_config

DATA_DIR = vb_config.DATA_DIR
EVIDENCE_JSON = DATA_DIR / "evidence.json"
PICKS_JSON = DATA_DIR / "picks.json"
STATUS_JSON = DATA_DIR / "status.json"


@dataclass(frozen=True)
class Artifact:
    key: str
    label: str
    path: object
    job: str                       # the job that rebuilds it
    depends_on: tuple = field(default_factory=tuple)
    note: str = ""


ARTIFACTS = (
    Artifact("history", "Match results + closing odds", vb_config.DATA_DIR / "history.csv",
             "fetch-results", note="free, no API key"),
    Artifact("odds", "Upcoming fixture prices", "odds_*.csv",
             "fetch-odds", note="costs Odds API credits"),
    Artifact("predictions", "Walk-forward predictions", cf_config.PREDICTIONS_CSV,
             "rebuild-model", ("history",)),
    Artifact("calibration", "Calibrators", cf_config.CALIBRATION_JSON,
             "recalibrate", ("predictions",)),
    Artifact("reliability", "Reliability record", cf_config.RELIABILITY_CSV,
             "recalibrate", ("predictions",)),
    Artifact("picks", "The card", PICKS_JSON, "refresh-picks",
             ("history", "odds", "calibration")),
    Artifact("evidence", "Value-betting evidence", EVIDENCE_JSON, "rebuild-evidence",
             ("history",)),
    Artifact("ledger", "Daily-pick record", DATA_DIR / "best_picks.csv",
             "refresh-picks", note="written before each match, then graded"),
    Artifact("accas", "Accumulator record", DATA_DIR / "best_accas.csv",
             "refresh-picks", note="one slip a day, kept in its own book"),
)

BY_KEY = {a.key: a for a in ARTIFACTS}


def _mtime(path):
    """When this artifact was last written.

    A glob stands for a set of files that are written together — the odds are
    one file per league, and naming a single one of them would have the chip go
    red the day that league went out of season.
    """
    if isinstance(path, str):
        newest = [p.stat().st_mtime for p in DATA_DIR.glob(path)]
        return datetime.fromtimestamp(max(newest)) if newest else None
    return datetime.fromtimestamp(path.stat().st_mtime) if path.exists() else None


def _age_words(when):
    if when is None:
        return "never"
    delta = datetime.now() - when
    if delta < timedelta(minutes=1):
        return "just now"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)} min ago"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)} h ago"
    return f"{delta.days} d ago"


def status(items=ARTIFACTS):
    """One row per artifact: present, when built, and whether it is behind.

    "Behind" is honest about the thing that actually goes wrong here — you
    fetch new odds, forget to re-price, and read yesterday's card as if it were
    today's. Comparing modification times catches that without any bookkeeping.
    """
    by_key = {a.key: a for a in items}
    times = {a.key: _mtime(a.path) for a in items}
    rows = []
    for artifact in items:
        built = times[artifact.key]
        stale_after = [
            by_key[dep].label for dep in artifact.depends_on
            if dep in by_key and times.get(dep) and built and times[dep] > built
        ]
        rows.append({
            "key": artifact.key,
            "label": artifact.label,
            "job": artifact.job,
            "note": artifact.note,
            "exists": built is not None,
            "built": built.strftime("%d %b %H:%M") if built else None,
            "age": _age_words(built),
            "stale_after": stale_after,
        })
    return rows


def ready():
    """Which of the two halves can currently render."""
    present = {row["key"] for row in status() if row["exists"]}
    return {
        "card": {"picks"} <= present,
        "reliability": {"reliability"} <= present,
        "evidence": {"evidence"} <= present,
        "any": bool(present & {"picks", "reliability", "evidence"}),
    }


# -- loaders ---------------------------------------------------------------

def load_json(path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def load_picks():
    """The card as a plain dict, ready to be embedded in a page."""
    return load_json(PICKS_JSON, default=None)


def load_evidence():
    return load_json(EVIDENCE_JSON, default=None)


def load_reliability():
    if not cf_config.RELIABILITY_CSV.exists():
        return None
    return pd.read_csv(cf_config.RELIABILITY_CSV)


def data_summary():
    """A one-line description of the dataset behind everything else."""
    path = vb_config.DATA_DIR / "history.csv"
    if not path.exists():
        return None
    frame = pd.read_csv(path, usecols=["date", "competition"])
    return {
        "matches": int(len(frame)),
        "competitions": int(frame["competition"].nunique()),
        "first": str(frame["date"].min())[:10],
        "last": str(frame["date"].max())[:10],
        # Per league, because the sources lag at different speeds: the main
        # divisions are published the same night, the extra-country files can
        # be a week behind. A global "last result" date would call a Finnish
        # pick overdue while the file simply has not caught up.
        "last_by_competition": {
            str(comp): str(when)[:10]
            for comp, when in frame.groupby("competition")["date"].max().items()
        },
    }
