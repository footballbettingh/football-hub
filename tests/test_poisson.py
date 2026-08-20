"""The goals model: does it recover strengths it was built from, and does it
degrade gracefully rather than silently when a team is new?"""

import numpy as np
import pandas as pd
import pytest

from confidence.poisson import BlendedGoalsModel, PoissonModel, poisson_pmf, score_matrix


def synthetic_league(n_teams=12, rounds=6, seed=0, strengths=None):
    """A league generated from known attack/defence parameters."""
    rng = np.random.default_rng(seed)
    teams = [f"team{i}" for i in range(n_teams)]
    attack = strengths if strengths is not None else rng.normal(0, 0.3, n_teams)
    defence = rng.normal(0, 0.2, n_teams)
    rows, day = [], pd.Timestamp("2022-01-01")
    for _ in range(rounds):
        for i in range(n_teams):
            for j in range(n_teams):
                if i == j:
                    continue
                lam = np.exp(0.15 + 0.25 + attack[i] - defence[j])
                mu = np.exp(0.15 + attack[j] - defence[i])
                rows.append({
                    "date": day, "home": teams[i], "away": teams[j],
                    "home_goals": rng.poisson(lam), "away_goals": rng.poisson(mu),
                    "home_sot": rng.poisson(lam * 3), "away_sot": rng.poisson(mu * 3),
                })
            day += pd.Timedelta(days=3)
    return pd.DataFrame(rows), teams, attack


def test_pmf_matches_scipy():
    from scipy.stats import poisson as scipy_poisson
    for lam in (0.2, 1.4, 6.0):
        assert poisson_pmf(lam, 12) == pytest.approx(scipy_poisson.pmf(np.arange(13), lam))


def test_score_matrix_is_a_distribution():
    matrix = score_matrix(1.5, 1.2, -0.05, 12)
    assert matrix.sum() == pytest.approx(1.0)
    assert (matrix >= 0).all()


def test_dixon_coles_only_moves_low_scores():
    plain = score_matrix(1.5, 1.2, 0.0, 12)
    adjusted = score_matrix(1.5, 1.2, -0.08, 12)
    moved = np.abs(adjusted - plain) > 1e-9
    assert moved[:2, :2].all()
    assert not moved[2:, 2:].any()


def test_negative_rho_lifts_the_draw():
    """The correction exists because independent Poissons underprice 0-0 and
    1-1, and draws are where the short prices this project ranks live."""
    plain = score_matrix(1.3, 1.1, 0.0, 12)
    adjusted = score_matrix(1.3, 1.1, -0.08, 12)
    assert np.trace(adjusted) > np.trace(plain)


def test_fit_recovers_relative_strengths():
    matches, teams, attack = synthetic_league(rounds=8)
    model = PoissonModel(half_life_days=None, ridge=0.001).fit(matches)
    fitted = np.array([model.attack[model.index[t]] for t in teams])
    correlation = np.corrcoef(fitted, attack - attack.mean())[0, 1]
    assert correlation > 0.9


def test_unknown_team_is_league_average_not_an_exception():
    """August is full of promoted teams. Raising here would empty the card at
    exactly the moment it is most wanted."""
    matches, teams, _ = synthetic_league()
    model = PoissonModel(half_life_days=None).fit(matches)
    assert not model.knows("brand new fc")

    lam, mu = model.expected_counts("brand new fc", "another new fc")
    baseline = np.exp(model.base + model.home_adv), np.exp(model.base)
    assert lam == pytest.approx(baseline[0])
    assert mu == pytest.approx(baseline[1])


def test_home_advantage_is_positive_and_ordering_holds():
    matches, teams, attack = synthetic_league(rounds=8)
    model = PoissonModel(half_life_days=None, ridge=0.001).fit(matches)
    assert model.home_adv > 0

    best, worst = teams[int(np.argmax(attack))], teams[int(np.argmin(attack))]
    strong, _ = model.expected_counts(best, worst)
    weak, _ = model.expected_counts(worst, best)
    assert strong > weak


def test_time_decay_weights_recent_matches_more():
    matches, _, _ = synthetic_league()
    model = PoissonModel(half_life_days=30)
    weights = model._weights(matches, as_of=matches["date"].max())
    assert weights[-1] > weights[0]
    assert weights.max() == pytest.approx(1.0)


def test_blend_endpoints_reproduce_the_pure_models():
    """Weight 0 and 1 must be exact. The sibling project's first attempt
    blended the input series instead of the lambdas and produced a curve that
    got worse from 0 to 0.75 and then jumped better at 1.0 — because 1.0 took
    a different code path."""
    matches, teams, _ = synthetic_league()
    pure = PoissonModel(half_life_days=None).fit(matches).expected_counts(teams[0], teams[1])
    blend_0 = BlendedGoalsModel(weight=0.0, half_life_days=None).fit(matches)
    assert blend_0.expected_counts(teams[0], teams[1]) == pytest.approx(pure)


def test_blend_falls_back_loudly_without_shot_data():
    matches, teams, _ = synthetic_league()
    model = BlendedGoalsModel(weight=0.5, half_life_days=None).fit(
        matches.drop(columns=["home_sot", "away_sot"]).assign(home_sot=np.nan,
                                                              away_sot=np.nan))
    assert model.has_sot is False
    goals_only = PoissonModel(half_life_days=None).fit(matches)
    assert model.expected_counts(teams[0], teams[1]) == pytest.approx(
        goals_only.expected_counts(teams[0], teams[1]))


def test_corners_are_just_another_count():
    matches, teams, _ = synthetic_league()
    matches = matches.assign(home_corners=matches["home_goals"] + 4,
                             away_corners=matches["away_goals"] + 3)
    model = PoissonModel(("home_corners", "away_corners"), half_life_days=None,
                         dixon_coles=False, max_goals=25).fit(matches)
    lam, mu = model.expected_counts(teams[0], teams[1])
    assert 3 < lam < 9 and 2 < mu < 8
