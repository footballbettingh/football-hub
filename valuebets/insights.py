"""Derived findings — the things worth knowing that a bare ROI number hides.

Each insight is computed, carries its own evidence table, and states a verdict
in one of four states (good / warning / critical / neutral). Nothing here is
hand-written commentary: if the data changes, the wording and the verdict change
with it.

The insights are deliberately the ones that can DISPROVE an edge. A dashboard
that only surfaces flattering numbers is a marketing page, not an analysis.
"""

import numpy as np
import pandas as pd

from .backtest import OUTCOMES, STAKE, bootstrap_roi_ci

MODEL_COLS = ["model_home", "model_draw", "model_away"]
MARKET_COLS = ["market_home", "market_draw", "market_away"]


def _roi(chunk, stake=STAKE):
    return float(chunk.pnl.sum() / (len(chunk) * stake) * 100) if len(chunk) else 0.0


def _onehot(actual):
    return np.stack([(actual == o).to_numpy(dtype=float) for o in OUTCOMES], axis=1)


def _check_finite(probs, name):
    """A single NaN anywhere makes the mean NaN, and a NaN headline reads as a
    formatting glitch rather than a data fault. Fail loudly instead."""
    probs = np.asarray(probs, dtype=float)
    if not np.isfinite(probs).all():
        bad = int((~np.isfinite(probs)).any(axis=1).sum())
        raise ValueError(f"{name}: {bad} row(s) contain non-finite probabilities")
    return probs


def brier(probs, actual):
    """Multiclass Brier score — mean squared error over the 3 outcomes.

    Lower is better. This is the cleanest head-to-head between the model and
    the market, because it uses every match rather than only the ones the model
    liked, and it rewards being right *and* being appropriately confident.
    """
    probs = _check_finite(probs, "brier")
    return float(np.mean(np.sum((probs - _onehot(actual)) ** 2, axis=1)))


def log_loss(probs, actual, eps=1e-12):
    probs = _check_finite(probs, "log_loss")
    picked = np.sum(probs * _onehot(actual), axis=1)
    return float(-np.mean(np.log(np.clip(picked, eps, 1.0))))


# --------------------------------------------------------------------------
# individual insights
# --------------------------------------------------------------------------

def model_vs_market(preds):
    """Does the model forecast better than the closing price? The decisive one."""
    if preds.empty:
        return None
    model = preds[MODEL_COLS].to_numpy()
    market = preds[MARKET_COLS].to_numpy()
    mb, kb = brier(model, preds.actual), brier(market, preds.actual)
    ml, kl = log_loss(model, preds.actual), log_loss(market, preds.actual)
    delta = (mb - kb) / kb * 100

    beats = mb < kb
    return {
        "id": "model-vs-market",
        "title": "Model vs the closing price",
        "state": "good" if beats else "critical",
        "headline": (f"The market forecasts {abs(delta):.1f}% "
                     f"{'worse' if beats else 'better'} than the model"),
        "detail": (
            "Brier score over every scored match, lower is better. This is the "
            "cleanest test there is: it uses all matches rather than only the ones "
            "the model liked. "
            + ("The model is the better forecaster, which is the precondition for an edge."
               if beats else
               "The closing price is the better forecaster. If the model cannot out-predict "
               "the market, no staking rule or filter will turn it into profit — the edge "
               "has to come from forecasting, not from bet selection.")),
        "evidence": {
            "type": "table",
            "columns": ["", "Brier (lower better)", "Log loss"],
            "rows": [["Model", f"{mb:.4f}", f"{ml:.4f}"],
                     ["Market close", f"{kb:.4f}", f"{kl:.4f}"]],
        },
        "stat": f"{mb:.4f} vs {kb:.4f}",
    }


