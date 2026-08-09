"""Local web UI actions keep the same round and submission rules as the CLI."""

import json
from pathlib import Path

import pytest

from super6.round import load_round, round_from_dict, round_to_dict
from super6.web import (
    WebConfig,
    create_round,
    cup_prediction,
    import_opponent,
    load_ui_state,
    mark_round_entered,
    ollama_status,
    extractor_settings_for_ui,
    save_pick_overrides,
    save_results,
    save_settings,
    web_extractor_config,
)
from super6 import prediction_history
from super6.prospective import load_ledger


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


def test_ollama_is_not_probed_when_the_optional_extractor_is_disabled(monkeypatch):
    monkeypatch.delenv("CLAIM_EXTRACTOR", raising=False)
    health, config = ollama_status()
    assert health is None
    assert config.kind == "null"


def test_ollama_can_be_configured_and_disabled_from_web_settings(tmp_path):
    config = configured_round(tmp_path)
    saved = save_settings(
        {
            "knowledge_extractor": {
                "enabled": True,
                "base_url": "http://ollama.local:11434/",
                "model": "llama3.1:8b",
                "mode": "shadow",
            }
        },
        config,
    )
    extractor = web_extractor_config(config)
    assert extractor.kind == "ollama"
    assert extractor.base_url == "http://ollama.local:11434"
    assert extractor.model == "llama3.1:8b"
    assert saved["settings"]["knowledge_extractor"]["enabled"] is True

    save_settings(
        {"knowledge_extractor": {
            "enabled": False,
            "base_url": "http://ollama.local:11434",
            "model": "llama3.1:8b",
            "mode": "shadow",
        }},
        config,
    )
    assert web_extractor_config(config).kind == "null"


def test_ollama_web_settings_reject_credentials_in_the_url(tmp_path):
    with pytest.raises(ValueError, match="without credentials"):
        save_settings(
            {"knowledge_extractor": {
                "enabled": True,
                "base_url": "http://user:secret@ollama.local:11434",
                "model": "llama3.1",
                "mode": "shadow",
            }},
            configured_round(tmp_path),
        )


def test_ollama_web_settings_never_expose_environment_url_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIM_EXTRACTOR", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://user:secret@ollama.local:11434")
    settings = extractor_settings_for_ui(configured_round(tmp_path))
    assert settings["base_url"] == "http://ollama.local:11434"


def test_manual_entry_freezes_prospective_arms_and_results_score_them(tmp_path):
    config = configured_round(tmp_path)
    config = WebConfig(
        **{**config.__dict__, "ledger_path": tmp_path / "ledger.csv", "arm_registry_path": tmp_path / "arms.json", "prediction_history_path": tmp_path / "history.json"}
    )
    picks = ["2-0", "1-0", "2-1", "1-2", "3-0", "1-2"]
    prediction_history.capture_round_summary(
        2026,
        {
            "round_number": 14,
            "model_version": "PHASE1_MODEL_V1",
            "fixtures": [
                {"home_team": fixture["home"], "away_team": fixture["away"], "pick": pick}
                for fixture, pick in zip(payload()["fixtures"], picks)
            ],
        },
        path=config.prediction_history_path,
    )
    mark_round_entered({"picks": picks}, config)
    save_results({"results": ["2-0", "1-1", None, "1-2", "3-0", "1-2"]}, config)
    ledger = load_ledger(config.ledger_path)
    assert set(ledger["arm"]) == {"PHASE1_MODEL_V1", "SUBMITTED"}
    assert set(ledger["status"].dropna()) == {"OK", "VOID"}


def test_dashboard_marks_a_round_fixture_outside_the_corpus_as_unmodellable(tmp_path):
    """The live payload — not just the contract — has to carry the verdict."""
    from super6.web import dashboard_payload

    round_payload = payload()
    round_payload["fixtures"][0] = {"home": "Arsenal", "away": "Oldham"}
    round_path = tmp_path / "round.json"
    round_path.write_text(json.dumps(round_payload))
    config = WebConfig(
        round_path=round_path,
        database_path=tmp_path / "s.db",
        submissions_path=tmp_path / "submissions.json",
        event_log_path=tmp_path / "activity.jsonl",
        ui_state_path=tmp_path / "ui.json",
        prediction_history_path=tmp_path / "history.json",
    )
    fixtures = dashboard_payload(config)["round"]["fixtures"]
    assert fixtures[0]["modellability"]["modellable"] is False
    assert fixtures[0]["modellability"]["unknown_teams"] == ["Oldham"]
    assert all(f["modellability"]["modellable"] for f in fixtures[1:])


