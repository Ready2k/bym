"""Elo ratings (SPEC.md §29/§30).

Plain result-based Elo, computed sequentially over the match corpus so
every match's rating is strictly pre-match — walk-forward safe by
construction (§16). Ratings are pooled across divisions deliberately:
promoted/relegated teams carry their rating across the boundary, which is
exactly the cross-division information the per-division Dixon-Coles fits
give up.

Kept deliberately simple for the first audition: no margin-of-victory
multiplier, no seasonal regression — each of those is its own future §28
ablation rung, not a freebie.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

INIT_RATING = 1500.0
K_FACTOR = 20.0
HOME_ADVANTAGE = 60.0  # Elo points added to home rating in expectation only


@dataclass(frozen=True)
class EloResult:
    # matches frame with elo_home / elo_away pre-match columns appended
    matches: pd.DataFrame
    ratings: dict[str, float]  # final post-corpus ratings


def compute(
    matches: pd.DataFrame,
    k: float = K_FACTOR,
    home_advantage: float = HOME_ADVANTAGE,
    init: float = INIT_RATING,
) -> EloResult:
    """Append pre-match elo_home/elo_away to a date-sorted match frame."""
    matches = matches.sort_values("date").reset_index(drop=True)
    ratings: dict[str, float] = {}
    elo_h_col: list[float] = []
    elo_a_col: list[float] = []

    for row in matches.itertuples(index=False):
        r_h = ratings.get(row.home_team, init)
        r_a = ratings.get(row.away_team, init)
        elo_h_col.append(r_h)
        elo_a_col.append(r_a)

        expected_h = 1.0 / (1.0 + 10 ** (-((r_h + home_advantage) - r_a) / 400.0))
        if row.home_goals > row.away_goals:
            score_h = 1.0
        elif row.home_goals < row.away_goals:
            score_h = 0.0
        else:
            score_h = 0.5
        delta = k * (score_h - expected_h)
        ratings[row.home_team] = r_h + delta
        ratings[row.away_team] = r_a - delta

    out = matches.copy()
    out["elo_home"] = elo_h_col
    out["elo_away"] = elo_a_col
    return EloResult(out, ratings)


def elo_diff(elo_home: float, elo_away: float) -> float:
    """§30: d_Elo = (Elo_home - Elo_away) / 400."""
    return (elo_home - elo_away) / 400.0
