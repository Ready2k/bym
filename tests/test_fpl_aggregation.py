import pytest

from bym.fpl import aggregation
from bym.fpl.players import Position
from bym.fpl.projection import FixtureProjection

SEASON = 2022
GW = 5


def fixture_proj(player_id, fixture_id, goal_lambda=0.0, assist_lambda=0.0):
    return FixtureProjection(
        player_id=player_id, fixture_id=fixture_id, season=SEASON, gameweek=GW,
        p_appearance=1.0, p_start=1.0, p_60_plus=1.0, expected_minutes=90.0,
        goal_lambda=goal_lambda, assist_lambda=assist_lambda,
        p_clean_sheet=0.0, expected_saves=None,
    )


def test_a_player_with_one_fixture_gets_that_fixtures_points():
    projs = [fixture_proj(1, "fxA", goal_lambda=1.0)]
    result = aggregation.aggregate_gameweek(
        projs, {1: Position.FWD.value}, SEASON, GW
    )
    assert len(result) == 1
    assert result[0].fixtures_played == 1
    assert result[0].expected_points == pytest.approx(4.0 + 2.0)  # 1 goal (FWD=4) + 2 appearance


def test_a_double_gameweek_sums_both_fixtures():
    projs = [
        fixture_proj(1, "fxA", goal_lambda=1.0),
        fixture_proj(1, "fxB", goal_lambda=1.0),
    ]
    result = aggregation.aggregate_gameweek(
        projs, {1: Position.FWD.value}, SEASON, GW
    )
    assert len(result) == 1
    assert result[0].fixtures_played == 2
    # Two independent fixture-level breakdowns, each captain-eligible.
    assert len(result[0].fixture_breakdowns) == 2
    single_fixture_points = 4.0 + 2.0
    assert result[0].expected_points == pytest.approx(2 * single_fixture_points)


def test_a_blank_gameweek_player_gets_an_explicit_zero_row():
    """A player with no fixture this Gameweek must appear with
    fixtures_played=0, not be silently absent — a squad optimiser needs
    to know they will score zero, not merely have nothing to say."""
    result = aggregation.aggregate_gameweek(
        [], {1: Position.FWD.value}, SEASON, GW, player_ids={1}
    )
    assert len(result) == 1
    assert result[0].fixtures_played == 0
    assert result[0].expected_points == 0.0
    assert result[0].fixture_breakdowns == ()


def test_without_explicit_player_ids_a_blanking_player_is_simply_absent():
    result = aggregation.aggregate_gameweek(
        [fixture_proj(1, "fxA")], {1: Position.MID.value}, SEASON, GW
    )
    assert {r.player_id for r in result} == {1}


def test_missing_position_for_a_player_with_fixtures_raises():
    with pytest.raises(ValueError, match="no entry in player_positions"):
        aggregation.aggregate_gameweek(
            [fixture_proj(1, "fxA")], {}, SEASON, GW
        )


def test_a_projection_for_a_different_gameweek_is_rejected():
    wrong_gw = FixtureProjection(
        player_id=1, fixture_id="fxA", season=SEASON, gameweek=GW + 1,
        p_appearance=1.0, p_start=1.0, p_60_plus=1.0, expected_minutes=90.0,
        goal_lambda=0.0, assist_lambda=0.0, p_clean_sheet=0.0, expected_saves=None,
    )
    with pytest.raises(ValueError, match="not the requested"):
        aggregation.aggregate_gameweek(
            [wrong_gw], {1: Position.MID.value}, SEASON, GW
        )


def test_points_variance_is_not_fabricated():
    result = aggregation.aggregate_gameweek(
        [fixture_proj(1, "fxA")], {1: Position.MID.value}, SEASON, GW
    )
    assert result[0].points_variance is None
