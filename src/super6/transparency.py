"""Truthful, frontend-neutral read models for forecast explainability.

The weekly product needs to say *why* it selected a score without asking a
user to reconstruct a probability matrix.  This module is intentionally a
read-only adapter over the existing recommendation objects: it does not fit a
model, infer missing provenance, or manufacture historical evaluation data.

The payloads here are suitable for fixture detail and Model Lab views.  They
are kept outside ``web.py`` so another delivery surface can expose exactly the
same explanation without duplicating Super 6 scoring arithmetic.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from .config import ModelConfig
from .model import golden_goal
from .recommend import FixtureRecommendation, RoundRecommendation
from .scoring import EXACT_POINTS, RESULT_POINTS


@dataclass(frozen=True)
class ReadModel:
    """Base class for immutable, JSON-safe presentation read models."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScorelineCell(ReadModel):
    """One selectable scoreline from the finite, renormalised score grid."""

    home_goals: int
    away_goals: int
    score: str
    probability: float
    expected_points: float
    selected: bool = False


@dataclass(frozen=True)
class ScorelineHeatmap(ReadModel):
    """The entire score grid used to select a fixture recommendation."""

    home_team: str
    away_team: str
    max_goals: int
    lambda_home: float
    lambda_away: float
    rho: float
    cells: list[ScorelineCell] = field(default_factory=list)


@dataclass(frozen=True)
class ExpectedValueBreakdown(ReadModel):
    """Human-readable decomposition of the chosen scoreline's EV.

    A correct exact score earns five points, of which two are already the
    correct-result award.  Expressing the exact term as a three-point bonus
    makes the formula both correct and easy to audit:
    ``2 × P(result) + 3 × P(exact score)``.
    """

    score: str
    result_probability: float
    exact_score_probability: float
    result_points: int
    exact_score_bonus_points: int
    result_expected_points: float
    exact_score_bonus_expected_points: float
    expected_points: float
    formula: str = "EV = 2 × P(result) + 3 × P(exact score)"


@dataclass(frozen=True)
class GoldenGoalBand(ReadModel):
    minute: int
    sky_interval: str
    probability: float
    recommended: bool = False


@dataclass(frozen=True)
class GoldenGoalDistribution(ReadModel):
    """Golden Goal probabilities without pretending they affect league EV."""

    recommended_minute: int
    recommended_sky_interval: str
    interval_probabilities: dict[str, float]
    no_goal_probability: float
    most_likely_minutes: list[GoldenGoalBand] = field(default_factory=list)
    rationale: str = (
        "The selected minute minimises expected tiebreak distance conditional "
        "on a goal; it is not necessarily the single most likely minute."
    )


@dataclass(frozen=True)
class SourceFreshness(ReadModel):
    """An explicit provenance/freshness statement supplied by the caller.

    ``observed_at`` is deliberately optional.  Missing source timestamps must
    remain missing instead of being replaced with forecast-generation time.
    """

    source_id: str
    available: bool
    observed_at: str | None = None
    freshness: str = "UNKNOWN"
    detail: str | None = None


@dataclass(frozen=True)
class ForecastProvenance(ReadModel):
    """Reconstruction metadata for a single model recommendation."""

    generated_at: str
    model_version: str
    model_inputs: dict[str, float | int]
    sources: list[SourceFreshness] = field(default_factory=list)


@dataclass(frozen=True)
class FixtureTransparency(ReadModel):
    """Complete, truthful explanation of one selected fixture scoreline."""

    heatmap: ScorelineHeatmap
    selected_pick: ExpectedValueBreakdown
    provenance: ForecastProvenance


@dataclass(frozen=True)
class ModelSummary(ReadModel):
    """Static model configuration plus only the evaluation supplied to it."""

    model_version: str
    configuration: dict[str, Any]
    training_cutoff: str | None = None
    evaluation_status: str = "UNAVAILABLE"
    evaluation_reason: str | None = (
        "No validated evaluation result has been supplied."
    )
    evaluation: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class PerformanceSummary(ReadModel):
    """Prospective-performance view which preserves an absent sample."""

    status: str
    rounds_recorded: int
    arms: list[dict[str, Any]] = field(default_factory=list)
    sample_warning: str = (
        "Six fixtures per round is a thin sample. Treat early differences as noise."
    )


def scoreline_heatmap(recommendation: FixtureRecommendation) -> ScorelineHeatmap:
    """Expose every real selectable scoreline and its Super 6 EV.

    The finite matrix is already tail-controlled and renormalised by
    :func:`super6.model.matrix.score_matrix`; this adapter never folds omitted
    tail probability into an arbitrary final scoreline.
    """

    matrix = recommendation.matrix
    ev_matrix = matrix.ev_matrix()
    cells = [
        ScorelineCell(
            home_goals=home_goals,
            away_goals=away_goals,
            score=f"{home_goals}-{away_goals}",
            probability=float(matrix.probs[home_goals, away_goals]),
            expected_points=float(ev_matrix[home_goals, away_goals]),
            selected=(
                home_goals == recommendation.pick_home
                and away_goals == recommendation.pick_away
            ),
        )
        for home_goals in range(matrix.max_goals + 1)
        for away_goals in range(matrix.max_goals + 1)
    ]
    return ScorelineHeatmap(
        home_team=recommendation.home,
        away_team=recommendation.away,
        max_goals=matrix.max_goals,
        lambda_home=matrix.lambda_home,
        lambda_away=matrix.lambda_away,
        rho=matrix.rho,
        cells=cells,
    )


