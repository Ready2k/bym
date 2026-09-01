from datetime import datetime, timezone

import pytest

from bym import db
from bym.fpl import game_state, projection_store, recommendation
from bym.fpl.aggregation import GameweekProjection
from bym.fpl.projection import FixtureProjection
from bym.fpl.scoring import ExpectedPointsBreakdown

from test_fpl_game_state import SEASON, sample_payload

GW = 3
MODEL = "BASELINE_FPL_001"
OBSERVED_AT = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
RUN_AT = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "fpl.db") as connection:
        yield connection


def _fixture(player_id: int) -> FixtureProjection:
    return FixtureProjection(
        player_id=player_id, fixture_id=f"{SEASON}:{GW}:A:B:{player_id}",
        season=SEASON, gameweek=GW, p_appearance=1.0, p_start=1.0,
        p_60_plus=1.0, expected_minutes=90.0, goal_lambda=0.0,
        assist_lambda=0.0, p_clean_sheet=0.0, expected_saves=None,
        model_version=MODEL,
    )


def _breakdown() -> ExpectedPointsBreakdown:
    return ExpectedPointsBreakdown(
        appearance_points=2.0, goal_points=0.0, assist_points=0.0,
        clean_sheet_points=0.0, saves_points=0.0,
    )


def _record_complete_projections(conn, state):
    for index, player_id in enumerate(game_state.candidate_universe(state), start=1):
        fixture = _fixture(player_id)
        projection_store.record_fixture_projection(conn, fixture, _breakdown())
        projection_store.record_gameweek_projection(
            conn,
            GameweekProjection(
                player_id=player_id, season=SEASON, gameweek=GW,
                expected_points=float(index), fixtures_played=1,
                fixture_breakdowns=(_breakdown(),), model_version=MODEL,
                computed_at=datetime.now(timezone.utc).isoformat(),
            ),
        )


def _legal_payload():
    """The generic game-state fixture does not enforce club legality.

    The recommendation runner does, so distribute this synthetic universe
    across clubs before using it in an optimiser integration test.
    """
    payload = sample_payload()
    for index, candidate in enumerate(payload["candidate_players"]):
        candidate["club"] = f"Club{index}"
    return payload


def test_runs_from_one_explicit_snapshot_and_records_an_immutable_recommendation(conn):
    state, _ = game_state.store_import(conn, _legal_payload(), SEASON, now=OBSERVED_AT)
    _record_complete_projections(conn, state)

    run = recommendation.recommend_from_snapshot(
        conn, state.id, MODEL, top_k=1, now=RUN_AT
    )

    assert run.game_state_snapshot_id == state.id
    assert run.recommendation_snapshot_id > 0
    row = conn.execute(
        "SELECT game_state_snapshot_id, model_version, top_k FROM fpl_recommendation_snapshots"
    ).fetchone()
    assert tuple(row) == (state.id, MODEL, 1)


def test_refuses_a_partial_projection_universe(conn):
    state, _ = game_state.store_import(conn, sample_payload(), SEASON, now=OBSERVED_AT)
    player_id = next(iter(game_state.candidate_universe(state)))
    fixture = _fixture(player_id)
    projection_store.record_fixture_projection(conn, fixture, _breakdown())
    projection_store.record_gameweek_projection(
        conn,
        GameweekProjection(
            player_id=player_id, season=SEASON, gameweek=GW, expected_points=2.0,
            fixtures_played=1, fixture_breakdowns=(_breakdown(),), model_version=MODEL,
        ),
    )

    with pytest.raises(recommendation.RecommendationPreflightError, match="missing Gameweek"):
        recommendation.recommend_from_snapshot(conn, state.id, MODEL, top_k=1, now=RUN_AT)


def test_refuses_to_recommend_after_the_gameweek_deadline(conn):
    state, _ = game_state.store_import(conn, _legal_payload(), SEASON, now=OBSERVED_AT)
    _record_complete_projections(conn, state)

    with pytest.raises(recommendation.RecommendationPreflightError, match="not before deadline"):
        recommendation.recommend_from_snapshot(
            conn, state.id, MODEL, top_k=1,
            now=datetime(2026, 8, 21, tzinfo=timezone.utc),
        )


def test_refuses_a_snapshot_explicitly_marked_as_partial(conn):
    payload = _legal_payload()
    payload["candidate_universe_complete"] = False
    state, _ = game_state.store_import(conn, payload, SEASON, now=OBSERVED_AT)
    _record_complete_projections(conn, state)

    with pytest.raises(recommendation.RecommendationPreflightError, match="incomplete candidate universe"):
        recommendation.recommend_from_snapshot(conn, state.id, MODEL, top_k=1, now=RUN_AT)
