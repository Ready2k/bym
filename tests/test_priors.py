import numpy as np
import pandas as pd
import pytest

from bym.model import dixon_coles, priors

from test_dixon_coles import synthetic_matches


def two_season_two_division() -> pd.DataFrame:
    """2023: A/B/C/D in E0, W/X/Y/Z in E1. 2024: D relegated to E1,
    W promoted to E0; N enters E1 from outside the corpus."""

    def season(year, div, teams, start):
        rows = []
        date = pd.Timestamp(start)
        for _ in range(6):
            for h in teams:
                for a in teams:
                    if h != a:
                        rows.append(
                            {
                                "date": date,
                                "season": year,
                                "division": div,
                                "home_team": h,
                                "away_team": a,
                                "home_goals": 1,
                                "away_goals": 1,
                            }
                        )
            date += pd.Timedelta(days=7)
        return rows

    return pd.DataFrame(
        season(2023, "E0", ["A", "B", "C", "D"], "2023-08-01")
        + season(2023, "E1", ["W", "X", "Y", "Z"], "2023-08-01")
        + season(2024, "E0", ["A", "B", "C", "W"], "2024-08-01")
        + season(2024, "E1", ["D", "X", "Y", "N"], "2024-08-01")
    )


def test_arrival_directions():
    m = two_season_two_division()
    e0 = priors.arrivals(m, "E0", 2024)
    e1 = priors.arrivals(m, "E1", 2024)
    assert e0 == {"W": "below"}  # promoted into the top flight
    assert e1["D"] == "above"  # relegated in from E0
    assert e1["N"] == "below"  # absent from corpus = promoted from below


def test_prior_pulls_data_poor_team():
    """A team with almost no matches must sit near the prior mean; the
    same team with plenty of data must escape it (§29.4 continuous
    shrinkage)."""
    m = synthetic_matches(30)
    # one new team with a single match
    newcomer = pd.DataFrame(
        [
            {
                "date": m["date"].max() + pd.Timedelta(days=7),
                "home_team": "Newbie",
                "away_team": "Strong",
                "home_goals": 1,
                "away_goals": 1,
            }
        ]
    )
    m2 = pd.concat([m, newcomer], ignore_index=True)
    spec = priors.PriorSpec(-0.35, 0.02, 0.3, 0.02)
    fitted = dixon_coles.fit(m2, xi=0.0, param_priors={"Newbie": spec})
    assert fitted.alpha["Newbie"] == pytest.approx(-0.35, abs=0.12)
    assert fitted.beta["Newbie"] == pytest.approx(0.3, abs=0.12)

    # abundant data overrides the prior: pin "Strong" to a wrong prior
    fitted2 = dixon_coles.fit(
        m, xi=0.0, param_priors={"Strong": priors.PriorSpec(-0.5, 0.02, 0.5, 0.02)}
    )
    unpinned = dixon_coles.fit(m, xi=0.0)
    assert fitted2.alpha["Strong"] > 0.15  # still clearly strong
    assert abs(fitted2.alpha["Strong"] - unpinned.alpha["Strong"]) < 0.15


def test_estimate_returns_specs():
    m = two_season_two_division()
    est = priors.estimate(m, min_cell=1, min_matches=50)
    spec = est.lookup("E1", "above")
    assert spec is not None
    assert np.isfinite(spec.mean_alpha)
    assert spec.var_alpha >= priors.VAR_FLOOR


def test_season_decay_downweights_previous_season():
    """With heavy season decay, a team that was strong last season but
    average this season should rate closer to average than without decay."""
    m = synthetic_matches(30).copy()
    m["season"] = 2023
    # this season: Strong plays like everyone else (all draws)
    recent = synthetic_matches(8).copy()
    recent["date"] = recent["date"] + pd.Timedelta(days=400)
    recent["season"] = 2024
    recent["home_goals"] = 1
    recent["away_goals"] = 1
    combined = pd.concat([m, recent], ignore_index=True)

    flat = dixon_coles.fit(combined, xi=0.0)
    decayed = dixon_coles.fit(
        combined, xi=0.0, season_decay=0.3, reference_season=2024
    )
    assert abs(decayed.alpha["Strong"]) < abs(flat.alpha["Strong"])


def test_season_decay_requires_metadata():
    m = synthetic_matches(5)  # no season column
    with pytest.raises(ValueError, match="season"):
        dixon_coles.fit(m, xi=0.0, season_decay=0.8, reference_season=2024)
