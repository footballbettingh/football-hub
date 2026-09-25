"""Calibration is the part that can flatter itself.

An isotonic map fitted on the same rows it is scored on will look perfect and
mean nothing, so the leakage tests here matter more than the accuracy ones.
"""

import numpy as np
import pytest

from confidence.calibrate import Calibrators, Isotonic, calibration_scope, walk_forward
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


def test_isotonic_keeps_its_correction_past_the_last_knot():
    """Past the fitted range the map is extrapolated, and that is where it
    broke: the raw value was handed back whenever it was the larger one, so a
    top knot that pulled the claim DOWN stopped pulling one step later.

    These are the BTTS calibrator's own end knots from a real build — 0.709
    fitted to 0.644 at the top, 0.291 to 0.357 at the bottom. A raw 70% read
    as 64% and a raw 72% as 72%: the market that overstates itself most at
    the top had its correction switched off for exactly its most extreme
    claims, with a six-point step in between.
    """
    calibrator = Isotonic([0.2911, 0.5, 0.7089], [0.3565, 0.5, 0.6435], n=116450)
    grid = np.linspace(0.0, 1.0, 1001)
    mapped = calibrator(grid)
    assert np.all(np.diff(mapped) >= -1e-12)
    assert np.abs(np.diff(mapped)).max() < 0.005
    assert calibrator([0.72])[0] < 0.66
    assert calibrator([0.28])[0] > 0.34


def test_isotonic_never_extrapolates_a_claim_outward():
    """Where the end knot pushed the claim out, past it there is no evidence
    for pushing further. The 1X2 calibrator's real end knots: 0.848 up to
    0.872 at the top, 0.047 down to 0.028 at the bottom. Beyond them a claim
    holds the knot's level until the raw value passes it, and no further."""
    calibrator = Isotonic([0.0466, 0.5, 0.8484], [0.0283, 0.5, 0.8721], n=174675)
    assert calibrator([0.86, 0.95]) == pytest.approx([0.8721, 0.95])
    assert calibrator([0.04, 0.02]) == pytest.approx([0.0283, 0.02])
    grid = np.linspace(0.0, 1.0, 1001)
    assert np.all(np.diff(calibrator(grid)) >= -1e-12)


def test_isotonic_extrapolation_is_continuous_and_bounded():
    """No step at either end knot, and nothing past 0 or 1."""
    stated, outcomes = miscalibrated(power=0.6)       # overconfident, like BTTS
    calibrator = Isotonic.fit(np.clip(stated, 0.25, 0.75), outcomes)
    for knot, fitted in ((calibrator.x[0], calibrator.y[0]),
                         (calibrator.x[-1], calibrator.y[-1])):
        either_side = calibrator([knot - 1e-6, knot + 1e-6])
        assert either_side == pytest.approx([fitted, fitted], abs=1e-4)
    ends = calibrator([0.0, 1.0])
    assert 0.0 < ends[0] < 0.01 and 0.99 < ends[1] < 1.0


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


def test_calibrators_are_fitted_per_line_or_per_market():
    keys, probs, results = _fake_arrays()
    fitted = Calibrators.fit(keys, probs, results, min_samples=500)
    assert set(fitted.by_scope) == {"1x2", "btts"}
    assert not fitted.by_scope["1x2"].is_identity


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


# -- one curve per line ------------------------------------------------------

@pytest.mark.parametrize("key, scope", [
    ("ou2.5_over", ("ou2.5", False)), ("ou2.5_under", ("ou2.5", True)),
    ("tt1.5_away_under", ("tt1.5_away", True)),
    ("corners10.5_over", ("corners10.5", False)),
    ("btts_no", ("btts", True)), ("dnb_away", ("dnb", True)),
    ("hcp_away+1.5", ("hcp_home-1.5", True)),
    ("1x2_draw", ("1x2", False)), ("dc_x2", ("dc", False)),
])
def test_each_selection_knows_its_curve(key, scope):
    assert calibration_scope(key) == scope


def _two_lines(n=40000, seed=11):
    """Two lines of one market, wrong in opposite directions at the same
    probabilities: the low line's overs land less often than they say, the
    high line's more — what a count more spread out than a Poisson does."""
    rng = np.random.default_rng(seed)
    low, high = rng.uniform(0.55, 0.85, n), rng.uniform(0.25, 0.55, n)
    keys = ["corners8.5_over", "corners8.5_under", "corners10.5_over", "corners10.5_under"]
    probs = np.column_stack([low, 1 - low, high, 1 - high])
    over_low = rng.uniform(size=n) < low - 0.04
    over_high = rng.uniform(size=n) < high + 0.04
    results = np.column_stack([over_low, ~over_low, over_high, ~over_high]).astype(np.int8)
    return keys, probs, results


def test_lines_that_err_opposite_ways_are_each_mended():
    """One pooled curve could not: at a shared probability it must move both
    lines the same way. Scored on a second draw, not the one fitted."""
    keys, probs, results = _two_lines()
    fitted = Calibrators.fit(keys, probs, results)
    fresh_keys, fresh_probs, fresh_results = _two_lines(seed=12)
    out = fitted.apply(fresh_keys, fresh_probs)
    for column in (0, 2):
        gap = fresh_results[:, column].mean() - out[:, column].mean()
        raw_gap = fresh_results[:, column].mean() - fresh_probs[:, column].mean()
        assert abs(gap) < 0.01 < abs(raw_gap)


def test_both_sides_of_a_line_still_add_to_one():
    """A line is one curve and its complement, so over and under cannot drift
    apart — the property the pooled curve had by being symmetric, kept."""
    keys, probs, results = _two_lines()
    out = Calibrators.fit(keys, probs, results).apply(keys, probs)
    assert out[:, 0] + out[:, 1] == pytest.approx(np.ones(len(out)))
    assert out[:, 2] + out[:, 3] == pytest.approx(np.ones(len(out)))


def test_a_calibration_file_from_before_still_applies(tmp_path):
    """A file with one curve per market group — which the cached data folder
    holds until the next `fb.py calibrate` — keeps working, curve for curve."""
    path = tmp_path / "calibration.json"
    path.write_text('{"meta": {}, "groups": {"ou": {"x": [0.2, 0.8], "y": [0.3, 0.7], '
                    '"n": 5000}}}', encoding="utf-8")
    old = Calibrators.load(path)
    curve = Isotonic([0.2, 0.8], [0.3, 0.7], n=5000)
    assert old.calibrate("ou2.5_under", [0.5])[0] == pytest.approx(curve([0.5])[0])
    assert old.calibrate("btts_yes", [0.5])[0] == pytest.approx(0.5)   # nothing fitted
