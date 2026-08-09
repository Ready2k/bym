"""Local decision-first web UI for Super 6 (UIUX.md Phase 1).

The UI deliberately talks only to the public read-model contracts.  It is
small enough to run from a checkout without a frontend build chain, while
keeping the HTTP boundary that a hosted UI can later retain.
"""

from __future__ import annotations

import json
import mimetypes
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import db, events, submission
from .api import service
from .config import FROZEN_PHASE1_V1
from .data.football_data import load_matches
from .engine import run_engine
from .round import Round, load_round, round_from_dict, round_to_dict
from .sources.registry import load_registry


ASSET_DIR = Path(__file__).with_name("web_assets")
DEFAULT_ROUND = Path("rounds/example_round.json")


@dataclass(frozen=True)
class WebConfig:
    round_path: Path = DEFAULT_ROUND
    data_path: Path = Path("data")
    database_path: Path = Path("data/super6.db")
    submissions_path: Path = Path("data/submissions.json")
    registry_path: Path = Path("config/source_registry.json")
    event_log_path: Path = Path("data/events/activity.jsonl")
    ledger_path: Path = Path("results/prospective_ledger.csv")
    ui_state_path: Path = Path("data/ui_state.json")
    prediction_history_path: Path = Path("data/prediction_history.json")
    opponent_history_path: Path = Path("data/opponent_history.json")
    arm_registry_path: Path = Path("results/prospective_arms.json")
    seasons: tuple[int, int] = (2019, 2025)
    divisions: tuple[str, ...] = ("E0", "E1")


def _fit_router(matches):
    """Use the frozen production configuration without making web depend on CLI."""
    from .cli import _fit_router

    return _fit_router(matches, FROZEN_PHASE1_V1.xi, FROZEN_PHASE1_V1)


def _season_for(rnd: Round) -> int:
    return rnd.deadline_at.year if rnd.deadline_at.year else datetime.now().year


def _known_teams(matches) -> set[str]:
    return set(matches["home_team"]) | set(matches["away_team"])


def _corpus_label(divisions: tuple[str, ...]) -> str:
    return "+".join(divisions)


DEFAULT_UI_STATE = {
    "league_name": "My Super 6 League",
    "player_name": "You",
    "notifications": {
        "prediction_changes": True,
        "strategy_changes": True,
        "lineup_impact": True,
        "market_movement": True,
        "deadline_reminder": True,
        "informational": False,
    },
    "notification_delivery": {"in_app": True, "email": False},
    "notification_email": "",
    "budget": {"target_monthly": 10, "hard_ceiling": 40},
    "overrides": {},
    "opponents": {},
    "results": {},
}


def load_ui_state(path: Path) -> dict:
    """Read user-controlled UI state; retain defaults across upgrades."""
    state = json.loads(path.read_text()) if path.exists() else {}
    merged = {**DEFAULT_UI_STATE, **state}
    merged["notifications"] = {
        **DEFAULT_UI_STATE["notifications"], **state.get("notifications", {})
    }
    merged["budget"] = {**DEFAULT_UI_STATE["budget"], **state.get("budget", {})}
    merged["notification_delivery"] = {
        **DEFAULT_UI_STATE["notification_delivery"],
        **state.get("notification_delivery", {}),
    }
    merged["notification_email"] = str(state.get("notification_email", ""))
    for key in ("overrides", "opponents", "results"):
        merged[key] = dict(state.get(key, {}))
    return merged


