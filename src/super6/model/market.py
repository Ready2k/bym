"""Market calibration (SPEC.md §32).

§32.1 margin removal: simple proportional normalisation of 1/odds.
(Shin's method is a future ablation candidate, not an assumed default.)

§32.3 Option B — result-constrained reweighting: a 1X2 market prices
H/D/A only, saying nothing about how a result's probability splits across
scorelines. So blending happens at the result level, then each scoreline
is rescaled within its result by q_R / p_DC(R), preserving Dixon-Coles'
relative shape within each result:

    q(h,a) = p_DC(h,a) × [ q_R / p_DC(R) ]

This guarantees Σ q(h,a) = 1 and never pretends H/D/A prices contain
exact-score information they don't have. (Option A — market shifts λ —
remains unimplemented until an ablation asks for it.)
"""

from __future__ import annotations

import numpy as np

from .matrix import ScoreMatrix


def implied_result_probs(
    odds_h: float, odds_d: float, odds_a: float
) -> tuple[float, float, float]:
    """§32.1: margin-adjusted implied probabilities, proportional method."""
    inv = np.array([1.0 / odds_h, 1.0 / odds_d, 1.0 / odds_a])
    q = inv / inv.sum()
    return float(q[0]), float(q[1]), float(q[2])


def blend_matrix(
    matrix: ScoreMatrix,
    market_h: float,
    market_d: float,
    market_a: float,
    weight: float,
) -> ScoreMatrix:
    """Blend model and market result probabilities with the given market
    weight, then apply §32.3's result-constrained reweighting.

    weight=0 → pure model; weight=1 → market result probabilities with the
    model's within-result score shape.
    """
    if not 0.0 <= weight <= 1.0:
        raise ValueError(f"blend weight must be in [0,1], got {weight}")
    q_h = weight * market_h + (1.0 - weight) * matrix.p_home_win
    q_d = weight * market_d + (1.0 - weight) * matrix.p_draw
    q_a = weight * market_a + (1.0 - weight) * matrix.p_away_win

    g = matrix.max_goals + 1
    h = np.arange(g)[:, None]
    a = np.arange(g)[None, :]
    p_res = np.where(
        h > a, matrix.p_home_win, np.where(h < a, matrix.p_away_win, matrix.p_draw)
    )
    q_res = np.where(h > a, q_h, np.where(h < a, q_a, q_d))
    scale = q_res / np.clip(p_res, 1e-12, None)
    probs = matrix.probs * scale
    return ScoreMatrix(
        probs / probs.sum(),  # exact up to float error; renormalise anyway
        matrix.lambda_home,
        matrix.lambda_away,
        matrix.rho,
    )
