from __future__ import annotations

import json

from bym.web import (
    WebConfig,
    fpl_status_payload,
    review_fpl_decision,
    save_fpl_lineup,
    save_fpl_phase2_input,
    save_fpl_phase3_input,
)

from test_fpl_lineup_state import _payload as lineup_payload
from test_fpl_phase2 import payload as phase2_payload
from test_fpl_phase3 import payload as phase3_payload


def _config(tmp_path) -> WebConfig:
    return WebConfig(
        database_path=tmp_path / "bym.db",
        fpl_lineup_state_path=tmp_path / "fpl_lineup_state.json",
        fpl_phase2_input_path=tmp_path / "fpl_phase2_input.json",
        fpl_phase3_input_path=tmp_path / "fpl_phase3_input.json",
        fpl_assisted_actions_path=tmp_path / "fpl_assisted_actions.json",
        fpl_decision_history_path=tmp_path / "fpl_decision_history.json",
    )


def test_browser_workflow_saves_validated_local_inputs_and_returns_their_results(tmp_path):
    config = _config(tmp_path)
    lineup = lineup_payload()
    lineup["deadline"] = "2099-09-04T18:30:00+01:00"

    assert save_fpl_lineup(lineup, config)["players"] == 15
    phase2 = save_fpl_phase2_input(phase2_payload(), config)
    phase3 = save_fpl_phase3_input(phase3_payload(), config)

    assert phase2["plan"]["scope"] == "SHORTLIST_ONLY_HORIZON"
    assert phase2["plan"]["alternatives"]
    assert phase3["plan"]["scope"] == "PRE_DEADLINE_NAMED_OPTIONS_ONLY"
    assert json.loads(config.fpl_phase2_input_path.read_text())["gameweeks"] == [3, 4]
    assert json.loads(config.fpl_phase3_input_path.read_text())["rivals"][0]["name"] == "Mate"

    dashboard = fpl_status_payload(config)["dashboard"]
    assert dashboard["squad"]["gameweek"] == 3
    assert dashboard["phase2"]["input"]["shortlist"][0]["name"] == "Target FWD"
    assert dashboard["phase3"]["plan"]["recommended_option"] == "Named upgrade"


def test_browser_review_endpoint_marks_the_exact_current_decision(tmp_path):
    config = _config(tmp_path)
    lineup = lineup_payload()
    lineup["deadline"] = "2099-09-04T18:30:00+01:00"
    save_fpl_lineup(lineup, config)

    recorded = review_fpl_decision({}, config)
    dashboard = fpl_status_payload(config)["dashboard"]

    assert recorded["status"] == "REVIEWED"
    assert dashboard["review"]["current_reviewed_at"] == recorded["review"]["reviewed_at"]
    assert dashboard["review"]["reviews"][0]["source"]["kind"] == "MANUAL_SNAPSHOT"