def save_ui_state(state: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def _round_key(rnd: Round) -> str:
    return f"{_season_for(rnd)}:{rnd.round_number}"


def _scoreline(value: str) -> tuple[int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"\d+-\d+", value):
        raise ValueError("Scores must use H-A format, for example 2-0.")
    home, away = (int(part) for part in value.split("-"))
    if home > 20 or away > 20:
        raise ValueError("Scores must be between 0 and 20.")
    return home, away


def ollama_status() -> tuple[object | None, object]:
    """Return configured extractor health without probing an unused host.

    A default/null extractor has no Ollama dependency at all. Calling a
    network probe in that state would both slow normal page loads and turn
    an intentionally unused optional component into a spurious warning.
    """
    from .knowledge.config import ExtractorKind, from_env

    config = from_env()
    if config.kind != ExtractorKind.OLLAMA.value:
        return None, config
    from .knowledge.ollama import check_health

    return check_health(config, timeout=3), config


def _append_forecast_change_events(snapshot: dict, rnd: Round, config: WebConfig) -> list[events.Event]:
    """Turn a newly persisted forecast diff into the material activity feed."""
    if not snapshot.get("changes"):
        return []
    created: list[events.Event] = []
    for change in snapshot["changes"]:
        if change["kind"] != "CHANGED":
            continue
        fields = {field["field"]: field for field in change["fields"]}
        home, away = change.get("home_team"), change.get("away_team")
        if "score" in fields:
            score = fields["score"]
            created.append(events.make_event(
                events.EventType.PREDICTION_CHANGED,
                events.Severity.IMPORTANT,
                f"Recommendation changed {score['was']} → {score['now']}.",
                rnd.round_number, home, away,
                detail={"changes": change["fields"], "snapshot_at": snapshot["recorded_at"]},
            ))
        elif "confidence" in fields:
            confidence = fields["confidence"]
            created.append(events.make_event(
                events.EventType.CONFIDENCE_CHANGED,
                events.Severity.INFORMATIONAL,
                f"Confidence changed {confidence['was']} → {confidence['now']}.",
                rnd.round_number, home, away,
                detail={"changes": change["fields"], "snapshot_at": snapshot["recorded_at"]},
            ))
    events.append(created, config.event_log_path)
    state = load_ui_state(config.ui_state_path)
    if created and state["notification_delivery"]["email"] and state["notification_email"]:
        from .notifications import email_delivery_status, send_email_alert
        from smtplib import SMTPException

        if email_delivery_status(True, state["notification_email"]) == "READY":
            try:
                body = "\n\n".join(
                    f"{event.fixture}\n{event.summary}\nRound {rnd.round_number}".strip()
                    for event in created
                )
                send_email_alert(
                    destination=state["notification_email"],
                    subject=f"Super 6 — Round {rnd.round_number} recommendation changed",
                    body=body,
                )
            except (OSError, RuntimeError, ValueError, SMTPException) as exc:
                events.append([events.make_event(
                    events.EventType.POLL_FAILED,
                    events.Severity.INFORMATIONAL,
                    f"Email alert was not delivered: {exc}", rnd.round_number,
                )], config.event_log_path)
    return created


def _emit_deadline_reminders(rnd: Round, submission_state: str, config: WebConfig) -> None:
    """Record any newly crossed deadline threshold for this round.

    Fires on page load, which alone only helps someone already looking —
    `super6 watch` covers the rest. Each threshold is emitted once, guarded
    by what is already in the append-only log, so refreshing does not
    manufacture reminders.
    """
    from . import reminders

    existing = events.load(config.event_log_path)
    created = reminders.build_events(
        rnd.deadline_at, datetime.now(timezone.utc), submission_state,
        existing, rnd.round_number,
    )
    if not created:
        return
    events.append(created, config.event_log_path)
    state = load_ui_state(config.ui_state_path)
    if not (
        state["notifications"].get("deadline_reminder")
        and state["notification_delivery"]["email"]
        and state["notification_email"]
    ):
        return
    from smtplib import SMTPException

    from .notifications import email_delivery_status, send_email_alert

    if email_delivery_status(True, state["notification_email"]) != "READY":
        return
    try:
        send_email_alert(
            destination=state["notification_email"],
            subject=f"Super 6 — Round {rnd.round_number} deadline approaching",
            body="\n\n".join(event.summary for event in created),
        )
    except (OSError, RuntimeError, ValueError, SMTPException) as exc:
        events.append([events.make_event(
            events.EventType.POLL_FAILED, events.Severity.INFORMATIONAL,
            f"Deadline reminder email was not delivered: {exc}", rnd.round_number,
        )], config.event_log_path)


def _fixture_history_payload(season: int, summary: dict, config: WebConfig) -> None:
    """Attach compact, human-readable audit versions to each fixture."""
    from .prediction_history import fixture_history

    for fixture in summary["fixtures"]:
        versions = fixture_history(
            season,
            summary["round_number"],
            f"{fixture['home_team']}::{fixture['away_team']}",
            path=config.prediction_history_path,
        )
        fixture["history"] = [
            {
                "version": version["version"],
                "recorded_at": version["recorded_at"],
                "pick": version["fixture"]["score"],
                "confidence": version["fixture"]["confidence"],
                "expected_points": version["fixture"]["expected_points"],
                "model_version": version["model_version"],
                "changes": version["changes"],
                "reason": (
                    "Initial model forecast"
                    if not version["changes"]
                    else "; ".join(
                        f"{field['field'].replace('_', ' ')}: {field['was']} → {field['now']}"
                        for change in version["changes"]
                        for field in change["fields"]
                    )
                ),
            }
            for version in versions
        ]


def dashboard_payload(config: WebConfig) -> dict:
    return _dashboard(config)[0]


def _dashboard(config: WebConfig):
    """Build a fresh decision payload, and hand back the engine result too.

    The strategy layer needs the recommendation's score matrices to
    simulate from, and re-running the engine to get them would double the
    cost of every page load.
    """
    rnd = load_round(config.round_path)
    matches = load_matches(
        config.data_path,
        list(range(config.seasons[0], config.seasons[1] + 1)),
        config.divisions,
        fetch=False,
    )
    router = _fit_router(matches)
    known_teams = _known_teams(matches)
    ollama_health, extractor_config = ollama_status()
    season = _season_for(rnd)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect(config.database_path) as conn:
        result = run_engine(
            conn, rnd, router, season=season, submission_path=config.submissions_path,
            known_teams=known_teams, corpus=_corpus_label(config.divisions),
        )
        health = service.build_system_health(
            conn,
            registry=load_registry(config.registry_path),
            ollama_health=ollama_health,
            extractor_config=extractor_config,
            schema_version=db.schema_version(conn),
            last_forecast_at=generated_at,
        )

    picks = [fixture.pick for fixture in result.round_summary.fixtures]
    ui_state = load_ui_state(config.ui_state_path)
    override_picks = ui_state["overrides"].get(_round_key(rnd), {})
    from .prediction_history import capture_round_summary, changes_for_visit

    snapshot = capture_round_summary(
        season, result.round_summary, now=datetime.now(timezone.utc),
        path=config.prediction_history_path,
    )
    _append_forecast_change_events(snapshot, rnd, config)
    # This is a real forecast refresh, so reconcile the user-confirmed picks
    # against the newly persisted recommendation using this UI's state file.
    record = submission.reconcile(
        season, rnd.round_number, picks, rnd.deadline_at, path=config.submissions_path
    )
    summary = result.round_summary.to_dict()
    # The only postponement signal this system has is the user recording a
    # fixture VOID; nothing polls for it. So status is derived from that
    # and left SCHEDULED otherwise, rather than implying a fixture-state
    # feed that does not exist.
    recorded_results = ui_state["results"].get(_round_key(rnd), [])
    for index, fixture in enumerate(summary["fixtures"]):
        fixture["strategy_pick"] = fixture["pick"]
        fixture["user_pick"] = override_picks.get(str(index))
        if index < len(recorded_results):
            recorded = recorded_results[index]
            if recorded is None:
                fixture["status"] = "VOID"
            elif recorded:
                fixture["status"] = "RESULTED"
    summary["submission_state"] = record.state
    summary["submission"] = {
        "submitted_picks": record.submitted_picks,
        "submitted_golden_goal": record.submitted_golden_goal,
        "submitted_at": record.submitted_at,
        "reviewed_at": record.reviewed_at,
        "drift": record.drift,
    }
    summary["meta"] = {
        "generated_at": generated_at,
        "round_source": str(config.round_path),
        "current_picks": picks,
        "forecast_version": snapshot["version"],
    }
    _fixture_history_payload(season, summary, config)
    from .transparency import (
        SourceFreshness, fixture_transparency, golden_goal_distribution,
    )

    if result.recommendation is not None:
        for fixture, recommendation in zip(summary["fixtures"], result.recommendation.fixtures):
            market = fixture.get("market") or {}
            availability = fixture.get("availability") or {}
            sources = [
                SourceFreshness(
                    source_id="market",
                    available=bool(market.get("available")),
                    observed_at=None,
                    freshness="CURRENT" if market.get("available") else "UNAVAILABLE",
                    detail=market.get("reason_unavailable"),
                ),
                SourceFreshness(
                    source_id="team_availability",
                    available=fixture.get("availability") is not None,
                    observed_at=None,
                    freshness=availability.get("data_quality", "UNKNOWN"),
                ),
            ]
            fixture["explanation"] = fixture_transparency(
                recommendation, generated_at=generated_at,
                model_version=summary["model_version"], sources=sources,
            ).to_dict()
        summary["golden_goal"]["distribution"] = golden_goal_distribution(
            result.recommendation
        ).to_dict()
    _emit_deadline_reminders(rnd, record.state, config)
    summary["changes_since_last_visit"] = changes_for_visit(
        season, rnd.round_number, path=config.prediction_history_path
    )
    health_payload = health.to_dict()
    health_payload["ollama"] = {
        "configured": extractor_config.kind == "ollama",
        "host": extractor_config.host if extractor_config.kind == "ollama" else None,
        "model": extractor_config.model if extractor_config.kind == "ollama" else None,
        "detail": getattr(ollama_health, "detail", "") if ollama_health else (
            "Ollama is not configured. The knowledge extractor is intentionally disabled."
        ),
    }
    return {
        "round": summary,
        "health": health_payload,
        "notes": result.notes,
        "settings": {
            key: ui_state[key]
            for key in (
                "league_name", "player_name", "notifications", "notification_delivery",
                "notification_email", "budget",
            )
        },
        "teams": sorted(known_teams),
    }, result


def _cup_corpus(config: WebConfig, extended: bool) -> tuple[tuple[str, ...], list[int]]:
    """Divisions and seasons for a cup forecast, refusing to quietly shrink.

    The lower divisions were only downloaded from the season the extended
    corpus was introduced, so an extended request over the full season
    range would either hit the network mid-request or silently drop the
    seasons whose files are absent. Both are worse than saying so.
    """
    from .config import DEFAULT_DATA

    seasons = list(range(config.seasons[0], config.seasons[1] + 1))
    if not extended:
        return config.divisions, seasons
    divisions = tuple(DEFAULT_DATA.extended_divisions)
    missing = [
        f"{year}_{div}"
        for year in seasons
        for div in divisions
        if not (config.data_path / f"{year}_{div}.csv").exists()
    ]
    if missing:
        available = [
            year for year in seasons
            if all((config.data_path / f"{year}_{div}.csv").exists() for div in divisions)
        ]
        if not available:
            raise ValueError(
                "The extended corpus is not downloaded. Run: super6 fetch "
                f"--seasons {seasons[0]} {seasons[-1]} --divisions "
                + " ".join(divisions)
            )
        return divisions, available
    return divisions, seasons


def cup_prediction(payload: dict, config: WebConfig) -> dict:
    """Forecast one user-selected cup tie without changing the active round."""
    home = " ".join(str(payload.get("home", "")).split())
    away = " ".join(str(payload.get("away", "")).split())
    if not home or not away or len(home) > 80 or len(away) > 80:
        raise ValueError("Enter a home and away team, each up to 80 characters.")
    if home.casefold() == away.casefold():
        raise ValueError("Choose two different teams.")
    extended = bool(payload.get("extended"))
    divisions, seasons = _cup_corpus(config, extended)
    matches = load_matches(config.data_path, seasons, divisions, fetch=False)
    router = _fit_router(matches)
    known_teams = _known_teams(matches)
    corpus = _corpus_label(divisions)
    # Widening the corpus changes the pooled cross-division fallback, so the
    # result is no longer the frozen, holdout-tested model and must not claim
    # to be. Same rule the CLI's `forecast --extended` prints.
    model_version = f"UNFROZEN_EXTENDED_CORPUS_{corpus}" if extended else "PHASE1_MODEL_V1"
    from .recommend import recommend_fixture
    from .transparency import SourceFreshness, fixture_transparency

    recommendation = recommend_fixture(router(home, away), home, away, FROZEN_PHASE1_V1)
    modellability = service.build_modellability(home, away, known_teams, corpus)
    fixture = service.build_fixture_forecast(
        recommendation, modellability=modellability
    ).to_dict()
    fixture["explanation"] = fixture_transparency(
        recommendation,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        model_version=model_version,
        sources=[SourceFreshness(
            source_id="historical_match_data",
            available=True,
            freshness="CURRENT",
            detail=(
                f"Team-specific history from {corpus}"
                if modellability.modellable
                else modellability.reason
            ),
        )],
    ).to_dict()
    return {
        "fixture": fixture,
        "corpus": corpus,
        "seasons": [seasons[0], seasons[-1]],
        "extended": extended,
        "model_version": model_version,
        "note": (
            "Cup ties are model forecasts only; they do not alter your active "
            "Super 6 round."
            + (
                ""
                if not extended
                else " This corpus is wider than the one PHASE1_MODEL_V1 was "
                "fitted and holdout-tested on, so treat it as a sanity check, "
                "never as a Super 6 pick."
            )
        ),
    }


def create_round(payload: dict, config: WebConfig) -> dict:
    """Validate and persist a manually configured round outside Sky's boundary."""
    rnd = round_from_dict(payload)
    path = config.round_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(round_to_dict(rnd), indent=2) + "\n")
    return {"round_path": str(path), "round": round_to_dict(rnd)}