CUP_CONFIG = WebConfig(data_path=Path("data"), seasons=(2019, 2025), divisions=("E0", "E1"))


def test_cup_prediction_refuses_to_recommend_a_team_outside_the_corpus():
    forecast = cup_prediction({"home": "Stoke", "away": "Oldham"}, CUP_CONFIG)
    modellability = forecast["fixture"]["modellability"]
    assert forecast["fixture"]["home_team"] == "Stoke"
    assert modellability["modellable"] is False
    assert modellability["unknown_teams"] == ["Oldham"]
    assert modellability["corpus"] == "E0+E1"
    assert forecast["model_version"] == "PHASE1_MODEL_V1"
    # The raw output stays available for audit; the verdict is what gates the UI.
    assert forecast["fixture"]["explanation"]["selected_pick"]["expected_points"] > 0


def test_cup_prediction_marks_a_two_known_team_tie_modellable():
    forecast = cup_prediction({"home": "Stoke", "away": "Millwall"}, CUP_CONFIG)
    assert forecast["fixture"]["modellability"]["modellable"] is True
    assert forecast["fixture"]["modellability"]["unknown_teams"] == []
    assert forecast["extended"] is False


def test_cup_prediction_extended_corpus_models_lower_division_teams_but_drops_the_frozen_label():
    forecast = cup_prediction(
        {"home": "Stoke", "away": "Oldham", "extended": True}, CUP_CONFIG
    )
    assert forecast["fixture"]["modellability"]["modellable"] is True
    assert forecast["extended"] is True
    assert forecast["corpus"] == "E0+E1+E2+E3+EC"
    assert forecast["model_version"] != "PHASE1_MODEL_V1"
    # Only the seasons whose lower-division files exist may be used, and the
    # response has to say which ones those were.
    assert forecast["seasons"][0] >= 2019


def test_cup_prediction_rejects_a_fixture_against_itself():
    with pytest.raises(ValueError, match="two different teams"):
        cup_prediction({"home": "Stoke", "away": "stoke"}, CUP_CONFIG)


def test_opponent_bias_and_predicted_picks_are_wired_into_the_league_view(tmp_path):
    """§11/§49.3: favourite bias, chase/defend and a predicted-pick preview
    for the current round should be computable end to end from recorded
    opponent picks plus our own stored forecast snapshots — not just at the
    library level, but through the same web.py helpers `_league_payload`
    actually calls."""
    from types import SimpleNamespace

    from super6.web import _fixture_favoured_result, _opponent_bias_payload, _predicted_picks_payload
    from super6.league import OpponentRound

    prediction_path = tmp_path / "prediction_history.json"
    prediction_history.record_snapshot(
        2026, 1,
        [
            {"home_team": "Arsenal", "away_team": "Everton", "result": "HOME", "score": "2-0"},
            {"home_team": "Chelsea", "away_team": "Newcastle", "result": "HOME", "score": "1-0"},
            {"home_team": "Fulham", "away_team": "Brentford", "result": "DRAW", "score": "1-1"},
            {"home_team": "Leeds", "away_team": "Tottenham", "result": "AWAY", "score": "1-2"},
            {"home_team": "Man City", "away_team": "Bournemouth", "result": "HOME", "score": "3-0"},
            {"home_team": "West Ham", "away_team": "Aston Villa", "result": "AWAY", "score": "1-2"},
        ],
        path=prediction_path,
    )
    config = WebConfig(prediction_history_path=prediction_path)
    history = [
        OpponentRound(
            "Dave", 2026, 1,
            # Fixture 3 is forecast AWAY; Dave picks a home win there instead
            # — one deliberate mismatch against five matches.
            ((2, 0), (2, 1), (1, 1), (1, 0), (3, 0), (0, 2)),
            "2026-08-08T10:00:00+00:00",
        ),
    ]

    favourite, chase_defend = _opponent_bias_payload(history, {}, config)
    assert len(favourite) == 1
    dave = favourite[0]
    assert dave.participant == "Dave"
    assert dave.sample_size == 6
    assert dave.favourite_rate == pytest.approx(5 / 6, abs=1e-4)
    assert chase_defend == ()  # a single round has no earlier round to compare against

    recommendation = SimpleNamespace(
        fixtures=[
            SimpleNamespace(home="Arsenal", away="Everton", pick_home=2, pick_away=0),
            SimpleNamespace(home="Chelsea", away="Newcastle", pick_home=1, pick_away=0),
        ]
    )
    predicted = _predicted_picks_payload(recommendation, history, favourite, config)
    assert list(predicted) == ["Dave"]
    assert len(predicted["Dave"]) == 2
    for fixture in predicted["Dave"]:
        assert fixture["predicted"]  # at least one candidate scoreline surfaced
        assert sum(item["probability"] for item in fixture["predicted"]) <= 1.0001

    assert _fixture_favoured_result(recommendation.fixtures[0]) == "HOME"


