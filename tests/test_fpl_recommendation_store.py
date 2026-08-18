import json

import pytest

from bym import db
from bym.fpl import recommendation_store
from bym.fpl.optimiser import SquadSelection

SEASON = 2026
GW = 4


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "bym.db") as c:
        yield c


def _selection(**overrides):
    defaults = dict(
        squad_ids=frozenset({1, 2, 3}),
        starter_ids=frozenset({1, 2}),
        bench_order=(3,),
        captain_id=1,
        vice_captain_id=2,
        transfers_in=frozenset({3}),
        transfers_out=frozenset({9}),
        hit_cost=0.0,
        xi_expected_points=10.0,
        evaluated_expected_points=10.5,
        net_expected_points=10.5,
    )
    defaults.update(overrides)
    return SquadSelection(**defaults)


def test_record_recommendation_returns_a_new_row_id(conn):
    row_id = recommendation_store.record_recommendation(
        conn, SEASON, GW, _selection(), "BASELINE_FPL_001", top_k=10
    )
    rows = recommendation_store.recommendations_for(conn, SEASON, GW)
    assert len(rows) == 1
    assert rows[0]["id"] == row_id


def test_repeated_calls_are_never_merged_even_with_identical_content(conn):
    """§11.2: every recommendation snapshot is a new immutable observation
    — including a pre-deadline reforecast that happens to land on the
    exact same numbers as the last one."""
    selection = _selection()
    recommendation_store.record_recommendation(conn, SEASON, GW, selection, "BASELINE_FPL_001", 10)
    recommendation_store.record_recommendation(conn, SEASON, GW, selection, "BASELINE_FPL_001", 10)
    assert len(recommendation_store.recommendations_for(conn, SEASON, GW)) == 2


def test_recommendations_for_returns_snapshots_in_recorded_order(conn):
    recommendation_store.record_recommendation(
        conn, SEASON, GW, _selection(hit_cost=0.0), "BASELINE_FPL_001", 10
    )
    recommendation_store.record_recommendation(
        conn, SEASON, GW, _selection(hit_cost=4.0), "BASELINE_FPL_001", 10
    )
    rows = recommendation_store.recommendations_for(conn, SEASON, GW)
    assert [r["hit_cost"] for r in rows] == [0.0, 4.0]


def test_recommendations_for_is_scoped_to_one_gameweek(conn):
    recommendation_store.record_recommendation(conn, SEASON, GW, _selection(), "BASELINE_FPL_001", 10)
    recommendation_store.record_recommendation(conn, SEASON, GW + 1, _selection(), "BASELINE_FPL_001", 10)
    assert len(recommendation_store.recommendations_for(conn, SEASON, GW)) == 1


def test_squad_fields_round_trip_through_json(conn):
    row_id = recommendation_store.record_recommendation(
        conn, SEASON, GW, _selection(), "BASELINE_FPL_001", top_k=25
    )
    row = recommendation_store.recommendations_for(conn, SEASON, GW)[0]
    assert row["id"] == row_id
    assert json.loads(row["squad_ids"]) == [1, 2, 3]
    assert json.loads(row["starter_ids"]) == [1, 2]
    assert json.loads(row["bench_order"]) == [3]
    assert json.loads(row["transfers_in"]) == [3]
    assert json.loads(row["transfers_out"]) == [9]
    assert row["captain_id"] == 1
    assert row["vice_captain_id"] == 2
    assert row["top_k"] == 25
    assert row["model_version"] == "BASELINE_FPL_001"


def test_game_state_snapshot_id_is_nullable(conn):
    row_id = recommendation_store.record_recommendation(
        conn, SEASON, GW, _selection(), "BASELINE_FPL_001", top_k=10,
        game_state_snapshot_id=None,
    )
    row = recommendation_store.recommendations_for(conn, SEASON, GW)[0]
    assert row["id"] == row_id
    assert row["game_state_snapshot_id"] is None