def mark_round_entered(payload: dict, config: WebConfig) -> dict:
    rnd = load_round(config.round_path)
    picks = payload.get("picks")
    if not isinstance(picks, list) or len(picks) != len(rnd.fixtures):
        raise ValueError("Exactly six predictions are required before marking entered.")
    for pick in picks:
        _scoreline(pick)
    golden_goal = payload.get("golden_goal")
    if golden_goal is not None and (not isinstance(golden_goal, int) or not 1 <= golden_goal <= 90):
        raise ValueError("Golden Goal minute must be a whole number from 1 to 90.")
    record = submission.mark_submitted(
        _season_for(rnd),
        rnd.round_number,
        picks,
        golden_goal=golden_goal,
        path=config.submissions_path,
    )
    _record_prospective_picks(rnd, picks, config)
    return {"submission": vars(record)}


def _record_prospective_picks(rnd: Round, submitted_picks: list[str], config: WebConfig) -> None:
    """Freeze the model and human arms before a result can be recorded."""
    from .prediction_history import current_score_picks
    from .prospective import (
        FROZEN_ARM, MARKET_ARM, SUBMITTED_ARM, already_recorded, load_ledger,
        market_pick, record_picks, save_ledger,
    )

    season = _season_for(rnd)
    ledger = load_ledger(config.ledger_path)
    fixtures = [(fixture.home, fixture.away) for fixture in rnd.fixtures]
    model_scores = current_score_picks(
        season, rnd.round_number, path=config.prediction_history_path
    )
    if len(model_scores) != len(fixtures):
        # A direct API user can mark their manual entry without first opening
        # the dashboard.  In that case there is no honest frozen model arm yet.
        model_scores = []
    if model_scores and not already_recorded(ledger, season, rnd.round_number, FROZEN_ARM):
        ledger = record_picks(
            ledger, season, rnd.round_number, fixtures, FROZEN_ARM,
            [_scoreline(score) for score in model_scores], config.arm_registry_path,
        )
    if (
        all(fixture.odds is not None for fixture in rnd.fixtures)
        and not already_recorded(ledger, season, rnd.round_number, MARKET_ARM)
    ):
        ledger = record_picks(
            ledger, season, rnd.round_number, fixtures, MARKET_ARM,
            [market_pick(fixture.odds) for fixture in rnd.fixtures if fixture.odds is not None],
            config.arm_registry_path,
        )
    if not already_recorded(ledger, season, rnd.round_number, SUBMITTED_ARM):
        ledger = record_picks(
            ledger, season, rnd.round_number, fixtures, SUBMITTED_ARM,
            [_scoreline(score) for score in submitted_picks], config.arm_registry_path,
        )
    save_ledger(ledger, config.ledger_path)