def edge_reliability(bets):
    """If the model's edge estimate means anything, bigger edge -> better ROI."""
    if len(bets) < 40:
        return None
    buckets = [(0.03, 0.05), (0.05, 0.08), (0.08, 0.12), (0.12, 1.0)]
    rows, rois, sizes = [], [], []
    for lo, hi in buckets:
        chunk = bets[(bets.edge >= lo) & (bets.edge < hi)]
        if chunk.empty:
            continue
        r = _roi(chunk)
        rows.append([f"{lo * 100:.0f}-{hi * 100:.0f}%" if hi < 1 else f"{lo * 100:.0f}%+",
                     f"{len(chunk)}", f"{chunk.won.mean() * 100:.1f}%", f"{r:+.2f}%"])
        rois.append(r)
        sizes.append(len(chunk))

    if len(rois) < 3:
        return None
    # Rank correlation between bucket order and ROI: +1 means the edge estimate
    # ranks bets perfectly, ~0 means it carries no information.
    order = np.arange(len(rois))
    corr = float(np.corrcoef(order, rois)[0, 1]) if np.std(rois) > 0 else 0.0
    monotone = corr > 0.5

    return {
        "id": "edge-reliability",
        "title": "Does a bigger edge pay better?",
        "state": "good" if monotone else "warning",
        "headline": ("Higher model edge did produce higher returns"
                     if monotone else
                     "Bigger claimed edges did not produce better returns"),
        "detail": (
            "Bets grouped by the edge the model claimed at the time. If the edge "
            "number carries information, ROI should climb down this table. "
            + ("It does, which is weak evidence the estimate is meaningful."
               if monotone else
               "It doesn't — so the edge figure is not ranking bets usefully, and "
               "raising the edge threshold would not have improved results. That is "
               "the signature of noise rather than signal.")),
        "evidence": {
            "type": "table",
            "columns": ["Claimed edge", "Bets", "Win rate", "ROI"],
            "rows": rows,
        },
        "stat": f"rank corr {corr:+.2f}",
    }


