import json

import pytest

from bym import db
from bym.fpl import dashboard, lineup_state


def _payload():
    players = [
        {"name": "GK One", "club": "A", "position": "GK", "current_price": 4.5, "fixture": "A", "is_starter": True, "is_captain": False, "is_vice_captain": False},
        {"name": "GK Two", "club": "B", "position": "GK", "current_price": 4.0, "fixture": "B", "is_starter": False, "bench_order": 1},
    ]
    for index in range(5):
        players.append({"name": f"DEF {index}", "club": f"D{index}", "position": "DEF", "current_price": 4.5, "fixture": "D", "is_starter": index < 3})
    for index in range(5):
        players.append({"name": f"MID {index}", "club": f"M{index}", "position": "MID", "current_price": 6.0, "fixture": "M", "is_starter": True})
    for index in range(3):
        players.append({"name": f"FWD {index}", "club": f"F{index}", "position": "FWD", "current_price": 6.0, "fixture": "F", "is_starter": index < 2})
    bench = [player for player in players if not player["is_starter"]]
    for order, player in enumerate(bench, start=1):
        player["bench_order"] = order
    players[2]["is_captain"] = True
    players[7]["is_vice_captain"] = True
    return {
        "observed_at": "2026-09-01T09:03:00+01:00",
        "gameweek": 3,
        "deadline": "2026-09-04T18:30:00+01:00",
        "bank": 0.0,
        "free_transfers": 2,
        "source_label": "Manager-provided screenshot",
        "players": players,
        "shortlist": [{"name": "Target", "club": "X", "position": "MID", "current_price": 5.5, "total_points": 18, "fixture": "Y (H)"}],
        "shortlist_recommendation": "MID 0 → Target",
        "shortlist_reasoning": "Visible shortlist evidence only.",
    }


def test_parse_validates_a_complete_lineup_only_snapshot():
    state = lineup_state.parse(_payload())

    assert len(state.players) == 15
    assert state.players[2].is_captain
    assert state.shortlist[0].name == "Target"


def test_parse_rejects_a_non_starting_captain():
    payload = _payload()
    payload["players"][2]["is_captain"] = False
    payload["players"][1]["is_captain"] = True

    with pytest.raises(lineup_state.LineupStateError, match="captain"):
        lineup_state.parse(payload)


def test_dashboard_uses_the_lineup_only_snapshot_when_no_game_state_exists(tmp_path):
    state_path = tmp_path / "fpl_lineup_state.json"
    state_path.write_text(json.dumps(_payload()))
    with db.connect(tmp_path / "bym.db") as conn:
        result = dashboard.dashboard_payload(conn, state_path)

    assert result["status"] == "LINEUP_ONLY"
    assert result["source"]["kind"] == "MANUAL_SNAPSHOT"
    assert result["source"]["current_player_feed"] is False
    assert result["squad"]["candidate_universe_complete"] is False
    assert result["squad"]["players"][0]["name"] == "DEF 0"
    assert result["shortlist"]["recommendation"] == "MID 0 → Target"
    assert result["lineup_estimate"]["scope"] == "CURRENT SQUAD ONLY"
    assert len(result["lineup_estimate"]["players"]) == 15
    assert result["lineup_estimate"]["reasoning"]["captain"]
    assert any(item["title"] == "Not a live player forecast" for item in result["lineup_estimate"]["downside"])
    assert result["lineup_estimate"]["captain"] in {
        player["name"] for player in _payload()["players"]
    }
