"""The two ways a daily run could damage what it was meant to keep.

A write interrupted part-way left a file cut off, and the run's cache and
commit steps carried it forward — for the ledger, the one file here that
cannot be rebuilt. And a stage that failed on a bug was logged exactly like a
provider having a bad afternoon: one line, "skipped", exit 0, a green run.
"""

import os
import subprocess

import pandas as pd
import pytest
import requests

import fb
from hub import ledger, pipeline
from valuebets import config, files


# -- writing whole or not at all ---------------------------------------------

def test_a_write_replaces_the_file_and_leaves_nothing_behind(tmp_path):
    path = tmp_path / "book.csv"
    path.write_text("old\n", encoding="utf-8")
    files.write_text(path, "new\n")
    assert path.read_text(encoding="utf-8") == "new\n"
    assert [p.name for p in tmp_path.iterdir()] == ["book.csv"]


def test_a_write_that_dies_part_way_leaves_the_old_file_whole(tmp_path, monkeypatch):
    """The crash the plain write could not survive: the text is out, the
    flush to disk is not. The old file must still be all there."""
    path = tmp_path / "best_picks.csv"
    path.write_text("day,band\n2026-09-20,main\n", encoding="utf-8")

    def power_cut(fd):
        raise OSError("killed")
    monkeypatch.setattr(os, "fsync", power_cut)

    with pytest.raises(OSError):
        files.write_text(path, "day,band\n2026-09-20,")
    assert path.read_text(encoding="utf-8") == "day,band\n2026-09-20,main\n"
    assert [p.name for p in tmp_path.iterdir()] == ["best_picks.csv"]


def test_a_csv_written_whole_is_the_csv_pandas_would_have_written(tmp_path):
    """Byte for byte, line endings included: the ledger is committed, and a
    change of spelling would read in git as every row rewritten."""
    frame = pd.DataFrame({"day": ["2026-09-20", "2026-09-21"], "prob": [0.61234, None],
                          "selection": ['Over 2.5, "goals"', "Home"]})
    direct, whole = tmp_path / "direct.csv", tmp_path / "whole.csv"
    frame.to_csv(direct, index=False, float_format="%.5f")
    files.write_csv(frame, whole, index=False, float_format="%.5f")
    assert whole.read_bytes() == direct.read_bytes()


# -- the ledger's append-only rules ------------------------------------------

def _book(rows):
    return pd.DataFrame(rows, columns=["day", "prob", "outcome", "settled_at"]).astype(str)


COMMITTED = [["2026-09-20", "0.61000", "won", "2026-09-21"],
             ["2026-09-22", "0.58000", "pending", ""]]
SETTLED = ("outcome", "settled_at")


def test_new_rows_and_filled_results_are_what_a_book_is_allowed():
    grown = COMMITTED[:1] + [["2026-09-22", "0.58000", "lost", "2026-09-23"],
                             ["2026-09-24", "0.66000", "pending", ""]]
    assert ledger.changes(_book(COMMITTED), _book(grown), SETTLED) == []


def test_a_row_cut_off_the_end_is_caught():
    problems = ledger.changes(_book(COMMITTED), _book(COMMITTED[:1]), SETTLED)
    assert problems == ["1 row(s) gone: 2 committed, 1 now"]


def test_a_claim_rewritten_after_the_fact_is_caught():
    edited = [["2026-09-20", "0.71000", "won", "2026-09-21"], COMMITTED[1]]
    assert ledger.changes(_book(COMMITTED), _book(edited), SETTLED) == [
        "line 2, prob: '0.61000' became '0.71000'"]


def test_a_result_changed_once_given_is_caught():
    regraded = [["2026-09-20", "0.61000", "lost", "2026-09-21"], COMMITTED[1]]
    assert ledger.changes(_book(COMMITTED), _book(regraded), SETTLED) == [
        "line 2, outcome: 'won' became 'lost'"]


