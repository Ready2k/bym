"""Local decision-first web UI for Super 6 (UIUX.md Phase 1).

The UI deliberately talks only to the public read-model contracts.  It is
small enough to run from a checkout without a frontend build chain, while
keeping the HTTP boundary that a hosted UI can later retain.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import RLock
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import db, events, prediction_history, strategy_history, submission
from .api import service
from .config import DEFAULT_LMS, FROZEN_PHASE1_V1
from .data.football_data import load_matches
from .engine import run_engine
from .round import (
    Round,
    load_round,
    resolve_round_teams,
    round_from_dict,
    round_to_dict,
)
from .sources.registry import load_registry
from .sources.teams import resolve as resolve_team_name


ASSET_DIR = Path(__file__).with_name("web_assets")
DEFAULT_ROUND = Path("rounds/2026_27_round_01.json")


@dataclass(frozen=True)
class WebConfig:
    round_path: Path = DEFAULT_ROUND
    data_path: Path = Path("data")
    database_path: Path = Path("data/bym.db")
    submissions_path: Path = Path("data/submissions.json")
    registry_path: Path = Path("config/source_registry.json")
    event_log_path: Path = Path("data/events/activity.jsonl")
    ledger_path: Path = Path("results/prospective_ledger.csv")
    ui_state_path: Path = Path("data/ui_state.json")
    prediction_history_path: Path = Path("data/prediction_history.json")
    strategy_history_path: Path = Path("data/strategy_history.json")
    opponent_history_path: Path = Path("data/opponent_history.json")
    arm_registry_path: Path = Path("results/prospective_arms.json")
    lms_gameweek_path: Path = Path("data/lms_gameweek.json")
    lms_state_path: Path = Path("data/lms_state.json")
    seasons: tuple[int, int] = (2019, 2025)
    divisions: tuple[str, ...] = ("E0", "E1")


def _fit_router(matches):
    """Use the frozen production configuration without making web depend on CLI."""
    from .cli import _fit_router

    return _fit_router(matches, FROZEN_PHASE1_V1.xi, FROZEN_PHASE1_V1)


# A fitted router is deterministic for a particular local data snapshot, but
# expensive: a full fit involves one pooled and one per-division optimisation.
# The web server used to repeat that work for the round and LMS payloads on
# every page load. Keep the current local snapshot warm, and invalidate it
# whenever one of its source CSVs changes.
_MODEL_CONTEXT: dict[tuple, tuple[object, object, set[str]]] = {}
_MODEL_CONTEXT_LOCK = RLock()


def _model_data_fingerprint(config: WebConfig) -> tuple:
    entries = []
    for season in range(config.seasons[0], config.seasons[1] + 1):
        for division in config.divisions:
            path = config.data_path / f"{season}_{division}.csv"
            try:
                stat = path.stat()
                entries.append((str(path.resolve()), stat.st_mtime_ns, stat.st_size))
            except OSError:
                entries.append((str(path.resolve()), None, None))
    return tuple(entries)


def _model_context(config: WebConfig) -> tuple[object, object, set[str]]:
    """Load and fit the current local model snapshot once per server process."""
    key = (
        str(config.data_path.resolve()), config.seasons, config.divisions,
        _model_data_fingerprint(config),
    )
    with _MODEL_CONTEXT_LOCK:
        cached = _MODEL_CONTEXT.get(key)
        if cached is not None:
            return cached
        matches = load_matches(
            config.data_path,
            list(range(config.seasons[0], config.seasons[1] + 1)),
            config.divisions,
            fetch=False,
        )
        context = (matches, _fit_router(matches), _known_teams(matches))
        # There is only one active data snapshot in the local server. Dropping
        # stale entries prevents a long-running process from retaining every
        # historical download after a refresh.
        _MODEL_CONTEXT.clear()
        _MODEL_CONTEXT[key] = context
        return context


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
    # §13.1 season state / §13 season simulator: Sky does not publish how
    # many Super 6 rounds a season holds, so this stays a plain user
    # setting rather than something derived — a reasonable Premier League
    # default, editable once the real cadence for the season is known.
    "season_rounds_total": 38,
    "overrides": {},
    "opponents": {},
    "results": {},
    # §27 decision: the private league's third tiebreak (after points, then
    # exact scores) is Golden Goal closeness. Round-level, not per-fixture —
    # keyed the same way as "results" above, one actual first-goal minute
    # per round rather than one per fixture.
    "golden_goal_actual": {},
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
    for key in ("overrides", "opponents", "results", "golden_goal_actual"):
        merged[key] = dict(state.get(key, {}))
    return merged


def save_ui_state(state: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def _round_key(rnd: Round) -> str:
    return f"{_season_for(rnd)}:{rnd.round_number}"


def recorded_results(state: dict) -> dict[tuple[int, int, int], tuple[int, int] | None]:
    """UI state's per-round score lists as (season, round, fixture) → scoreline.

    The shape `league_standings`, `chase_defend_tendencies` and
    `claim_outcomes` all take. A malformed round key is skipped rather than
    failing the whole read — one bad entry should not hide every other
    recorded result.
    """
    results: dict[tuple[int, int, int], tuple[int, int] | None] = {}
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
    return results


def _scoreline(value: str) -> tuple[int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"\d+-\d+", value):
        raise ValueError("Scores must use H-A format, for example 2-0.")
    home, away = (int(part) for part in value.split("-"))
    if home > 20 or away > 20:
        raise ValueError("Scores must be between 0 and 20.")
    return home, away


def web_extractor_config(config: WebConfig):
    """Resolve the extractor selected in the web UI, else retain env support.

    The UI state deliberately wins only once a user has saved an extractor
    preference. Existing local deployments configured with environment
    variables therefore keep working without a migration.
    """
    from .knowledge.config import ExtractorConfig, ExtractorKind, from_env

    setting = load_ui_state(config.ui_state_path).get("knowledge_extractor")
    if not isinstance(setting, dict) or not setting.get("configured"):
        return from_env()
    return ExtractorConfig(
        kind=(ExtractorKind.OLLAMA.value if setting.get("enabled") else ExtractorKind.NULL.value),
        mode=setting["mode"],
        base_url=setting["base_url"],
        model=setting["model"],
    )


def extractor_settings_for_ui(config: WebConfig) -> dict:
    """Return the effective, safe-to-display extractor preferences."""
    state = load_ui_state(config.ui_state_path)
    setting = state.get("knowledge_extractor")
    effective = web_extractor_config(config)
    parsed = urlparse(effective.base_url)
    try:
        port = parsed.port
    except ValueError:
        port = None
    if parsed.hostname:
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        safe_base_url = f"{parsed.scheme}://{host}{f':{port}' if port else ''}{parsed.path}"
    else:
        safe_base_url = ""
    return {
        "configured": bool(isinstance(setting, dict) and setting.get("configured")),
        "enabled": effective.kind == "ollama",
        "base_url": safe_base_url.rstrip("/"),
        "model": effective.model,
        "mode": effective.mode,
    }


def _valid_ollama_base_url(value: object) -> str:
    """Validate an Ollama endpoint before saving or probing it."""
    base_url = str(value or "").strip()
    parsed = urlparse(base_url)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(
            "Enter an Ollama base URL such as http://localhost:11434 (without credentials)."
        ) from exc
    if (
        len(base_url) > 512
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Enter an Ollama base URL such as http://localhost:11434 (without credentials)."
        )
    return base_url.rstrip("/")


def available_ollama_models(payload: dict, session=None) -> dict:
    """List models exposed by an explicitly supplied Ollama endpoint."""
    import requests

    base_url = _valid_ollama_base_url(payload.get("base_url"))
    host = urlparse(base_url).hostname or "the Ollama host"
    try:
        response = (session or requests).get(f"{base_url}/api/tags", timeout=5)
    except requests.Timeout as exc:
        raise ValueError(f"Timed out while contacting {host}.") from exc
    except requests.RequestException as exc:
        raise ValueError(f"Could not contact Ollama at {host}.") from exc
    if response.status_code >= 400:
        raise ValueError(f"Ollama at {host} returned HTTP {response.status_code}.")
    try:
        models = response.json().get("models")
    except ValueError as exc:
        raise ValueError(f"Ollama at {host} returned an invalid model list.") from exc
    if not isinstance(models, list):
        raise ValueError(f"Ollama at {host} returned an invalid model list.")
    names = sorted({
        str(model["name"]).strip()
        for model in models
        if isinstance(model, dict) and isinstance(model.get("name"), str) and model["name"].strip()
    }, key=str.casefold)
    return {"models": names[:100]}


def ollama_status(extractor_config=None, *, probe: bool = True) -> tuple[object | None, object]:
    """Return configured extractor health without blocking routine page loads.

    A default/null extractor has no Ollama dependency at all. Calling a
    network probe in that state would both slow normal page loads and turn
    an intentionally unused optional component into a spurious warning.
    """
    from .knowledge.config import ExtractorKind, from_env

    config = extractor_config or from_env()
    if config.kind != ExtractorKind.OLLAMA.value or not probe:
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
        elif "basis" in fields:
            basis = fields["basis"]
            created.append(events.make_event(
                events.EventType.PREDICTION_CHANGED,
                events.Severity.IMPORTANT,
                f"Forecast basis changed {basis['was']} → {basis['now']}.",
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
    `bym watch` covers the rest. Each threshold is emitted once, guarded
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
                # §19/§43: what this specific forecast actually consumed —
                # basis (model vs market-only fallback), and the market/
                # availability state at the moment it was recorded, not
                # the current live value.
                "basis": version["fixture"].get("basis"),
                "market_snapshot": version["fixture"].get("market_snapshot"),
                "availability_snapshot": version["fixture"].get("availability_snapshot"),
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
    matches, router, known_teams = _model_context(config)
    extractor_config = web_extractor_config(config)
    # A configured local extractor may be on a laptop or a home server that
    # is asleep. Its status is operational information, not a prerequisite to
    # rendering football forecasts; probing it here could leave the UI on its
    # loading screen behind a slow DNS lookup. Explicit settings actions still
    # use the live endpoint.
    ollama_health, extractor_config = ollama_status(extractor_config, probe=False)
    season = _season_for(rnd)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect(config.database_path) as conn:
        from .knowledge.config import build_extractor

        result = run_engine(
            conn, rnd, router, season=season, submission_path=config.submissions_path,
            known_teams=known_teams, corpus=_corpus_label(config.divisions),
            tavily_api_key=os.environ.get("TAVILY_API_KEY"),
            extractor=build_extractor(extractor_config),
            extractor_config=extractor_config,
            # Never from a page load. Knowledge research is acquisition:
            # for one round it is 12 teams of Tavily searches plus a remote
            # LLM call each, at a 120s timeout apiece. Running that inline
            # made every request hang and burned ~490 Tavily credits of a
            # 1000/month tier in a day — invisible until the extractor was
            # first switched on, because `kind` was NULL before that.
            #
            # `scheduler.KNOWLEDGE_RESEARCH` and `worker._run_knowledge_job`
            # already do exactly this on a cadence, and worker.py calls
            # itself "the one place the knowledge layer runs". It now is.
            # The UI reads the claims that produced, rather than causing
            # them — the same rule §67 sets for the conversational layer.
            run_knowledge=False,
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
            "Ollama status is checked when you request its models."
            if extractor_config.kind == "ollama"
            else "Ollama is not configured. The knowledge extractor is intentionally disabled."
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
        } | {"knowledge_extractor": extractor_settings_for_ui(config)},
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
                "The extended corpus is not downloaded. Run: bym fetch "
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
    # Cup Match accepts the names supporters actually type (for example,
    # "Nottingham Forest") as well as Football-Data's compact canonical
    # names ("Nott'm Forest"). Resolution is explicit and returns None on
    # ambiguity, so this never turns a genuinely unknown club into a guess.
    model_home = resolve_team_name(home, known_teams) or home
    model_away = resolve_team_name(away, known_teams) or away
    corpus = _corpus_label(divisions)
    # Widening the corpus changes the pooled cross-division fallback, so the
    # result is no longer the frozen, holdout-tested model and must not claim
    # to be. Same rule the CLI's `forecast --extended` prints.
    model_version = f"UNFROZEN_EXTENDED_CORPUS_{corpus}" if extended else "PHASE1_MODEL_V1"
    from .recommend import recommend_fixture
    from .transparency import SourceFreshness, fixture_transparency

    recommendation = recommend_fixture(
        router(model_home, model_away), model_home, model_away, FROZEN_PHASE1_V1
    )
    modellability = service.build_modellability(
        model_home, model_away, known_teams, corpus
    )
    fixture = service.build_fixture_forecast(
        recommendation, modellability=modellability
    ).to_dict()
    # The recommendation uses canonical data names, but the result should
    # reflect the club name the user selected in the Cup Match form.
    fixture["home_team"] = home
    fixture["away_team"] = away
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
    # Resolve on the way in, not on every read. This is where a club name
    # enters the system — the "Other" option in the fixture form accepts
    # free text, and Sky writes "Manchester City" where the corpus says
    # "Man City". Canonicalising here means the file on disk is already
    # right for every later reader, including ones that never resolve.
    try:
        matches = load_matches(
            config.data_path,
            list(range(config.seasons[0], config.seasons[1] + 1)),
            config.divisions,
            fetch=False,
        )
        rnd, _ = resolve_round_teams(rnd, _known_teams(matches))
    except (FileNotFoundError, ValueError) as exc:
        # A round must still be saveable when the corpus is absent — the
        # UI's own unmodellable banner covers what this would have caught.
        logging.getLogger(__name__).warning("team resolution skipped: %s", exc)
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
    if "season_rounds_total" in payload:
        total = int(payload["season_rounds_total"])
        if not 1 <= total <= 60:
            raise ValueError("Season length must be between 1 and 60 rounds.")
        state["season_rounds_total"] = total
    if "knowledge_extractor" in payload:
        extractor = payload["knowledge_extractor"]
        if not isinstance(extractor, dict):
            raise ValueError("LLM settings must be an object.")
        enabled = extractor.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("Choose whether to enable the Ollama extractor.")
        mode = str(extractor.get("mode", "")).lower()
        if mode not in {"shadow", "active"}:
            raise ValueError("Extractor mode must be Shadow or Active.")
        base_url = _valid_ollama_base_url(extractor.get("base_url"))
        model = str(extractor.get("model", "")).strip()
        if not model or len(model) > 256 or any(char.isspace() for char in model):
            raise ValueError("Enter an Ollama model name without spaces.")
        state["knowledge_extractor"] = {
            "configured": True,
            "enabled": enabled,
            "base_url": base_url,
            "model": model,
            "mode": mode,
        }
    save_ui_state(state, config.ui_state_path)
    return {"settings": {key: state[key] for key in (
        "league_name", "player_name", "notifications", "notification_delivery",
        "notification_email", "budget", "season_rounds_total",
    )} | {"knowledge_extractor": extractor_settings_for_ui(config)}}


def _golden_goal_minute(value) -> int | None:
    """Parse an optional Golden Goal minute; blank/absent means no guess
    was shared, never a guess of minute 0 or 1 by default."""
    if value in (None, ""):
        return None
    minute = int(value)
    if not 1 <= minute <= 90:
        raise ValueError("Golden Goal minute must be between 1 and 90.")
    return minute


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
    golden_goal = _golden_goal_minute(payload.get("golden_goal"))
    state = load_ui_state(config.ui_state_path)
    opponent = state["opponents"].setdefault(name, {"rounds": {}})
    opponent["rounds"][_round_key(rnd)] = picks
    opponent.setdefault("golden_goal", {})[_round_key(rnd)] = golden_goal
    save_ui_state(state, config.ui_state_path)
    from .league import record_opponent_round

    try:
        record_opponent_round(
            name, _season_for(rnd), rnd.round_number,
            [_scoreline(pick) for pick in picks], golden_goal=golden_goal,
            path=config.opponent_history_path,
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
    golden_goal_actual = _golden_goal_minute(payload.get("golden_goal_actual"))
    state = load_ui_state(config.ui_state_path)
    state["results"][_round_key(rnd)] = cleaned
    state["golden_goal_actual"][_round_key(rnd)] = golden_goal_actual
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
    return {"results": cleaned, "golden_goal_actual": golden_goal_actual}


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
        "golden_goal_actual": state["golden_goal_actual"].get(_round_key(rnd)),
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
                        data.get("golden_goal", {}).get(round_key),
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
                record.submitted_golden_goal,
            ))
            known.add(key)

    results = recorded_results(state)
    golden_goal_actual = {}
    for round_key, minute in state["golden_goal_actual"].items():
        try:
            season_text, round_text = round_key.split(":", 1)
            golden_goal_actual[(int(season_text), int(round_text))] = minute
        except (TypeError, ValueError):
            continue
    # The league table is this season's bragging rights, not an all-time
    # ledger — a new season (a round whose deadline falls in a later year)
    # starts every participant back at 0-0 with no manual reset. Opponent
    # modelling below still learns from the unfiltered `history` across
    # every season recorded, since behavioural tendencies only get more
    # reliable with more evidence.
    season = _season_for(rnd)
    season_history = [record for record in history if record.season == season]
    season_results = {key: value for key, value in results.items() if key[0] == season}
    season_golden_goal_actual = {
        key: value for key, value in golden_goal_actual.items() if key[0] == season
    }
    table = league_standings(season_history, season_results, season_golden_goal_actual)
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
    favourite, team_bias, chase_defend = _opponent_bias_payload(history, results, config)
    payload = {
        "season": season,
        "standings": [asdict(row) for row in table],
        "profiles": [asdict(profile) for profile in opponent_profiles(history)],
        "favourite_bias": [asdict(item) for item in favourite],
        "team_bias": [asdict(item) for item in team_bias],
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
    round_strategy = _strategy_payload(rnd, table, history, state, recommendation)
    payload.update(round_strategy)
    payload["season_strategy"] = _season_strategy_payload(
        rnd, table, history, favourite, recommendation, state, config
    )

    # §19: persist whichever recommendations were actually produced, so a
    # forecast_run has a strategy decision to link to. The raw picks are
    # the model's own — the thing a strategy pick is a deviation *from*.
    if recommendation is not None:
        raw_picks = [f"{f.pick_home}-{f.pick_away}" for f in recommendation.fixtures]
        rivals = {
            record.participant
            for record in history
            if record.season == season and record.round_number == rnd.round_number
            and record.participant != state["player_name"]
        }
        opponent = strategy_history.opponent_snapshot(
            rivals,
            # The round payload is only produced at all when every rival's
            # picks are in (`_strategy_payload` returns {} otherwise), so a
            # recorded round decision is always a complete-field one.
            field_complete=bool(round_strategy),
            rounds_of_history=len(history),
            joined_picks=sum(len(record.picks) for record in history),
        )
        for scope, produced in (
            (strategy_history.SCOPE_ROUND, round_strategy),
            (strategy_history.SCOPE_SEASON, payload["season_strategy"]),
        ):
            if produced:
                _record_strategy_decision(
                    rnd, produced, scope=scope, raw_picks=raw_picks,
                    opponent=opponent, config=config,
                )
    return payload


MAX_SYNTHETIC_ROUNDS = 12
SEASON_SIMULATIONS = 1000


def _season_strategy_payload(
    rnd: Round, table, history, favourite, recommendation, state: dict, config: WebConfig,
) -> dict:
    """§12-13 season-length strategy — a genuine season Monte Carlo
    (`season_simulation.py`) layered on top of `_strategy_payload`'s
    single-round view above. Returns `{}` (never a half-computed season
    estimate) whenever there isn't enough to run one: no recommendation
    yet, no rivals in the table, or the configured season has no rounds
    left.

    Performance note: only the *current* round's own contribution differs
    between candidates; every further round contributes the same shared
    noise regardless of which one is being tested. Simulating the full
    remaining season (up to ~38 rounds) at every page load is therefore
    unnecessary — a representative subset (`MAX_SYNTHETIC_ROUNDS`) stands
    in for the unknown-round horizon. This is a documented performance
    cap, not a claim that fewer rounds actually remain.
    """
    from . import opponent_intelligence as oi
    from . import season_simulation as ss
    from . import strategy
    from .league import opponent_profiles

    if recommendation is None:
        return {}
    player = state["player_name"]
    rounds_remaining = int(state.get("season_rounds_total", 38)) - rnd.round_number + 1
    if rounds_remaining < 1:
        return {}

    season_state = ss.season_state(table, player, rounds_remaining)
    if season_state is None:
        return {}

    profiles = {profile.participant: profile for profile in opponent_profiles(history)}
    average = oi.league_average_profile(history)
    joined = oi.join_picks_with_forecasts(history, prediction_path=config.prediction_history_path)
    average_rate = oi.league_average_favourite_rate(joined)
    bias_by_participant = {item.participant: item for item in favourite}

    current_round_picks = [
        record for record in history
        if record.season == _season_for(rnd) and record.round_number == rnd.round_number
        and record.participant != player
    ]
    if not any(row.participant != player for row in table):
        return {}

    candidates = list(strategy.build_candidates(recommendation, current_round_picks))
    correlate = strategy.build_correlate_candidate(
        recommendation, profiles, bias_by_participant, average, average_rate
    )
    if correlate is not None and correlate.picks not in {c.picks for c in candidates}:
        candidates.append(correlate)

    synthetic_rounds: list = []
    extra_rounds = min(rounds_remaining - 1, MAX_SYNTHETIC_ROUNDS)
    if extra_rounds > 0:
        matches = load_matches(
            config.data_path,
            list(range(config.seasons[0], config.seasons[1] + 1)),
            config.divisions,
            fetch=False,
        )
        router = _fit_router(matches)
        synthetic_rounds = [
            ss.sample_synthetic_round(matches, router, FROZEN_PHASE1_V1, seed=ss.DEFAULT_SEED, slot=slot)
            for slot in range(extra_rounds)
        ]

    simulation = ss.simulate_remaining_season(
        recommendation, candidates, table, player, current_round_picks,
        profiles, bias_by_participant, average, average_rate,
        synthetic_rounds=synthetic_rounds, simulations=SEASON_SIMULATIONS,
    )
    if simulation is None:
        return {}
    recommendation_out = ss.recommend_season_strategy(simulation)
    by_name = {c.name: c for c in candidates}
    return {
        "season_state": asdict(season_state),
        "mode": recommendation_out.mode,
        "active": recommendation_out.chosen.name != recommendation_out.baseline.name,
        "picks": [f"{h}-{a}" for h, a in by_name[recommendation_out.chosen.name].picks],
        "reason": (
            strategy.describe_candidate(by_name[recommendation_out.chosen.name], recommendation)
            + f" Simulated across {simulation.simulations} worlds and "
            f"{len(synthetic_rounds)} representative future rounds "
            f"(of {rounds_remaining - 1} remaining beyond this one)."
        ),
        "comparison": {
            "mean_gain_pp": round(recommendation_out.comparison.mean_gain * 100, 2),
            "lower_bound_pp": round(recommendation_out.comparison.lower_bound * 100, 2),
            "upper_bound_pp": round(recommendation_out.comparison.upper_bound * 100, 2),
        },
        "candidates": [
            {
                "name": candidate.name,
                "finish_first_probability": candidate.finish_first_probability,
                "expected_final_points": candidate.expected_final_points,
                "recommended": candidate.name == recommendation_out.chosen.name,
            }
            for candidate in simulation.candidates
        ],
    }


def _opponent_bias_payload(history, results, config: WebConfig):
    """§11 favourite-bias, team-identity bias and chase/defend tendencies,
    joined against our own stored forecasts (`opponent_intelligence.py`).
    Returns empty tuples rather than raising when there is nothing to join
    yet."""
    from . import opponent_intelligence as oi

    joined = oi.join_picks_with_forecasts(history, prediction_path=config.prediction_history_path)
    favourite = oi.favourite_bias_profiles(joined)
    team_bias = oi.team_bias_profiles(joined, promoted_teams=_promoted_teams(config))
    chase_defend = oi.chase_defend_tendencies(history, joined, results)
    return favourite, team_bias, chase_defend


def _promoted_teams(config: WebConfig) -> tuple[str, ...]:
    """Teams newly arrived in their division from below, for §11's
    "underrates promoted teams".

    Division membership only — `priors.arrivals` derives this from which
    division a team played in last season versus this one, never from
    results — so it stays public pre-season information and keeps
    `opponent_intelligence` free of any model dependency.

    "This season" means the most recent season present in the corpus, so
    between a season's promotions and its first fetched fixtures this
    answers for the season just gone. That resolves itself with no code
    change once `bym fetch` pulls the new season down; until then the
    lag is the honest answer rather than a guess at who went up.
    """
    from .model.priors import arrivals

    seasons = list(range(config.seasons[0], config.seasons[1] + 1))
    try:
        matches = load_matches(config.data_path, seasons, config.divisions, fetch=False)
    except FileNotFoundError:
        return ()
    latest = int(matches["season"].max())
    promoted = {
        team
        for division in config.divisions
        for team, direction in arrivals(matches, division, latest).items()
        if direction == "below"
    }
    return tuple(sorted(promoted))


def _fixture_favoured_result(fixture) -> str:
    """Our own recommended pick's implied result — the "favourite" a rival's
    pick is compared against. See opponent_intelligence.favourite_bias_profiles
    for the same approximation stated explicitly."""
    from . import opponent_intelligence as oi

    return oi.result_of(fixture.pick_home, fixture.pick_away)


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


def _current_forecast_version(season: int, round_number: int, config: WebConfig) -> int | None:
    """The forecast version a strategy decision is being taken against (§19)."""
    snapshots = prediction_history.round_snapshots(
        season, round_number, config.prediction_history_path
    )
    return snapshots[-1]["version"] if snapshots else None


def _record_strategy_decision(
    rnd: Round,
    payload: dict,
    *,
    scope: str,
    raw_picks,
    opponent,
    config: WebConfig,
) -> None:
    """Persist a computed recommendation so §19's linkage has a target.

    Best-effort by design: a decision record failing to write must never
    take down the page that shows the decision. The store is an audit
    trail, not a dependency of the recommendation itself.
    """
    strategy = payload.get("strategy") if scope == strategy_history.SCOPE_ROUND else payload
    if not strategy or not strategy.get("picks"):
        return
    season = _season_for(rnd)
    try:
        strategy_history.record_strategy(
            season,
            rnd.round_number,
            scope=scope,
            mode=strategy.get("mode", "NORMAL"),
            active=bool(strategy.get("active")),
            raw_picks=raw_picks,
            strategy_picks=strategy["picks"],
            reason=strategy.get("reason", ""),
            opponent=opponent,
            forecast_version=_current_forecast_version(season, rnd.round_number, config),
            path=config.strategy_history_path,
        )
    except (OSError, ValueError):
        pass


def research_now(payload: dict, config: WebConfig) -> dict:
    """Run one knowledge sweep, on demand (§8).

    Research left the page-load path because it is acquisition, not
    presentation — 36 Tavily credits and a dozen remote LLM calls per
    sweep, which made every request hang. But removing it entirely left no
    way to say "look now", which is exactly what you want in the hour
    before a deadline when a lineup lands.

    So: explicit, one sweep, and it reports what it cost. Refused when the
    monthly budget is nearly gone, because spending the last credits on a
    manual click is how you arrive at the deadline blind.
    """
    from . import worker as worker_mod
    from .knowledge import queries
    from .knowledge.config import build_extractor
    from .knowledge import worker as knowledge_worker

    extractor_config = web_extractor_config(config)
    if extractor_config.kind == "null":
        raise ValueError(
            "The knowledge extractor is off. Enable Ollama in Settings first."
        )
    tavily_key = os.environ.get("TAVILY_API_KEY")
    if not tavily_key:
        raise ValueError("Research needs a Tavily API key (secrets/tavily).")
    if worker_mod.knowledge_budget_low():
        raise ValueError(
            "Tavily's monthly credits are nearly gone. Scheduled sweeps have "
            "already degraded to a wider cadence; a manual sweep now would "
            "spend what is left before the deadline."
        )

    extractor = build_extractor(extractor_config)
    if not payload.get("force") and not worker_mod.extractor_is_responsive(extractor):
        # Refused rather than silently burning 36 credits on searches whose
        # articles cannot be read. `force` is the "gather evidence now,
        # extract later" escape hatch: evidence is stored independently of
        # extraction, so the articles are not wasted — they can be
        # re-extracted once the model keeps up.
        raise ValueError(
            "The model did not answer a trivial prompt in three attempts, so "
            "no credits were spent. Check Ollama is running, or retry with "
            "'gather evidence anyway' to cache the articles for later "
            "extraction."
        )

    rnd = load_round(config.round_path)
    accepted = rejected = failures = 0
    with db.connect(config.database_path) as conn:
        for fixture in rnd.fixtures:
            for team in (fixture.home, fixture.away):
                result = knowledge_worker.research(
                    conn, fixture.home, fixture.away,
                    queries.plan_team_queries(team), tavily_key, extractor,
                    round_number=rnd.round_number, mode=extractor_config.mode,
                )
                accepted += result.claims_accepted
                rejected += result.claims_rejected
                failures += result.extraction_failures
    return {
        "claims_accepted": accepted,
        "claims_rejected": rejected,
        "extraction_failures": failures,
        "credits_spent": len(rnd.fixtures) * 2 * 3,
        "message": (
            f"Researched {len(rnd.fixtures) * 2} teams — {accepted} claims "
            f"accepted, {rejected} rejected, {failures} extraction failures."
        ),
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


# ---------------------------------------------------------------------------
# Last Man Standing — separate game, separate state file. Reuses the same
# fitted Dixon-Coles router the Super 6 dashboard above just built, read as
# win/draw/loss instead of an exact score. Nothing here feeds back into the
# Super 6 round/engine state, and nothing above feeds into this.
# ---------------------------------------------------------------------------


def _lms_candidate_dict(candidate) -> dict:
    return {
        "team": candidate.team,
        "opponent": candidate.opponent,
        "is_home": candidate.is_home,
        "p_win": candidate.p_win,
        "p_draw": candidate.p_draw,
        "p_loss": candidate.p_loss,
        "confidence": candidate.confidence,
    }


def _lms_lineage_dict(steps) -> dict:
    """An entrant's path plus both cumulative survival curves.

    Both curves are computed here rather than in the browser so the chart
    and the table it twins are guaranteed to be showing the same numbers.
    """
    from . import lms as lms_mod

    yours = lms_mod.cumulative_survival([s.p_win for s in steps])
    plan = lms_mod.cumulative_survival([s.plan_p_win for s in steps])
    return {
        "steps": [
            {
                "gameweek": s.gameweek,
                "status": s.status,
                "team": s.team,
                "p_win": s.p_win,
                "opponent": s.opponent,
                "is_home": s.is_home,
                "plan_team": s.plan_team,
                "plan_p_win": s.plan_p_win,
                "delta": s.delta,
                "result": s.result,
                "survival_yours": yours[i],
                "survival_plan": plan[i],
            }
            for i, s in enumerate(steps)
        ],
        "survival_yours": yours[-1] if yours else None,
        "survival_plan": plan[-1] if plan else None,
    }


def _lms_previews(
    lms_mod, router, base_steps, gameweeks, outlooks, open_idx, candidates,
    reference_plan,
) -> dict:
    """For each club eligible this week, the horizon it leads to.

    Keyed by club name. The entrant's own state is copied per candidate
    rather than mutated — a preview that left a club in `used_clubs`
    would be a data-loss bug wearing a feature's clothes.
    """
    if not open_idx:
        return {}
    week_number = gameweeks[open_idx[0]].number
    later_idx = open_idx[1:]
    reference_by_gw = {p.gameweek: p for p in reference_plan.picks}
    # Weeks already settled are identical under every candidate, so they
    # are priced once here and reused. Calling `lineage` per candidate
    # instead would rebuild a score matrix for each, which is the cost the
    # "one model pass per gameweek" test exists to prevent.
    settled = [
        s for s in base_steps if s.status == "BOOKED" and s.gameweek != week_number
    ]
    spent = {s.team.lower() for s in settled if s.team}

    out = {}
    for candidate in candidates:
        team = candidate["team"]
        if team.lower() in spent:
            continue
        ref = reference_by_gw.get(week_number)
        chosen = lms_mod.LineageStep(
            gameweek=week_number,
            status="BOOKED",
            team=team,
            p_win=candidate["p_win"],
            plan_team=ref.candidate.team if ref else None,
            plan_p_win=ref.candidate.p_win if ref else None,
            opponent=candidate["opponent"],
            is_home=candidate["is_home"],
            result="PENDING",
        )
        rest = lms_mod.plan_horizon(
            router,
            [gameweeks[i] for i in later_idx],
            spent | {team.lower()},
            outlooks=[outlooks[i] for i in later_idx],
        )
        steps = settled + [chosen]
        for pick in rest.picks:
            later_ref = reference_by_gw.get(pick.gameweek)
            steps.append(
                lms_mod.LineageStep(
                    gameweek=pick.gameweek,
                    status="PLANNED",
                    team=pick.candidate.team,
                    p_win=pick.candidate.p_win,
                    plan_team=later_ref.candidate.team if later_ref else pick.candidate.team,
                    plan_p_win=later_ref.candidate.p_win if later_ref else pick.candidate.p_win,
                    opponent=pick.candidate.opponent,
                    is_home=pick.candidate.is_home,
                )
            )
        steps.sort(key=lambda s: s.gameweek)
        out[team] = _lms_lineage_dict(steps)
    return out


def _lms_plan_dict(plan) -> dict:
    """A horizon plan flattened for the UI, greedy comparison included.

    The comparison is part of the payload rather than a footnote because
    the plan's whole claim is that it beats taking the favourite; showing
    the recommendation without the number behind it would make it an
    assertion the user has to take on trust.
    """
    return {
        "picks": [
            {
                "gameweek": p.gameweek,
                "weeks_ahead": p.weeks_ahead,
                "planning_p_win": p.planning_p_win,
                **_lms_candidate_dict(p.candidate),
            }
            for p in plan.picks
        ],
        "survival_p": plan.survival_p,
        "greedy_survival_p": plan.greedy_survival_p,
        "survival_gain": plan.survival_gain,
        "greedy_team": plan.greedy_picks[0].candidate.team if plan.greedy_picks else None,
        "unplannable": list(plan.unplannable),
    }


def lms_payload(config: WebConfig, router=None, known_teams: set[str] | None = None) -> dict:
    """Every entrant's survival state, this gameweek's eligible clubs, and
    the multi-gameweek plan behind the recommendation.

    The gameweek itself is fetched from football-data.org, not typed in —
    see `_lms_current_gameweek`. `router`/`known_teams` let a caller that
    already fitted one for the Super 6 round hand it over instead of
    paying for a second fit — passed by `app_payload` below; a bare
    `lms_payload(config)` call (e.g. a future standalone endpoint) fits
    its own.

    Every entrant's ranking and plan comes off ONE pass of score matrices
    per gameweek (`fixture_outlooks`). The only thing that differs between
    entrants is which clubs are excluded, so recomputing the model per
    entrant would multiply the dashboard's cost by the size of the
    competition for identical numbers.
    """
    from . import lms as lms_mod

    states = lms_mod.load_states(config.lms_state_path)

    if router is None or known_teams is None:
        matches = load_matches(
            config.data_path,
            list(range(config.seasons[0], config.seasons[1] + 1)),
            config.divisions,
            fetch=False,
        )
        if known_teams is None:
            known_teams = _known_teams(matches)
        if router is None:
            router = _fit_router(matches)

    gameweeks, fixtures_info, fetched = _lms_current_gameweek(config, known_teams)

    # Settled before anything is read off `states`, and before the
    # no-fixtures branch below: the last gameweek of a season still has
    # picks to settle after the fixture list has run out.
    auto_settle_info = _lms_auto_settle(states, config, fetched)
    if auto_settle_info["settled"]:
        lms_mod.save_states(states, config.lms_state_path)

    entrants = {
        name: {"entrant": s.entrant, "alive": s.alive, "used_clubs": s.used_clubs, "history": s.history}
        for name, s in states.items()
    }

    if not gameweeks:
        return {
            "gameweek": None, "entrants": entrants, "recommendations": {}, "plans": {},
            "lineages": {}, "previews": {}, "switch_candidates": {},
            "decision_weeks": {}, "unknown_teams": [],
            "auto_settle": auto_settle_info,
            "fixtures_source": fixtures_info,
        }

    gw = gameweeks[0]
    unknown = sorted(
        {team for f in gw.fixtures for team in (f.home, f.away) if team not in known_teams}
    )

    outlooks = [lms_mod.fixture_outlooks(router, week) for week in gameweeks]

    def decision_index(state) -> int | None:
        """Which gameweek this entrant is actually deciding right now.

        A booked pick is not a closed decision until the round kicks off,
        so a changeable week is still the one being chosen — and offering
        the NEXT week's clubs beside a Switch button that posts THIS week's
        number would record a club against a gameweek it is not playing in.
        """
        booked = next(
            (h for h in (state.history if state else []) if h["gameweek"] == gameweeks[0].number),
            None,
        )
        if booked and not lms_mod.pick_is_locked(gameweeks[0], booked):
            return 0
        open_idx = lms_mod.open_gameweek_indices(gameweeks, state)
        return open_idx[0] if open_idx else None

    def for_used(used: set[str], state=None) -> tuple[list[dict], dict]:
        # Slice gameweeks and their score matrices together: a gameweek
        # this entrant has already booked is spent, and planning from it
        # both names a pick for a passed deadline and understates every
        # week after it. `outlooks` is shared across entrants, so the two
        # lists must be indexed by the same positions.
        open_idx = lms_mod.open_gameweek_indices(gameweeks, state)
        weeks = [gameweeks[i] for i in open_idx]
        week_outlooks = [outlooks[i] for i in open_idx]
        if not weeks:
            return [], _lms_plan_dict(lms_mod.plan_horizon(router, [], used))
        candidates = lms_mod.rank_candidates(week_outlooks[0], used)
        plan = lms_mod.plan_horizon(router, weeks, used, outlooks=week_outlooks)
        return [_lms_candidate_dict(c) for c in candidates], _lms_plan_dict(plan)

    recommendations, plans = {}, {}
    recommendations["__all__"], plans["__all__"] = for_used(set())
    # The unconstrained plan over the whole horizon — what "the
    # recommendation" meant for a week an entrant has already booked, and
    # so the only honest thing to compare a booked pick against.
    reference_plan = lms_mod.plan_horizon(router, gameweeks, set(), outlooks=outlooks)
    lineages = {}
    previews = {}
    switch_candidates = {}
    decision_weeks = {}
    for name, state in states.items():
        if state.alive:
            recommendations[name], plans[name] = for_used(
                set(state.used_clubs), state
            )
            open_idx = lms_mod.open_gameweek_indices(gameweeks, state)
            entrant_plan = lms_mod.plan_horizon(
                router,
                [gameweeks[i] for i in open_idx],
                set(state.used_clubs),
                outlooks=[outlooks[i] for i in open_idx],
            )
            entrant_steps = lms_mod.lineage(
                router, state, gameweeks, entrant_plan, reference_plan
            )
            lineages[name] = _lms_lineage_dict(entrant_steps)
            decision = decision_index(state)
            if decision is not None:
                booked_now = next(
                    (h for h in state.history
                     if h["gameweek"] == gameweeks[decision].number),
                    None,
                )
                # The club currently booked for this week does not block
                # its own week's ranking — switching away from it releases
                # it, and switching back to it is a no-op, not a clash.
                spent_elsewhere = {c.lower() for c in state.used_clubs} - (
                    {booked_now["team"].lower()} if booked_now else set()
                )
                switch_candidates[name] = [
                    _lms_candidate_dict(c)
                    for c in lms_mod.rank_candidates(
                        outlooks[decision], spent_elsewhere
                    )
                ]
                decision_weeks[name] = gameweeks[decision].number
                previews[name] = _lms_previews(
                    lms_mod, router, entrant_steps, gameweeks, outlooks,
                    [decision] + [i for i in open_idx if i > decision],
                    switch_candidates[name], reference_plan,
                )
            # One re-plan per eligible club for the week being picked.
            # "What do Hull cost me?" is a question about the horizon, not
            # about Saturday, and the only honest answer re-solves the
            # weeks after it with that club spent. Precomputed because the
            # score matrices are already in hand — the assignment is the
            # cheap part, and doing it here keeps the UI a pure render.

    return {
        "gameweek": {
            "number": gw.number,
            "fixtures": [
                {"home": f.home, "away": f.away, "odds": list(f.odds) if f.odds else None}
                for f in gw.fixtures
            ],
            "horizon": [week.number for week in gameweeks],
            # The kickoff that ends this gameweek's changeable window. The
            # UI needs it to say whether a switch is still possible, not
            # merely to be told no after trying.
            "deadline_at": gw.deadline_at.isoformat() if gw.deadline_at else None,
        },
        "entrants": entrants,
        "recommendations": recommendations,
        "plans": plans,
        "lineages": lineages,
        "previews": previews,
        "switch_candidates": switch_candidates,
        "decision_weeks": decision_weeks,
        "unknown_teams": unknown,
        "auto_settle": auto_settle_info,
        "fixtures_source": fixtures_info,
    }


def _lms_current_gameweek(config: WebConfig, known_teams: set[str]):
    """The gameweeks LMS should plan over right now, fetched from
    football-data.org's Premier League fixture list — nobody has to type
    fixtures in by hand. Falls back to whatever is cached at
    `lms_gameweek_path` (a previous successful fetch, or a manual entry
    from before a key was configured) when the live fetch can't run; the
    cache is overwritten on the next successful fetch, since the live list
    is the source of truth once a key is available.

    Returns (gameweeks, {"mode": "auto" | "manual" | "none", "note": ...},
    matches). The first gameweek is the one being picked; the rest are the
    lookahead, and come out of the same single request, so planning ahead
    costs no extra quota. `matches` is that fetched list, handed to
    `_lms_auto_settle` so one page load makes one request rather than two
    against a ten-per-minute free tier. It is None when nothing was
    fetched (no key, or the request failed), and the fallback cache can
    only ever supply one gameweek — a manually entered fixture list is
    this week's, so the plan degenerates to the weekly favourite.
    """
    from . import lms as lms_mod
    from .secrets import get_key
    from .sources import football_data_org as fdo
    from .sources.registry import SourceNotApprovedError
    from .sources.teams import resolve as resolve_team_name

    cached = None
    if config.lms_gameweek_path.exists():
        try:
            cached = lms_mod.load_gameweek(config.lms_gameweek_path)
        except (ValueError, KeyError, OSError):
            cached = None
    fallback = [cached] if cached else []

    api_key = get_key("FOOTBALL_DATA_ORG_KEY")
    if not api_key:
        note = (
            "No FOOTBALL_DATA_ORG_KEY configured — using the saved fixtures below."
            if cached else
            "No FOOTBALL_DATA_ORG_KEY configured — add fixtures manually below, "
            "or add a key to fetch them automatically."
        )
        return fallback, {"mode": "manual" if cached else "none", "note": note}, None

    matches = None
    try:
        matches = fdo.fetch_matches("E0", _lms_fd_season(), api_key)
        matchdays = fdo.upcoming_matchdays_with_kickoff(
            matches, DEFAULT_LMS.horizon_gameweeks
        )
        if not matchdays:
            note = "football-data.org has no upcoming Premier League fixtures right now."
            return fallback, {"mode": "manual" if cached else "none", "note": note}, matches
        gameweeks = [
            lms_mod.Gameweek(
                number=matchday,
                fixtures=tuple(
                    lms_mod.GameweekFixture(
                        home=resolve_team_name(m.home_team, known_teams) or m.home_team,
                        away=resolve_team_name(m.away_team, known_teams) or m.away_team,
                    )
                    for m in fdo.fixtures_for_matchday(matches, matchday)
                ),
                # The first kickoff closes this gameweek to changes.
                deadline_at=_parse_kickoff(kickoff),
            )
            for matchday, kickoff in matchdays
        ]
        _write_lms_gameweek(gameweeks[0], config.lms_gameweek_path)
        return gameweeks, {"mode": "auto", "note": None}, matches
    except SourceNotApprovedError as exc:
        return fallback, {"mode": "manual" if cached else "none", "note": str(exc)}, matches
    except Exception as exc:
        note = f"Fixture fetch failed: {exc}"
        if cached:
            note += " — using the last saved fixtures."
        return fallback, {"mode": "manual" if cached else "none", "note": note}, matches


def _parse_kickoff(value):
    """football-data.org's `utcDate` as an aware datetime, or None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _write_lms_gameweek(gw, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "gameweek": gw.number,
                **({"deadline_at": gw.deadline_at.isoformat()}
                   if gw.deadline_at else {}),
                "fixtures": [
                    {
                        "home": f.home,
                        "away": f.away,
                        **({"odds": list(f.odds)} if f.odds is not None else {}),
                    }
                    for f in gw.fixtures
                ],
            },
            indent=2,
        )
        + "\n"
    )


