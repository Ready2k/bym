"""§7's zero-cost xG stand-in, from shot counts already in the corpus."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bym.model import shots


def corpus(n=400, seed=0):
    rng = np.random.default_rng(seed)
    on_target = rng.poisson(4.5, n)
    off_target = rng.poisson(7.0, n)
    goals = rng.poisson(np.clip(0.32 * on_target, 0.05, None))
    return pd.DataFrame({
        "date": pd.date_range("2020-08-01", periods=n, freq="D"),
        "home_team": ["A"] * n, "away_team": ["B"] * n,
        "home_goals": goals, "away_goals": rng.poisson(1.2, n),
        "home_shots": on_target + off_target, "home_shots_target": on_target,
        "away_shots": rng.poisson(11, n), "away_shots_target": rng.poisson(4, n),
    })


def test_the_conversion_recovers_the_real_scoring_rate():
    """Roughly one shot on target in three becomes a goal — if the fit says
    otherwise, it is not measuring what it claims to."""
    frame = corpus()
    conversion = shots.fit_conversion(frame)
    assert conversion is not None
    predicted = conversion.expected_goals(
        frame["home_shots_target"].to_numpy(float),
        (frame["home_shots"] - frame["home_shots_target"]).to_numpy(float),
    )
    assert predicted.mean() == pytest.approx(frame["home_goals"].mean(), rel=0.05)
    assert conversion.on_target > 0  # more shots on target, more goals


def test_the_proxy_is_smoother_than_the_goals_it_replaces():
    """The entire thesis: goals are a noisy realisation, so a smoother
    target should estimate underlying strength better."""
    frame = corpus()
    proxy = shots.with_proxy_goals(frame, shots.fit_conversion(frame))
    assert proxy["home_goals"].var() < frame["home_goals"].var()
    assert proxy["home_goals"].mean() == pytest.approx(
        frame["home_goals"].mean(), rel=0.05
    )


def test_proxy_goals_are_continuous_which_is_why_rho_must_be_fixed():
    """`_tau_log` identifies its {0,1}² cells by exact equality. A proxy
    value is never exactly 0 or 1, so ρ would be unidentifiable and drift
    silently — the ablation arm fixes it to zero and says so."""
    frame = corpus()
    proxy = shots.with_proxy_goals(frame, shots.fit_conversion(frame))
    assert not (proxy["home_goals"] % 1 == 0).any()


def test_everything_else_in_the_frame_survives_the_substitution():
    """The proxy frame goes straight to dixon_coles.fit, so it has to be
    the same frame with a different target — not a reconstruction."""
    frame = corpus()
    proxy = shots.with_proxy_goals(frame, shots.fit_conversion(frame))
    assert list(proxy.columns) == list(frame.columns)
    for column in ("date", "home_team", "away_team", "home_shots"):
        assert proxy[column].equals(frame[column])


def test_a_corpus_without_shot_columns_yields_no_proxy_rather_than_a_guess():
    frame = corpus().drop(columns=["home_shots_target"])
    assert shots.fit_conversion(frame) is None


def test_too_little_history_yields_no_proxy():
    assert shots.fit_conversion(corpus(n=40)) is None


def test_the_real_corpus_supports_the_proxy_for_both_divisions():
    """Championship coverage is the point: every free xG source either
    omits it or lost its feed, and this is the only signal that spans the
    whole corpus."""
    from pathlib import Path

    from bym.data.football_data import load_matches

    for division in ("E0", "E1"):
        matches = load_matches(Path("data"), [2023], (division,), fetch=False)
        for column in shots.REQUIRED_COLUMNS:
            assert matches[column].notna().mean() > 0.95, (division, column)
        conversion = shots.fit_conversion(matches)
        assert conversion is not None
        # ~0.3 goals per shot on target is the well-known rate; a fit far
        # from it would mean the columns are not what they claim to be.
        assert 0.1 < np.exp(conversion.intercept + conversion.on_target) < 1.5
