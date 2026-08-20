"""Walk-forward backtest: does the model find profitable value bets, or not?

Rules to avoid fooling yourself:
1. NO LOOKAHEAD. The model is refit on matches that finished STRICTLY BEFORE
   the bet's match date. Same-day results are excluded too — placing a Saturday
   bet, you don't know Saturday's other results.
2. Track EVERY flagged bet, not just wins, and report over the full sample.
3. Compare against DE-VIGGED market probabilities. Raw 1/odds sums to ~1.06;
   counting that margin as edge is the easiest way to fake a profitable
   backtest.
4. One model PER COMPETITION. Team strengths are ratios against the league
   average, so pooling divisions treats a mid-table Championship side as a
   mid-table Premier League side.

Beyond point-estimate ROI this reports the things that say whether an edge is
real: a bootstrap confidence interval and a per-period breakdown.
"""

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from .markets import DEFAULT_MARKETS, resolve_markets
from .model import PoissonModel

# ---- defaults (overridable via BacktestConfig) ----
MIN_TRAINING_MATCHES = 150
# Wider than the original 1.70-2.00. That band was a guess, and narrowing the
# price range does not narrow the model's error — it just discards bets. See
# `vb.py sweep` for the measured effect of each band.
ODDS_MIN, ODDS_MAX = 1.60, 2.50
MIN_EDGE = 0.03
STAKE = 10
REFIT_EVERY = 5
BOOTSTRAP_SAMPLES = 5000

OUTCOMES = ("home", "draw", "away")


@dataclass
class BacktestConfig:
    min_training_matches: int = MIN_TRAINING_MATCHES
    odds_min: float = ODDS_MIN
    odds_max: float = ODDS_MAX
    min_edge: float = MIN_EDGE
    stake: float = STAKE
    refit_every: int = REFIT_EVERY
    use_devig: bool = True
    markets: tuple = DEFAULT_MARKETS
    model_kwargs: dict = field(default_factory=dict)

    # --- post-selection filters -------------------------------------------
    # These are applied to bets AFTER they are generated, so they can be varied
    # without re-running the walk-forward loop. Every one of them is a rule
    # chosen by a human looking at results, which is exactly how a backtest gets
    # flattered — so `validate()` exists to judge them on data that did not
    # choose them. Defaults are OFF: the honest baseline is no filtering.
    max_edge: float = None          # drop selections claiming MORE than this
    exclude_competitions: tuple = ()
    one_per_match: str = None       # None | "edge" | "lowest_edge" | market label


def implied_prob(odds):
    return 1 / odds


def devig_probabilities(home_odds, draw_odds, away_odds):
    """Bookmaker implied probabilities with the margin removed."""
    raw = np.array([1 / home_odds, 1 / draw_odds, 1 / away_odds], dtype=float)
    return raw / raw.sum()


def load_dataset(path):
    """Load a matches+odds table (the football_data_uk shape)."""
    frame = pd.read_csv(path, parse_dates=["date"])
    needed = {"date", "home_team", "away_team", "home_goals", "away_goals",
              "home_odds", "draw_odds", "away_odds"}
    missing = needed - set(frame.columns)
    if missing:
        raise SystemExit(f"{path} is missing columns: {sorted(missing)}")
    frame = frame.dropna(subset=["home_odds", "draw_odds", "away_odds"])

    # Drop rows whose CONSENSUS prices are present-but-incomplete. One such row
    # out of 64,715 (a single MLS fixture missing AvgCH) turned the market's
    # Brier score into NaN and silently poisoned the headline comparison. The
    # consensus columns are optional overall, so this only applies where they
    # exist at all.
    cons = ["home_odds_cons", "draw_odds_cons", "away_odds_cons"]
    if all(c in frame.columns for c in cons):
        usable = frame[cons].notna().all(axis=1) & (frame[cons] > 1.0).all(axis=1)
        dropped = int((~usable).sum())
        if dropped:
            print(f"  dropped {dropped} row(s) with unusable consensus prices")
        frame = frame[usable]

    if "competition" not in frame.columns:
        frame["competition"] = "ALL"
    return frame.sort_values("date", kind="mergesort").reset_index(drop=True)


