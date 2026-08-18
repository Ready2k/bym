import pytest

from bym import db
from bym.fpl import player_store, projection_store
from bym.fpl.aggregation import GameweekProjection
from bym.fpl.projection import FixtureProjection
from bym.fpl.scoring import ExpectedPointsBreakdown

SEASON = 2022
GW = 3


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "bym.db") as c:
        # fpl_fixture_projections/fpl_gameweek_projections carry a real FK
        # to fpl_players — every test player_id used below must exist.
        for name in ("Player One", "Player Two"):
            player_store.get_or_create_player(c, name)
        yield c


def _proj(player_id=1, **overrides):
    defaults = dict(
        player_id=player_id, fixture_id="2022:3:Chelsea:Everton", season=SEASON, gameweek=GW,
        p_appearance=0.9, p_start=0.8, p_60_plus=0.8, expected_minutes=75.0,
        goal_lambda=0.3, assist_lambda=0.2, p_clean_sheet=0.4, expected_saves=None,
    )
    defaults.update(overrides)
    return FixtureProjection(**defaults)


def _breakdown(**overrides):
    defaults = dict(
        appearance_points=1.6, goal_points=1.5, assist_points=0.6,
        clean_sheet_points=0.4, saves_points=0.0,
    )
    defaults.update(overrides)
    return ExpectedPointsBreakdown(**defaults)


def test_record_fixture_projection_creates_a_new_row(conn):
    row_id, was_new = projection_store.record_fixture_projection(conn, _proj(), _breakdown())
    assert was_new
    rows = projection_store.fixture_projections_for(conn, SEASON, GW, _proj().model_version)
    assert len(rows) == 1
    assert rows[0]["id"] == row_id
    assert rows[0]["expected_points"] == pytest.approx(_breakdown().total)
    assert rows[0]["goal_lambda"] == pytest.approx(0.3)


def test_record_fixture_projection_updates_in_place_for_the_same_model_version(conn):
    projection_store.record_fixture_projection(conn, _proj(goal_lambda=0.3), _breakdown())
    _, was_new = projection_store.record_fixture_projection(
        conn, _proj(goal_lambda=0.9), _breakdown()
    )
    assert not was_new
    rows = projection_store.fixture_projections_for(conn, SEASON, GW, _proj().model_version)
    assert len(rows) == 1
    assert rows[0]["goal_lambda"] == pytest.approx(0.9)


def test_record_fixture_projection_keeps_separate_rows_per_model_version(conn):
    projection_store.record_fixture_projection(
        conn, _proj(model_version="BASELINE_FPL_001"), _breakdown()
    )
    projection_store.record_fixture_projection(
        conn, _proj(model_version="FPL_002_XG"), _breakdown()
    )
    assert len(projection_store.fixture_projections_for(conn, SEASON, GW, "BASELINE_FPL_001")) == 1
    assert len(projection_store.fixture_projections_for(conn, SEASON, GW, "FPL_002_XG")) == 1


def test_omitted_components_are_persisted_as_null_not_zero(conn):
    projection_store.record_fixture_projection(conn, _proj(), _breakdown())
    rows = projection_store.fixture_projections_for(conn, SEASON, GW, _proj().model_version)
    assert rows[0]["expected_defcon_points"] is None
    assert rows[0]["expected_bonus"] is None
    assert rows[0]["points_variance"] is None


def _gw_proj(**overrides):
    defaults = dict(
        player_id=1, season=SEASON, gameweek=GW, expected_points=4.1,
        fixtures_played=1, fixture_breakdowns=(), computed_at="2026-08-17T00:00:00+00:00",
    )
    defaults.update(overrides)
    return GameweekProjection(**defaults)


def test_record_gameweek_projection_creates_and_updates_in_place(conn):
    projection_store.record_gameweek_projection(conn, _gw_proj(expected_points=4.1))
    _, was_new = projection_store.record_gameweek_projection(
        conn, _gw_proj(expected_points=5.5)
    )
    assert not was_new
    rows = projection_store.gameweek_projections_for(conn, SEASON, GW, _gw_proj().model_version)
    assert len(rows) == 1
    assert rows[0]["expected_points"] == pytest.approx(5.5)


def test_gameweek_projections_for_filters_by_season_and_gameweek(conn):
    projection_store.record_gameweek_projection(conn, _gw_proj(player_id=1, gameweek=3))
    projection_store.record_gameweek_projection(conn, _gw_proj(player_id=2, gameweek=4))
    rows = projection_store.gameweek_projections_for(conn, SEASON, 3, _gw_proj().model_version)
    assert {r["player_id"] for r in rows} == {1}
