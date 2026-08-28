"""Append-only strategy-recommendation snapshots (SPEC.md §19).

§19 requires a `forecast_run` to answer two questions it currently cannot:
which recommendation was actually chosen — the raw model pick or the
strategy-adjusted one — and what opponent data existed at the moment that
decision was made.

Neither was answerable because no strategy recommendation was persisted
anywhere. `web._strategy_payload` and `_season_strategy_payload` compute
one fresh on every page load and throw it away, so there was nothing for a
forecast to link to. This module is that missing store, built to the same
shape as `prediction_history`: atomic writes, monotonic versions per
round, a diff against the previous snapshot, and a quiet no-op when
nothing has changed (a page refresh must not manufacture audit history).

**Direction of the link.** §19's diagram hangs `strategy_prediction` off
`forecast_run`. Implemented literally that would mean writing the forecast
record first and then reaching back to add a field once the strategy is
computed from it — which is precisely what an append-only store forbids,
and rewriting history to record history is self-defeating. The reference
therefore points the other way: a strategy snapshot names the
`forecast_version` it was derived from. §19's question ("for this forecast
run, what was the strategy pick?") is answered by joining on that, and the
causal order — forecast first, strategy second — is preserved rather than
implied.

Nothing here influences a forecast. This is a record of decisions already
made, in the same sense `prediction_history` is (§22).
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = 1
DEFAULT_STRATEGY_PATH = Path("data/strategy_history.json")

# Round-level (§12) and season-length (§13) strategy are recommendations
# about the same round from different horizons, and can disagree — the
# round view optimises this round, the season view the remaining season.
# They are versioned separately so neither overwrites the other's history.
SCOPE_ROUND = "ROUND"
SCOPE_SEASON = "SEASON"
SCOPES = (SCOPE_ROUND, SCOPE_SEASON)

# Fields whose change is worth a diff entry. `reason` is deliberately
# excluded: it embeds live probabilities, so it changes on almost every
# recomputation and would drown the genuine decision changes.
_TRACKED_FIELDS = ("mode", "active", "strategy_picks", "raw_picks", "forecast_version")


def _utc_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def _round_key(season: int, round_number: int) -> str:
    return f"{season}:{round_number}"


def _picks(value: Any) -> list[str]:
    """Normalise a pick set to a list of "H-A" strings.

    Accepts either the string form the UI payloads carry or the (home,
    away) tuples the strategy modules pass around, so callers do not each
    convert it differently.
    """
    if value is None:
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
        else:
            home, away = item
            out.append(f"{int(home)}-{int(away)}")
    return out


def _empty_store() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "rounds": {}}


def load_history(path: Path = DEFAULT_STRATEGY_PATH) -> dict[str, Any]:
    """Load the store, rejecting a malformed file rather than erasing it."""
    if not path.exists():
        return _empty_store()
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict) or not isinstance(payload.get("rounds"), dict):
        raise ValueError("strategy history is not a valid snapshot store")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported strategy history schema version")
    return payload


def save_history(store: Mapping[str, Any], path: Path = DEFAULT_STRATEGY_PATH) -> None:
    """Atomically save, so an interrupted write keeps the prior history.

    Unique temp file per call — see `prediction_history.save_history`'s
    docstring for why a fixed `.tmp` name races under the web UI's
    concurrent `/api/app` requests.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(store, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def opponent_snapshot(
    participants: Sequence[str],
    *,
    field_complete: bool,
    rounds_of_history: int = 0,
    joined_picks: int = 0,
) -> dict[str, Any]:
    """§19's `opponent_snapshot`: what was known about rivals at decision time.

    Compact by design, like `prediction_history`'s market snapshot — a
    handful of stable counts rather than a copy of every pick, which the
    opponent history already holds. `field_complete` is the one that
    actually qualifies the decision: a strategy computed against a partial
    field is a different kind of claim from one computed against all of it.
    """
    # Total order, not merely case-insensitive: two names differing only by
    # case tie under casefold alone, and `sorted` would then fall back to
    # the set's iteration order, which Python randomises per process. That
    # would make this snapshot vary run to run — and because snapshots are
    # compared for equality, a rerun would append a spurious version
    # recording a decision nobody changed. Same hazard `model/priors.py`
    # documents for the MLE penalty term.
    names = sorted({str(name) for name in participants}, key=lambda name: (name.casefold(), name))
    return {
        "participants": len(names),
        "participant_names": names,
        "field_complete": bool(field_complete),
        "rounds_of_history": int(rounds_of_history),
        "joined_picks": int(joined_picks),
    }


def _changes(before: Mapping[str, Any] | None, after: Mapping[str, Any]) -> list[dict[str, Any]]:
    if before is None:
        return []
    return [
        {"field": field, "was": before.get(field), "now": after.get(field)}
        # `field in before` distinguishes a genuine change from a field
        # that simply postdates the earlier snapshot — the same trap
        # prediction_history documents.
        for field in _TRACKED_FIELDS
        if field in before and before.get(field) != after.get(field)
    ]


def record_strategy(
    season: int,
    round_number: int,
    *,
    scope: str,
    mode: str,
    active: bool,
    raw_picks: Any,
    strategy_picks: Any,
    reason: str = "",
    opponent: Mapping[str, Any] | None = None,
    forecast_version: int | None = None,
    now: datetime | None = None,
    path: Path = DEFAULT_STRATEGY_PATH,
) -> dict[str, Any]:
    """Append one strategy decision, returning the stored snapshot.

    Re-recording an identical decision is a no-op that returns the existing
    snapshot without writing: `_strategy_payload` runs on every page load,
    and a refresh is not a decision. Only a change in what was actually
    recommended creates a new version.
    """
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}, got {scope!r}")

    record = {
        "scope": scope,
        "mode": str(mode),
        "active": bool(active),
        "raw_picks": _picks(raw_picks),
        "strategy_picks": _picks(strategy_picks),
        "reason": str(reason),
        "opponent_snapshot": dict(opponent) if opponent else None,
        "forecast_version": forecast_version,
    }

    store = load_history(path)
    key = _round_key(season, round_number)
    round_data = store["rounds"].setdefault(
        key, {"season": season, "round_number": round_number, "snapshots": []}
    )
    snapshots = round_data.setdefault("snapshots", [])
    if not isinstance(snapshots, list):
        raise ValueError("strategy history snapshots are not valid")

    same_scope = [item for item in snapshots if item.get("scope") == scope]
    previous = same_scope[-1] if same_scope else None
    if previous is not None and all(
        previous.get(field) == record.get(field)
        for field in (*_TRACKED_FIELDS, "opponent_snapshot")
    ):
        return copy.deepcopy(previous)

    snapshot = {
        "version": len(same_scope) + 1,
        "recorded_at": _utc_iso(now),
        **record,
        "changes": _changes(previous, record),
    }
    snapshots.append(snapshot)
    save_history(store, path)
    return copy.deepcopy(snapshot)