def run(dataset, cfg=None, with_predictions=False):
    """Walk-forward over the dataset, one competition at a time.

    With `with_predictions`, also returns the model's probabilities for EVERY
    scored match, not just the ones that cleared the bet filters. Insights like
    calibration and model-vs-market Brier score need the full set — judging a
    model only on the bets it flagged is survivorship bias.
    """
    cfg = cfg or BacktestConfig()
    bet_frames, pred_frames = [], []
    for _, group in dataset.groupby("competition", sort=True):
        bets, preds = _run_one(group, cfg)
        bet_frames.append(bets)
        pred_frames.append(preds)

    bet_frames = [f for f in bet_frames if not f.empty]
    bets = (pd.concat(bet_frames, ignore_index=True).sort_values("date").reset_index(drop=True)
            if bet_frames else pd.DataFrame(
                columns=["date", "competition", "market", "match", "outcome", "odds",
                         "model_prob", "market_prob", "edge", "won", "pnl"]))
    if not with_predictions:
        return bets

    pred_frames = [f for f in pred_frames if not f.empty]
    preds = (pd.concat(pred_frames, ignore_index=True).sort_values("date").reset_index(drop=True)
             if pred_frames else pd.DataFrame())
    return bets, preds


def _run_one(df, cfg):
    df = df.sort_values("date", kind="mergesort").reset_index(drop=True)
    if len(df) <= cfg.min_training_matches:
        return pd.DataFrame(), pd.DataFrame()

    markets = resolve_markets(cfg.markets)
    # A market whose price columns are absent is skipped entirely rather than
    # producing rows with missing prices. O/U 1.5 has no free historical odds,
    # so it drops out here on real data — silently betting it would be worse.
    markets = [m for m in markets if all(c in df.columns for c in m.columns)]
    consensus = {m.key: m.has_consensus(df.columns) for m in markets}

    # df is date-sorted, so the "strictly earlier" cut is a binary search rather
    # than a full rescan per row (which made the loop O(n^2) at 5k+ matches).
    dates = pd.DatetimeIndex(df.date)
    # Shot columns ride along so the model can rate teams on a less noisy
    # signal than goals; absent, it falls back to goals and says so.
    fit_columns = [c for c in ("date", "home_team", "away_team", "home_goals",
                               "away_goals", "home_sot", "away_sot",
                               "home_shots", "away_shots") if c in df.columns]

    # One model object reused across refits, so the MLE estimator can warm-start
    # from the previous solution. Cold-starting every refit is ~6x slower and
    # lands on the same optimum. The ratio estimator is unaffected.
    model = PoissonModel(**cfg.model_kwargs)
    fitted = False
    bets, predictions, last_fit = [], [], -10 ** 9
    competition = df.competition.iloc[0]

    for i in range(cfg.min_training_matches, len(df)):
        row = df.iloc[i]
        cut = int(dates.searchsorted(row.date, side="left"))
        if cut < cfg.min_training_matches:
            continue

        # Refitting every match matches the every-5th model to ~4 decimals but
        # is N times slower. Refitting less often only ever uses OLDER data, so
        # it cannot introduce lookahead.
        if not fitted or i - last_fit >= cfg.refit_every:
            model.fit(df.iloc[:cut][fit_columns], as_of=row.date)
            fitted = True
            last_fit = i

        if row.home_team not in model.home_attack or row.away_team not in model.away_attack:
            continue  # promoted team with no history yet

        label = f"{row.home_team} vs {row.away_team}"
        score = f"{int(row.home_goals)}-{int(row.away_goals)}"

        # Predictions are recorded for 1X2 only: calibration and the Brier
        # comparison against the market are defined over one outcome space, and
        # 1X2 is the one every dataset has.
        probs = model.predict_probabilities(row.home_team, row.away_team)
        fair_1x2 = ((row.home_odds_cons, row.draw_odds_cons, row.away_odds_cons)
                    if consensus.get("1x2") else (row.home_odds, row.draw_odds, row.away_odds))
        market_1x2 = (devig_probabilities(*fair_1x2) if cfg.use_devig
                      else np.array([implied_prob(o) for o in fair_1x2]))
        results_1x2 = (row.home_goals > row.away_goals,
                       row.home_goals == row.away_goals,
                       row.away_goals > row.home_goals)
        predictions.append({
            "date": row.date, "competition": competition, "match": label,
            "actual": OUTCOMES[int(np.argmax(results_1x2))],
            "model_home": float(probs[0]), "model_draw": float(probs[1]),
            "model_away": float(probs[2]),
            "market_home": float(market_1x2[0]), "market_draw": float(market_1x2[1]),
            "market_away": float(market_1x2[2]),
        })

        for market in markets:
            if not market.available(row):
                continue
            for sel in market.selections(model, row, row.home_team, row.away_team,
                                         use_devig=cfg.use_devig,
                                         consensus=consensus[market.key]):
                if not (cfg.odds_min <= sel.odds <= cfg.odds_max):
                    continue
                edge = sel.model_prob - sel.market_prob
                if edge < cfg.min_edge:
                    continue
                bets.append({
                    "date": row.date,
                    "competition": competition,
                    "market": sel.market,
                    "match": label,
                    "home_team": row.home_team,
                    "away_team": row.away_team,
                    "score": score,
                    "outcome": sel.name,
                    "odds": sel.odds,
                    "model_prob": sel.model_prob,
                    "market_prob": sel.market_prob,
                    "edge": float(edge),
                    "won": sel.won,
                    "pnl": cfg.stake * (sel.odds - 1) if sel.won else -cfg.stake,
                })

    return pd.DataFrame(bets), pd.DataFrame(predictions)


