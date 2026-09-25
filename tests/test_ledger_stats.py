"""Grading the ledger with the yardsticks that fit it.

Two things the History page used to get wrong in the same direction: it read
picks claiming 45% and 77% as one coin tossed n times, which is wider than the
truth when the claims are spread; and it had nothing to say for the first few
hundred picks, because a hit rate needs that many. The sum of the claims fixes
the first. Asking each pick again at the close fixes the second — how far a
claim moved by kick-off hardly varies from pick to pick, so dozens say what a
hit rate needs hundreds for.
"""

import numpy as np
import pandas as pd
import pytest

from confidence import config as cf_config, markets, predict
from confidence.calibrate import Calibrators, Isotonic
from confidence.poisson import score_matrix
from hub import components as c, ledger, pages


def _book(rows):
    return pd.DataFrame(rows).reindex(columns=ledger.COLUMNS)


# -- the record against the sum of its claims --------------------------------

def test_the_summary_counts_wins_against_the_sum_of_the_claims():
    frame = _book([
        {"day": "2026-08-14", "band": "main", "prob": 0.6, "outcome": "won"},
        {"day": "2026-08-15", "band": "main", "prob": 0.6, "outcome": "won"},
        {"day": "2026-08-16", "band": "safe", "prob": 0.8, "outcome": "won"},
        {"day": "2026-08-17", "band": "safe", "prob": 0.8, "outcome": "void"},
    ])
    head = ledger.summary(frame, today="2026-08-20")
    assert head["expected_wins"] == pytest.approx(2.0)       # the void is out
    assert head["z"] == pytest.approx(1.0 / np.sqrt(0.24 + 0.24 + 0.16))
    assert head["brier"] == pytest.approx((0.16 + 0.16 + 0.04) / 3)


@pytest.mark.parametrize("settled, z, verdict", [
    (40, 0.3, "Far too early"),
    (300, 0.6, "Landing where it said it would"),
    (300, -2.4, "Landing less often than it said"),
    (300, 2.4, "Landing more often than it said"),
])
def test_the_verdict_reads_the_record_in_standard_errors(settled, z, verdict):
    head = {"settled": settled, "wins": 180, "expected_wins": 175.0, "z": z}
    assert verdict in pages._history_verdict(head)


# -- the same claim, asked again at the close --------------------------------

PREDICTIONS = pd.DataFrame([{
    "date": pd.Timestamp("2026-09-12"), "competition": "PL",
    "home": "arsenal", "away": "chelsea",
    "lam_model": 1.5, "mu_model": 1.1, "rho_model": -0.05,
    "lam_market": 1.7, "mu_market": 0.9, "rho_market": -0.08,
}])


def _pick(key, home="arsenal", away="chelsea", prob=0.5, band="main"):
    return {"day": "2026-09-12", "band": band, "competition": "PL", "home": home,
            "away": away, "match": f"{home} v {away}", "key": key, "prob": prob}


def _at_the_close(key):
    lam, mu, rho = predict.fuse(1.5, 1.1, -0.05, 1.7, 0.9, -0.08, 0.9)
    return markets.goal_probabilities(score_matrix(lam, mu, rho))[key]


def test_a_pick_is_asked_again_on_the_closing_line():
    close = ledger.closing_probs(_book([_pick("tt1.5_home_over")]), PREDICTIONS,
                                 weight=0.9)
    assert close[0] == pytest.approx(_at_the_close("tt1.5_home_over"))


def test_the_close_goes_through_the_live_calibration():
    calibrators = Calibrators({"tt": Isotonic([0.1, 0.9], [0.2, 0.8], n=5000)})
    close = ledger.closing_probs(_book([_pick("tt1.5_home_over")]), PREDICTIONS,
                                 calibrators, weight=0.9)
    raw = _at_the_close("tt1.5_home_over")
    assert close[0] == pytest.approx(calibrators.by_group["tt"](np.array([raw]))[0])


def test_there_is_no_close_for_corners_or_for_a_match_not_yet_reached():
    close = ledger.closing_probs(_book([
        _pick("corners8.5_over"),                       # nothing quotes corners
        _pick("btts_yes", home="leeds", away="fulham"),  # not in the walk-forward
    ]), PREDICTIONS, weight=0.9)
    assert np.isnan(close).all()


def test_drift_is_the_close_against_the_claim_in_points():
    closing = pd.DataFrame({"band": ["safe", "safe", "main"],
                            "prob": [0.77, 0.75, 0.60],
                            "prob_close": [0.74, 0.73, np.nan]})
    moved = ledger.drift(closing)
    assert (moved["n"], moved["drift_pp"]) == (2, pytest.approx(-2.5))
    assert moved["se_pp"] == pytest.approx(np.std([-3.0, -2.0], ddof=1) / np.sqrt(2))
    assert ledger.drift_by_band(closing)["main"]["n"] == 0


def test_the_close_is_written_beside_the_ledger_not_into_it(tmp_path, monkeypatch):
    book = tmp_path / "best_picks.csv"
    ledger.save(_book([_pick("tt1.5_home_over"), _pick("corners8.5_over", band="safe")]),
                book)
    PREDICTIONS.to_csv(tmp_path / "predictions.csv", index=False)
    monkeypatch.setattr(cf_config, "PREDICTIONS_CSV", tmp_path / "predictions.csv")
    monkeypatch.setattr(cf_config, "CALIBRATION_JSON", tmp_path / "absent.json")
    before = book.read_bytes()

    ledger.write_closing(path=tmp_path / "ledger_close.csv", ledger_path=book)

    written = ledger.load_closing(tmp_path / "ledger_close.csv")
    assert list(written.columns) == ledger.CLOSE_COLUMNS
    assert written["prob_close"].notna().tolist() == [True, False]
    assert book.read_bytes() == before


def test_the_history_page_shows_the_close_beside_the_record():
    frame = _book([
        {"day": "2026-09-12", "band": "safe", "competition": "PL",
         "competition_name": "Premier League", "match": "a v b", "selection": "x",
         "prob": 0.77, "fair_odds": 1.3, "outcome": "won"},
        {"day": "2026-09-13", "band": "main", "competition": "PL",
         "competition_name": "Premier League", "match": "c v d", "selection": "y",
         "prob": 0.60, "fair_odds": 1.67, "outcome": "lost"},
    ])
    closing = pd.DataFrame({"day": ["2026-09-12", "2026-09-13"], "band": ["safe", "main"],
                            "match": ["a v b", "c v d"], "key": ["k", "k"],
                            "prob": [0.77, 0.60], "prob_close": [0.74, 0.61]})
    context = {"picks": None, "reliability": None, "evidence": None, "data": None,
               "ledger": frame, "closing": closing}
    html = pages.render("history", c.Links("server"), context)
    assert "To the close" in html
    assert "-3.0pp" in html and "+1.0pp" in html
    assert "nan" not in html.lower()