def selected_pick_ev(recommendation: FixtureRecommendation) -> ExpectedValueBreakdown:
    """Return the exact arithmetic used by :meth:`ScoreMatrix.ev`."""

    result_ev = RESULT_POINTS * recommendation.p_result
    exact_bonus = EXACT_POINTS - RESULT_POINTS
    exact_bonus_ev = exact_bonus * recommendation.p_score
    return ExpectedValueBreakdown(
        score=recommendation.pick,
        result_probability=recommendation.p_result,
        exact_score_probability=recommendation.p_score,
        result_points=RESULT_POINTS,
        exact_score_bonus_points=exact_bonus,
        result_expected_points=result_ev,
        exact_score_bonus_expected_points=exact_bonus_ev,
        expected_points=result_ev + exact_bonus_ev,
    )


def golden_goal_distribution(
    recommendation: RoundRecommendation, top_n: int = 5
) -> GoldenGoalDistribution:
    """Summarise the Golden Goal forecast, retaining the full interval view.

    ``top_n`` is only a disclosure limit for individual minutes.  Interval
    probabilities and no-goal mass remain exact regardless of the limit.
    """

    if top_n < 1:
        raise ValueError("top_n must be at least 1")
    forecast = recommendation.golden
    order = forecast.band_probs.argsort()[::-1][:top_n]
    bands = [
        GoldenGoalBand(
            minute=int(index + 1),
            sky_interval=golden_goal.sky_interval(int(index + 1)),
            probability=float(forecast.band_probs[index]),
            recommended=int(index + 1) == forecast.recommended_minute,
        )
        for index in order
    ]
    return GoldenGoalDistribution(
        recommended_minute=forecast.recommended_minute,
        recommended_sky_interval=golden_goal.sky_interval(forecast.recommended_minute),
        interval_probabilities={
            label: float(probability)
            for label, probability in forecast.intervals().items()
        },
        no_goal_probability=forecast.p_no_goal,
        most_likely_minutes=bands,
    )


def fixture_transparency(
    recommendation: FixtureRecommendation,
    *,
    generated_at: str,
    model_version: str,
    sources: Iterable[SourceFreshness] = (),
) -> FixtureTransparency:
    """Build a full explainability payload for one fixture.

    Callers own provenance timestamps and source state.  Passing no sources is
    valid and explicitly means no source provenance is available for this
    forecast; this layer does not infer either one from an engine run.
    """

    matrix = recommendation.matrix
    provenance = ForecastProvenance(
        generated_at=generated_at,
        model_version=model_version,
        model_inputs={
            "lambda_home": matrix.lambda_home,
            "lambda_away": matrix.lambda_away,
            "rho": matrix.rho,
            "max_goals": matrix.max_goals,
        },
        sources=list(sources),
    )
    return FixtureTransparency(
        heatmap=scoreline_heatmap(recommendation),
        selected_pick=selected_pick_ev(recommendation),
        provenance=provenance,
    )


def model_summary(
    model_version: str,
    config: ModelConfig,
    *,
    training_cutoff: str | None = None,
    evaluation: Iterable[Mapping[str, Any]] | None = None,
) -> ModelSummary:
    """Build Model Lab metadata without claiming an unevaluated result.

    ``evaluation`` is expected to be an already-validated backtest/ablation
    row set (for example ``walk_forward.compare(...).to_dict('records')``).
    The adapter copies supplied rows but never runs a backtest implicitly.
    """

    rows = [dict(row) for row in evaluation] if evaluation is not None else []
    return ModelSummary(
        model_version=model_version,
        configuration={
            "xi": config.xi,
            "tail_tolerance": config.tail_tolerance,
            "rho_bounds": config.rho_bounds,
            "lambda_floor": config.lambda_floor,
            "lambda_ceiling": config.lambda_ceiling,
            "market_blend_weight": config.market_blend_weight,
            "use_promoted_priors": config.use_promoted_priors,
            "season_decay": config.season_decay,
            "refit_every_days": config.refit_every_days,
        },
        training_cutoff=training_cutoff,
        evaluation_status="AVAILABLE" if rows else "UNAVAILABLE",
        evaluation_reason=(
            None if rows else "No validated evaluation result has been supplied."
        ),
        evaluation=rows,
    )


def performance_summary(
    arms: Iterable[Mapping[str, Any]] | None,
    *,
    rounds_recorded: int | None = None,
) -> PerformanceSummary:
    """Adapt recorded prospective standings, preserving the empty state.

    This accepts the records output by ``prospective.standings`` or the
    existing API service.  It does not synthesize zero-valued arm rows.
    """

    rows = [dict(row) for row in arms] if arms is not None else []
    recorded = (
        max((int(row.get("rounds", 0)) for row in rows), default=0)
        if rounds_recorded is None
        else rounds_recorded
    )
    return PerformanceSummary(
        status="AVAILABLE" if rows else "UNAVAILABLE",
        rounds_recorded=recorded,
        arms=rows,
        sample_warning=(
            "Six fixtures per round is a thin sample. Treat early differences as noise."
            if rows
            else (
                "No scored prospective rounds are recorded yet. Performance will "
                "appear once pre-deadline picks and final results exist."
            )
        ),
    )