def test_predicted_picks_is_empty_without_a_recommendation_or_history(tmp_path):
    from super6.web import _predicted_picks_payload

    config = WebConfig(prediction_history_path=tmp_path / "prediction_history.json")
    assert _predicted_picks_payload(None, [], (), config) == {}


def test_season_strategy_payload_wires_season_state_and_mode(tmp_path):
    """§12-13: with `season_rounds_total` set equal to the current round
    number (rounds_remaining == 1), the synthetic-round horizon is empty
    and no historical corpus is ever touched — this isolates the wiring
    (state/history/table -> season_simulation) from the cost of fitting a
    real corpus, which season_simulation.py's own tests already cover."""
    from datetime import datetime

    from super6.config import FROZEN_PHASE1_V1
    from super6.league import LeagueStanding, OpponentRound
    from super6.model import dixon_coles
    from super6.recommend import recommend_round
    from super6.round import Fixture, Round
    from super6.web import _opponent_bias_payload, _season_strategy_payload

    from test_dixon_coles import synthetic_matches

    params = dixon_coles.fit(synthetic_matches(), xi=0.0)
    rnd = Round(
        round_number=5,
        deadline_at=datetime(2026, 10, 18, 14, 0),
        fixtures=(
            Fixture("Strong", "Weak"), Fixture("Weak", "Strong"),
            Fixture("Middling A", "Middling B"), Fixture("Middling B", "Middling A"),
            Fixture("Strong", "Middling A"), Fixture("Weak", "Middling B"),
        ),
    )
    recommendation = recommend_round(params, rnd, FROZEN_PHASE1_V1)
    max_ev = tuple((f.pick_home, f.pick_away) for f in recommendation.fixtures)

    history = [OpponentRound("Dave", 2026, 5, max_ev, "t")]
    table = (
        LeagueStanding("You", 2, 20, 2, 6, 1),
        LeagueStanding("Dave", 1, 25, 3, 6, 1),
    )
    config = WebConfig(prediction_history_path=tmp_path / "prediction_history.json")
    favourite, _ = _opponent_bias_payload(history, {}, config)
    state = {"player_name": "You", "season_rounds_total": 5}

    payload = _season_strategy_payload(rnd, table, history, favourite, recommendation, state, config)
    assert payload["season_state"]["points_gap"] == -5
    assert payload["season_state"]["rounds_remaining"] == 1
    assert payload["mode"] in {"NORMAL", "ATTACK", "DEFEND"}
    assert len(payload["picks"]) == 6
    assert payload["candidates"]
    assert any(c["name"] == "MAX_EV" for c in payload["candidates"])


def test_season_strategy_payload_is_empty_without_a_recommendation(tmp_path):
    from super6.web import _season_strategy_payload

    config = WebConfig(prediction_history_path=tmp_path / "prediction_history.json")
    from super6.round import Fixture, Round
    from datetime import datetime

    rnd = Round(round_number=1, deadline_at=datetime(2026, 10, 18, 14, 0), fixtures=(Fixture("A", "B"),) * 1)
    assert _season_strategy_payload(rnd, (), [], (), None, {"player_name": "You"}, config) == {}