def apply_filters(bets, cfg):
    """Post-selection filters. Returns a new frame; never mutates."""
    if bets.empty:
        return bets
    out = bets
    if cfg.exclude_competitions:
        out = out[~out.competition.isin(cfg.exclude_competitions)]
    if cfg.max_edge is not None:
        out = out[out.edge < cfg.max_edge]
    if cfg.one_per_match and not out.empty:
        # A match can produce both a 1X2 and an O/U selection (15% of matches
        # do). Those are correlated bets on one event, so keeping one is
        # defensible — but WHICH one matters, and "biggest edge" is the worst
        # available rule, because a big claimed edge is model error.
        if cfg.one_per_match == "edge":
            out = out.sort_values("edge", ascending=False)
        elif cfg.one_per_match == "lowest_edge":
            out = out.sort_values("edge", ascending=True)
        else:
            out = out.assign(_pref=(out.market != cfg.one_per_match)).sort_values(
                ["_pref", "edge"]).drop(columns="_pref", errors="ignore")
        out = out.drop_duplicates(subset=["date", "match"], keep="first")
    return out.sort_values("date").reset_index(drop=True)


# A full resample matrix is samples x n float64: at 5,000 x 18,960 that is
# 723 MiB, which blew up once the dataset reached 40 competitions. Drawing in
# chunks gives identical statistics at a bounded memory cost.
_BOOTSTRAP_CHUNK = 250


def _bootstrap_chunked(n, samples, rng, statistic):
    out = []
    for start in range(0, samples, _BOOTSTRAP_CHUNK):
        size = min(_BOOTSTRAP_CHUNK, samples - start)
        draws = rng.integers(0, n, size=(size, n))
        out.append(np.asarray(statistic(draws), dtype=float))
    return np.concatenate(out)


def bootstrap_roi_ci_by_match(bets, stake=STAKE, samples=BOOTSTRAP_SAMPLES, seed=42):
    """Bootstrap resampling MATCHES rather than individual bets.

    Two selections on the same fixture share a scoreline, so they are not
    independent draws. Resampling bets treats them as if they were, which
    narrows the interval it should widen. The effect here is mild (width 4.55
    -> 4.75 on the full sample) but the by-match version is the correct one.
    """
    if bets.empty:
        return 0.0, 0.0
    grouped = bets.groupby(["date", "match"])
    pnl = grouped.pnl.sum().to_numpy()
    counts = grouped.size().to_numpy()
    rng = np.random.default_rng(seed)
    rois = _bootstrap_chunked(
        len(pnl), samples, rng,
        lambda d: pnl[d].sum(axis=1) / (counts[d].sum(axis=1) * stake) * 100)
    return float(np.percentile(rois, 2.5)), float(np.percentile(rois, 97.5))


def validate(bets, cfg, split=0.5, stake=STAKE):
    """Judge a set of filters on data that did not choose them.

    Split the sample by date; the filters were picked by looking at results, so
    the only meaningful question is what they do on the half that wasn't
    consulted. A filter that helps in-sample and not out-of-sample is a
    description of the past, not a strategy.
    """
    if bets.empty:
        return None
    ordered = bets.sort_values("date")
    cut = ordered.date.quantile(split)
    halves = {"train": ordered[ordered.date <= cut], "test": ordered[ordered.date > cut]}

    result = {"cut": cut}
    for name, half in halves.items():
        base = half
        filtered = apply_filters(half, cfg)
        lo, hi = bootstrap_roi_ci_by_match(filtered, stake)
        result[name] = {
            "span": (half.date.min(), half.date.max()),
            "n_before": int(len(base)), "n_after": int(len(filtered)),
            "roi_before": float(base.pnl.sum() / (len(base) * stake) * 100) if len(base) else 0.0,
            "roi_after": float(filtered.pnl.sum() / (len(filtered) * stake) * 100) if len(filtered) else 0.0,
            "ci_after": (lo, hi),
        }
    result["gain_train"] = result["train"]["roi_after"] - result["train"]["roi_before"]
    result["gain_test"] = result["test"]["roi_after"] - result["test"]["roi_before"]
    # Held up = the filter did at least roughly as well out of sample as in it.
    result["held_up"] = result["gain_test"] > 0
    return result