def _lms_auto_settle(states: dict, config: WebConfig, prefetched=None) -> dict:
    """Try to settle every PENDING pick from a real results feed before
    anyone sees them, so no entrant is ever the one deciding whether their
    own pick survived.

    Every pending pick, not just the current gameweek's: once a gameweek's
    last fixture has kicked off, the feed's current matchday moves on, and
    a pick tied to the gameweek on screen would never settle at all.

    `prefetched` is the Premier League match list `_lms_current_gameweek`
    already fetched this page load. Reused rather than re-requested: it is
    the same season's fixtures and results, from the same endpoint, and
    the free tier allows ten requests a minute across everything the
    system does.

    Degrades like every other external call in this file: no key, no
    coverage, or a request failure all just mean "nothing settled this
    time" with a note explaining why, never a failed page load. `lms-settle`
    on the CLI does the identical fetch for headless/cron use.
    """
    from . import lms as lms_mod
    from .secrets import get_key
    from .sources import football_data_org as fdo
    from .sources.registry import SourceNotApprovedError
    from .sources.teams import resolve as resolve_team_name

    api_key = get_key("FOOTBALL_DATA_ORG_KEY")
    if not api_key:
        return {
            "ran": False, "settled": [],
            "note": "No FOOTBALL_DATA_ORG_KEY configured — pending picks "
                     "must be settled by hand below until one is added.",
        }
    try:
        matches = list(prefetched) if prefetched else []
        for division in config.divisions:
            if division not in fdo.COMPETITION_CODES:
                continue
            if division == "E0" and prefetched:
                continue
            matches.extend(fdo.fetch_matches(division, _lms_fd_season(), api_key))
        settled = []
        for pending in lms_mod.pending_gameweeks(states).values():
            fixtures = [(f.home, f.away) for f in pending.fixtures]
            results = fdo.results_for_round(matches, fixtures, resolve_team_name)
            settled.extend(lms_mod.auto_settle(states, pending, results))
        return {"ran": True, "settled": settled, "note": None}
    except SourceNotApprovedError as exc:
        return {"ran": False, "settled": [], "note": str(exc)}
    except Exception as exc:
        return {"ran": False, "settled": [], "note": f"result fetch failed: {exc}"}