def save_pick_overrides(payload: dict, config: WebConfig) -> dict:
    rnd = load_round(config.round_path)
    picks = payload.get("picks")
    if not isinstance(picks, list) or len(picks) != len(rnd.fixtures):
        raise ValueError("Provide one score for each of the six fixtures.")
    for pick in picks:
        _scoreline(pick)
    state = load_ui_state(config.ui_state_path)
    recommendations = payload.get("recommendations", [])
    state["overrides"][_round_key(rnd)] = {
        str(index): pick
        for index, pick in enumerate(picks)
        if index >= len(recommendations) or pick != recommendations[index]
    }
    save_ui_state(state, config.ui_state_path)
    return {"overrides": state["overrides"].get(_round_key(rnd), {})}


def save_settings(payload: dict, config: WebConfig) -> dict:
    state = load_ui_state(config.ui_state_path)
    for key in ("league_name", "player_name"):
        if key in payload:
            value = str(payload[key]).strip()
            if not value or len(value) > 80:
                raise ValueError(f"{key.replace('_', ' ').capitalize()} must be 1–80 characters.")
            state[key] = value
    if "notifications" in payload:
        if not isinstance(payload["notifications"], dict):
            raise ValueError("Notifications must be an object.")
        for key in DEFAULT_UI_STATE["notifications"]:
            if key in payload["notifications"]:
                state["notifications"][key] = bool(payload["notifications"][key])
    if "notification_delivery" in payload:
        delivery = payload["notification_delivery"]
        if not isinstance(delivery, dict):
            raise ValueError("Notification delivery must be an object.")
        for key in DEFAULT_UI_STATE["notification_delivery"]:
            if key in delivery:
                state["notification_delivery"][key] = bool(delivery[key])
    if "notification_email" in payload:
        address = str(payload["notification_email"]).strip()
        if len(address) > 254 or (address and ("@" not in address or " " in address)):
            raise ValueError("Enter a valid email address, or leave it blank.")
        state["notification_email"] = address
    if "budget" in payload:
        budget = payload["budget"]
        if not isinstance(budget, dict):
            raise ValueError("Budget must be an object.")
        for key in ("target_monthly", "hard_ceiling"):
            if key in budget:
                amount = float(budget[key])
                if amount < 0 or amount > 10_000:
                    raise ValueError("Budget must be between 0 and 10,000.")
                state["budget"][key] = amount
        if state["budget"]["hard_ceiling"] < state["budget"]["target_monthly"]:
            raise ValueError("Hard ceiling cannot be lower than the monthly target.")
    save_ui_state(state, config.ui_state_path)
    return {"settings": {key: state[key] for key in (
        "league_name", "player_name", "notifications", "notification_delivery",
        "notification_email", "budget",
    )}}