def calibration(preds, bins=6):
    """When the model says 40%, does it happen 40% of the time?"""
    if preds.empty:
        return None
    probs = preds[MODEL_COLS].to_numpy().ravel()
    hits = _onehot(preds.actual).ravel()
    edges = np.linspace(0.05, 0.65, bins + 1)
    rows, gaps, weights = [], [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (probs >= lo) & (probs < hi)
        if mask.sum() < 30:
            continue
        predicted, observed = probs[mask].mean(), hits[mask].mean()
        rows.append([f"{lo * 100:.0f}-{hi * 100:.0f}%", f"{mask.sum():,}",
                     f"{predicted * 100:.1f}%", f"{observed * 100:.1f}%",
                     f"{(observed - predicted) * 100:+.1f}pp"])
        gaps.append(abs(observed - predicted))
        weights.append(mask.sum())

    if not rows:
        return None
    mae = float(np.average(gaps, weights=weights)) * 100
    state = "good" if mae < 2 else "warning" if mae < 4 else "critical"
    return {
        "id": "calibration",
        "title": "Is the model calibrated?",
        "state": state,
        "headline": f"Predictions are off by {mae:.1f} percentage points on average",
        "detail": (
            "Every model probability bucketed against how often that outcome actually "
            "happened. A calibrated model sits on the diagonal. Miscalibration matters "
            "more than accuracy here: a model that says 45% when the truth is 40% will "
            "invent edge on exactly the bets it feels most confident about."),
        "evidence": {
            "type": "table",
            "columns": ["Predicted", "Cases", "Avg predicted", "Actually happened", "Gap"],
            "rows": rows,
        },
        "stat": f"{mae:.1f}pp mean gap",
    }


def stability(bets, periods=4):
    """An edge that lives in one period is overfitting."""
    if len(bets) < periods * 10:
        return None
    ordered = bets.sort_values("date").reset_index(drop=True)
    rows, rois = [], []
    for i, pos in enumerate(np.array_split(np.arange(len(ordered)), periods), 1):
        chunk = ordered.iloc[pos]
        r = _roi(chunk)
        rois.append(r)
        rows.append([f"Period {i}",
                     f"{chunk.date.min():%b %Y} - {chunk.date.max():%b %Y}",
                     f"{len(chunk)}", f"{r:+.2f}%"])
    spread = max(rois) - min(rois)
    flips = sum(1 for r in rois if r < 0) not in (0, len(rois))
    state = "good" if spread < 10 else "warning" if spread < 25 else "critical"
    return {
        "id": "stability",
        "title": "Is the result stable over time?",
        "state": state,
        "headline": f"ROI swings {spread:.0f} points between periods",
        "detail": (
            "The sample split into four equal blocks by date. A real edge is boring — "
            "similar returns throughout. "
            + ("These periods disagree wildly, and the sign flips between them, which is "
               "what overfitting looks like from the outside."
               if flips and spread >= 25 else
               "Wide swings mean the headline ROI is an average over periods that do not "
               "resemble each other."
               if spread >= 10 else
               "The periods broadly agree, which is what you want to see.")),
        "evidence": {
            "type": "table",
            "columns": ["Period", "Span", "Bets", "ROI"],
            "rows": rows,
        },
        "stat": f"{spread:.0f}pt spread",
    }


def by_competition(bets):
    if bets.empty or bets.competition.nunique() < 2:
        return None
    rows, rois = [], []
    for comp, chunk in bets.groupby("competition"):
        if len(chunk) < 10:
            continue
        r = _roi(chunk)
        rois.append(r)
        rows.append([comp, f"{len(chunk)}", f"{chunk.won.mean() * 100:.1f}%", f"{r:+.2f}%"])
    if len(rows) < 2:
        return None
    rows.sort(key=lambda r: float(r[3].rstrip("%")), reverse=True)
    positive = sum(1 for r in rois if r > 0)
    return {
        "id": "by-competition",
        "title": "Which leagues carry the result?",
        "state": "neutral",
        "headline": f"{positive} of {len(rois)} leagues finished positive",
        "detail": (
            "Each competition is modelled separately, so these are independent-ish "
            "samples of the same strategy. If profit comes from one league only, you "
            "have found a quirk of that league — or noise — rather than a method. "
            "Per-league samples are small; treat the ordering as indicative."),
        "evidence": {
            "type": "table",
            "columns": ["League", "Bets", "Win rate", "ROI"],
            "rows": rows,
        },
        "stat": f"{positive}/{len(rois)} positive",
    }


def outcome_split(bets):
    if bets.empty:
        return None
    rows = []
    for outcome in OUTCOMES:
        chunk = bets[bets.outcome == outcome]
        if chunk.empty:
            continue
        rows.append([outcome.title(), f"{len(chunk)}", f"{chunk.odds.mean():.2f}",
                     f"{chunk.won.mean() * 100:.1f}%", f"{_roi(chunk):+.2f}%"])
    if len(rows) < 2:
        return None
    dominant = max(rows, key=lambda r: int(r[1]))
    share = int(dominant[1]) / len(bets) * 100
    return {
        "id": "outcome-split",
        "title": "What is the model actually betting on?",
        "state": "warning" if share > 70 else "neutral",
        "headline": f"{share:.0f}% of bets are on {dominant[0].lower()}",
        "detail": (
            "A model that only ever backs one side of the market is expressing a "
            "single systematic opinion, not finding per-match value. That concentrates "
            "all the risk into whether that one opinion is right."
            if share > 70 else
            "Bets are spread across outcome types, so the result is not one systematic "
            "lean dressed up as many separate bets."),
        "evidence": {
            "type": "table",
            "columns": ["Bet", "Count", "Avg odds", "Win rate", "ROI"],
            "rows": rows,
        },
        "stat": f"{dominant[0].lower()} {share:.0f}%",
    }


def market_split(bets):
    """Different markets, different error profiles. A pooled ROI hides that."""
    if bets.empty or "market" not in bets.columns or bets.market.nunique() < 2:
        return None
    rows, best = [], None
    for market, chunk in bets.groupby("market"):
        lo, hi = bootstrap_roi_ci(chunk.pnl)
        roi = _roi(chunk)
        rows.append([market, f"{len(chunk):,}", f"{chunk.odds.mean():.2f}",
                     f"{chunk.won.mean() * 100:.1f}%", f"{roi:+.2f}%",
                     f"[{lo:+.1f}%, {hi:+.1f}%]"])
        if best is None or len(chunk) > best[1]:
            best = (market, len(chunk), lo)
    rows.sort(key=lambda r: -int(r[1].replace(",", "")))

    any_proven = any(float(r[5].split(",")[0].strip("[+%")) > 0 for r in rows)
    return {
        "id": "market-split",
        "title": "Which market is worth modelling?",
        "state": "good" if any_proven else "neutral",
        "headline": (f"{best[0]} gives the largest sample ({best[1]:,} bets)"),
        "detail": (
            "Totals (Over/Under) and 1X2 are separate bets on the same model. Totals "
            "produce far more qualifying selections, because a two-way market prices "
            "closer to even money more often — which means a much tighter confidence "
            "interval for the same seasons of data. "
            + ("At least one market's interval clears zero."
               if any_proven else
               "Neither market's interval clears zero, so neither has demonstrated an "
               "edge — but the totals interval is narrow enough that a real edge of a "
               "few percent would have shown up by now.")),
        "evidence": {
            "type": "table",
            "columns": ["Market", "Bets", "Avg odds", "Win rate", "ROI", "95% CI"],
            "rows": rows,
        },
        "stat": f"{len(rows)} markets",
    }


def band_sensitivity(sweep_rows):
    """Does widening the odds band help? Measured, not assumed."""
    if not sweep_rows or len(sweep_rows) < 3:
        return None
    usable = [r for r in sweep_rows if r["n"]]
    if len(usable) < 3:
        return None

    rows = [[r["band"], f"{r['n']:,}", f"{r['win_rate'] * 100:.1f}%",
             f"{r['roi']:+.2f}%", f"[{r['ci'][0]:+.1f}%, {r['ci'][1]:+.1f}%]"]
            for r in usable]
    widths = [r["odds_max"] - r["odds_min"] for r in usable]
    rois = [r["roi"] for r in usable]
    corr = float(np.corrcoef(widths, rois)[0, 1]) if np.std(rois) > 0 else 0.0
    worst = min(usable, key=lambda r: r["roi"])
    helps = corr > 0

    return {
        "id": "band-sensitivity",
        "title": "Does a wider odds band help?",
        "state": "warning" if not helps else "good",
        "headline": ("Widening the band improved returns" if helps else
                     "Every widening of the band made returns worse"),
        "detail": (
            "The same model and edge threshold, run over different price ranges. "
            + ("Wider bands did better, so the original range was leaving bets on the "
               "table."
               if helps else
               "The band is not an arbitrary restriction — it is a filter on the model's "
               "own worst errors. Removing it entirely "
               f"({worst['band']}) drops ROI to {worst['roi']:+.2f}% with an interval that "
               "excludes zero, i.e. a reliably losing strategy: at extreme prices the model "
               "backs long-odds home underdogs, which is exactly its known bias. A narrow "
               "band buys accuracy at the cost of sample size.")
            + " Note this table is a diagnostic, not a tuning dial — picking the "
              "best-scoring row would be fitting the parameter to the test set."),
        "evidence": {
            "type": "table",
            "columns": ["Odds band", "Bets", "Win rate", "ROI", "95% CI"],
            "rows": rows,
        },
        "stat": f"corr(width, ROI) {corr:+.2f}",
    }


def edge_deciles(bets, n=10):
    """ROI by edge decile — finer than the bucket view, and it tells a story."""
    if len(bets) < n * 30:
        return None
    frame = bets.copy()
    try:
        frame["_d"] = pd.qcut(frame.edge, n, labels=False, duplicates="drop")
    except ValueError:
        return None

    rows, rois = [], []
    for d, g in frame.groupby("_d"):
        r = _roi(g)
        rois.append(r)
        rows.append([f"{d + 1}", f"{g.edge.min() * 100:.1f}-{g.edge.max() * 100:.1f}%",
                     f"{len(g):,}", f"{g.won.mean() * 100:.1f}%", f"{r:+.2f}%"])

    third = max(len(rois) // 3, 1)
    low, high = float(np.mean(rois[:third])), float(np.mean(rois[-third:]))
    inverted = low > high
    return {
        "id": "edge-deciles",
        "title": "Small disagreements beat big ones",
        "state": "warning" if inverted else "neutral",
        "headline": (f"Lowest-edge bets returned {low:+.2f}%, highest-edge {high:+.2f}%"
                     if inverted else
                     f"Highest-edge bets returned {high:+.2f}%, lowest {low:+.2f}%"),
        "detail": (
            "Every bet split into ten equal groups by the edge the model claimed. "
            + ("The ranking is inverted: the model's *small* disagreements with the "
               "market pay, and its large ones lose. That is not a paradox — it follows "
               "from the market being the better forecaster. When this model says it is "
               "twenty points better than the closing line, the overwhelmingly likely "
               "explanation is that the model is wrong, not that the market is. A big "
               "claimed edge is a error signal, so capping edge from ABOVE is the "
               "counter-intuitive move that survives out-of-sample testing."
               if inverted else
               "Higher claimed edges did produce higher returns, which is weak evidence "
               "the edge estimate carries information.")),
        "evidence": {
            "type": "table",
            "columns": ["Decile", "Edge range", "Bets", "Win rate", "ROI"],
            "rows": rows,
        },
        "stat": f"low {low:+.1f}% vs high {high:+.1f}%",
    }


def holdout(validation, label):
    """Did a hand-picked filter survive data that did not choose it?"""
    if not validation:
        return None
    tr, te = validation["train"], validation["test"]
    held = validation["held_up"]
    proven = te["ci_after"][0] > 0
    return {
        "id": "holdout",
        "title": "Do the filters survive out of sample?",
        "state": "good" if (held and proven) else "warning" if held else "critical",
        "headline": (f"{label}: {validation['gain_test']:+.2f} points out of sample"),
        "detail": (
            "Filters like &ldquo;drop this league&rdquo; or &ldquo;cap the edge&rdquo; are "
            "chosen by a human looking at results, which is exactly how a backtest gets "
            "flattered. So the sample is split by date: the filters were picked on the "
            "first half and are scored here on the second, which never influenced them. "
            + ("The gain survived, which is the encouraging direction — a rule that only "
               "works on the data that produced it is a description of the past. "
               if held else
               "The gain did not survive. That is curve fitting: the rule describes the "
               "past rather than predicting the future. ")
            + ("The out-of-sample interval also excludes zero."
               if proven else
               "Note the out-of-sample interval still includes zero, so this is a "
               "promising direction, not a demonstrated edge.")),
        "evidence": {
            "type": "table",
            "columns": ["Half", "Span", "Bets", "ROI before", "ROI after", "Gain"],
            "rows": [
                ["Train (chose the filters)",
                 f"{tr['span'][0]:%b %Y}-{tr['span'][1]:%b %Y}", f"{tr['n_after']:,}",
                 f"{tr['roi_before']:+.2f}%", f"{tr['roi_after']:+.2f}%",
                 f"{validation['gain_train']:+.2f}pp"],
                ["Test (never consulted)",
                 f"{te['span'][0]:%b %Y}-{te['span'][1]:%b %Y}", f"{te['n_after']:,}",
                 f"{te['roi_before']:+.2f}%", f"{te['roi_after']:+.2f}%",
                 f"{validation['gain_test']:+.2f}pp"],
            ],
        },
        "stat": f"{validation['gain_test']:+.2f}pp out of sample",
    }


def random_benchmark(bets, dataset, cfg, samples=200, seed=5):
    """What would picking at RANDOM in the same odds band have returned?

    The most honest number in the project. Best prices across bookmakers carry
    almost no margin, so a random selection inside the band sits near zero. If
    the model lands BELOW that, its selections are not merely weak — they are
    adversely selected: it systematically backs the side the market considers
    overpriced, and the market is right.

    Vectorised: masked uniform randoms + argmax picks one in-band outcome per
    match, so 200 replications over 60k matches stay cheap.
    """
    if bets.empty or dataset is None or dataset.empty:
        return None
    cols = ("home_odds", "draw_odds", "away_odds")
    if not all(c in dataset.columns for c in cols):
        return None

    frame = dataset.dropna(subset=list(cols))
    prices = frame[list(cols)].to_numpy(dtype=float)
    won = np.stack([(frame.home_goals > frame.away_goals),
                    (frame.home_goals == frame.away_goals),
                    (frame.away_goals > frame.home_goals)], axis=1)
    band = (prices >= cfg.odds_min) & (prices <= cfg.odds_max)
    usable = band.any(axis=1)
    if usable.sum() < 100:
        return None
    prices, won, band = prices[usable], won[usable], band[usable]

    rng = np.random.default_rng(seed)
    rows_idx = np.arange(len(prices))
    rois = []
    for _ in range(samples):
        # argmax over randoms masked to the band = uniform pick among in-band legs
        pick = np.argmax(rng.random(prices.shape) * band, axis=1)
        p = prices[rows_idx, pick]
        w = won[rows_idx, pick]
        rois.append(float(np.where(w, p - 1.0, -1.0).mean() * 100))

    rnd = float(np.mean(rois))
    model_roi = float(bets.pnl.sum() / (len(bets) * cfg.stake) * 100)
    delta = model_roi - rnd
    adverse = delta < 0

    return {
        "id": "random-benchmark",
        "title": "Is the model better than picking at random?",
        "state": "critical" if adverse else "good",
        "headline": (f"The model does {abs(delta):.2f} points "
                     f"{'WORSE' if adverse else 'better'} than random selection"),
        "detail": (
            "Random selection inside the same odds band, on the same fixtures, at the "
            "same best-of-bookmaker prices. It lands near zero rather than at a loss "
            "because shopping for the top price already strips out the bookmaker margin. "
            + ("The model finishes below that line, which means its selections are "
               "actively harmful rather than merely weak: it keeps backing the side the "
               "market prices as too short, and the market keeps being right. Every "
               "time this model 'finds value', what it has really found is its own "
               "largest error."
               if adverse else
               "The model finishes above that line, so its selection rule carries real "
               "information even if the margin is not enough to profit.")),
        "evidence": {
            "type": "table",
            "columns": ["Selection rule", "Bets", "ROI"],
            "rows": [["Random pick in band", f"{int(usable.sum()):,}", f"{rnd:+.2f}%"],
                     ["This model", f"{len(bets):,}", f"{model_roi:+.2f}%"],
                     ["Difference", "", f"{delta:+.2f}pp"]],
        },
        "stat": f"{delta:+.2f}pp vs random",
    }


def compute(bets, preds, limit=5, sweep_rows=None, validation=None, validation_label="",
            dataset=None, cfg=None):
    """Run every insight; return the ones that had enough data, most decisive first."""
    candidates = [
        random_benchmark(bets, dataset, cfg) if (dataset is not None and cfg) else None,
        model_vs_market(preds),
        holdout(validation, validation_label),
        edge_deciles(bets),
        edge_reliability(bets),
        band_sensitivity(sweep_rows or []),
        market_split(bets),
        calibration(preds),
        stability(bets),
        outcome_split(bets),
        by_competition(bets),
    ]
    found = [c for c in candidates if c]
    # Findings that argue against an edge come first — they are the ones that
    # change a decision.
    rank = {"critical": 0, "warning": 1, "neutral": 2, "good": 3}
    found.sort(key=lambda c: rank.get(c["state"], 9))
    return found[:limit]


# --------------------------------------------------------------------------
# best pick
# --------------------------------------------------------------------------

def top_picks(fixtures, limit=20):
    """Ranked shortlist of upcoming selections.

    Qualifying picks first (those the strategy would actually place), then the
    rest by edge. The page lets the reader choose how many to show; this just
    supplies an ordering that never puts an unplayable long shot above a real
    selection.
    """
    playable = [f for f in fixtures if f.get("known")]
    playable.sort(key=lambda f: (not f.get("qualifies"), -f["edge"]))
    return playable[:limit]


def best_pick(fixtures):
    """The single strongest upcoming selection, with an honest qualifier.

    `fixtures` is the list produced by site.build.fixture_rows: each has a model
    probability, a de-vigged market probability, an edge and a `qualifies` flag.
    """
    playable = [f for f in fixtures if f.get("known")]
    if not playable:
        return None
    qualifying = [f for f in playable if f.get("qualifies")]
    pool = qualifying or playable
    pick = max(pool, key=lambda f: f["edge"])
    return {
        "pick": pick,
        "qualifies": bool(qualifying),
        "n_qualifying": len(qualifying),
        "n_considered": len(playable),
    }
