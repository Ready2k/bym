"""A zero-cost stand-in for xG, from shot counts already in the corpus (§7).

Real expected goals needs shot locations and chance quality, and every
source that has them is either paywalled above §25's £10/month or does
not cover the Championship — which is 59% of this model's corpus. See
docs/next_steps.md item 4 for the survey.

What the free corpus *does* carry, at 100% coverage for both divisions
and every season, is shots and shots on target. That supports a much
cruder statement — how many goals a performance of this shape usually
produces — and §28 does not require a candidate to be the best possible
version of itself before being tested. If a crude proxy adds nothing,
that is useful evidence about whether the real thing is worth paying for.

The thesis being tested is the one that motivates xG generally: goals are
a noisy realisation of a team's underlying performance, so fitting team
strength on a *less noisy* target should estimate strength better. On
this corpus the proxy has variance 0.64 against actual goals' 1.46, so it
is indeed smoother; whether smoother is better is the ablation's job.

Deliberately not a shot-quality model. `shots on target` and `shots off
target` enter as plain counts, because that is all the data distinguishes
— pretending to more resolution than the source has would be the kind of
false precision §22 exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize

REQUIRED_COLUMNS = ("home_shots", "away_shots", "home_shots_target", "away_shots_target")


@dataclass(frozen=True)
class ShotConversion:
    """Poisson coefficients turning a shot profile into expected goals."""

    intercept: float
    on_target: float
    off_target: float

    def expected_goals(self, on_target: np.ndarray, off_target: np.ndarray) -> np.ndarray:
        return np.exp(
            self.intercept + self.on_target * on_target + self.off_target * off_target
        )


def _sides(matches: pd.DataFrame) -> pd.DataFrame:
    """One row per team-performance, so both sides train the conversion."""
    home = pd.DataFrame({
        "goals": matches["home_goals"].to_numpy(dtype=float),
        "on_target": matches["home_shots_target"].to_numpy(dtype=float),
        "off_target": (matches["home_shots"] - matches["home_shots_target"]).to_numpy(dtype=float),
    })
    away = pd.DataFrame({
        "goals": matches["away_goals"].to_numpy(dtype=float),
        "on_target": matches["away_shots_target"].to_numpy(dtype=float),
        "off_target": (matches["away_shots"] - matches["away_shots_target"]).to_numpy(dtype=float),
    })
    return pd.concat([home, away], ignore_index=True).dropna()


def fit_conversion(matches: pd.DataFrame) -> ShotConversion | None:
    """Fit goals ~ Poisson(shots on/off target) on the supplied matches.

    Must be given training data only: the caller is responsible for the
    walk-forward split, exactly as it is for the goal model itself.
    Returns None when the frame lacks shot columns or has too few rows to
    fit on — the caller then simply has no proxy, rather than a fitted
    number nobody could defend.
    """
    if any(column not in matches.columns for column in REQUIRED_COLUMNS):
        return None
    sides = _sides(matches)
    if len(sides) < 200:
        return None
    design = np.column_stack(
        [np.ones(len(sides)), sides["on_target"], sides["off_target"]]
    )
    goals = sides["goals"].to_numpy(dtype=float)

    def negative_log_likelihood(beta: np.ndarray) -> float:
        linear = design @ beta
        return -float(np.sum(goals * linear - np.exp(linear)))

    result = minimize(negative_log_likelihood, np.zeros(3), method="BFGS")
    if not np.all(np.isfinite(result.x)):
        return None
    return ShotConversion(float(result.x[0]), float(result.x[1]), float(result.x[2]))


def with_proxy_goals(
    matches: pd.DataFrame, conversion: ShotConversion
) -> pd.DataFrame:
    """Return a copy whose goal columns hold shot-derived expected goals.

    The frame is otherwise untouched, so it can be handed straight to
    `dixon_coles.fit` — the proxy arm is the same model family fitted on a
    different target, which is what makes the comparison a clean one.

    Note for the caller: those goals are continuous, and `_tau_log`
    identifies its {0,1}² cells by exact equality. A proxy value is never
    exactly 0 or 1, so ρ would be unidentifiable and silently drift.
    Fit the proxy arm with `fix_rho_zero=True` and say so, rather than
    letting the Dixon-Coles correction quietly switch itself off.
    """
    out = matches.copy()
    out["home_goals"] = conversion.expected_goals(
        matches["home_shots_target"].to_numpy(dtype=float),
        (matches["home_shots"] - matches["home_shots_target"]).to_numpy(dtype=float),
    )
    out["away_goals"] = conversion.expected_goals(
        matches["away_shots_target"].to_numpy(dtype=float),
        (matches["away_shots"] - matches["away_shots_target"]).to_numpy(dtype=float),
    )
    return out