def test_a_column_dropped_is_caught():
    problems = ledger.changes(_book(COMMITTED), _book(COMMITTED).drop(columns="prob"),
                              SETTLED)
    assert problems == ["column(s) gone: prob"]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repository holding the two books, with the project pointed at it."""
    data = tmp_path / "data"
    data.mkdir()
    picks, accas = data / "best_picks.csv", data / "best_accas.csv"
    picks.write_text("day,prob,outcome,settled_at\n2026-09-20,0.61000,pending,\n",
                     encoding="utf-8", newline="\n")
    accas.write_text("issued,legs,outcome\n2026-09-20,4,pending\n",
                     encoding="utf-8", newline="\n")

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "core.autocrlf=false", *args],
                       cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    git("add", ".")
    git("commit", "-q", "-m", "ledger")

    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ledger, "LEDGER_CSV", picks)
    monkeypatch.setattr(ledger, "ACCA_CSV", accas)
    return picks


def test_a_healthy_book_passes_the_check(repo):
    with repo.open("a", encoding="utf-8", newline="\n") as book:
        book.write("2026-09-21,0.55000,pending,\n")
    assert fb.main(["check-ledger"]) == 0


def test_a_damaged_book_is_refused(repo, capsys):
    repo.write_text("day,prob,outcome,settled_at\n", encoding="utf-8", newline="\n")
    assert fb.main(["check-ledger"]) == 1
    assert "1 row(s) gone" in capsys.readouterr().out


def test_a_damaged_book_can_be_put_back_from_the_commit(repo):
    committed = repo.read_bytes()
    repo.write_text("day,prob,outcome,settled_at\n2026-09-20,0.9", encoding="utf-8")
    assert fb.main(["check-ledger", "--restore"]) == 0
    assert repo.read_bytes() == committed


# -- a bug is not a bad afternoon --------------------------------------------

@pytest.mark.parametrize("failure", [
    SystemExit("No fixture files"),
    requests.ConnectionError("provider down"),
])
def test_a_provider_failure_is_skipped(failure):
    skipped, broken = [], []

    def fail():
        raise failure
    assert fb._step("Fetching prices", fail, skipped=skipped, broken=broken) is False
    assert (skipped, broken) == (["Fetching prices"], [])


def test_a_bug_is_survived_but_recorded_with_its_traceback(capsys):
    skipped, broken = [], []

    def fail():
        return {}["lam_model"]
    assert fb._step("Rebuilding the model", fail, skipped=skipped, broken=broken) is False
    assert (skipped, broken) == ([], ["Rebuilding the model"])
    out = capsys.readouterr().out
    assert "FAILED ON A BUG - KeyError" in out and "Traceback" in out


def test_on_actions_each_failure_is_annotated(capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    def outage():
        raise requests.ConnectionError("down")

    def bug():
        raise KeyError("x")
    fb._step("Fetching prices", outage, skipped=[])
    fb._step("Recalibrating", bug, broken=[])
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("::warning::Fetching prices: skipped") for line in lines)
    assert any(line.startswith("::error::Recalibrating: failed on a bug") for line in lines)


@pytest.fixture
def run(monkeypatch):
    """`fb.py run` with every stage stubbed; `fail` makes one of them raise."""
    from hub import card, evidence
    for name in ("fetch_results", "fetch_odds_paced", "rebuild_model", "recalibrate"):
        monkeypatch.setattr(pipeline, name, lambda: None)
    monkeypatch.setattr(card, "build", lambda: None)
    monkeypatch.setattr(evidence, "build", lambda: None)

    def go(stage=None, failure=None):
        if stage:
            def fail():
                raise failure
            monkeypatch.setattr(pipeline, stage, fail)
        return fb.main(["run", "--no-notify", "--no-evidence"])
    return go


def test_a_clean_run_exits_zero(run):
    assert run() == 0


def test_a_run_through_a_provider_outage_still_exits_zero(run):
    assert run("fetch_odds_paced", requests.ConnectionError("down")) == 0


def test_a_run_that_hit_a_bug_exits_non_zero_after_finishing(run, monkeypatch):
    """Finishing matters as much as the exit code: the card is still built, so
    the site and the ledger go out on what was on hand."""
    from hub import card
    built = []
    monkeypatch.setattr(card, "build", lambda: built.append(True))
    assert run("rebuild_model", KeyError("lam_model")) == 1
    assert built == [True]