def _lms_fd_season() -> int:
    """English football-season start year from today's date (Aug-Dec belongs
    to the season starting that year; Jan-Jul belongs to the previous one)."""
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 7 else now.year - 1


def save_lms_gameweek(payload: dict, config: WebConfig) -> dict:
    """Manual override — only meaningful until the next successful
    football-data.org fetch overwrites the same cache file (see
    `_lms_current_gameweek`). Exists for when there's no API key, or this
    gameweek's fetched fixtures need correcting (a late postponement, a
    team name the resolver missed)."""
    from . import lms as lms_mod

    number = payload.get("gameweek")
    fixtures = payload.get("fixtures")
    if not isinstance(number, int) or not fixtures:
        raise ValueError("Provide a gameweek number and at least one fixture.")
    gw = lms_mod.gameweek_from_dict({"gameweek": number, "fixtures": fixtures})
    _write_lms_gameweek(gw, config.lms_gameweek_path)
    return lms_payload(config)


def save_lms_pick(payload: dict, config: WebConfig) -> dict:
    from . import lms as lms_mod

    entrant = str(payload.get("entrant") or "").strip()
    team = str(payload.get("team") or "").strip()
    opponent = str(payload.get("opponent") or "").strip()
    gameweek = payload.get("gameweek")
    if not entrant or not team or not opponent or gameweek is None:
        raise ValueError("entrant, gameweek, team and opponent are all required.")
    states = lms_mod.load_states(config.lms_state_path)
    state = states.get(entrant, lms_mod.LMSState(entrant=entrant))
    gameweek = int(gameweek)
    is_home = bool(payload.get("is_home"))
    # An existing pick for this gameweek is a change, not a duplicate: the
    # format allows one right up until the round kicks off, and refusing
    # it left the state file as the only way to correct a mis-click.
    if any(h["gameweek"] == gameweek for h in state.history):
        weeks, _, _ = _lms_current_gameweek(config, set())
        week = next((w for w in weeks if w.number == gameweek), None)
        lms_mod.change_pick(
            state, gameweek, team, opponent, is_home, gameweek_fixtures=week
        )
    else:
        lms_mod.record_pick(state, gameweek, team, opponent, is_home)
    states[entrant] = state
    lms_mod.save_states(states, config.lms_state_path)
    return lms_payload(config)


