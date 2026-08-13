"""Round recommendations and the pre-round report (SPEC.md §20).

Column semantics (§20 — never conflated):
    RESULT = P(selected outcome), SCORE = P(exact selected scoreline),
    EV = 2·RESULT + 3·SCORE, CONF = calibrated HIGH | MEDIUM | LOW category.

Phase 1A confidence (documented placeholder): with a single model and no
market/news feeds yet, three of §20's five factors (model_agreement,
market_divergence, calibration_quality) are unavailable, so CONF v1 is
derived from the score distribution alone via config thresholds. The full
five-factor version arrives with Phase 2 feeds and must be
calibration-tested (§16) before being trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from .config import DEFAULT_MODEL, ModelConfig
from .model import golden_goal, market
from .model.dixon_coles import DixonColesParams
from .model.matrix import ScoreMatrix, score_matrix
from .round import Round


@dataclass(frozen=True)
class FixtureRecommendation:
    home: str
    away: str
    pick_home: int
    pick_away: int
    p_result: float
    p_score: float
    ev: float
    confidence: str
    matrix: ScoreMatrix
    # What actually produced this forecast. A fixture the model cannot
    # judge is not silently given the frozen model's name.
    basis: str = "PHASE1_MODEL_V1"
    # The matrix *before* any market blend. §33's divergence check compares
    # the model's own opinion against the market, so it must not be handed
    # a matrix the market has already been folded into — that would compare
    # the market with itself and report agreement by construction. Equal to
    # `matrix` when no odds were supplied.
    raw_matrix: ScoreMatrix | None = None

    @property
    def model_only_matrix(self) -> ScoreMatrix:
        return self.raw_matrix if self.raw_matrix is not None else self.matrix

    @property
    def pick(self) -> str:
        return f"{self.pick_home}-{self.pick_away}"

    def alternatives(self, top_n: int = 5) -> list[tuple[str, float, float]]:
        """§41.3-style meaningful alternatives: (score, P(score), EV)."""
        ev = self.matrix.ev_matrix()
        flat = ev.flatten()
        order = flat.argsort()[::-1][:top_n]
        g = ev.shape[1]
        return [
            (f"{i // g}-{i % g}", float(self.matrix.probs.flatten()[i]), float(flat[i]))
            for i in order
        ]


@dataclass(frozen=True)
class RoundRecommendation:
    round_number: int
    fixtures: tuple[FixtureRecommendation, ...]
    golden: golden_goal.GoldenGoalForecast

    @property
    def expected_round_score(self) -> float:
        return sum(f.ev for f in self.fixtures)


def confidence_v1(p_result: float, p_score: float, cfg: ModelConfig) -> str:
    """Phase 1A CONF placeholder — see module docstring."""
    if p_result >= cfg.conf_high_result_p and p_score >= cfg.conf_high_score_p:
        return "HIGH"
    if p_result < cfg.conf_low_result_p or p_score < cfg.conf_low_score_p:
        return "LOW"
    return "MEDIUM"


MARKET_ONLY_BASIS = "MARKET_ONLY_FALLBACK"


def recommend_fixture(
    params: DixonColesParams,
    home: str,
    away: str,
    cfg: ModelConfig = DEFAULT_MODEL,
    odds: tuple[float, float, float] | None = None,
    blend_weight: float | None = None,
    basis: str = "PHASE1_MODEL_V1",
) -> FixtureRecommendation:
    """`blend_weight` overrides the frozen w for this fixture only.

    Used at w=1.0 for a fixture the model cannot judge: the result
    probabilities then come entirely from the market, while the within-
    result scoreline shape still comes from the matrix. That is the right
    split — for an unknown team it is the *relative* strength that is
    broken, not the shape of a 1-0 versus a 2-0 given a home win.
    """
    lam_h, lam_a = params.lambdas(home, away, cfg)
    m = raw = score_matrix(lam_h, lam_a, params.rho, cfg)
    weight = cfg.market_blend_weight if blend_weight is None else blend_weight
    if odds is not None and weight is not None:
        mh, md, ma = market.implied_result_probs(*odds)
        m = market.blend_matrix(m, mh, md, ma, weight)
    ph, pa = m.best_pick()
    p_result = m.p_result(ph, pa)
    p_score = m.p_score(ph, pa)
    return FixtureRecommendation(
        home=home,
        away=away,
        pick_home=ph,
        pick_away=pa,
        p_result=p_result,
        p_score=p_score,
        ev=m.ev(ph, pa),
        confidence=confidence_v1(p_result, p_score, cfg),
        matrix=m,
        raw_matrix=raw,
        basis=basis,
    )


ParamRouter = Callable[[str, str], DixonColesParams]


def recommend_round(
    params: DixonColesParams | ParamRouter,
    rnd: Round,
    cfg: ModelConfig = DEFAULT_MODEL,
    market_only: set[tuple[str, str]] | None = None,
) -> RoundRecommendation:
    """`params` is either a single fitted parameter set, or a router
    (home, away) -> params — used for per-division fits, where each fixture
    is scored by its own competition's model.

    `market_only` names fixtures the model cannot judge but which do have
    odds: those are forecast at w=1.0 and labelled as such, instead of
    presenting the league-average prior as a model opinion.

    Every fixture still contributes to the Golden Goal, including the
    market-only ones. That hazard is driven by λ_home + λ_away — the goal
    *total*, not the split — and the league-average prior is a defensible
    total even when it is a useless guess at who wins. Dropping the fixture
    would instead assert it produces no goals at all, which biases the
    recommended minute later across the whole round.
    """
    router: ParamRouter = (
        params if callable(params) else (lambda home, away: params)
    )
    market_only = market_only or set()
    recs = tuple(
        recommend_fixture(
            router(f.home, f.away), f.home, f.away, cfg, odds=f.odds,
            blend_weight=1.0 if (f.home, f.away) in market_only else None,
            basis=(
                MARKET_ONLY_BASIS
                if (f.home, f.away) in market_only
                else "PHASE1_MODEL_V1"
            ),
        )
        for f in rnd.fixtures
    )
    golden = golden_goal.forecast(
        [r.matrix.lambda_home + r.matrix.lambda_away for r in recs]
    )
    return RoundRecommendation(rnd.round_number, recs, golden)


def format_report(rec: RoundRecommendation) -> str:
    """§20 pre-round report."""
    lines = [f"SUPER 6 - ROUND {rec.round_number}", ""]
    lines.append(
        f"{'':24s} {'PICK':>5s} {'RESULT':>8s} {'SCORE':>7s} {'EV':>6s}  CONF"
    )
    for f in rec.fixtures:
        name = f"{f.home[:10].upper()} v {f.away[:10].upper()}"
        lines.append(
            f"{name:24s} {f.pick:>5s} {f.p_result:>7.1%} {f.p_score:>6.1%} "
            f"{f.ev:>6.2f}  {f.confidence}"
        )
    g = rec.golden
    lines.append("")
    lines.append(
        f"Golden Goal: minute {g.recommended_minute} "
        f"(Sky interval {golden_goal.sky_interval(g.recommended_minute)})"
    )
    intervals = "   ".join(f"{k} mins {v:.0%}" for k, v in g.intervals().items())
    lines.append(f"First-goal intervals: {intervals}")
    lines.append(f"Expected round score: {rec.expected_round_score:.2f}")
    return "\n".join(lines)