def round_snapshots(
    season: int,
    round_number: int,
    path: Path = DEFAULT_STRATEGY_PATH,
    *,
    scope: str | None = None,
) -> list[dict[str, Any]]:
    """Every stored decision for a round, oldest first, optionally by scope."""
    data = load_history(path)["rounds"].get(_round_key(season, round_number), {})
    snapshots = copy.deepcopy(data.get("snapshots", []))
    if scope is None:
        return snapshots
    return [item for item in snapshots if item.get("scope") == scope]


def latest(
    season: int,
    round_number: int,
    scope: str = SCOPE_ROUND,
    path: Path = DEFAULT_STRATEGY_PATH,
) -> dict[str, Any] | None:
    """The current decision of that scope, or None if none was ever recorded."""
    snapshots = round_snapshots(season, round_number, path, scope=scope)
    return snapshots[-1] if snapshots else None


def for_forecast_version(
    season: int,
    round_number: int,
    forecast_version: int,
    path: Path = DEFAULT_STRATEGY_PATH,
    *,
    scope: str | None = None,
) -> list[dict[str, Any]]:
    """§19's actual question, answered by join: for this `forecast_run`,
    which strategy decisions were taken against it?

    Returns every matching decision rather than one, because a single
    forecast can be re-strategised as rivals' picks arrive — the field
    filling up changes the recommendation without the forecast moving.
    """
    return [
        snapshot
        for snapshot in round_snapshots(season, round_number, path, scope=scope)
        if snapshot.get("forecast_version") == forecast_version
    ]
