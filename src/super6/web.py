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
    seasons: tuple[int, int] = (2019, 2025)
    divisions: tuple[str, ...] = ("E0", "E1")


def _fit_router(matches):
    """Use the frozen production configuration without making web depend on CLI."""
    from .cli import _fit_router

    return _fit_router(matches, FROZEN_PHASE1_V1.xi, FROZEN_PHASE1_V1)


def _season_for(rnd: Round) -> int:
    return rnd.deadline_at.year if rnd.deadline_at.year else datetime.now().year


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


def dashboard_payload(config: WebConfig) -> dict:
    """Build a fresh decision payload from the live engine contracts."""
    rnd = load_round(config.round_path)
    matches = load_matches(
        config.data_path,
        list(range(config.seasons[0], config.seasons[1] + 1)),
        config.divisions,
        fetch=False,
    )
    router = _fit_router(matches)
    with db.connect(config.database_path) as conn:
        result = run_engine(conn, rnd, router)
        health = service.build_system_health(
            conn,
            registry=load_registry(config.registry_path),
            schema_version=db.schema_version(conn),
        )

    picks = [fixture.pick for fixture in result.round_summary.fixtures]
    ui_state = load_ui_state(config.ui_state_path)
    override_picks = ui_state["overrides"].get(_round_key(rnd), {})
    record = submission.get(
        _season_for(rnd), rnd.round_number, path=config.submissions_path
    )
    # Reconcile is intentionally performed only by user actions or forecast
    # refreshes; a display request must not quietly alter user state.
    summary = result.round_summary.to_dict()
    for index, fixture in enumerate(summary["fixtures"]):
        fixture["strategy_pick"] = fixture["pick"]
        fixture["user_pick"] = override_picks.get(str(index))
    summary["submission_state"] = record.state
    summary["submission"] = {
        "submitted_picks": record.submitted_picks,
        "submitted_golden_goal": record.submitted_golden_goal,
        "submitted_at": record.submitted_at,
        "reviewed_at": record.reviewed_at,
        "drift": record.drift,
    }
    summary["meta"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "round_source": str(config.round_path),
        "current_picks": picks,
    }
    return {
        "round": summary,
        "health": health.to_dict(),
        "notes": result.notes,
        "settings": {
            key: ui_state[key]
            for key in ("league_name", "player_name", "notifications", "budget")
        },
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
    return {"submission": vars(record)}


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
    return {"settings": {key: state[key] for key in ("league_name", "player_name", "notifications", "budget")}}


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


def app_payload(config: WebConfig) -> dict:
    """One truthful payload for all UI phases; absent evidence remains absent."""
    payload = dashboard_payload(config)
    rnd = load_round(config.round_path)
    state = load_ui_state(config.ui_state_path)
    registry = load_registry(config.registry_path)
    activity = [asdict(event) for event in events.load(config.event_log_path)[-100:]][::-1]
    sources = [asdict(record) for _, record in sorted(registry.items())]
    payload.update(
        {
            "activity": activity,
            "performance": _performance_payload(config),
            "results": _results_payload(rnd, payload["round"], state),
            "opponents": state["opponents"],
            "sources": sources,
            "model": {
                "version": payload["round"]["model_version"],
                "frozen_config": asdict(FROZEN_PHASE1_V1),
                "note": "The quantitative core is frozen as PHASE1_MODEL_V1. Later layers are shown separately until prospective evidence promotes them.",
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
        self.send_header("Cache-Control", "no-cache")
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