def import_opponent(payload: dict, config: WebConfig) -> dict:
    rnd = load_round(config.round_path)
    name = str(payload.get("name", "")).strip()
    picks = payload.get("picks")
    if not name or len(name) > 80:
        raise ValueError("Opponent name must be 1–80 characters.")
    if not isinstance(picks, list) or len(picks) != len(rnd.fixtures):
        raise ValueError("Import exactly six opponent predictions.")
    for pick in picks:
        _scoreline(pick)
    state = load_ui_state(config.ui_state_path)
    opponent = state["opponents"].setdefault(name, {"rounds": {}})
    opponent["rounds"][_round_key(rnd)] = picks
    save_ui_state(state, config.ui_state_path)
    from .league import record_opponent_round

    try:
        record_opponent_round(
            name, _season_for(rnd), rnd.round_number,
            [_scoreline(pick) for pick in picks], path=config.opponent_history_path,
        )
    except ValueError as exc:
        if "already recorded" not in str(exc):
            raise
    return {"opponents": state["opponents"]}


def save_results(payload: dict, config: WebConfig) -> dict:
    rnd = load_round(config.round_path)
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != len(rnd.fixtures):
        raise ValueError("Provide a result or VOID for each of the six fixtures.")
    cleaned = []
    for result in results:
        if result is None:
            cleaned.append(None)
        else:
            _scoreline(result)
            cleaned.append(result)
    state = load_ui_state(config.ui_state_path)
    state["results"][_round_key(rnd)] = cleaned
    save_ui_state(state, config.ui_state_path)
    from .prospective import load_ledger, save_ledger, score_round

    season = _season_for(rnd)
    ledger = load_ledger(config.ledger_path)
    if not ledger.empty and ((ledger["season"] == season) & (ledger["round"] == rnd.round_number)).any():
        ledger = score_round(
            ledger,
            season,
            rnd.round_number,
            {
                index: (None if score is None else _scoreline(score))
                for index, score in enumerate(cleaned)
            },
        )
        save_ledger(ledger, config.ledger_path)
    return {"results": cleaned}


def _performance_payload(config: WebConfig) -> dict:
    from .prospective import load_ledger, standings

    table = standings(load_ledger(config.ledger_path))
    rows = table.to_dict(orient="records") if not table.empty else []
    return {
        "arms": rows,
        "sample_warning": (
            "No scored prospective rounds are recorded yet. Performance will appear "
            "once pre-deadline picks and final results exist."
            if not rows else
            "Six fixtures per round is a thin sample. Treat early differences as noise."
        ),
    }


