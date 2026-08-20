"""The walk-forward run: refit, predict the next round, never look ahead.

For every competition the loop walks its match dates in order, refits on
matches finishing STRICTLY earlier, and prices the round that follows. Nothing
downstream can accidentally see a result before it happened, because the fit
never received it.

What lands in predictions.csv is parameters, not probabilities:

    lam_model  mu_model  rho_model     what the goals model believed
    lam_market mu_market rho_market    what the closing line implies
    corner_lam corner_mu              the same estimator run on corners
    q_home q_draw q_away q_over       de-vigged closing probabilities

Everything the rest of the project reports is derived from those, so changing
the fusion weight or adding a market costs a few seconds rather than a rerun.
"""

import time

import numpy as np
import pandas as pd

from . import config
from .implied import devig, implied_lambdas
from .poisson import BlendedGoalsModel, PoissonModel
from .predict import CORNER_MAX

OUTPUT_COLUMNS = [
    "date", "competition", "season", "home", "away",
    "home_goals", "away_goals", "total_corners",
    "lam_model", "mu_model", "rho_model",
    "lam_market", "mu_market", "rho_market", "implied_resid",
    "corner_lam", "corner_mu",
    "new_team", "has_totals", "n_train",
    "q_home", "q_draw", "q_away", "q_over",
    "home_odds", "draw_odds", "away_odds", "over25_odds", "under25_odds",
]

_1X2_CONS = ("home_odds_cons", "draw_odds_cons", "away_odds_cons")
_1X2_BEST = ("home_odds", "draw_odds", "away_odds")
_OU_CONS = ("over25_odds_cons", "under25_odds_cons")
_OU_BEST = ("over25_odds", "under25_odds")


def _prices(row, columns):
    """The prices if all of them are present and sane, else None.

    A partial market cannot be de-vigged — normalising over two of three
    outcomes produces confident nonsense rather than a missing value.
    """
    out = []
    for column in columns:
        value = getattr(row, column, None)
        if value is None or value != value or float(value) <= 1.0:
            return None
        out.append(float(value))
    return out


def _market_view(row, method):
    """De-vigged closing probabilities for one match.

    Consensus (average) prices are what gets de-vigged; best prices are kept
    only for the payout side of the arithmetic. The sibling project measured a
    ~0.998 overround on best prices against ~1.065 on the average — shopping the
    top price already strips the margin, so de-vigging one does nothing while
    looking like it did something.
    """
    prices = _prices(row, _1X2_CONS) or _prices(row, _1X2_BEST)
    if prices is None:
        return None, None
    q_1x2 = devig(prices, method)

    totals = _prices(row, _OU_CONS) or _prices(row, _OU_BEST)
    q_over = float(devig(totals, method)[0]) if totals else None
    return q_1x2, q_over


def run(history, refit_days=None, min_train=None, devig_method=None,
        half_life_days=None, ridge=None, signal_weight=0.5,
        competitions=None, progress=print):
    """Price every match in `history` out of sample. Returns a DataFrame."""
    refit_days = config.REFIT_DAYS if refit_days is None else refit_days
    min_train = config.MIN_TRAIN_MATCHES if min_train is None else min_train
    devig_method = devig_method or config.DEVIG
    half_life_days = config.HALF_LIFE_DAYS if half_life_days is None else half_life_days
    ridge = config.RIDGE if ridge is None else ridge

    if competitions:
        history = history[history["competition"].isin(competitions)]

    rows = []
    started = time.time()
    names = sorted(history["competition"].unique())
    for position, competition in enumerate(names, start=1):
        sub = history[history["competition"] == competition].sort_values("date")
        rows.extend(_run_competition(sub, refit_days, min_train, devig_method,
                                     half_life_days, ridge, signal_weight))
        if progress:
            progress(f"  [{position:>2}/{len(names)}] {competition:<14} "
                     f"{len(sub):>5} matches   {len(rows):>6} priced   "
                     f"{time.time() - started:>5.0f}s")

    frame = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    return frame.sort_values(["date", "competition", "home"]).reset_index(drop=True)


def _run_competition(sub, refit_days, min_train, devig_method, half_life_days,
                     ridge, signal_weight):
    model = BlendedGoalsModel(weight=signal_weight, half_life_days=half_life_days,
                              ridge=ridge, max_goals=12)
    corners = PoissonModel(("home_corners", "away_corners"), dixon_coles=False,
                           half_life_days=half_life_days, ridge=ridge,
                           max_goals=CORNER_MAX)
    corners_fitted = False

    last_fit = None
    out = []
    for date, round_matches in sub.groupby("date", sort=True):
        train = sub[sub["date"] < date]
        if len(train) < min_train:
            continue

        if last_fit is None or (date - last_fit).days >= refit_days:
            model.fit(train, as_of=date)
            corner_train = train.dropna(subset=["home_corners", "away_corners"])
            if len(corner_train) >= min_train:
                corners.fit(corner_train, as_of=date)
                corners_fitted = True
            last_fit = date

        for row in round_matches.itertuples():
            out.append(_price_match(row, model, corners if corners_fitted else None,
                                    devig_method, len(train)))
    return out


def _price_match(row, model, corners, devig_method, n_train):
    lam_model, mu_model = model.expected_counts(row.home, row.away)
    rho_model = model.rho

    q_1x2, q_over = _market_view(row, devig_method)
    if q_1x2 is None:
        lam_market = mu_market = rho_market = resid = np.nan
        q_home = q_draw = q_away = np.nan
    else:
        q_home, q_draw, q_away = (float(x) for x in q_1x2)
        lam_market, mu_market, rho_market, resid = implied_lambdas(
            q_home, q_draw, q_away, q_over, rho=rho_model,
            init=(lam_model, mu_model))

    corner_lam = corner_mu = np.nan
    if corners is not None:
        corner_lam, corner_mu = corners.expected_counts(row.home, row.away)

    return (
        row.date, row.competition, row.season, row.home, row.away,
        row.home_goals, row.away_goals, getattr(row, "total_corners", np.nan),
        lam_model, mu_model, rho_model,
        lam_market, mu_market, rho_market, resid,
        corner_lam, corner_mu,
        not (model.knows(row.home) and model.knows(row.away)),
        q_over is not None,
        n_train,
        q_home, q_draw, q_away, np.nan if q_over is None else q_over,
        getattr(row, "home_odds", np.nan), getattr(row, "draw_odds", np.nan),
        getattr(row, "away_odds", np.nan),
        getattr(row, "over25_odds", np.nan), getattr(row, "under25_odds", np.nan),
    )
