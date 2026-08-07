import numpy as np
import pandas as pd
import pytest

from super6.backtest.walk_forward import run_favourite_baseline, run_model

from test_dixon_coles import synthetic_matches

RNG = np.random.default_rng(7)


def two_division_matches() -> pd.DataFrame:
    """Two divisions with different scoring environments and disjoint teams."""
    top = synthetic_matches(40).copy()
    top["division"] = "E0"
    lower = synthetic_matches(40).copy()
    lower["division"] = "E1"
    for col in ("home_team", "away_team"):
        lower[col] = lower[col] + " (E1)"
    # lower division scores more heavily — a real μ difference to detect
    lower["home_goals"] = lower["home_goals"] + RNG.poisson(0.8, len(lower))
    lower["away_goals"] = lower["away_goals"] + RNG.poisson(0.8, len(lower))
    df = pd.concat([top, lower], ignore_index=True).sort_values("date")
    df["season"] = 2024
    return df.reset_index(drop=True)


@pytest.fixture(scope="module")
def matches():
    return two_division_matches()


@pytest.fixture(scope="module")
def test_mask(matches):
    cutoff = matches["date"].max() - pd.Timedelta(days=14)
    return matches["date"] > cutoff


def test_pooled_and_per_division_both_run(matches, test_mask):
    pooled = run_model(matches, test_mask, xi=0.0)
    split = run_model(matches, test_mask, xi=0.0, per_division=True)
    assert len(pooled.predictions) == len(split.predictions) == test_mask.sum()
    assert np.isfinite(pooled.metrics.log_loss)
    assert np.isfinite(split.metrics.log_loss)


def test_per_division_predictions_differ_from_pooled(matches, test_mask):
    """A pooled fit shares one μ/γ across divisions with different scoring
    rates, so per-division probabilities must genuinely differ."""
    pooled = run_model(matches, test_mask, xi=0.0).predictions
    split = run_model(matches, test_mask, xi=0.0, per_division=True).predictions
    merged = pooled.merge(
        split, on=["date", "home_team", "away_team"], suffixes=("_pool", "_div")
    )
    assert (merged["p_h_pool"] - merged["p_h_div"]).abs().max() > 1e-4


def test_no_future_leakage_guard(matches):
    """First fixtures of the corpus have <200 training matches before them —
    the walk-forward must skip them, never fit on future data."""
    early_mask = matches["date"] <= matches["date"].min()
    result = run_model(matches, early_mask, xi=0.0)
    assert len(result.predictions) == 0


def test_market_baselines(matches, test_mask):
    m = matches.copy()
    # synthetic odds: available and closing disagree on the favourite
    m["odds_h"], m["odds_d"], m["odds_a"] = 1.8, 3.5, 4.0
    m["odds_ch"], m["odds_cd"], m["odds_ca"] = 4.0, 3.5, 1.8
    available = run_favourite_baseline(m, test_mask, closing=False)
    oracle = run_favourite_baseline(m, test_mask, closing=True)
    assert (available.predictions["pred_home"] > available.predictions["pred_away"]).all()
    assert (oracle.predictions["pred_home"] < oracle.predictions["pred_away"]).all()