def _results_payload(rnd: Round, summary: dict, state: dict) -> dict:
    from .scoring import super6_points

    actual = state["results"].get(_round_key(rnd), [])
    picks = [
        fixture.get("user_pick") or fixture["strategy_pick"] or fixture["pick"]
        for fixture in summary["fixtures"]
    ]
    fixtures = []
    total = exact = correct = voids = 0
    for index, fixture in enumerate(summary["fixtures"]):
        score = actual[index] if index < len(actual) else ""
        item = {"fixture": fixture, "pick": picks[index], "actual": score}
        if score is None:
            item["status"] = "VOID"
            voids += 1
        elif score:
            ph, pa = _scoreline(picks[index])
            ah, aa = _scoreline(score)
            points = super6_points(ph, pa, ah, aa)
            item.update({"status": "RESULTED", "points": points})
            total += points
            if points == 5:
                exact += 1
            elif points == 2:
                correct += 1
        else:
            item["status"] = "PENDING"
        fixtures.append(item)
    return {
        "fixtures": fixtures,
        "summary": {"points": total, "exact": exact, "correct": correct, "void": voids},
    }


def _league_payload(
    rnd: Round, state: dict, config: WebConfig, recommendation=None
) -> dict:
    """Score only the participant picks and results the user has recorded."""
    from .league import (
        OpponentRound, league_standings, load_opponent_history, opponent_profiles,
    )

    history = list(load_opponent_history(config.opponent_history_path))
    known = {(item.participant.casefold(), item.season, item.round_number) for item in history}
    # Preserve entries recorded by earlier UI versions while promoting the
    # append-only league history as the canonical store going forward.
    for participant, data in state["opponents"].items():
        for round_key, picks in data.get("rounds", {}).items():
            try:
                season_text, round_text = round_key.split(":", 1)
                season, round_number = int(season_text), int(round_text)
                key = (participant.casefold(), season, round_number)
                if key not in known:
                    history.append(OpponentRound(
                        participant, season, round_number,
                        tuple(_scoreline(pick) for pick in picks), "legacy-ui-import",
                    ))
                    known.add(key)
            except (TypeError, ValueError):
                continue
    for record in submission.load_states(config.submissions_path).values():
        if len(record.submitted_picks) != len(rnd.fixtures):
            continue
        key = (state["player_name"].casefold(), record.season, record.round_number)
        if key not in known:
            history.append(OpponentRound(
                state["player_name"], record.season, record.round_number,
                tuple(_scoreline(pick) for pick in record.submitted_picks),
                record.submitted_at or "recorded-by-user",
            ))
            known.add(key)

    results = {}
    for round_key, scores in state["results"].items():
        try:
            season_text, round_text = round_key.split(":", 1)
            for index, score in enumerate(scores):
                if score is not None and score != "":
                    results[(int(season_text), int(round_text), index)] = _scoreline(score)
                elif score is None:
                    results[(int(season_text), int(round_text), index)] = None
        except (TypeError, ValueError):
            continue
    table = league_standings(history, results)
    # You belong in your own league table from the start. Without this you
    # appear only once a round of yours has been scored, which also means
    # the P(finish 1st) column has nobody to show a figure against.
    player = state["player_name"]
    if player and not any(row.participant == player for row in table):
        from .league import LeagueStanding

        # Ranked by the same rule as everyone else — joint with the others
        # on nil points, not stranded at the bottom of a table of zeros.
        table = (*table, LeagueStanding(
            participant=player,
            rank=1 + sum(1 for row in table if row.points > 0),
            points=0, exact_scores=0, fixtures_scored=0, rounds_scored=0,
        ))
    favourite, chase_defend = _opponent_bias_payload(history, results, config)
    payload = {
        "standings": [asdict(row) for row in table],
        "profiles": [asdict(profile) for profile in opponent_profiles(history)],
        "favourite_bias": [asdict(item) for item in favourite],
        "chase_defend": [asdict(item) for item in chase_defend],
        "predicted_picks": _predicted_picks_payload(recommendation, history, favourite, config),
        "finish_first": None,
        "finish_first_reason": (
            "A finish-first estimate activates after complete, recorded "
            "remaining-round simulations are available."
        ),
        "strategy": {
            "mode": "NORMAL",
            "active": False,
            "reason": (
                "Import every rival's picks for this round to compare "
                "strategies. Until the field is complete the maximum-EV "
                "model picks remain in use."
            ),
        },
    }
    payload.update(_strategy_payload(rnd, table, history, state, recommendation))
    return payload


def _opponent_bias_payload(history, results, config: WebConfig):
    """§11 favourite-bias and chase/defend tendencies, joined against our own
    stored forecasts (`opponent_intelligence.py`). Returns empty tuples
    rather than raising when there is nothing to join yet."""
    from . import opponent_intelligence as oi

    joined = oi.join_picks_with_forecasts(history, prediction_path=config.prediction_history_path)
    favourite = oi.favourite_bias_profiles(joined)
    chase_defend = oi.chase_defend_tendencies(history, joined, results)
    return favourite, chase_defend


