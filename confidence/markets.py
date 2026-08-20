"""Every bettable selection, derived from one joint score distribution.

The point of going through a matrix rather than a formula per market is that
the numbers cannot disagree with each other. P(BTTS yes) and P(home wins 1-0)
come from the same 13x13 grid, so the card is internally consistent by
construction — no chance of recommending Over 3.5 and Under 2.5 on the same
fixture because two different approximations were used.

Keys are stable strings (`ou2.5_over`, `btts_yes`) and are the join between the
probability side and the settlement side. `tests/test_markets.py` asserts the
two sides expose exactly the same key set, because a typo there would silently
grade a bet against nothing.

`None` as a result means the bet is void, not lost: Draw No Bet on a draw is
returned rather than settled, and the evaluator must drop it instead of
counting it as a loss.
"""

import numpy as np

TOTAL_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)
TEAM_LINES = (0.5, 1.5, 2.5)
CORNER_LINES = (7.5, 8.5, 9.5, 10.5, 11.5)

# market group -> pretty name, used for grouping in reports and for fitting one
# calibrator per group rather than one per selection.
GROUPS = {
    "1x2": "1X2",
    "dc": "Double chance",
    "btts": "Both teams to score",
    "ou": "Total goals",
    "tt": "Team goals",
    "hcp": "Handicap",
    "dnb": "Draw no bet",
    "corners": "Corners",
}


# Longest prefix first, so "corners" is never read as "c" and "1x2" survives
# having a digit on the end (stripping digits would leave "1x").
_PREFIXES = sorted(GROUPS, key=len, reverse=True)


def group_of(key: str) -> str:
    for prefix in _PREFIXES:
        if key.startswith(prefix):
            return prefix
    raise KeyError(f"selection key {key!r} belongs to no known market group")


def _totals_distribution(matrix):
    """P(total goals = n) for n = 0 .. 2 * max_goals."""
    n = matrix.shape[0]
    totals = np.zeros(2 * n - 1)
    for i in range(n):
        totals[i:i + n] += matrix[i]
    return totals


def goal_probabilities(matrix):
    """Every goals-based selection, as {key: probability}."""
    n = matrix.shape[0]
    p_home = float(np.tril(matrix, -1).sum())
    p_draw = float(np.trace(matrix))
    p_away = float(np.triu(matrix, 1).sum())

    home_marginal = matrix.sum(axis=1)
    away_marginal = matrix.sum(axis=0)
    totals = _totals_distribution(matrix)
    cum_totals = np.cumsum(totals)

    diff = np.subtract.outer(np.arange(n), np.arange(n))
    p_home_by2 = float(matrix[diff >= 2].sum())
    p_away_by2 = float(matrix[diff <= -2].sum())

    out = {
        "1x2_home": p_home,
        "1x2_draw": p_draw,
        "1x2_away": p_away,

        "dc_1x": p_home + p_draw,
        "dc_12": p_home + p_away,
        "dc_x2": p_draw + p_away,

        # BTTS: everything outside the first row and first column
        "btts_yes": float(matrix[1:, 1:].sum()),
        "btts_no": float(matrix[0, :].sum() + matrix[:, 0].sum() - matrix[0, 0]),

        # -1.5 wins by two clear goals; +1.5 survives anything short of it
        "hcp_home-1.5": p_home_by2,
        "hcp_home+1.5": 1.0 - p_away_by2,
        "hcp_away-1.5": p_away_by2,
        "hcp_away+1.5": 1.0 - p_home_by2,

        # Draw No Bet is void on a draw, so its probability is conditional
        "dnb_home": p_home / max(p_home + p_away, 1e-12),
        "dnb_away": p_away / max(p_home + p_away, 1e-12),
    }

    for line in TOTAL_LINES:
        under = float(cum_totals[int(np.floor(line))])
        out[f"ou{line:g}_over"] = 1.0 - under
        out[f"ou{line:g}_under"] = under

    for side, marginal in (("home", home_marginal), ("away", away_marginal)):
        cum = np.cumsum(marginal)
        for line in TEAM_LINES:
            under = float(cum[int(np.floor(line))])
            out[f"tt{line:g}_{side}_over"] = 1.0 - under
            out[f"tt{line:g}_{side}_under"] = under

    return out


def goal_results(home_goals, away_goals):
    """Settlement for every key in `goal_probabilities`. None means void."""
    home_goals, away_goals = int(home_goals), int(away_goals)
    total = home_goals + away_goals
    margin = home_goals - away_goals

    out = {
        "1x2_home": margin > 0,
        "1x2_draw": margin == 0,
        "1x2_away": margin < 0,

        "dc_1x": margin >= 0,
        "dc_12": margin != 0,
        "dc_x2": margin <= 0,

        "btts_yes": home_goals > 0 and away_goals > 0,
        "btts_no": home_goals == 0 or away_goals == 0,

        "hcp_home-1.5": margin >= 2,
        "hcp_home+1.5": margin >= -1,
        "hcp_away-1.5": margin <= -2,
        "hcp_away+1.5": margin <= 1,

        "dnb_home": None if margin == 0 else margin > 0,
        "dnb_away": None if margin == 0 else margin < 0,
    }

    for line in TOTAL_LINES:
        out[f"ou{line:g}_over"] = total > line
        out[f"ou{line:g}_under"] = total < line

    for side, goals in (("home", home_goals), ("away", away_goals)):
        for line in TEAM_LINES:
            out[f"tt{line:g}_{side}_over"] = goals > line
            out[f"tt{line:g}_{side}_under"] = goals < line

    return out


def corner_probabilities(matrix, lines=CORNER_LINES):
    """Total-corner selections from a corners score matrix."""
    cum = np.cumsum(_totals_distribution(matrix))
    out = {}
    for line in lines:
        under = float(cum[int(np.floor(line))])
        out[f"corners{line:g}_over"] = 1.0 - under
        out[f"corners{line:g}_under"] = under
    return out


def corner_results(total_corners, lines=CORNER_LINES):
    if total_corners is None or total_corners != total_corners:
        return {}
    out = {}
    for line in lines:
        out[f"corners{line:g}_over"] = total_corners > line
        out[f"corners{line:g}_under"] = total_corners < line
    return out


# -- presentation ---------------------------------------------------------

def label(key: str) -> str:
    """Human-readable name for a selection key."""
    fixed = {
        "1x2_home": "Home win", "1x2_draw": "Draw", "1x2_away": "Away win",
        "dc_1x": "Home or draw (1X)", "dc_12": "Home or away (12)",
        "dc_x2": "Draw or away (X2)",
        "btts_yes": "Both teams to score", "btts_no": "Not both teams to score",
        "hcp_home-1.5": "Home -1.5", "hcp_home+1.5": "Home +1.5",
        "hcp_away-1.5": "Away -1.5", "hcp_away+1.5": "Away +1.5",
        "dnb_home": "Home draw-no-bet", "dnb_away": "Away draw-no-bet",
    }
    if key in fixed:
        return fixed[key]
    if key.startswith("ou"):
        line, side = key[2:].split("_")
        return f"{side.capitalize()} {line} goals"
    if key.startswith("tt"):
        rest, side, direction = key[2:].split("_")
        return f"{side.capitalize()} team {direction} {rest} goals"
    if key.startswith("corners"):
        line, side = key[len("corners"):].split("_")
        return f"{side.capitalize()} {line} corners"
    return key


ALL_KEYS = sorted(set(goal_results(0, 0)) | set(corner_results(0)))
