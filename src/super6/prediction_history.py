"""Append-only forecast snapshots and user-facing prediction change history.

The forecasting engine is intentionally stateless: each run produces the
best current recommendation from the evidence it can see.  The UI needs a
separate, durable record in order to answer equally important questions such
as "what changed?" and "has this moved since I last looked?".  This module is
that record.  It deliberately stores plain JSON rather than changing the
forecast database schema, so it can be used by both the local UI and a future
API worker.

Snapshots are never edited or removed.  A small ``visits`` map sits alongside
them solely to remember when a named viewer last opened a round; it is not
part of the forecast audit trail.
"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_HISTORY_PATH = Path("data/prediction_history.json")
SCHEMA_VERSION = 1
_TRACKED_FIELDS = ("result", "score", "confidence", "expected_points", "alternatives", "basis")


def _utc_iso(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _round_key(season: int, round_number: int) -> str:
    if not isinstance(season, int) or not isinstance(round_number, int):
        raise TypeError("season and round_number must be integers")
    return f"{season}:{round_number}"


def _plain(value: Any) -> Any:
    """Convert a contract/dataclass-like value to JSON-safe plain data."""
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _score_result(score: str | None) -> str | None:
    if not isinstance(score, str) or "-" not in score:
        return None
    try:
        home, away = (int(part.strip()) for part in score.split("-", 1))
    except ValueError:
        return None
    if home > away:
        return "HOME"
    if home < away:
        return "AWAY"
    return "DRAW"


def _number(value: Any, digits: int) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _fixture_key(fixture: Mapping[str, Any], index: int) -> str:
    for key in ("fixture_id", "id", "key"):
        value = fixture.get(key)
        if value not in (None, ""):
            return str(value)
    home = fixture.get("home_team", fixture.get("home"))
    away = fixture.get("away_team", fixture.get("away"))
    if home and away:
        return f"{home}::{away}"
    return f"fixture-{index + 1}"


def _alternatives(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalised = []
    for alternative in value:
        plain = _plain(alternative)
        if not isinstance(plain, Mapping):
            continue
        score = plain.get("score", plain.get("pick"))
        normalised.append(
            {
                "score": str(score) if score is not None else None,
                "score_probability": _number(
                    plain.get("score_probability", plain.get("probability")), 4
                ),
                "expected_points": _number(
                    plain.get("expected_points", plain.get("ev")), 3
                ),
            }
        )
    return normalised


def _market_snapshot(value: Any) -> dict[str, Any] | None:
    """§19 odds-snapshot linkage: the market state actually consumed by
    this fixture's forecast, not the live value (which keeps moving after
    the snapshot is taken). Compact by design — a handful of stable
    numbers, not the full bookmaker-by-bookmaker breakdown."""
    plain = _plain(value)
    if not isinstance(plain, Mapping) or not plain.get("available"):
        return None
    return {
        "home_probability": _number(plain.get("home_probability"), 4),
        "draw_probability": _number(plain.get("draw_probability"), 4),
        "away_probability": _number(plain.get("away_probability"), 4),
        "bookmaker_count": plain.get("bookmaker_count"),
        "market_agreement": plain.get("market_agreement"),
        "stale": bool(plain.get("stale", False)),
    }


def _availability_snapshot(value: Any) -> dict[str, Any] | None:
    """§19 news/team-intelligence snapshot linkage. Counts and quality
    labels only, not the individual claims — those already have their own
    provenance trail (knowledge/store.py); this just records how much of
    it existed when the forecast was made. `quantitative_impact` is always
    "NONE DIRECTLY" today (docs/phase2_increment3.md) — recorded as-is
    rather than omitted, so a future reader can see evidence existed
    without it having changed anything, instead of wondering if it was
    silently dropped."""
    plain = _plain(value)
    if not isinstance(plain, Mapping):
        return None
    items = plain.get("items")
    return {
        "confirmed_lineups": plain.get("confirmed_lineups"),
        "expected_lineups": plain.get("expected_lineups"),
        "data_quality": plain.get("data_quality"),
        "feature_completeness": plain.get("feature_completeness"),
        "item_count": len(items) if isinstance(items, list) else 0,
        "quantitative_impact": plain.get("quantitative_impact", "NONE DIRECTLY"),
    }


def normalise_fixture(fixture: Mapping[str, Any] | Any, index: int) -> dict[str, Any]:
    """Return the small, stable part of a fixture needed for comparison —
    plus, per §19, enough of its *inputs* to answer "why did we predict
    this" later: the model basis, and the market/availability state the
    forecast actually consumed. Reproducibility for the two things that
    can currently move a forecast (§30's model, §32's market blend);
    availability is recorded for provenance even though nothing yet routes
    from it to a probability (docs/phase2_increment3.md). Opponent/strategy
    snapshot linkage (§19's `opponent_snapshot`, `strategy_prediction`) is
    not included — no strategy recommendation is persisted anywhere yet to
    link to, so there would be nothing honest to point at.

    The function accepts either a read-model contract or the dictionary it
    serialises to.  It keeps the raw team labels for display but deliberately
    does not copy volatile source data into every snapshot.
    """
    plain = _plain(fixture)
    if not isinstance(plain, Mapping):
        raise TypeError("each fixture must be a mapping or provide to_dict()")
    score = plain.get("score", plain.get("pick"))
    score = str(score) if score is not None else None
    result = plain.get("result", plain.get("outcome"))
    result = str(result).upper() if result is not None else _score_result(score)
    return {
        "fixture_key": _fixture_key(plain, index),
        "fixture_index": index,
        "home_team": plain.get("home_team", plain.get("home")),
        "away_team": plain.get("away_team", plain.get("away")),
        "result": result,
        "score": score,
        "confidence": plain.get("confidence"),
        "expected_points": _number(
            plain.get("expected_points", plain.get("ev")), 3
        ),
        "alternatives": _alternatives(plain.get("alternatives")),
        "basis": plain.get("forecast_basis", plain.get("basis")),
        "market_snapshot": _market_snapshot(plain.get("market")),
        "availability_snapshot": _availability_snapshot(plain.get("availability")),
    }


def _empty_store() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "rounds": {}, "visits": {}}


def load_history(path: Path = DEFAULT_HISTORY_PATH) -> dict[str, Any]:
    """Load snapshot data, rejecting malformed files rather than erasing it."""
    if not path.exists():
        return _empty_store()
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict) or not isinstance(payload.get("rounds"), dict):
        raise ValueError("prediction history is not a valid snapshot store")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported prediction history schema version")
    payload.setdefault("visits", {})
    if not isinstance(payload["visits"], dict):
        raise ValueError("prediction history visits are not valid")
    return payload


def save_history(store: Mapping[str, Any], path: Path = DEFAULT_HISTORY_PATH) -> None:
    """Atomically save the store so an interrupted refresh keeps prior history."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _changes(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    before_by_key = {fixture["fixture_key"]: fixture for fixture in before}
    after_by_key = {fixture["fixture_key"]: fixture for fixture in after}
    changes: list[dict[str, Any]] = []
    for fixture in after:
        previous = before_by_key.get(fixture["fixture_key"])
        if previous is None:
            changes.append(
                {
                    "fixture_key": fixture["fixture_key"],
                    "fixture_index": fixture["fixture_index"],
                    "home_team": fixture.get("home_team"),
                    "away_team": fixture.get("away_team"),
                    "kind": "ADDED",
                    "fields": [],
                }
            )
            continue
        # A field absent from the *previous* snapshot (not merely `None`)
        # means it postdates that snapshot — a newly tracked field (like
        # `basis` here) appearing for the first time on an already-running
        # round, not a real forecast change. `field in previous` — not
        # `.get()` — is what tells those two cases apart; without it every
        # existing round would report a spurious one-off "changed" the
        # moment a new field is added to _TRACKED_FIELDS.
        fields = [
            {"field": field, "was": previous.get(field), "now": fixture.get(field)}
            for field in _TRACKED_FIELDS
            if field in previous and previous.get(field) != fixture.get(field)
        ]
        if fields:
            changes.append(
                {
                    "fixture_key": fixture["fixture_key"],
                    "fixture_index": fixture["fixture_index"],
                    "home_team": fixture.get("home_team"),
                    "away_team": fixture.get("away_team"),
                    "kind": "CHANGED",
                    "fields": fields,
                }
            )
    for fixture in before:
        if fixture["fixture_key"] not in after_by_key:
            changes.append(
                {
                    "fixture_key": fixture["fixture_key"],
                    "fixture_index": fixture["fixture_index"],
                    "home_team": fixture.get("home_team"),
                    "away_team": fixture.get("away_team"),
                    "kind": "REMOVED",
                    "fields": [],
                }
            )
    return changes


def record_snapshot(
    season: int,
    round_number: int,
    fixtures: Iterable[Mapping[str, Any] | Any],
    *,
    model_version: str = "",
    now: datetime | None = None,
    path: Path = DEFAULT_HISTORY_PATH,
) -> dict[str, Any]:
    """Append a complete forecast snapshot and return its precise diff.

    The first snapshot has no changes: there is no earlier recommendation to
    compare it with.  Later snapshots compare outcomes, exact scores,
    confidence bands, expected points and the ordered alternatives list.
    """
    key = _round_key(season, round_number)
    normalised = [normalise_fixture(fixture, index) for index, fixture in enumerate(fixtures)]
    seen = set()
    for fixture in normalised:
        if fixture["fixture_key"] in seen:
            raise ValueError("forecast snapshot contains duplicate fixture keys")
        seen.add(fixture["fixture_key"])

    store = load_history(path)
    round_data = store["rounds"].setdefault(
        key, {"season": season, "round_number": round_number, "snapshots": []}
    )
    snapshots = round_data.setdefault("snapshots", [])
    if not isinstance(snapshots, list):
        raise ValueError("prediction history snapshots are not valid")
    previous = snapshots[-1]["fixtures"] if snapshots else []
    # Routine refreshes should remain quiet.  They are still represented by
    # the current engine payload, but do not create a meaningless duplicate
    # audit version or re-send an alert.
    if (
        snapshots
        and previous == normalised
        and snapshots[-1].get("model_version") == str(model_version)
    ):
        return copy.deepcopy(snapshots[-1])
    snapshot = {
        "version": len(snapshots) + 1,
        "recorded_at": _utc_iso(now),
        "model_version": str(model_version),
        "fixtures": normalised,
        "changes": _changes(previous, normalised) if snapshots else [],
    }
    snapshots.append(snapshot)
    save_history(store, path)
    return copy.deepcopy(snapshot)


def capture_round_summary(
    season: int,
    summary: Mapping[str, Any] | Any,
    *,
    now: datetime | None = None,
    path: Path = DEFAULT_HISTORY_PATH,
) -> dict[str, Any]:
    """Convenience API for a ``RoundSummary`` contract or its JSON payload."""
    plain = _plain(summary)
    if not isinstance(plain, Mapping):
        raise TypeError("round summary must be a mapping or provide to_dict()")
    round_number = plain.get("round_number")
    fixtures = plain.get("fixtures")
    if not isinstance(round_number, int) or not isinstance(fixtures, list):
        raise ValueError("round summary requires round_number and fixtures")
    return record_snapshot(
        season,
        round_number,
        fixtures,
        model_version=str(plain.get("model_version", "")),
        now=now,
        path=path,
    )


def round_snapshots(
    season: int, round_number: int, path: Path = DEFAULT_HISTORY_PATH
) -> list[dict[str, Any]]:
    """Return an immutable copy of every append-only snapshot for a round."""
    data = load_history(path)["rounds"].get(_round_key(season, round_number), {})
    return copy.deepcopy(data.get("snapshots", []))


def current_score_picks(
    season: int, round_number: int, path: Path = DEFAULT_HISTORY_PATH
) -> list[str]:
    """Return the latest score picks in fixture order for submission reconcile.

    ``submission.reconcile`` purposefully works with the exact score strings
    a person entered in Super 6.  Keeping this adapter here makes the
    integration explicit without coupling the append-only history store to
    the submission state machine.
    """
    snapshots = round_snapshots(season, round_number, path)
    if not snapshots:
        return []
    fixtures = sorted(
        snapshots[-1]["fixtures"], key=lambda fixture: fixture["fixture_index"]
    )
    return [fixture["score"] for fixture in fixtures if fixture["score"] is not None]


def fixture_history(
    season: int,
    round_number: int,
    fixture_key: str,
    path: Path = DEFAULT_HISTORY_PATH,
) -> list[dict[str, Any]]:
    """Return the version trail for one fixture, oldest first."""
    history = []
    for snapshot in round_snapshots(season, round_number, path):
        fixture = next(
            (item for item in snapshot["fixtures"] if item["fixture_key"] == fixture_key),
            None,
        )
        if fixture is None:
            continue
        history.append(
            {
                "version": snapshot["version"],
                "recorded_at": snapshot["recorded_at"],
                "model_version": snapshot["model_version"],
                "fixture": fixture,
                "changes": [
                    change
                    for change in snapshot["changes"]
                    if change["fixture_key"] == fixture_key
                ],
            }
        )
    return history


def last_visited_at(
    season: int,
    round_number: int,
    *,
    viewer: str = "default",
    path: Path = DEFAULT_HISTORY_PATH,
) -> str | None:
    return load_history(path)["visits"].get(_round_key(season, round_number), {}).get(viewer)


def changes_since_last_visit(
    season: int,
    round_number: int,
    *,
    viewer: str = "default",
    path: Path = DEFAULT_HISTORY_PATH,
) -> list[dict[str, Any]]:
    """Return all snapshot changes recorded after this viewer last opened it."""
    visited_at = last_visited_at(season, round_number, viewer=viewer, path=path)
    if visited_at is None:
        return []
    changes = []
    for snapshot in round_snapshots(season, round_number, path):
        if snapshot["recorded_at"] > visited_at:
            changes.extend(snapshot["changes"])
    return changes


def mark_visited(
    season: int,
    round_number: int,
    *,
    viewer: str = "default",
    now: datetime | None = None,
    path: Path = DEFAULT_HISTORY_PATH,
) -> str:
    """Persist a viewer's last-opened timestamp without modifying snapshots."""
    if not viewer or len(viewer) > 200:
        raise ValueError("viewer must be between 1 and 200 characters")
    store = load_history(path)
    store["visits"].setdefault(_round_key(season, round_number), {})[viewer] = _utc_iso(now)
    save_history(store, path)
    return store["visits"][_round_key(season, round_number)][viewer]


def changes_for_visit(
    season: int,
    round_number: int,
    *,
    viewer: str = "default",
    now: datetime | None = None,
    path: Path = DEFAULT_HISTORY_PATH,
) -> list[dict[str, Any]]:
    """Read changes since the prior visit, then mark this visit as seen."""
    changes = changes_since_last_visit(
        season, round_number, viewer=viewer, path=path
    )
    mark_visited(season, round_number, viewer=viewer, now=now, path=path)
    return changes
