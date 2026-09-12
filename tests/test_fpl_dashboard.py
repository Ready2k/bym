from bym import db
from bym.fpl import dashboard, game_state

from test_fpl_game_state import SEASON, sample_payload


def test_dashboard_says_when_no_squad_has_been_saved(tmp_path):
    with db.connect(tmp_path / "bym.db") as conn:
        result = dashboard.dashboard_payload(conn)

    assert result["status"] == "AWAITING_SQUAD"
    assert result["squad"] is None
    assert result["recommendation"] is None
    assert result["phase2"]["status"] == "AWAITING_HORIZON_INPUT"
    assert result["phase3"]["status"] == "AWAITING_OPPONENT_INPUT"


def test_dashboard_exposes_current_squad_and_its_explicit_market_scope(tmp_path):
    with db.connect(tmp_path / "bym.db") as conn:
        game_state.store_import(conn, sample_payload(candidate_universe_complete=False), SEASON)
        result = dashboard.dashboard_payload(conn)

    assert result["status"] == "SHORTLIST_ONLY"
    assert result["recommendation"] is None
    assert result["squad"]["gameweek"] == 3
    assert result["squad"]["bank"] == 1.5
    assert len(result["squad"]["players"]) == 15
    captain = next(player for player in result["squad"]["players"] if player["is_captain"])
    assert captain["name"] == "Forward One"
    assert all(not player["is_starter"] for player in result["squad"]["players"][-4:])


def test_dashboard_marks_an_attested_complete_universe_as_full_market_ready(tmp_path):
    with db.connect(tmp_path / "bym.db") as conn:
        game_state.store_import(conn, sample_payload(), SEASON)
        result = dashboard.dashboard_payload(conn)

    assert result["status"] == "FULL_MARKET_READY"
    assert result["squad"]["candidate_universe_complete"] is True
