"""Elo ratings (SPEC.md §29/§30).

Plain result-based Elo, computed sequentially over the match corpus so
every match's rating is strictly pre-match — walk-forward safe by
construction (§16). Ratings are pooled across divisions deliberately:
promoted/relegated teams carry their rating across the boundary, which is
exactly the cross-division information the per-division Dixon-Coles fits
give up.

Kept deliberately simple for the first audition: no seasonal regression —
that remains its own future §28 ablation rung, not a freebie. The
margin-of-victory multiplier (§32.1's standing candidate) is available
behind `margin_of_victory=True` and is off by default; plain result-based
Elo stays the incumbent until an ablation says otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

INIT_RATING = 1500.0
K_FACTOR = 20.0
HOME_ADVANTAGE = 60.0  # Elo points added to home rating in expectation only


@dataclass(frozen=True)
class EloResult:
    # matches frame with elo_home / elo_away pre-match columns appended
    matches: pd.DataFrame
    ratings: dict[str, float]  # final post-corpus ratings


MOV_AUTOCORRELATION = 2.2  # damping constant on the winner's rating edge


def mov_multiplier(margin: int, winner_edge: float) -> float:
    """§32.1 candidate: scale K by how emphatic the result was.

    ``ln(|margin| + 1) × 2.2 / (0.001 × winner_edge + 2.2)`` — the usual
    form. The first term makes a 4-0 move the rating further than a 1-0
    with diminishing returns, so one thrashing cannot dominate a season.
    The second is the autocorrelation correction: without it, strong teams
    gain disproportionately from beating weak ones, because they both win
    more often *and* win by more, and the rating would double-count that.

    Draws take a margin floor of 1. A literal zero margin would multiply
    the update by ln(1) = 0 and freeze every rating on every draw, which
    would silently discard about a quarter of all matches — the goalless
    draw between equals is genuine information that neither team is ahead.
    """
    damped = MOV_AUTOCORRELATION / (0.001 * winner_edge + MOV_AUTOCORRELATION)
    return float(np.log(max(abs(margin), 1) + 1.0) * damped)


def compute(
    matches: pd.DataFrame,
    k: float = K_FACTOR,
    home_advantage: float = HOME_ADVANTAGE,
    init: float = INIT_RATING,
    margin_of_victory: bool = False,
) -> EloResult:
    """Append pre-match elo_home/elo_away to a date-sorted match frame.

    margin_of_victory=True scales each update by `mov_multiplier`, so the
    size of a win counts and not only its direction (§32.1 ablation
    candidate, off by default).
    """
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
        effective_k = k
        if margin_of_victory:
            # The winner's edge is measured with home advantage applied,
            # matching the expectation the surprise is being judged against.
            edge = (r_h + home_advantage) - r_a
            if score_h < 0.5:
                edge = -edge
            effective_k = k * mov_multiplier(
                int(row.home_goals) - int(row.away_goals), edge
            )
        delta = effective_k * (score_h - expected_h)
        ratings[row.home_team] = r_h + delta
        ratings[row.away_team] = r_a - delta

    out = matches.copy()
    out["elo_home"] = elo_h_col
    out["elo_away"] = elo_a_col
    return EloResult(out, ratings)


def elo_diff(elo_home: float, elo_away: float) -> float:
    """§30: d_Elo = (Elo_home - Elo_away) / 400."""
    return (elo_home - elo_away) / 400.0
