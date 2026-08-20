"""Betting markets, as pluggable objects.

The backtest used to hardcode home/draw/away. Adding Over/Under meant either
duplicating the loop or generalising it; this is the generalisation.

A market knows three things: which columns carry its prices, how to turn the
model into a probability for each of its selections, and how to settle them.
Everything else — the odds band, the edge threshold, walk-forward refitting,
staking — is market-agnostic and lives in backtest.py.

De-vigging is done WITHIN a market, over that market's own mutually exclusive
and exhaustive outcomes. 1X2 normalises over three, Over/Under over two. Mixing
them would be meaningless.

Column convention, so a new market needs no changes elsewhere:

    1X2         home_odds     draw_odds     away_odds      (+ _cons)
    O/U 2.5     over25_odds   under25_odds                 (+ _cons)
    O/U 1.5     over15_odds   under15_odds                 (+ _cons)
"""

from dataclasses import dataclass

import numpy as np


@dataclass
class Selection:
    """One bettable outcome, already priced and settled."""
    market: str
    name: str
    model_prob: float
    market_prob: float
    odds: float
    won: bool


def _devig(prices):
    """Implied probabilities with the bookmaker margin removed."""
    raw = np.array([1.0 / p for p in prices], dtype=float)
    return raw / raw.sum()


class Market:
    key = ""
    label = ""
    columns = ()          # price columns, in selection order

    def price_columns(self, consensus=False):
        return tuple(c + "_cons" for c in self.columns) if consensus else self.columns

    def available(self, row):
        return all(_present(row, c) for c in self.columns)

    def has_consensus(self, columns):
        return all(c + "_cons" in columns for c in self.columns)

    def model_probs(self, model, home, away):
        raise NotImplementedError

    def results(self, row):
        raise NotImplementedError

    def names(self):
        raise NotImplementedError

    def selections(self, model, row, home, away, use_devig=True, consensus=False,
                   settle=True):
        """Price every selection in this market.

        `settle=False` is for fixtures that haven't been played: same prices and
        probabilities, `won` left as None. Keeping one code path means the
        fixtures page and the backtest cannot drift apart in how they compute
        an edge — which is the whole point of showing them side by side.
        """
        prices = [float(row[c]) for c in self.columns]
        fair = [float(row[c]) for c in self.price_columns(consensus)]

        market_probs = _devig(fair) if use_devig else np.array([1.0 / p for p in fair])
        model_probs = self.model_probs(model, home, away)
        results = self.results(row) if settle else [None] * len(prices)

        return [Selection(self.label, name, float(mp), float(kp), price,
                          None if won is None else bool(won))
                for name, mp, kp, price, won
                in zip(self.names(), model_probs, market_probs, prices, results)]


def _present(row, column):
    if column not in row:
        return False
    value = row[column]
    return value is not None and value == value and float(value) > 1.0  # NaN-safe


class MatchResult(Market):
    """Home / Draw / Away — the 1X2 market."""
    key = "1x2"
    label = "1X2"
    columns = ("home_odds", "draw_odds", "away_odds")

    def names(self):
        return ("Home", "Draw", "Away")

    def model_probs(self, model, home, away):
        return np.array(model.predict_probabilities(home, away))

    def results(self, row):
        return (row.home_goals > row.away_goals,
                row.home_goals == row.away_goals,
                row.away_goals > row.home_goals)


class OverUnder(Market):
    """Total goals over/under a line.

    The line is part of the identity, so O/U 2.5 and O/U 1.5 are separate
    markets with separate prices, separate edges and separate results.
    """

    def __init__(self, line):
        self.line = float(line)
        tag = f"{self.line:g}".replace(".", "")
        self.key = f"ou{self.line:g}"
        self.label = f"O/U {self.line:g}"
        self.columns = (f"over{tag}_odds", f"under{tag}_odds")

    def names(self):
        return (f"Over {self.line:g}", f"Under {self.line:g}")

    def model_probs(self, model, home, away):
        over = model.predict_over_under(home, away, self.line)
        return np.array([over, 1.0 - over])

    def results(self, row):
        total = row.home_goals + row.away_goals
        return (total > self.line, total < self.line)  # .5 lines never push


# Registry. Order is display order.
ALL_MARKETS = [MatchResult(), OverUnder(2.5), OverUnder(1.5)]
BY_KEY = {m.key: m for m in ALL_MARKETS}
DEFAULT_MARKETS = ("1x2", "ou2.5")   # the two with historical closing odds


def resolve_markets(keys):
    """Turn CLI/config keys into market objects, failing loudly on typos."""
    out = []
    for key in keys:
        key = key.strip().lower()
        if key not in BY_KEY:
            raise SystemExit(f"Unknown market {key!r}. Known: {', '.join(BY_KEY)}")
        out.append(BY_KEY[key])
    return out
