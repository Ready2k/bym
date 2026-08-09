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


SHIN_MAX_ITERATIONS = 100
SHIN_TOLERANCE = 1e-10


def shin_z(odds_h: float, odds_d: float, odds_a: float) -> float:
    """§32.1 ablation candidate: Shin's estimated insider-trading share.

    Shin (1993) models the bookmaker's margin as protection against traders
    with private information, rather than as a flat proportional loading.
    The recovered probabilities are

        p_i = [ sqrt(z² + 4(1-z)·π_i²/Σπ) - z ] / (2(1-z))

    with π_i = 1/odds_i and z the insider share that makes Σp_i = 1.
    Solved by fixed-point iteration, which converges monotonically for the
    two-to-few outcome case this is applied to.

    Why it might beat proportional normalisation: the proportional method
    removes margin evenly, which systematically overstates longshots and
    understates favourites (the favourite-longshot bias). Shin's removes
    more margin from the longshot, which is where the bookmaker's
    insider exposure actually sits. Whether that helps *here* is what the
    ablation is for — §28, not assumed.

    z = 0 recovers the proportional method exactly.
    """
    inv = np.array([1.0 / odds_h, 1.0 / odds_d, 1.0 / odds_a], dtype=float)
    booksum = float(inv.sum())
    if booksum <= 1.0:  # no margin (or a crossed book) — nothing to remove
        return 0.0
    z = 0.0
    for _ in range(SHIN_MAX_ITERATIONS):
        # Fixed point of Σp_i(z) = 1. For n outcomes the rearrangement is
        # z' = (Σ√(...) - 2) / (n - 2); n is always 3 here (H/D/A).
        root = np.sqrt(z * z + 4.0 * (1.0 - z) * inv * inv / booksum)
        z_next = min(max(float(root.sum()) - 2.0, 0.0), 0.5)
        if abs(z_next - z) < SHIN_TOLERANCE:
            return z_next
        z = z_next
    return z


def implied_result_probs_shin(
    odds_h: float, odds_d: float, odds_a: float
) -> tuple[float, float, float]:
    """§32.1 ablation candidate: Shin-devigged implied probabilities.

    Falls back to the proportional method when the solved z leaves nothing
    to do (z=0) or the book has no margin, so this is never worse-defined
    than the incumbent.
    """
    z = shin_z(odds_h, odds_d, odds_a)
    if z <= 0.0:
        return implied_result_probs(odds_h, odds_d, odds_a)
    inv = np.array([1.0 / odds_h, 1.0 / odds_d, 1.0 / odds_a], dtype=float)
    booksum = float(inv.sum())
    root = np.sqrt(z * z + 4.0 * (1.0 - z) * inv * inv / booksum)
    p = (root - z) / (2.0 * (1.0 - z))
    p = p / p.sum()  # guard against float drift; the solve already targets 1
    return float(p[0]), float(p[1]), float(p[2])


DEVIG_METHODS = {
    "proportional": implied_result_probs,
    "shin": implied_result_probs_shin,
}


def devig(method: str):
    """Look up a margin-removal method by name, for ablation arms."""
    try:
        return DEVIG_METHODS[method]
    except KeyError:
        raise ValueError(
            f"unknown devig method {method!r}; expected one of {sorted(DEVIG_METHODS)}"
        ) from None


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