def _fixture_favoured_result(fixture) -> str:
    """Our own recommended pick's implied result — the "favourite" a rival's
    pick is compared against. See opponent_intelligence.favourite_bias_profiles
    for the same approximation stated explicitly."""
    if fixture.pick_home > fixture.pick_away:
        return "HOME"
    if fixture.pick_home < fixture.pick_away:
        return "AWAY"
    return "DRAW"


def _predicted_picks_payload(recommendation, history, favourite, config: WebConfig) -> dict:
    """§49.3 'Current-Round Opponent Forecast' — likely picks per rival for
    the fixtures in play *before* they have actually submitted this round,
    unlike the strategy panel's `_opponent_shares` which needs the field's
    actual picks already imported. Only produced for rivals with at least
    one joinable prior pick; everyone else has no evidence to predict from."""
    if recommendation is None or not history:
        return {}
    from . import opponent_intelligence as oi
    from .league import opponent_profiles

    profiles = {profile.participant: profile for profile in opponent_profiles(history)}
    average = oi.league_average_profile(history)
    if average is None:
        return {}
    joined = oi.join_picks_with_forecasts(history, prediction_path=config.prediction_history_path)
    average_rate = oi.league_average_favourite_rate(joined)
    bias_by_participant = {item.participant: item for item in favourite}

    out: dict[str, list[dict]] = {}
    for participant, profile in profiles.items():
        bias = bias_by_participant.get(participant)
        if bias is None or bias.sample_size == 0:
            continue
        per_fixture = []
        for fixture in recommendation.fixtures:
            distribution = oi.predict_pick_distribution(
                profile, bias, average, average_rate,
                fixture_favoured_result=_fixture_favoured_result(fixture),
            )
            top = sorted(distribution.items(), key=lambda item: -item[1])[:3]
            per_fixture.append({
                "home_team": fixture.home,
                "away_team": fixture.away,
                "predicted": [{"score": score, "probability": p} for score, p in top],
            })
        out[participant] = per_fixture
    return out


def _strategy_payload(rnd, standings, history, state, recommendation) -> dict:
    """Rank pick sets by simulated P(finish first), when the field is known.

    Returns an empty dict — leaving the honest "not active" default in
    place — whenever the inputs are incomplete. `recommend_strategy`
    already refuses a partial field; this only decides whether to ask.
    """
    from .league import recommend_strategy
    from .strategy import DEFAULT_SCENARIOS, build_candidates, describe_candidate, simulate_scenarios

    from .league import LeagueStanding

    if recommendation is None:
        return {}
    season, player = _season_for(rnd), state["player_name"]
    # You enter the standings only once a round of yours has been scored,
    # but strategy advice is worthless after you have already entered — it
    # exists to tell you what to enter. So join the table on nil points
    # rather than making the feature wait for a result.
    if not any(row.participant == player for row in standings):
        standings = [
            *standings,
            LeagueStanding(participant=player, rank=len(standings) + 1, points=0,
                           exact_scores=0, fixtures_scored=0, rounds_scored=0),
        ]
    if len(standings) < 2:
        return {}
    # Only the rivals: `recommend_strategy` checks the supplied picks cover
    # exactly the opponents, and evaluates the player's own row from each
    # candidate instead.
    round_picks = [
        record for record in history
        if record.season == season
        and record.round_number == rnd.round_number
        and record.participant != player
    ]
    opponents = {row.participant for row in standings} - {player}
    if not opponents or {r.participant for r in round_picks} != opponents:
        return {}

    candidates = build_candidates(recommendation, round_picks)
    scenarios = simulate_scenarios(recommendation)
    result = recommend_strategy(
        standings, round_picks, candidates, scenarios,
        participant=player, season=season, round_number=rnd.round_number,
        minimum_scenarios=min(DEFAULT_SCENARIOS, len(scenarios)),
    )
    if result is None:
        return {}

    from .strategy import preferred

    evaluations = (result.recommended, *result.alternatives)
    by_name = {candidate.name: candidate for candidate in candidates}
    max_ev = next(e for e in evaluations if e.name == "MAX_EV")
    # `recommend_strategy` ranks on the raw simulated figures. Deviating
    # costs certain expected points, so it is only worth doing when the
    # gain survives the simulation's own sampling error.
    chosen = preferred(evaluations, len(scenarios))
    picks = by_name[chosen.name].picks
    return {
        "finish_first": {
            "probability": chosen.finish_first_probability,
            "expected_final_points": chosen.expected_round_points,
            "simulations": chosen.simulations,
        },
        "finish_first_reason": (
            f"Estimated from {chosen.simulations} simulated rounds drawn from "
            "the model's own score distributions, against your rivals' "
            "imported picks."
        ),
        "strategy": {
            "mode": "NORMAL" if chosen.name == "MAX_EV" else "DIFFERENTIATE",
            "active": chosen.name != "MAX_EV",
            "reason": (
                describe_candidate(by_name[chosen.name], recommendation)
                + f" P(finish first) {chosen.finish_first_probability:.1%}"
                + (
                    ""
                    if chosen.name == "MAX_EV"
                    else f", against {max_ev.finish_first_probability:.1%} for the "
                    f"maximum-EV picks — worth {max_ev.expected_round_points - chosen.expected_round_points:.2f} "
                    "expected points to chase."
                )
            ),
            "picks": [f"{h}-{a}" for h, a in picks],
            "candidates": [
                {
                    "name": evaluation.name,
                    "finish_first_probability": evaluation.finish_first_probability,
                    "expected_round_points": evaluation.expected_round_points,
                    "description": describe_candidate(by_name[evaluation.name], recommendation),
                    "recommended": evaluation.name == chosen.name,
                }
                for evaluation in (result.recommended, *result.alternatives)
            ],
        },
    }


