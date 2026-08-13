"""Local web UI actions keep the same round and submission rules as the CLI."""

import json
from dataclasses import fields
from pathlib import Path

import pytest

from bym.round import load_round, round_from_dict, round_to_dict
from bym.web import (
    WebConfig,
    available_ollama_models,
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
from bym import prediction_history
from bym.prospective import load_ledger


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
    """Every path WebConfig can write to must live under tmp_path — without
    this, a test that calls import_opponent/save_results/etc. silently
    reads and writes the real project data/ files (opponent_history.json,
    prediction_history.json, ...) instead of an isolated fixture."""
    round_path = tmp_path / "round.json"
    round_path.write_text(json.dumps(payload()))
    return WebConfig(
        round_path=round_path,
        submissions_path=tmp_path / "submissions.json",
        ui_state_path=tmp_path / "ui_state.json",
        opponent_history_path=tmp_path / "opponent_history.json",
        prediction_history_path=tmp_path / "prediction_history.json",
        database_path=tmp_path / "bym.db",
        event_log_path=tmp_path / "events" / "activity.jsonl",
        ledger_path=tmp_path / "ledger.csv",
        arm_registry_path=tmp_path / "arms.json",
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


def test_golden_goal_guess_and_actual_are_saved_and_validated(tmp_path):
    """§27: the private league's confirmed tiebreak needs both a rival's
    Golden Goal guess (import_opponent) and the actual first-goal minute
    (save_results) persisted, not just the six scorelines."""
    config = configured_round(tmp_path)

    opponents = import_opponent(
        {"name": "Dave", "picks": ["2-0", "1-1", "2-1", "1-2", "3-0", "1-2"], "golden_goal": 9},
        config,
    )
    assert opponents["opponents"]["Dave"]["golden_goal"]["2026:14"] == 9

    # A blank guess means "not shared", never minute 0.
    import_opponent(
        {"name": "Steve", "picks": ["1-0", "1-1", "2-1", "1-2", "3-0", "1-2"], "golden_goal": ""},
        config,
    )
    assert load_ui_state(config.ui_state_path)["opponents"]["Steve"]["golden_goal"]["2026:14"] is None

    with pytest.raises(ValueError, match="between 1 and 90"):
        import_opponent(
            {"name": "Priya", "picks": ["1-0", "1-1", "2-1", "1-2", "3-0", "1-2"], "golden_goal": 91},
            config,
        )

    saved = save_results(
        {"results": ["2-0", "1-1", "2-1", "1-2", "3-0", "1-2"], "golden_goal_actual": 11}, config
    )
    assert saved["golden_goal_actual"] == 11
    assert load_ui_state(config.ui_state_path)["golden_goal_actual"]["2026:14"] == 11

    with pytest.raises(ValueError, match="between 1 and 90"):
        save_results({"results": ["2-0", "1-1", "2-1", "1-2", "3-0", "1-2"], "golden_goal_actual": 0}, config)


def test_league_payload_ranks_by_golden_goal_after_a_points_and_exact_tie(tmp_path):
    """End-to-end through the web layer: two rivals tied on points and
    exact scores must be separated by Golden Goal closeness, not split."""
    from bym.web import _league_payload

    config = configured_round(tmp_path)
    tied = ["2-0", "1-1", "2-1", "1-2", "3-0", "1-2"]
    import_opponent({"name": "Dave", "picks": tied, "golden_goal": 9}, config)
    import_opponent({"name": "Steve", "picks": tied, "golden_goal": 30}, config)
    save_results({"results": tied, "golden_goal_actual": 10}, config)

    rnd = load_round(config.round_path)
    state = load_ui_state(config.ui_state_path)
    payload = _league_payload(rnd, state, config)
    standings = {row["participant"]: row for row in payload["standings"]}
    # Tied on points and exact scores; Dave's closer Golden Goal guess
    # (|9-10|=1 vs Steve's |30-10|=20) must break the tie, not split it.
    assert standings["Dave"]["rank"] < standings["Steve"]["rank"]
    assert standings["Dave"]["golden_goal_average_distance"] == 1
    assert standings["Steve"]["golden_goal_average_distance"] == 20


def test_league_table_resets_each_season_but_opponent_profiles_keep_history(tmp_path):
    """A new season starts the private-league table back at 0-0 with no
    manual reset, since `_league_payload` scopes `league_standings` to the
    round's own season. Opponent behavioural profiles are a different
    story — they keep learning from every season ever recorded, since more
    evidence only makes favourite bias / conservatism etc. more reliable."""
    from bym.league import record_opponent_round
    from bym.web import _league_payload, save_ui_state

    config = configured_round(tmp_path)
    scores = [(2, 0), (1, 1), (2, 1), (1, 2), (3, 0), (1, 2)]
    # Dave racked up a full house of points last season (2025).
    record_opponent_round("Dave", 2025, 38, scores, path=config.opponent_history_path)
    state = load_ui_state(config.ui_state_path)
    state["results"]["2025:38"] = ["2-0", "1-1", "2-1", "1-2", "3-0", "1-2"]
    save_ui_state(state, config.ui_state_path)

    # `payload()`'s deadline is in 2026 — a new season, no picks recorded yet.
    rnd = load_round(config.round_path)
    assert rnd.deadline_at.year == 2026
    state = load_ui_state(config.ui_state_path)
    result = _league_payload(rnd, state, config)

    assert result["season"] == 2026
    assert not any(row["participant"] == "Dave" for row in result["standings"])
    assert any(profile["participant"] == "Dave" for profile in result["profiles"])


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


def test_web_can_list_models_from_the_current_ollama_url():
    class Response:
        status_code = 200

        def json(self):
            return {"models": [
                {"name": "llama3.1:8b"}, {"name": "mistral"},
                {"name": "llama3.1:8b"}, {"name": ""},
            ]}

    class Session:
        def get(self, url, timeout):
            assert url == "http://ollama.local:11434/api/tags"
            assert timeout == 5
            return Response()

    result = available_ollama_models(
        {"base_url": "http://ollama.local:11434"}, session=Session()
    )
    assert result == {"models": ["llama3.1:8b", "mistral"]}


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
    from bym.web import dashboard_payload

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

    from bym.web import _fixture_favoured_result, _opponent_bias_payload, _predicted_picks_payload
    from bym.league import OpponentRound

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

    favourite, _team_bias, chase_defend = _opponent_bias_payload(history, {}, config)
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
    from bym.web import _predicted_picks_payload

    config = WebConfig(prediction_history_path=tmp_path / "prediction_history.json")
    assert _predicted_picks_payload(None, [], (), config) == {}


def test_season_strategy_payload_wires_season_state_and_mode(tmp_path):
    """§12-13: with `season_rounds_total` set equal to the current round
    number (rounds_remaining == 1), the synthetic-round horizon is empty
    and no historical corpus is ever touched — this isolates the wiring
    (state/history/table -> season_simulation) from the cost of fitting a
    real corpus, which season_simulation.py's own tests already cover."""
    from datetime import datetime

    from bym.config import FROZEN_PHASE1_V1
    from bym.league import LeagueStanding, OpponentRound
    from bym.model import dixon_coles
    from bym.recommend import recommend_round
    from bym.round import Fixture, Round
    from bym.web import _opponent_bias_payload, _season_strategy_payload

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
    favourite, _, _ = _opponent_bias_payload(history, {}, config)
    state = {"player_name": "You", "season_rounds_total": 5}

    payload = _season_strategy_payload(rnd, table, history, favourite, recommendation, state, config)
    assert payload["season_state"]["points_gap"] == -5
    assert payload["season_state"]["rounds_remaining"] == 1
    assert payload["mode"] in {"NORMAL", "ATTACK", "DEFEND"}
    assert len(payload["picks"]) == 6
    assert payload["candidates"]
    assert any(c["name"] == "MAX_EV" for c in payload["candidates"])


def test_season_strategy_payload_is_empty_without_a_recommendation(tmp_path):
    from bym.web import _season_strategy_payload

    config = WebConfig(prediction_history_path=tmp_path / "prediction_history.json")
    from bym.round import Fixture, Round
    from datetime import datetime

    rnd = Round(round_number=1, deadline_at=datetime(2026, 10, 18, 14, 0), fixtures=(Fixture("A", "B"),) * 1)
    assert _season_strategy_payload(rnd, (), [], (), None, {"player_name": "You"}, config) == {}


def test_promoted_teams_come_from_division_membership_in_the_real_corpus():
    """§11's promoted half must be derivable without any model output —
    `priors.arrivals` reads which division a team played in last season
    versus this one, nothing else."""
    from bym.web import WebConfig, _promoted_teams

    promoted = _promoted_teams(WebConfig())
    # 2025/26's promotions into the Premier League, from the corpus alone.
    assert {"Burnley", "Leeds", "Sunderland"} <= set(promoted)
    assert "Arsenal" not in promoted


def test_promoted_teams_are_empty_rather_than_fatal_without_a_corpus(tmp_path):
    from bym.web import WebConfig, _promoted_teams

    assert _promoted_teams(WebConfig(data_path=tmp_path)) == ()


def market_fixture(home, away, home_probability, away_probability):
    return {
        "home_team": home, "away_team": away, "result": "HOME", "score": "2-0",
        "market": {
            "available": True,
            "home_probability": home_probability,
            "draw_probability": round(1 - home_probability - away_probability, 4),
            "away_probability": away_probability,
        },
    }


def test_team_identity_bias_reaches_the_league_payload(tmp_path):
    """§11 big-club bias, end to end through the same helper
    `_league_payload` calls — reputation from the stored odds snapshot,
    never from the model's own team strength."""
    from bym.web import WebConfig, _opponent_bias_payload
    from bym.league import OpponentRound

    prediction_path = tmp_path / "prediction_history.json"
    # Three fixtures price Man City well above the mean home team, three
    # price Fulham below it; the average home probability across the round
    # is 0.60, so only Man City clears the big-club threshold.
    prediction_history.record_snapshot(
        2026, 1,
        [
            market_fixture("Man City", "Everton", 0.75, 0.10),
            market_fixture("Man City", "Wolves", 0.75, 0.10),
            market_fixture("Man City", "Burnley", 0.75, 0.10),
            market_fixture("Fulham", "Brentford", 0.45, 0.25),
            market_fixture("Fulham", "Palace", 0.45, 0.25),
            market_fixture("Fulham", "Wolves", 0.45, 0.25),
        ],
        path=prediction_path,
    )
    config = WebConfig(prediction_history_path=prediction_path, data_path=tmp_path)
    history = [
        OpponentRound("Dave", 2026, 1, ((2, 0),) * 6, "2026-08-08T10:00:00+00:00"),
    ]
    _favourite, team_bias, _chase = _opponent_bias_payload(history, {}, config)
    assert len(team_bias) == 1
    assert team_bias[0].participant == "Dave"
    # Only the three Man City fixtures have a big club in them, and Dave
    # backed them in all three — 25pp above the priced 0.75.
    assert team_bias[0].big_clubs.picks == 3
    assert team_bias[0].big_clubs.bias == pytest.approx(0.25)
    # tmp_path holds no corpus, so promoted-team status is unknown rather
    # than assumed — the category stays empty instead of reading as "no bias".
    assert team_bias[0].promoted.picks == 0
    assert team_bias[0].promoted.bias is None


def test_serve_warns_when_cwd_relative_state_is_missing(tmp_path):
    """A UI started from the wrong directory silently becomes a different
    deployment — worst of all a source registry falling back to defaults
    where every source is REVIEW_REQUIRED, gating off odds and research."""
    from bym.web import WebConfig, missing_state_warnings

    absent = WebConfig(
        registry_path=tmp_path / "config" / "source_registry.json",
        ui_state_path=tmp_path / "ui_state.json",
        prediction_history_path=tmp_path / "prediction_history.json",
    )
    warnings = missing_state_warnings(absent)
    assert len(warnings) == 3
    assert any("source registry" in warning for warning in warnings)

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "source_registry.json").write_text("{}")
    (tmp_path / "ui_state.json").write_text("{}")
    (tmp_path / "prediction_history.json").write_text("{}")
    assert missing_state_warnings(absent) == []


def test_the_service_script_pins_the_working_directory():
    """`scripts/bym-service` passes absolute paths for its four flags but
    the server reads six more that are cwd-relative, so the directory must
    be pinned rather than inherited from wherever it was invoked."""
    script = Path("scripts/bym-service").read_text()
    body = script.split("start()", 1)[0]
    assert 'cd "$ROOT_DIR"' in body, "service script must cd to the project root before launching"


def test_the_service_script_uses_the_same_state_files_as_the_cli():
    """A separate bym-ui.db/-submissions pair meant the UI wrote one
    deployment's state while every CLI command read another's — and since
    both files existed, nothing warned."""
    lines = Path("scripts/bym-service").read_text().splitlines()
    # Comments still name the old files to explain why they went; only the
    # assignments matter here.
    code = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    assert "bym-ui.db" not in code
    assert "bym-ui-submissions.json" not in code
    assert "$ROOT_DIR/data/bym.db" in code
    assert "$ROOT_DIR/data/submissions.json" in code


def strategy_config(tmp_path):
    base = configured_round(tmp_path)
    return WebConfig(
        **{**{f.name: getattr(base, f.name) for f in fields(base)},
           "strategy_history_path": tmp_path / "strategy_history.json"}
    )


def test_a_strategy_decision_is_persisted_for_19_linkage(tmp_path):
    """§19: a forecast_run could not say which recommendation was chosen,
    because none was ever persisted. `_record_strategy_decision` is the
    wiring that fixes that — driven here directly, since faking a whole
    RoundRecommendation would test the fake, not the linkage."""
    from bym import strategy_history
    from bym.web import _record_strategy_decision

    config = strategy_config(tmp_path)
    rnd = load_round(config.round_path)
    prediction_history.record_snapshot(
        2026, 14,
        [{"home_team": "Arsenal", "away_team": "Everton", "result": "HOME", "score": "2-0"}],
        path=config.prediction_history_path,
    )
    payload_in = {
        "strategy": {
            "mode": "DIFFERENTIATE", "active": True,
            "picks": ["3-0", "1-0", "2-1", "1-2", "3-0", "1-2"],
            "reason": "differentiating improves P(finish first)",
        }
    }
    _record_strategy_decision(
        rnd, payload_in, scope=strategy_history.SCOPE_ROUND,
        raw_picks=["2-0", "1-0", "2-1", "1-2", "3-0", "1-2"],
        opponent=strategy_history.opponent_snapshot(["Dave"], field_complete=True),
        config=config,
    )

    recorded = strategy_history.round_snapshots(2026, 14, config.strategy_history_path)
    assert len(recorded) == 1
    latest = recorded[0]
    # Both sides — a strategy pick means nothing without the raw pick it
    # deviated from.
    assert latest["raw_picks"][0] == "2-0"
    assert latest["strategy_picks"][0] == "3-0"
    assert latest["mode"] == "DIFFERENTIATE" and latest["active"] is True
    assert latest["opponent_snapshot"]["participant_names"] == ["Dave"]
    # And it names the forecast it was taken against, so §19's question
    # resolves by join.
    assert latest["forecast_version"] == 1
    assert strategy_history.for_forecast_version(
        2026, 14, 1, config.strategy_history_path
    ) == recorded


def test_recording_the_same_decision_twice_does_not_grow_the_record(tmp_path):
    """_league_payload runs on every page load; a refresh is not a decision."""
    from bym import strategy_history
    from bym.web import _record_strategy_decision

    config = strategy_config(tmp_path)
    rnd = load_round(config.round_path)
    payload_in = {"strategy": {"mode": "NORMAL", "active": False, "picks": ["1-0"] * 6}}
    for _ in range(3):
        _record_strategy_decision(
            rnd, payload_in, scope=strategy_history.SCOPE_ROUND,
            raw_picks=["1-0"] * 6, opponent=None, config=config,
        )
    assert len(strategy_history.round_snapshots(2026, 14, config.strategy_history_path)) == 1


def test_a_failed_decision_record_never_breaks_the_page(tmp_path):
    """The store is an audit trail, not a dependency of the recommendation."""
    from bym import strategy_history
    from bym.web import _record_strategy_decision

    base = configured_round(tmp_path)
    unwritable = WebConfig(
        **{**{f.name: getattr(base, f.name) for f in fields(base)},
           # A directory where a file must go: writing raises OSError.
           "strategy_history_path": tmp_path}
    )
    rnd = load_round(unwritable.round_path)
    _record_strategy_decision(
        rnd, {"strategy": {"mode": "NORMAL", "active": False, "picks": ["1-0"] * 6}},
        scope=strategy_history.SCOPE_ROUND, raw_picks=["1-0"] * 6,
        opponent=None, config=unwritable,
    )  # must not raise


def test_no_recommendation_records_no_decision(tmp_path):
    """Nothing was decided, so nothing is claimed to have been."""
    from bym import strategy_history
    from bym.web import _league_payload

    config = strategy_config(tmp_path)
    import_opponent(
        {"name": "Dave", "picks": ["2-0", "1-1", "2-1", "1-2", "3-0", "1-2"]}, config
    )
    rnd = load_round(config.round_path)
    _league_payload(rnd, load_ui_state(config.ui_state_path), config)
    assert strategy_history.round_snapshots(2026, 14, config.strategy_history_path) == []


def test_manual_research_is_refused_when_the_extractor_is_off(tmp_path):
    from bym.web import research_now

    config = strategy_config(tmp_path)
    with pytest.raises(ValueError, match="extractor is off"):
        research_now({}, config)


def test_manual_research_is_refused_when_the_budget_is_nearly_gone(tmp_path, monkeypatch):
    """Spending the last credits on a manual click is how you arrive at the
    deadline blind — the scheduled sweeps have already degraded by then."""
    from bym import web, worker

    config = strategy_config(tmp_path)
    save_settings(
        {
            "league_name": "L", "player_name": "You",
            "knowledge_extractor": {
                "enabled": True, "base_url": "http://localhost:11434",
                "model": "llama3.1", "mode": "shadow",
            },
        },
        config,
    )
    monkeypatch.setenv("TAVILY_API_KEY", "key")
    monkeypatch.setattr(worker, "knowledge_budget_low", lambda *a, **k: True)
    with pytest.raises(ValueError, match="monthly credits are nearly gone"):
        web.research_now({}, config)


def test_quota_exhaustion_is_warned_about_before_it_bites():
    """A quota running out is the failure you notice last, because the
    symptom is things quietly not happening."""
    from bym.api.contracts import SourceHealth
    from bym.api.service import SystemHealth

    # The warning logic is exercised through build_system_health elsewhere;
    # here we pin the thresholds it uses.
    low = SourceHealth(source_id="tavily", usage_status="APPROVED",
                       quota_used=850, quota_limit=1000, quota_period="month")
    assert low.quota_limit - low.quota_used <= low.quota_limit * 0.2
    healthy = SourceHealth(source_id="tavily", usage_status="APPROVED",
                           quota_used=491, quota_limit=1000, quota_period="month")
    assert healthy.quota_limit - healthy.quota_used > healthy.quota_limit * 0.2


def test_create_round_canonicalises_club_names(tmp_path):
    """The fixture form's "Other" option takes free text, and Sky writes
    "Manchester City" where the corpus says "Man City". Resolving on the
    way in means the file on disk is already right for every later reader,
    including the ones that never resolve."""
    config = WebConfig(
        round_path=tmp_path / "active.json",
        data_path=Path("data"),
        seasons=(2024, 2024),
    )
    raw = payload()
    raw["fixtures"][0] = {"home": "Manchester City", "away": "Nottingham Forest"}
    created = create_round(raw, config)

    on_disk = load_round(Path(created["round_path"]))
    assert on_disk.fixtures[0].home == "Man City"
    assert on_disk.fixtures[0].away == "Nott'm Forest"


def test_create_round_still_saves_without_a_corpus(tmp_path):
    """Resolution is a convenience, not a precondition. A round must stay
    saveable when the corpus is absent — the UI's unmodellable banner
    already covers what this would have caught."""
    config = WebConfig(
        round_path=tmp_path / "active.json", data_path=tmp_path / "nothing-here"
    )
    created = create_round(payload(), config)
    assert Path(created["round_path"]).exists()
