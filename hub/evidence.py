"""The value-betting half, reduced to one JSON file.

The backtest, the band sweep, the out-of-sample filter validation and ten
insights take minutes to compute and never change between page loads, so they
are computed by a job and read by the site. Nothing here is new analysis — it
is the orchestration that used to live in the old project's `site/build.py`,
pointed at a file instead of at HTML.
"""

import json

import numpy as np

from valuebets import config as vb_config
from valuebets.backtest import (BacktestConfig, by_market, by_period, equity_curve,
                                load_dataset, run, summary, sweep_bands, validate)
from valuebets.insights import compute

from .artifacts import EVIDENCE_JSON

# Filters that were validated out of sample in the original project. Shown as
# evidence, not applied to anything: a rule picked by looking at results has to
# be scored on data that did not choose it.
VALIDATED = dict(max_edge=0.08, exclude_competitions=("FL1",))
VALIDATED_LABEL = "exclude FL1 + cap edge at 8%"

BANDS = [(1.70, 2.00), (1.60, 2.50), (1.50, 3.00), (1.01, 99.0)]

EQUITY_POINTS = 400        # enough to draw a curve, small enough to embed


def _plain(value):
    """numpy scalars are not JSON, and pandas hands them out everywhere."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def _thin(points, limit=EQUITY_POINTS):
    """Keep the shape of a curve without shipping every one of 7,000 bets."""
    if len(points) <= limit:
        return points
    step = len(points) / limit
    return [points[min(int(i * step), len(points) - 1)] for i in range(limit)]


def _history_rows():
    """Lines in history.csv, minus the header. Cheap enough to call per build."""
    path = vb_config.DATA_DIR / "history.csv"
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def build(progress=print, data=None):
    """Recompute everything and write evidence.json. Returns the payload."""
    path = data or (vb_config.DATA_DIR / "history.csv")
    progress(f"Loading {path}")
    dataset = load_dataset(str(path))
    progress(f"  {len(dataset):,} matches, {dataset.competition.nunique()} competitions")

    cfg = BacktestConfig()
    progress("Walk-forward backtest (this is the slow part)")
    bets, preds = run(dataset, cfg, with_predictions=True)
    head = summary(bets, cfg.stake)
    progress(f"  {head['n']:,} bets, ROI {head['roi']:+.2f}%")

    progress("Sweeping odds bands")
    sweep = sweep_bands(dataset, BANDS, cfg)

    progress("Validating filters out of sample")
    validation = validate(bets, BacktestConfig(**VALIDATED), stake=cfg.stake)

    progress("Computing insights")
    insights = compute(bets, preds, limit=10, sweep_rows=sweep, validation=validation,
                       validation_label=VALIDATED_LABEL, dataset=dataset, cfg=cfg)

    coverage = sorted(
        ([comp, int(len(group)), f"{group.date.min():%b %Y}", f"{group.date.max():%b %Y}"]
         for comp, group in dataset.groupby("competition")),
        key=lambda row: -row[1])

    payload = _plain({
        "summary": head,
        "insights": insights,
        "by_market": by_market(bets, cfg.stake),
        "by_period": by_period(bets, stake=cfg.stake),
        "sweep": [{k: v for k, v in row.items() if k != "verdict"} for row in sweep],
        "equity": _thin(equity_curve(bets)),
        "coverage": coverage,
        "n_matches": int(len(dataset)),
        # Rows in history.csv as it stood, so a later run can tell whether
        # this is still current without re-running the backtest to find out.
        # The count differs from n_matches above, which is after the loader
        # drops rows it cannot price.
        "source_rows": _history_rows(),
        "n_competitions": int(dataset.competition.nunique()),
        "config": {"odds_min": cfg.odds_min, "odds_max": cfg.odds_max,
                   "min_edge": cfg.min_edge, "markets": list(cfg.markets),
                   "stake": cfg.stake},
    })

    EVIDENCE_JSON.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_JSON.write_text(json.dumps(payload), encoding="utf-8")
    progress(f"Wrote {EVIDENCE_JSON}")
    return payload
