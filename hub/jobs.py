"""Background jobs, so a button press does not hold a browser connection open.

The expensive steps take minutes. A web request that waits for them would time
out, and worse, would give no sign of life while it did. So each job runs on a
worker thread with its stdout captured into a ring buffer, and the page polls
for new lines.

One job at a time, by design. They contend for the same CSVs — a rebuild
writing predictions.csv while a recalibrate reads it would fail in a way that
looks like a modelling bug rather than a scheduling one.
"""

import io
import json
import threading
import traceback
from collections import deque
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .artifacts import STATUS_JSON

MAX_LINES = 4000


@dataclass(frozen=True)
class Job:
    key: str
    label: str
    description: str
    run: Callable
    estimate: str
    produces: str = ""
    cost: str = ""            # non-empty means it spends something real


class _LineWriter(io.TextIOBase):
    """Turns whatever a job prints into timestamped lines on the runner."""

    def __init__(self, sink):
        self._sink = sink
        self._buffer = ""

    def write(self, text):
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._sink(line)
        return len(text)

    def flush(self):
        if self._buffer:
            self._sink(self._buffer)
            self._buffer = ""


class Runner:
    def __init__(self, jobs, status_path=STATUS_JSON):
        self.jobs = {job.key: job for job in jobs}
        self.status_path = status_path
        self._lock = threading.Lock()
        self._lines = deque(maxlen=MAX_LINES)
        self._first_index = 0          # log index of _lines[0], after trimming
        self._current = None
        self._thread = None
        self._last = self._load_last()

    # -- state ------------------------------------------------------------

    def _load_last(self):
        if not self.status_path.exists():
            return {}
        try:
            return json.loads(self.status_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_last(self):
        try:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            self.status_path.write_text(json.dumps(self._last, indent=1), encoding="utf-8")
        except OSError:
            pass                      # a status file we cannot write is not fatal

    def _append(self, line):
        with self._lock:
            if len(self._lines) == self._lines.maxlen:
                self._first_index += 1
            self._lines.append(line)

    def busy(self):
        return self._current is not None

    # -- running ----------------------------------------------------------

    def start(self, key, **kwargs):
        """Begin a job. Returns (ok, message)."""
        if key not in self.jobs:
            return False, f"Unknown job {key!r}"
        with self._lock:
            if self._current is not None:
                return False, f"{self.jobs[self._current].label} is still running"
            self._current = key
            self._lines.clear()
            self._first_index = 0

        job = self.jobs[key]
        self._append(f"=== {job.label} — started {datetime.now():%H:%M:%S}")
        self._thread = threading.Thread(target=self._run, args=(job, kwargs),
                                        name=f"job-{key}", daemon=True)
        self._thread.start()
        return True, f"{job.label} started"

    def _run(self, job, kwargs):
        started = datetime.now()
        record = {"job": job.key, "label": job.label,
                  "started": started.isoformat(timespec="seconds")}
        try:
            writer = _LineWriter(self._append)
            with redirect_stdout(writer):
                result = job.run(progress=self._append, **kwargs)
            writer.flush()
            record.update(ok=True, result=result if isinstance(result, dict) else None)
            self._append(f"=== done in {(datetime.now() - started).seconds}s")
        except BaseException as exc:                      # noqa: BLE001
            # A job that dies must say so on the page. Swallowing it here would
            # leave the button spinning forever with no explanation.
            record.update(ok=False, error=f"{type(exc).__name__}: {exc}")
            self._append(f"!!! {type(exc).__name__}: {exc}")
            for line in traceback.format_exc().splitlines()[-12:]:
                self._append("    " + line)
        finally:
            record["finished"] = datetime.now().isoformat(timespec="seconds")
            record["seconds"] = int((datetime.now() - started).total_seconds())
            with self._lock:
                self._last[job.key] = record
                self._current = None
            self._save_last()

    # -- reporting --------------------------------------------------------

    def snapshot(self, since=0):
        with self._lock:
            start = max(since - self._first_index, 0)
            lines = list(self._lines)[start:]
            next_index = self._first_index + len(self._lines)
            current = self._current
        return {
            "running": current,
            "label": self.jobs[current].label if current else None,
            "lines": lines,
            "next": next_index,
            "last": self._last,
        }


# -- the jobs --------------------------------------------------------------

def _build_jobs():
    from . import card, evidence, pipeline

    def full_refresh(progress=print):
        """Everything, in dependency order. The one-button option."""
        out = {}
        for step, function in (("results", pipeline.fetch_results),
                               ("model", pipeline.rebuild_model),
                               ("calibration", pipeline.recalibrate),
                               ("card", card.build)):
            progress(f"--- {step}")
            out[step] = function(progress=progress)
        return {"steps": list(out)}

    return [
        Job("refresh-picks", "Refresh the card",
            "Re-price the upcoming fixtures with the current model and calibrators.",
            card.build, "~20 seconds", "the card"),
        Job("check-leagues", "Check available leagues",
            "Ask The Odds API which leagues are in season and could be priced "
            "against the history here. Free — spends no credits.",
            pipeline.discover_leagues, "~5 seconds, free", "the league plan"),
        Job("fetch-odds", "Fetch new prices",
            "Pull current bookmaker prices for every league in the plan — or "
            "the ones already tracked, if you have not run the check yet.",
            pipeline.fetch_odds, "~10 seconds per league", "fixture prices",
            cost="spends Odds API credits (~4 per league)"),
        Job("fetch-results", "Fetch new results",
            "Re-download results and closing odds. Free, no API key.",
            pipeline.fetch_results, "1-3 minutes", "match history"),
        Job("recalibrate", "Recalibrate",
            "Refit the calibrators and the reliability record from existing predictions.",
            pipeline.recalibrate, "~2 minutes", "calibrators + reliability"),
        Job("rebuild-model", "Rebuild the model",
            "Walk-forward over every finished match. Needed after new results.",
            pipeline.rebuild_model, "6-8 minutes", "predictions"),
        Job("rebuild-evidence", "Rebuild the evidence",
            "Re-run the value-betting backtest, band sweep and insights.",
            evidence.build, "5-15 minutes", "evidence"),
        Job("full-refresh", "Full refresh",
            "Results, model, calibration and card, in order.",
            full_refresh, "8-12 minutes", "everything except prices"),
    ]


_runner = None


def runner():
    """The process-wide runner. Built lazily so importing this module is cheap."""
    global _runner
    if _runner is None:
        _runner = Runner(_build_jobs())
    return _runner
