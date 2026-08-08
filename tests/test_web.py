"""Local web UI actions keep the same round and submission rules as the CLI."""

import json
from pathlib import Path

import pytest

from super6.round import load_round, round_from_dict, round_to_dict
from super6.web import (
    WebConfig,
    create_round,
    import_opponent,
    load_ui_state,
    mark_round_entered,
    save_pick_overrides,
    save_results,
    save_settings,
)


def payload() -> dict:
    return {
        "round_number": 14,
        "deadline_at": "2026-10-18T14:00:00+01:00",
        "fixtures": [
            {"home": "Arsenal", "away": "Everton"},
            {"home": "Chelsea", "away": "Newcastle"},
            {"home": "Fulham", "away": "Brentford"},
            {"home": "Leeds", "away": "Tottenham"},
            {"home": "Man City", "away": "Bournemouth"},
            {"home": "West Ham", "away": "Aston Villa"},
        ],
    }


def test_round_payload_round_trips_without_a_web_specific_shape():
    rnd = round_from_dict(payload())
    saved = round_to_dict(rnd)
    assert saved["round_number"] == 14
    assert len(saved["fixtures"]) == 6
    assert saved["deadline_at"].endswith("+01:00")


def test_create_round_validates_before_persisting(tmp_path):
    config = WebConfig(round_path=tmp_path / "active.json")
    created = create_round(payload(), config)
    path = Path(created["round_path"])
    assert path.name == "active.json"
    assert load_round(path).round_number == 14

    invalid = payload()
    invalid["fixtures"] = invalid["fixtures"][:5]
    with pytest.raises(ValueError, match="exactly 6"):
        create_round(invalid, config)


def test_mark_entered_records_manual_confirmation_only(tmp_path):
    round_path = tmp_path / "round.json"
    round_path.write_text(json.dumps(payload()))
    config = WebConfig(
        round_path=round_path,
        submissions_path=tmp_path / "submissions.json",
    )
    result = mark_round_entered(
        {"picks": ["2-0", "1-0", "2-1", "1-2", "3-0", "1-2"], "golden_goal": 9},
        config,
    )
    assert result["submission"]["state"] == "MARKED_AS_SUBMITTED"
    assert result["submission"]["submitted_golden_goal"] == 9
    assert "sky" not in str(result).lower()


def test_mark_entered_requires_six_scorelines(tmp_path):
    round_path = tmp_path / "round.json"
    round_path.write_text(json.dumps(payload()))
    config = WebConfig(round_path=round_path, submissions_path=tmp_path / "submissions.json")
    with pytest.raises(ValueError, match="Exactly six"):
        mark_round_entered({"picks": ["2-0"]}, config)


def configured_round(tmp_path):
    round_path = tmp_path / "round.json"
    round_path.write_text(json.dumps(payload()))
    return WebConfig(
        round_path=round_path,
        submissions_path=tmp_path / "submissions.json",
        ui_state_path=tmp_path / "ui_state.json",
    )


def test_overrides_are_saved_separately_from_the_model_pick(tmp_path):
    config = configured_round(tmp_path)
    result = save_pick_overrides(
        {
            "picks": ["2-1", "1-0", "2-1", "1-2", "3-0", "1-2"],
            "recommendations": ["2-0", "1-0", "2-1", "1-2", "3-0", "1-2"],
        },
        config,
    )
    assert result["overrides"] == {"0": "2-1"}
    assert load_ui_state(config.ui_state_path)["overrides"]["2026:14"]["0"] == "2-1"


def test_settings_opponents_and_results_use_local_explicit_state(tmp_path):
    config = configured_round(tmp_path)
    settings = save_settings(
        {
            "league_name": "Friday Five",
            "player_name": "James",
            "notifications": {"market_movement": False},
            "budget": {"target_monthly": 12, "hard_ceiling": 30},
        },
        config,
    )
    assert settings["settings"]["player_name"] == "James"
    assert settings["settings"]["notifications"]["market_movement"] is False

    opponents = import_opponent(
        {"name": "Dave", "picks": ["2-0", "1-1", "2-1", "1-2", "3-0", "1-2"]},
        config,
    )
    assert opponents["opponents"]["Dave"]["rounds"]["2026:14"][0] == "2-0"

    saved = save_results(
        {"results": ["2-0", "1-1", None, "1-2", "3-0", "1-2"]}, config
    )
    assert saved["results"][2] is None
