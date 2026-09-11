"""Paths and defaults.

Since the merge there is ONE data folder, shared with the value-betting half:
`valuebets` fetches into it, `confidence` reads the results and writes its
predictions and calibrators alongside them. Before the merge this half read
from a sibling checkout, which is why `CF_SOURCE_DATA` still exists — point it
elsewhere and the raw inputs can live apart from the derived files again.

    set CF_SOURCE_DATA=D:\\somewhere\\data
    set CF_DATA_DIR=D:\\scratch\\out
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Where history.csv and the fetched fixture odds live.
SOURCE_DATA = Path(os.environ.get("CF_SOURCE_DATA", PROJECT_ROOT / "data"))

DATA_DIR = Path(os.environ.get("CF_DATA_DIR", PROJECT_ROOT / "data"))
REPORT_DIR = Path(os.environ.get("CF_REPORT_DIR", PROJECT_ROOT / "reports"))

HISTORY_CSV = SOURCE_DATA / "history.csv"

# Results the feed never published, entered by hand. Kept in its own file
# rather than merged into history.csv, which every fetch overwrites — and kept
# separate for a second reason: a row in here is not evidence of anything the
# way a feed row is, and the code that asks "are this league's results still
# arriving?" has to be able to tell the two apart.
MANUAL_RESULTS_CSV = SOURCE_DATA / "manual_results.csv"
FIXTURE_GLOB = "odds_*.csv"

PREDICTIONS_CSV = DATA_DIR / "predictions.csv"
CALIBRATION_JSON = DATA_DIR / "calibration.json"
RELIABILITY_CSV = DATA_DIR / "reliability.csv"
PICK_FACTORS_CSV = DATA_DIR / "pick_factors.csv"
PICKS_CSV = DATA_DIR / "picks.csv"

# -- modelling defaults ----------------------------------------------------

# How often the walk-forward run refits a competition. Every match day would be
# the purest choice and costs ~7x the time for a difference measured in the
# fourth decimal, because a single round barely moves a strength estimate.
REFIT_DAYS = 7

# A competition needs this many finished matches before it is predicted at all.
# Below it the strengths are mostly prior, and the market carries the whole
# forecast — which is fine for betting but pollutes any comparison of the two.
MIN_TRAIN_MATCHES = 200

HALF_LIFE_DAYS = 180
SHRINKAGE_GAMES = 5.0
RIDGE = 0.05
MAX_GOALS = 12

# How much of a fitted corner strength to keep, as a fraction. Every other
# market on the card is fused with a closing line and the line does the
# shrinking; corners are quoted by nobody here, so the raw team strengths go
# out unchecked and come out roughly twice as spread as reality — actual total
# corners regressed on predicted has slope 0.558 over 31,773 matches.
#
# Rebuilt at 0.55, the slope comes back to 0.859, the corner-market Brier
# falls from 0.2229 to 0.2209, and the spread between the best- and
# worst-behaved corner selection inside the main pick band falls from 15.8
# points to 5.9. The Brier sweep is flat between 0.41 and 0.55 — 0.00008 of
# Brier across that whole range — so it was settled on the metric that maps to
# the picks instead: mean absolute band gap, which prefers 0.55 to 0.468 in all
# three pick bands. A negative binomial on top of this was tried and bought
# almost nothing: the error was in the lambdas, not in the shape. Corners are
# still overdispersed against a Poisson (variance / mean of 1.18, against 1.02
# for goals) and the slope is not yet 1.0, so this is a large improvement
# rather than a repair.
CORNER_SHRINK = 0.55

# Weight on the market-implied score matrix when fusing it with the model's.
# 1.0 = trust the closing line completely. Measured, not guessed: see
# `python cf.py sweep`, and the table in the README.
MARKET_WEIGHT = 0.9

# De-vig method for turning prices into probabilities: "power" or "proportional".
DEVIG = "power"

# Only bets at or above this calibrated probability reach the shortlist.
MIN_CONFIDENCE = 0.75

# Best pick of the day: the price range worth singling one out in. Below 1.60
# the card is a wall of near-certainties that pay nothing; above 2.20 a single
# pick is a coin flip whichever way you dress it up. The sibling project also
# measured 1.70-2.00 as the only odds band whose value-betting ROI came out
# positive, which is weak evidence but points the same way.
BEST_ODDS_MIN = 1.60
BEST_ODDS_MAX = 2.20

# The slate: one pick per price band per match day. Three bands rather than one
# because they test the forecast at three different confidence levels — around
# 70%, around 55% and around 40% — and because a single pick a day needs ten
# months to reach a sample worth reading. Three bands over three days is nine
# measurements a day instead of one.
#
# `main` is the flagship, and is the same band as BEST_ODDS_MIN/MAX above.
PICK_BANDS = {
    "safe": (1.30, 1.60),
    "main": (BEST_ODDS_MIN, BEST_ODDS_MAX),
    "value": (2.20, 3.00),
}
BAND_ORDER = ("safe", "main", "value")

# How many match days ahead the card looks. Match days, not calendar days: an
# international break should push the horizon out rather than show two empty
# panels.
PICK_DAYS = 3

# Accumulator: how many legs, and the combined price it has to reach. Without a
# payout target the "safest accumulator" is four 99% legs returning 1.05.
ACCA_LEGS = 4
ACCA_TARGET_ODDS = 3.0
ACCA_MAX_LEGS = 6

# Choosing between selections that are all on the same price.
#
# A price band is 30 to 80 odds-points wide and the picks occupy one and a
# half of them: ranking on probability inside a band always returns the
# shortest price in it, so the band names a target price rather than a range.
# What actually decides the pick is the tie-break between the dozens of
# selections sitting on that price, and that used to be `hit_rate_n` — how
# many rows the market has in the reliability table. That is a fact about the
# dataset, not about the bet.
#
# So the price stays and the tie-break changes. Everything within
# PICK_PRICE_TOLERANCE of the shortest price available in the band competes,
# and the winner is the selection whose own key has come closest to its claim
# AT THAT PRICE — measured on a log grid of PICK_FACTOR_STEP, needing
# PICK_FACTOR_MIN_N graded bets before a cell is allowed to speak.
#
# Worth being plain about the size of this, and about how well it is measured.
# On the model as it now stands, backtested over 2,445 historical picks, it is
# worth +3.07 points in the safe band, +1.60 in main and -0.86 in value: +1.27
# pooled, against a standard error of about 1.4 points a band. Run against the
# model as it was before the corner shrink the same test gave +0.25 / +0.76 /
# +1.51, +0.84 pooled. The aggregate sign is stable across both; the per-band
# split is not, which is what one standard error looks like at eight hundred
# picks a band. Do not read the band numbers as a ranking of the bands.
#
# The price it buys does not move — 1.3016 to 1.3020, 1.6033 to 1.6042, 2.2069
# to 2.2069 — which is the whole point: it changes which selection is taken,
# not what is being bought. What really justifies it is what it replaces.
PICK_PRICE_TOLERANCE = 0.05
PICK_FACTOR_STEP = 1.025
PICK_FACTOR_MIN_N = 300

# A price a Dixon-Coles Poisson cannot represent. Over 56,856 historical
# matches only 15 exceeded 2pp — Udinese v Roma in April 2024 is the worst, a
# line implying a 57% draw, which is the abandoned-and-replayed fixture rather
# than a real opinion about football. The fit reports how far off it landed;
# anything above this is dropped from the card instead of being quietly
# rounded into a confident number.
MAX_IMPLIED_RESID = 0.02


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def require_source() -> None:
    if not HISTORY_CSV.exists():
        raise SystemExit(
            f"Historical data not found at {HISTORY_CSV}.\n"
            "Point CF_SOURCE_DATA at the folder holding history.csv, or run\n"
            "`python fb.py fetch results` first."
        )