def save_lms_result(payload: dict, config: WebConfig) -> dict:
    from . import lms as lms_mod

    entrant = str(payload.get("entrant") or "").strip()
    gameweek = payload.get("gameweek")
    result = payload.get("result")
    if not entrant or gameweek is None or not result:
        raise ValueError("entrant, gameweek and result are all required.")
    states = lms_mod.load_states(config.lms_state_path)
    if entrant not in states:
        raise ValueError(f"no Last Man Standing state recorded for {entrant}")
    lms_mod.record_result(states[entrant], int(gameweek), result)
    lms_mod.save_states(states, config.lms_state_path)
    return lms_payload(config)


def app_payload(config: WebConfig) -> dict:
    """One truthful payload for all UI phases; absent evidence remains absent."""
    payload, engine_result = _dashboard(config)
    _, router, known_teams = _model_context(config)
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
            # Reuse the fit that built the main round rather than spending a
            # second model fit just to project the LMS horizon.
            "lms": lms_payload(
                config,
                router=router,
                known_teams=known_teams,
            ),
        }
    )
    return payload


class BymRequestHandler(BaseHTTPRequestHandler):
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
        except (ValueError, KeyError) as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        except OSError:
            logging.exception("Internal server error handling GET %s", path)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "An internal error occurred."}
            )

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
            elif path == "/api/research":
                self._send_json(HTTPStatus.OK, research_now(payload, self.config))
            elif path == "/api/ollama/models":
                self._send_json(HTTPStatus.OK, available_ollama_models(payload))
            elif path == "/api/opponents":
                self._send_json(HTTPStatus.OK, import_opponent(payload, self.config))
            elif path == "/api/results":
                self._send_json(HTTPStatus.OK, save_results(payload, self.config))
            elif path == "/api/cup-prediction":
                self._send_json(HTTPStatus.OK, cup_prediction(payload, self.config))
            elif path == "/api/lms/gameweek":
                self._send_json(HTTPStatus.CREATED, save_lms_gameweek(payload, self.config))
            elif path == "/api/lms/pick":
                self._send_json(HTTPStatus.OK, save_lms_pick(payload, self.config))
            elif path == "/api/lms/result":
                self._send_json(HTTPStatus.OK, save_lms_result(payload, self.config))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except OSError:
            logging.exception("Internal server error handling POST %s", path)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "An internal error occurred."}
            )


