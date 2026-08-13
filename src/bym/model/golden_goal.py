"""Golden Goal model (SPEC.md §15) — deliberately simple (Priority 3, §1.1).

Phase 1A model: each match's goals arrive as a Poisson process at constant
rate (λ_home + λ_away)/90 per minute; the six fixtures superpose into one
process, so the first goal anywhere is exponential with the summed rate.

Sky minute convention (§15, verified): minute N covers (N-1):00-(N-1):59.
The recommendation minimises expected absolute band distance to the true
first-goal minute — the weighted median band — not the modal band, because
the tiebreaker is closest-minute distance.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MATCH_MINUTES = 90


@dataclass(frozen=True)
class GoldenGoalForecast:
    band_probs: np.ndarray  # P(first goal in Sky minute n), index 0 = minute 1
    p_no_goal: float
    recommended_minute: int

    def intervals(self, edges: tuple[int, ...] = (5, 10, 15, 20)) -> dict[str, float]:
        """§15-style probability intervals, e.g. '1-5 mins: 19%'."""
        out: dict[str, float] = {}
        lo = 1
        for hi in edges:
            out[f"{lo}-{hi}"] = float(self.band_probs[lo - 1 : hi].sum())
            lo = hi + 1
        out[f"{lo}+"] = float(self.band_probs[lo - 1 :].sum())
        return out


def first_goal_distribution(total_lambdas: list[float]) -> tuple[np.ndarray, float]:
    """P(first goal anywhere falls in Sky minute n) for n = 1..90, plus
    P(no goal in any fixture). total_lambdas = λ_home + λ_away per fixture."""
    rate_per_min = float(sum(total_lambdas)) / MATCH_MINUTES
    n = np.arange(1, MATCH_MINUTES + 1, dtype=float)
    band_probs = np.exp(-rate_per_min * (n - 1)) - np.exp(-rate_per_min * n)
    p_no_goal = float(np.exp(-rate_per_min * MATCH_MINUTES))
    return band_probs, p_no_goal


def recommend_minute(band_probs: np.ndarray) -> int:
    """Band minimising expected |guess - actual| conditional on a goal —
    the weighted median of the band distribution (§15)."""
    total = band_probs.sum()
    if total <= 0:
        return 1
    cdf = np.cumsum(band_probs) / total
    return int(np.searchsorted(cdf, 0.5) + 1)


def forecast(total_lambdas: list[float]) -> GoldenGoalForecast:
    band_probs, p_no_goal = first_goal_distribution(total_lambdas)
    return GoldenGoalForecast(
        band_probs=band_probs,
        p_no_goal=p_no_goal,
        recommended_minute=recommend_minute(band_probs),
    )


def sky_interval(minute: int) -> str:
    """Sky minute N = clock (N-1):00-(N-1):59 (§15). Always display both."""
    return f"{minute - 1}:00-{minute - 1}:59"
