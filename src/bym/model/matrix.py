"""Score probability matrix and Super 6 expected value (SPEC.md §31, §2/§33).

Tail handling (§31): the goal grid grows until the omitted tail mass is
below tail_tolerance, then the finite grid is renormalised proportionally —
tail mass is never folded into a real, selectable scoreline.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import poisson

from ..config import DEFAULT_MODEL, ModelConfig
from ..scoring import EXACT_POINTS, RESULT_POINTS


@dataclass(frozen=True)
class ScoreMatrix:
    """P[h, a] over h, a = 0..max_goals; sums to 1 after renormalisation."""

    probs: np.ndarray
    lambda_home: float
    lambda_away: float
    rho: float

    @property
    def max_goals(self) -> int:
        return self.probs.shape[0] - 1

    @property
    def p_home_win(self) -> float:
        return float(np.tril(self.probs, -1).sum())

    @property
    def p_draw(self) -> float:
        return float(np.trace(self.probs))

    @property
    def p_away_win(self) -> float:
        return float(np.triu(self.probs, 1).sum())

    def p_result(self, home_goals: int, away_goals: int) -> float:
        """P(result) for the outcome a scoreline belongs to (§2: includes
        the exact score itself)."""
        if home_goals > away_goals:
            return self.p_home_win
        if home_goals < away_goals:
            return self.p_away_win
        return self.p_draw

    def p_score(self, home_goals: int, away_goals: int) -> float:
        return float(self.probs[home_goals, away_goals])

    def ev(self, home_goals: int, away_goals: int) -> float:
        """§2: EV = 2·P(result) + 3·P(exact score)."""
        return RESULT_POINTS * self.p_result(home_goals, away_goals) + (
            EXACT_POINTS - RESULT_POINTS
        ) * self.p_score(home_goals, away_goals)

    def ev_matrix(self) -> np.ndarray:
        """EV for every scoreline in the grid — §2 requires calculating EV
        for every plausible scoreline, not just the modal one."""
        g = self.max_goals + 1
        h = np.arange(g)[:, None]
        a = np.arange(g)[None, :]
        p_res = np.where(
            h > a, self.p_home_win, np.where(h < a, self.p_away_win, self.p_draw)
        )
        return RESULT_POINTS * p_res + (EXACT_POINTS - RESULT_POINTS) * self.probs

    def best_pick(self) -> tuple[int, int]:
        """Scoreline with maximum Super 6 EV."""
        ev = self.ev_matrix()
        flat = int(np.argmax(ev))
        return flat // ev.shape[1], flat % ev.shape[1]


def score_matrix(
    lam_home: float,
    lam_away: float,
    rho: float = 0.0,
    cfg: ModelConfig = DEFAULT_MODEL,
) -> ScoreMatrix:
    """Build the Dixon-Coles-corrected score matrix (§31)."""
    for max_goals in range(cfg.min_goals_grid, cfg.max_goals_cap + 1):
        goals = np.arange(max_goals + 1)
        m = np.outer(poisson.pmf(goals, lam_home), poisson.pmf(goals, lam_away))
        # τ correction on the {0,1}² cells (§31), clipped to stay a valid pmf.
        m[0, 0] *= max(1.0 - lam_home * lam_away * rho, 1e-10)
        m[0, 1] *= max(1.0 + lam_home * rho, 1e-10)
        m[1, 0] *= max(1.0 + lam_away * rho, 1e-10)
        m[1, 1] *= max(1.0 - rho, 1e-10)
        deficit = abs(1.0 - m.sum())
        if deficit < cfg.tail_tolerance:
            return ScoreMatrix(m / m.sum(), lam_home, lam_away, rho)
    raise ValueError(
        f"Tail mass above tolerance {cfg.tail_tolerance} even at "
        f"max_goals={cfg.max_goals_cap} (λ={lam_home:.2f}/{lam_away:.2f}) — "
        "check λ caps (§30)."
    )
