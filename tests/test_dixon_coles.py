"""Fit-recovery tests on synthetic data — the fit must find home advantage
and the attack ordering that generated the data (§29-31)."""

import numpy as np
import pandas as pd
import pytest

from bym.model import dixon_coles

RNG = np.random.default_rng(42)
TEAMS = ["Strong", "Middling A", "Middling B", "Weak"]
ALPHA = {"Strong": 0.4, "Middling A": 0.0, "Middling B": 0.0, "Weak": -0.4}
BETA = {"Strong": -0.3, "Middling A": 0.0, "Middling B": 0.0, "Weak": 0.3}
MU, GAMMA = 0.15, 0.3


def synthetic_matches(n_rounds: int = 60) -> pd.DataFrame:
    rows = []
    date = pd.Timestamp("2024-08-01")
    for _ in range(n_rounds):
        for home in TEAMS:
            for away in TEAMS:
                if home == away:
                    continue
                lam_h = np.exp(MU + GAMMA + ALPHA[home] + BETA[away])
                lam_a = np.exp(MU + ALPHA[away] + BETA[home])
                rows.append(
                    {
                        "date": date,
                        "home_team": home,
                        "away_team": away,
                        "home_goals": RNG.poisson(lam_h),
                        "away_goals": RNG.poisson(lam_a),
                    }
                )
        date += pd.Timedelta(days=7)
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def fitted():
    return dixon_coles.fit(synthetic_matches(), xi=0.0)


def test_home_advantage_recovered(fitted):
    assert fitted.gamma == pytest.approx(GAMMA, abs=0.12)


def test_attack_ordering_recovered(fitted):
    assert fitted.alpha["Strong"] > fitted.alpha["Middling A"] > fitted.alpha["Weak"]


def test_defence_ordering_recovered(fitted):
    # β is defensive weakness: strong defence = lower β (§29.1)
    assert fitted.beta["Strong"] < fitted.beta["Middling A"] < fitted.beta["Weak"]


def test_identifiability_constraints(fitted):
    assert np.mean(list(fitted.alpha.values())) == pytest.approx(0.0, abs=1e-8)
    assert np.mean(list(fitted.beta.values())) == pytest.approx(0.0, abs=1e-8)


def test_lambdas_direction(fitted):
    strong_home, _ = fitted.lambdas("Strong", "Weak")
    weak_home, _ = fitted.lambdas("Weak", "Strong")
    assert strong_home > weak_home


def test_unseen_team_gets_league_average(fitted):
    lam_h, lam_a = fitted.lambdas("Newly Promoted", "Also Unknown")
    assert np.isfinite(lam_h) and np.isfinite(lam_a)
    assert lam_h > lam_a  # home advantage still applies


def test_recency_weighting_shifts_estimates():
    """With decay, a team that collapses mid-sample should rate worse under
    xi>0 than xi=0 (recent bad form weighted more)."""
    matches = synthetic_matches(30)
    # Make "Strong" lose heavily in the most recent 10 rounds
    recent = matches["date"] > matches["date"].max() - pd.Timedelta(days=70)
    is_strong_home = recent & (matches["home_team"] == "Strong")
    is_strong_away = recent & (matches["away_team"] == "Strong")
    matches.loc[is_strong_home, ["home_goals", "away_goals"]] = [0, 3]
    matches.loc[is_strong_away, ["home_goals", "away_goals"]] = [3, 0]

    flat = dixon_coles.fit(matches, xi=0.0)
    decayed = dixon_coles.fit(matches, xi=0.01)
    assert decayed.alpha["Strong"] < flat.alpha["Strong"]
