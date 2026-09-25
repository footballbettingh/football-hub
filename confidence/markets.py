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

from functools import lru_cache

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


# Every selection is the mass of one region of the score grid, so every
# selection for any number of matches is one matrix product: the grids
# flattened, against one column of ones and zeros per region. The functions
# below work on a stack of matches; the one-match versions under them are the
# same code on a stack of one, so the card and the calibration cannot come to
# disagree about what a key means.

@lru_cache(maxsize=None)
def _regions(n, lines, team_lines):
    """(names, weights): each region of an n x n grid, as a 0/1 column."""
    i, j = np.indices((n, n))
    regions = {}
    if team_lines is not None:            # the goals grid; corners are totals only
        regions.update({
            "home": i > j, "draw": i == j, "away": i < j,
            # BTTS: everything outside the first row and first column
            "btts_yes": (i > 0) & (j > 0), "btts_no": (i == 0) | (j == 0),
            "home_by2": i - j >= 2, "away_by2": i - j <= -2,
        })
        for line in team_lines:
            regions[f"home<={line:g}"] = i <= np.floor(line)
            regions[f"away<={line:g}"] = j <= np.floor(line)
    for line in lines:
        regions[f"total<={line:g}"] = i + j <= np.floor(line)
    names = list(regions)
    return names, np.stack([regions[k].ravel() for k in names], axis=1).astype(float)


def _masses(matrices, lines, team_lines=None):
    names, weights = _regions(matrices.shape[1], tuple(lines),
                              None if team_lines is None else tuple(team_lines))
    mass = matrices.reshape(len(matrices), -1) @ weights
    return {name: mass[:, k] for k, name in enumerate(names)}


def goal_probability_arrays(matrices):
    """Every goals-based selection for a stack of score grids, {key: array}."""
    m = _masses(matrices, TOTAL_LINES, TEAM_LINES)
    home, draw, away = m["home"], m["draw"], m["away"]
    out = {
        "1x2_home": home,
        "1x2_draw": draw,
        "1x2_away": away,

        "dc_1x": home + draw,
        "dc_12": home + away,
        "dc_x2": draw + away,

        "btts_yes": m["btts_yes"],
        "btts_no": m["btts_no"],

        # -1.5 wins by two clear goals; +1.5 survives anything short of it
        "hcp_home-1.5": m["home_by2"],
        "hcp_home+1.5": 1.0 - m["away_by2"],
        "hcp_away-1.5": m["away_by2"],
        "hcp_away+1.5": 1.0 - m["home_by2"],

        # Draw No Bet is void on a draw, so its probability is conditional
        "dnb_home": home / np.maximum(home + away, 1e-12),
        "dnb_away": away / np.maximum(home + away, 1e-12),
    }

    for line in TOTAL_LINES:
        under = m[f"total<={line:g}"]
        out[f"ou{line:g}_over"] = 1.0 - under
        out[f"ou{line:g}_under"] = under

    for side in ("home", "away"):
        for line in TEAM_LINES:
            under = m[f"{side}<={line:g}"]
            out[f"tt{line:g}_{side}_over"] = 1.0 - under
            out[f"tt{line:g}_{side}_under"] = under

    return out


def goal_result_arrays(home_goals, away_goals):
    """Settlement for every key, for arrays of finished scores: {key: int8
    array}, 1 won, 0 lost, -1 void."""
    home_goals = np.asarray(home_goals).astype(int)
    away_goals = np.asarray(away_goals).astype(int)
    total = home_goals + away_goals
    margin = home_goals - away_goals

    out = {
        "1x2_home": margin > 0,
        "1x2_draw": margin == 0,
        "1x2_away": margin < 0,

        "dc_1x": margin >= 0,
        "dc_12": margin != 0,
        "dc_x2": margin <= 0,

        "btts_yes": (home_goals > 0) & (away_goals > 0),
        "btts_no": (home_goals == 0) | (away_goals == 0),

        "hcp_home-1.5": margin >= 2,
        "hcp_home+1.5": margin >= -1,
        "hcp_away-1.5": margin <= -2,
        "hcp_away+1.5": margin <= 1,
    }

    for line in TOTAL_LINES:
        out[f"ou{line:g}_over"] = total > line
        out[f"ou{line:g}_under"] = total < line

    for side, goals in (("home", home_goals), ("away", away_goals)):
        for line in TEAM_LINES:
            out[f"tt{line:g}_{side}_over"] = goals > line
            out[f"tt{line:g}_{side}_under"] = goals < line

    out = {key: won.astype(np.int8) for key, won in out.items()}
    # Draw No Bet is returned on a draw rather than settled.
    out["dnb_home"] = np.where(margin == 0, -1, margin > 0).astype(np.int8)
    out["dnb_away"] = np.where(margin == 0, -1, margin < 0).astype(np.int8)
    return out


def corner_probability_arrays(matrices, lines=CORNER_LINES):
    """Total-corner selections for a stack of corner score grids."""
    m = _masses(matrices, lines)
    out = {}
    for line in lines:
        under = m[f"total<={line:g}"]
        out[f"corners{line:g}_over"] = 1.0 - under
        out[f"corners{line:g}_under"] = under
    return out


def corner_result_arrays(total_corners, lines=CORNER_LINES):
    total_corners = np.asarray(total_corners, dtype=float)
    out = {}
    for line in lines:
        out[f"corners{line:g}_over"] = (total_corners > line).astype(np.int8)
        out[f"corners{line:g}_under"] = (total_corners < line).astype(np.int8)
    return out


def _one(arrays):
    return {key: float(values[0]) for key, values in arrays.items()}


def _settled(arrays):
    return {key: None if values[0] < 0 else bool(values[0])
            for key, values in arrays.items()}


def goal_probabilities(matrix):
    """Every goals-based selection, as {key: probability}."""
    return _one(goal_probability_arrays(matrix[None]))


def goal_results(home_goals, away_goals):
    """Settlement for every key in `goal_probabilities`. None means void."""
    # int() first: a missing score raises here rather than settling as garbage.
    return _settled(goal_result_arrays([int(home_goals)], [int(away_goals)]))


def corner_probabilities(matrix, lines=CORNER_LINES):
    """Total-corner selections from a corners score matrix."""
    return _one(corner_probability_arrays(matrix[None], lines))


def corner_results(total_corners, lines=CORNER_LINES):
    if total_corners is None or total_corners != total_corners:
        return {}
    return _settled(corner_result_arrays([total_corners], lines))


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