def _alerts_payload(activity: list[dict], state: dict) -> dict:
    """Filter the in-app alert centre by the user's material-event choices."""
    notification_map = {
        "PREDICTION_CHANGED": "prediction_changes",
        "CONFIDENCE_CHANGED": "prediction_changes",
        "MARKET_MOVEMENT": "market_movement",
        "MARKET_MAJOR_MOVEMENT": "market_movement",
        "LINEUP_CONFIRMED": "lineup_impact",
        "LINEUP_SURPRISE": "lineup_impact",
        "DEADLINE_APPROACHING": "deadline_reminder",
    }
    enabled = state["notifications"]
    visible = [
        event for event in activity
        if (
            event.get("severity") != "INFORMATIONAL" or enabled.get("informational")
        )
        and enabled.get(notification_map.get(event.get("event_type"), "prediction_changes"), True)
    ]
    from .notifications import email_delivery_status

    return {
        "items": visible[:12],
        "in_app": bool(state["notification_delivery"]["in_app"]),
        "email_status": email_delivery_status(
            state["notification_delivery"]["email"], state["notification_email"]
        ),
    }


def app_payload(config: WebConfig) -> dict:
    """One truthful payload for all UI phases; absent evidence remains absent."""
    payload, engine_result = _dashboard(config)
    rnd = load_round(config.round_path)
    state = load_ui_state(config.ui_state_path)
    registry = load_registry(config.registry_path)
    activity = [asdict(event) for event in events.load(config.event_log_path)[-100:]][::-1]
    sources = [asdict(record) for _, record in sorted(registry.items())]
    from .transparency import model_summary, performance_summary

    performance = _performance_payload(config)
    model = model_summary(payload["round"]["model_version"], FROZEN_PHASE1_V1)
    payload.update(
        {
            "activity": activity,
            "alerts": _alerts_payload(activity, state),
            "performance": {
                **performance,
                "transparency": performance_summary(performance["arms"]).to_dict(),
            },
            "results": _results_payload(rnd, payload["round"], state),
            "opponents": state["opponents"],
            "league": _league_payload(
                rnd, state, config, engine_result.recommendation
            ),
            "sources": sources,
            "model": {
                "version": payload["round"]["model_version"],
                "frozen_config": asdict(FROZEN_PHASE1_V1),
                "note": "The quantitative core is frozen as PHASE1_MODEL_V1. Later layers are shown separately until prospective evidence promotes them.",
                "transparency": model.to_dict(),
            },
        }
    )
    return payload


class Super6RequestHandler(BaseHTTPRequestHandler):
    config: WebConfig

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        # Browser polling should not make the terminal look like an error log.
        return

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        self.send_header("Content-Length", str(len(body)))
        # `no-cache` still let browsers reuse a stale script here, because
        # nothing is served with a validator to revalidate against. The assets
        # are read from disk per request anyway, so refuse storage outright
        # rather than have an edited UI silently not take effect.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 100_000:
            raise ValueError("Request body is missing or too large.")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/api/dashboard":
                self._send_json(HTTPStatus.OK, dashboard_payload(self.config))
            elif path == "/api/app":
                self._send_json(HTTPStatus.OK, app_payload(self.config))
            elif path == "/api/health":
                self._send_json(HTTPStatus.OK, dashboard_payload(self.config)["health"])
            elif path in {"/", "/index.html"}:
                self._send_file(ASSET_DIR / "index.html")
            elif path.startswith("/assets/") and Path(path).name == path.removeprefix("/assets/"):
                self._send_file(ASSET_DIR / Path(path).name)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (OSError, ValueError, KeyError) as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/submission/entered":
                self._send_json(HTTPStatus.OK, mark_round_entered(payload, self.config))
            elif path == "/api/rounds":
                self._send_json(HTTPStatus.CREATED, create_round(payload, self.config))
            elif path == "/api/picks":
                self._send_json(HTTPStatus.OK, save_pick_overrides(payload, self.config))
            elif path == "/api/settings":
                self._send_json(HTTPStatus.OK, save_settings(payload, self.config))
            elif path == "/api/opponents":
                self._send_json(HTTPStatus.OK, import_opponent(payload, self.config))
            elif path == "/api/results":
                self._send_json(HTTPStatus.OK, save_results(payload, self.config))
            elif path == "/api/cup-prediction":
                self._send_json(HTTPStatus.OK, cup_prediction(payload, self.config))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def serve(config: WebConfig, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the local UI. Press Ctrl-C to stop it."""
    handler = type("ConfiguredSuper6Handler", (Super6RequestHandler,), {"config": config})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Super 6 UI running at http://{host}:{port}")
    print(f"Round: {config.round_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nSuper 6 UI stopped.")
    finally:
        server.server_close()
