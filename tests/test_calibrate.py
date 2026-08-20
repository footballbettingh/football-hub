"""Calibration is the part that can flatter itself.

An isotonic map fitted on the same rows it is scored on will look perfect and
mean nothing, so the leakage tests here matter more than the accuracy ones.
"""

import numpy as np
import pytest

from confidence.calibrate import Calibrators, Isotonic, walk_forward
from confidence.evaluate import expected_calibration_error


def miscalibrated(n=20000, seed=0, power=1.6):
    """Probabilities that rank correctly but are stated too confidently."""
    rng = np.random.default_rng(seed)
    stated = rng.uniform(0.02, 0.98, n)
    truth = stated ** power / (stated ** power + (1 - stated) ** power)
    outcomes = (rng.uniform(size=n) < truth).astype(float)
    return stated, outcomes


def test_isotonic_fixes_a_known_distortion():
    stated, outcomes = miscalibrated()
    before = expected_calibration_error(stated, outcomes)
    calibrator = Isotonic.fit(stated, outcomes)
    after = expected_calibration_error(calibrator(stated), outcomes)
    assert before > 0.03
    assert after < before / 3


def test_isotonic_is_monotone():
    """Calibration may change the level; reordering the card would mean the
    ranking users read is not the ranking that was validated."""
    stated, outcomes = miscalibrated()
    grid = np.linspace(0.01, 0.99, 200)
    mapped = Isotonic.fit(stated, outcomes)(grid)
    assert np.all(np.diff(mapped) >= -1e-12)


def test_isotonic_stays_the_identity_on_thin_data():
    calibrator = Isotonic.fit([0.4, 0.6, 0.8], [0, 1, 1])
    assert calibrator.is_identity
    assert calibrator([0.4, 0.9]) == pytest.approx([0.4, 0.9])


def test_isotonic_leaves_a_well_calibrated_forecast_alone():
    """Fitting a forecast that needs no fixing should barely move it.

    Barely, not exactly: a bin of a few hundred matches has a standard error
    of a couple of points, so the fit adds ~1pp of noise at this sample size
    and ~0.5pp at the size the real market groups reach. That is the price of
    being able to correct a real distortion, and it is measured in the SHRINK
    note in calibrate.py rather than assumed away.
    """
    rng = np.random.default_rng(1)
    stated = rng.uniform(0.05, 0.95, 40000)
    outcomes = (rng.uniform(size=40000) < stated).astype(float)
    mapped = Isotonic.fit(stated, outcomes)(stated)
    assert np.abs(mapped - stated).mean() < 0.015


def test_isotonic_survives_a_round_trip():
    stated, outcomes = miscalibrated()
    calibrator = Isotonic.fit(stated, outcomes)
    restored = Isotonic.from_dict(calibrator.to_dict())
    assert restored(stated) == pytest.approx(calibrator(stated), abs=1e-5)


def _fake_arrays(n=6000, seed=3):
    rng = np.random.default_rng(seed)
    keys = ["1x2_home", "1x2_draw", "1x2_away", "btts_yes", "btts_no"]
    probs = rng.uniform(0.05, 0.95, (n, len(keys)))
    truth = probs ** 1.5 / (probs ** 1.5 + (1 - probs) ** 1.5)
    results = (rng.uniform(size=probs.shape) < truth).astype(np.int8)
    return keys, probs, results


def test_calibrators_are_fitted_per_group():
    keys, probs, results = _fake_arrays()
    fitted = Calibrators.fit(keys, probs, results, min_samples=500)
    assert set(fitted.by_group) == {"1x2", "btts"}
    assert not fitted.by_group["1x2"].is_identity


def test_apply_touches_only_finite_entries():
    keys, probs, results = _fake_arrays()
    probs[3, 1] = np.nan
    fitted = Calibrators.fit(keys, probs, results, min_samples=500)
    out = fitted.apply(keys, probs)
    assert np.isnan(out[3, 1])
    assert np.isfinite(out[3, 0])


def test_walk_forward_never_scores_the_fold_it_learned_from():
    """The first fold has no earlier data, so it is left raw AND excluded.
    Scoring it would credit the calibrator for matches it never saw."""
    keys, probs, results = _fake_arrays()
    dates = np.arange(len(probs))
    calibrated, scored = walk_forward(keys, probs, results, dates, n_folds=5,
                                      min_samples=500)
    first_fold = np.array_split(np.argsort(dates), 5)[0]
    assert not scored[first_fold].any()
    assert calibrated[first_fold] == pytest.approx(probs[first_fold])
    assert scored.mean() == pytest.approx(0.8, abs=0.01)


def test_walk_forward_improves_calibration_out_of_sample():
    keys, probs, results = _fake_arrays(n=30000, seed=7)
    dates = np.arange(len(probs))
    calibrated, scored = walk_forward(keys, probs, results, dates, n_folds=5,
                                      min_samples=500)
    raw = expected_calibration_error(probs[scored].ravel(), results[scored].ravel())
    fixed = expected_calibration_error(calibrated[scored].ravel(), results[scored].ravel())
    assert fixed < raw
