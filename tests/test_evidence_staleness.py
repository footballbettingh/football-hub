"""When the evidence page is allowed to go on saying nothing.

The value-betting backtest costs no API credits — it reads history.csv, which
comes from the free CSVs — so the only thing standing between the Evidence page
and being filled in is whether the unattended run knows it is behind. It has to
work that out from the data, because the obvious signal is wrong: a CI runner
restores both files from a cache, so their modification times record when a
tarball was unpacked rather than when the numbers were worked out.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fb
from hub import evidence


def _point_at(tmp_path, monkeypatch, rows):
    """A history.csv with `rows` matches in it, and an empty artifact folder."""
    history = tmp_path / "history.csv"
    history.write_text("date,home\n" + "".join(f"2026-01-01,t{i}\n" for i in range(rows)),
                       encoding="utf-8")
    monkeypatch.setattr(evidence.vb_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr("hub.artifacts.EVIDENCE_JSON", tmp_path / "evidence.json")
    return tmp_path / "evidence.json"


def test_missing_evidence_is_stale(tmp_path, monkeypatch):
    """The case that matters most: a runner with a cold cache has no artifact
    at all, and the published page says 'No evidence built yet'."""
    _point_at(tmp_path, monkeypatch, rows=10)
    assert fb._evidence_is_stale() is True


def test_evidence_built_from_the_same_rows_is_current(tmp_path, monkeypatch):
    path = _point_at(tmp_path, monkeypatch, rows=10)
    path.write_text(json.dumps({"source_rows": 10}), encoding="utf-8")
    assert fb._evidence_is_stale() is False


def test_more_results_than_it_was_built_from_makes_it_stale(tmp_path, monkeypatch):
    """Results arrive daily; the backtest is only worth re-running when they do."""
    path = _point_at(tmp_path, monkeypatch, rows=42)
    path.write_text(json.dumps({"source_rows": 10}), encoding="utf-8")
    assert fb._evidence_is_stale() is True


def test_an_artifact_from_before_this_was_recorded_rebuilds_once(tmp_path, monkeypatch):
    """Older files have no source_rows. Treated as stale rather than as current,
    so the count gets written and every run after this one can tell."""
    path = _point_at(tmp_path, monkeypatch, rows=10)
    path.write_text(json.dumps({"n_matches": 64714}), encoding="utf-8")
    assert fb._evidence_is_stale() is True


def test_a_corrupt_artifact_rebuilds_rather_than_raising(tmp_path, monkeypatch):
    """A half-written file is what a killed job leaves behind."""
    path = _point_at(tmp_path, monkeypatch, rows=10)
    path.write_text('{"source_rows": 10', encoding="utf-8")
    assert fb._evidence_is_stale() is True


def test_history_rows_ignores_the_header(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch, rows=7)
    assert evidence._history_rows() == 7


def test_history_rows_is_zero_when_there_is_no_history(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence.vb_config, "DATA_DIR", tmp_path)
    assert evidence._history_rows() == 0


@pytest.mark.parametrize("flag", ["--no-evidence", "--force-evidence"])
def test_the_run_command_accepts_both_overrides(flag):
    """One for a run that must stay short, one for forcing a rebuild after a
    change to the backtest itself — where the row count has not moved but the
    answer has, and the staleness check alone would never notice."""
    with pytest.raises(SystemExit) as exit_info:
        fb.main(["run", flag, "--help"])
    assert exit_info.value.code == 0