def missing_state_warnings(config: WebConfig) -> list[str]:
    """Paths the UI reads that aren't CLI flags and don't exist here.

    These resolve against the working directory, so starting the server
    from the wrong place silently produces a *different* deployment:
    separate UI state and forecast history, and — worst of the set — a
    source registry that falls back to built-in defaults where every
    source is REVIEW_REQUIRED, gating off odds and research without ever
    saying so. Cheap to warn about, expensive to diagnose from the
    symptoms (see `scripts/bym-service`, which pins the directory).
    """
    checked = (
        ("source registry", config.registry_path),
        ("UI state", config.ui_state_path),
        ("prediction history", config.prediction_history_path),
    )
    return [
        f"{label} not found at {path.resolve()}"
        for label, path in checked
        if not path.exists()
    ]


def serve(config: WebConfig, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the local UI. Press Ctrl-C to stop it."""
    # Do the one expensive historical-model fit before opening the browser
    # endpoint. The service's ready message then means a first page visit can
    # render immediately instead of leaving the user on a loading panel.
    _model_context(config)
    handler = type("ConfiguredBymHandler", (BymRequestHandler,), {"config": config})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Beat Your Mates UI running at http://{host}:{port}")
    print(f"Round: {config.round_path}")
    warnings = missing_state_warnings(config)
    if warnings:
        print(f"\n  ! Started from {Path.cwd()}")
        for warning in warnings:
            print(f"  ! {warning}")
        print(
            "  ! These are read relative to the working directory. A missing "
            "registry\n  !   means every source falls back to REVIEW_REQUIRED "
            "— odds and research\n  !   will be gated off. Run from the project "
            "root, or use scripts/bym-service.\n"
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBeat Your Mates UI stopped.")
    finally:
        server.server_close()