def bootstrap_roi_ci(pnl, stake=STAKE, samples=BOOTSTRAP_SAMPLES, seed=42):
    """Percentile CI for ROI by resampling bets with replacement.

    This is the number that answers "edge, or luck?". If the interval includes
    zero the backtest has not demonstrated an edge, however good the point
    estimate looks.
    """
    pnl = np.asarray(pnl, dtype=float)
    if len(pnl) == 0:
        return 0.0, 0.0
    rng = np.random.default_rng(seed)
    rois = _bootstrap_chunked(
        len(pnl), samples, rng, lambda d: pnl[d].mean(axis=1) / stake * 100)
    return float(np.percentile(rois, 2.5)), float(np.percentile(rois, 97.5))


def summary(bets, stake=STAKE):
    """Headline numbers as a plain dict, for the CLI and the site alike."""
    n = len(bets)
    if not n:
        return {"n": 0, "roi": 0.0, "ci": (0.0, 0.0), "wins": 0, "win_rate": 0.0,
                "pnl": 0.0, "staked": 0.0, "avg_edge": 0.0, "verdict": "none"}
    pnl = float(bets.pnl.sum())
    staked = n * stake
    lo, hi = bootstrap_roi_ci(bets.pnl, stake)
    # Three distinct outcomes, not two. An interval entirely BELOW zero is a
    # reliably losing strategy — a stronger and more useful statement than
    # "nothing demonstrated", and reporting it as the latter is simply wrong.
    if lo > 0:
        verdict = "good" if n >= 200 else "warning"
    elif hi < 0:
        verdict = "losing"
    else:
        verdict = "critical"
    return {"n": n, "roi": pnl / staked * 100, "ci": (lo, hi),
            "wins": int(bets.won.sum()), "win_rate": float(bets.won.mean()),
            "pnl": pnl, "staked": staked, "avg_edge": float(bets.edge.mean()),
            "verdict": verdict}


def by_period(bets, periods=4, stake=STAKE):
    if bets.empty or len(bets) < periods * 5:
        return []
    ordered = bets.sort_values("date").reset_index(drop=True)
    out = []
    for i, pos in enumerate(np.array_split(np.arange(len(ordered)), periods), 1):
        chunk = ordered.iloc[pos]
        out.append({
            "label": f"Period {i}",
            "span": f"{chunk.date.min():%b %Y} - {chunk.date.max():%b %Y}",
            "n": int(len(chunk)),
            "roi": float(chunk.pnl.sum() / (len(chunk) * stake) * 100),
            "pnl": float(chunk.pnl.sum()),
        })
    return out


def by_market(bets, stake=STAKE):
    """Per-market breakdown. Markets have different sample sizes and different
    error profiles, so a pooled ROI can hide one carrying the other."""
    if bets.empty or "market" not in bets.columns:
        return []
    out = []
    for market, chunk in bets.groupby("market"):
        lo, hi = bootstrap_roi_ci(chunk.pnl, stake)
        out.append({"market": market, "n": int(len(chunk)),
                    "win_rate": float(chunk.won.mean()),
                    "avg_odds": float(chunk.odds.mean()),
                    "roi": float(chunk.pnl.sum() / (len(chunk) * stake) * 100),
                    "ci": (lo, hi)})
    return sorted(out, key=lambda r: -r["n"])


def sweep_bands(dataset, bands, cfg=None):
    """Measure ROI across odds bands, holding everything else fixed.

    The band is a free parameter that was originally guessed. This makes the
    cost of widening it measurable instead of arguable — and, importantly,
    it is a diagnostic, not a tuning loop: picking the band with the best
    backtest ROI would be fitting the parameter to the test set.
    """
    cfg = cfg or BacktestConfig()

    # ONE walk-forward, then subset by price. The band only decides which
    # selections become bets — it does not touch the model fit or the edge
    # arithmetic — so this is exactly equivalent to refitting per band, and
    # about five times faster. At 40 competitions the naive version dominated
    # the whole site build.
    widest = replace(cfg, odds_min=min(lo for lo, _ in bands),
                     odds_max=max(hi for _, hi in bands))
    all_bets = run(dataset, widest)

    rows = []
    for lo, hi in bands:
        subset = all_bets[(all_bets.odds >= lo) & (all_bets.odds <= hi)]
        rows.append({"band": f"{lo:.2f}-{hi:.2f}", "odds_min": lo, "odds_max": hi,
                     **summary(subset, cfg.stake)})
    return rows


def equity_curve(bets):
    if bets.empty:
        return []
    ordered = bets.sort_values("date").reset_index(drop=True)
    cum = ordered.pnl.cumsum()
    return [{"date": f"{r.date:%Y-%m-%d}", "match": r.match, "outcome": r.outcome,
             "odds": float(r.odds), "won": bool(r.won), "pnl": float(r.pnl),
             "cum": float(cum.iloc[i])}
            for i, r in ordered.iterrows()]
