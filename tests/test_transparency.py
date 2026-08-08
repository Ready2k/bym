"""Read-model tests for the Phase 4 transparency surface."""

from __future__ import annotations

import json

import pytest

from super6.config import FROZEN_PHASE1_V1
from super6.recommend import FixtureRecommendation, RoundRecommendation
from super6.transparency import (
    SourceFreshness,
    fixture_transparency,
    golden_goal_distribution,
    model_summary,
    performance_summary,
    scoreline_heatmap,
    selected_pick_ev,
)


def fixture_recommendation() -> FixtureRecommendation:
    """A direct fixture recommendation avoids a data-fit dependency here."""
    from super6.model.matrix import score_matrix

    matrix = score_matrix(1.8, 1.1, rho=-0.06)
    home, away = matrix.best_pick()
    return FixtureRecommendation(
        home="Home",
        away="Away",
        pick_home=home,
        pick_away=away,
        p_result=matrix.p_result(home, away),
        p_score=matrix.p_score(home, away),
        ev=matrix.ev(home, away),
        confidence="MEDIUM",
        matrix=matrix,
    )


def test_heatmap_has_every_grid_scoreline_and_marks_only_the_pick():
    recommendation = fixture_recommendation()
    heatmap = scoreline_heatmap(recommendation)

    assert len(heatmap.cells) == (heatmap.max_goals + 1) ** 2
    selected = [cell for cell in heatmap.cells if cell.selected]
    assert len(selected) == 1
    assert selected[0].score == recommendation.pick
    assert selected[0].expected_points == pytest.approx(recommendation.ev)
    assert sum(cell.probability for cell in heatmap.cells) == pytest.approx(1.0)


def test_selected_ev_breakdown_matches_engine_formula_without_double_counting():
    recommendation = fixture_recommendation()
    breakdown = selected_pick_ev(recommendation)

    assert breakdown.result_points == 2
    assert breakdown.exact_score_bonus_points == 3
    assert breakdown.result_expected_points == pytest.approx(
        2 * recommendation.p_result
    )
    assert breakdown.exact_score_bonus_expected_points == pytest.approx(
        3 * recommendation.p_score
    )
    assert breakdown.expected_points == pytest.approx(recommendation.ev)


def test_fixture_provenance_preserves_unknown_source_times_and_is_json_safe():
    recommendation = fixture_recommendation()
    view = fixture_transparency(
        recommendation,
        generated_at="2026-08-08T11:00:00+00:00",
        model_version="PHASE1_MODEL_V1",
        sources=[
            SourceFreshness(
                source_id="market",
                available=True,
                observed_at=None,
                freshness="UNKNOWN",
            )
        ],
    )

    assert view.provenance.sources[0].observed_at is None
    assert view.provenance.model_inputs["lambda_home"] == pytest.approx(1.8)
    json.dumps(view.to_dict())


def test_golden_goal_keeps_no_goal_mass_and_explains_minute_convention():
    from super6.model import golden_goal

    fixture = fixture_recommendation()
    round_recommendation = RoundRecommendation(
        round_number=1,
        fixtures=(fixture,) * 6,
        golden=golden_goal.forecast([2.9] * 6),
    )
    view = golden_goal_distribution(round_recommendation, top_n=3)

    assert len(view.most_likely_minutes) == 3
    assert view.recommended_sky_interval == golden_goal.sky_interval(
        view.recommended_minute
    )
    assert sum(view.interval_probabilities.values()) + view.no_goal_probability == pytest.approx(1.0)
    with pytest.raises(ValueError, match="top_n"):
        golden_goal_distribution(round_recommendation, top_n=0)


def test_model_summary_does_not_invent_an_evaluation():
    summary = model_summary("PHASE1_MODEL_V1", FROZEN_PHASE1_V1)

    assert summary.evaluation_status == "UNAVAILABLE"
    assert summary.evaluation == []
    assert summary.configuration["tail_tolerance"] == pytest.approx(1e-5)

    evaluated = model_summary(
        "PHASE1_MODEL_V1",
        FROZEN_PHASE1_V1,
        training_cutoff="2026-08-01",
        evaluation=[{"model": "dixon-coles", "fixtures": 120, "brier": 0.9}],
    )
    assert evaluated.evaluation_status == "AVAILABLE"
    assert evaluated.evaluation[0]["fixtures"] == 120


def test_performance_summary_keeps_absent_and_recorded_samples_distinct():
    empty = performance_summary(None)
    assert empty.status == "UNAVAILABLE"
    assert empty.arms == []
    assert "No scored" in empty.sample_warning

    recorded = performance_summary(
        [{"arm": "PHASE1_MODEL_V1", "rounds": 2, "fixtures": 12, "points": 18}]
    )
    assert recorded.status == "AVAILABLE"
    assert recorded.rounds_recorded == 2
    assert recorded.arms[0]["points"] == 18
