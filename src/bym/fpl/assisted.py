"""Local audit trail for user-confirmed FPL browser assistance.

The ledger stores only a human-readable proposed action.  It never stores FPL
credentials, cookies, page HTML, or browser-session data, and it has no code
that can submit an action to FPL.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


ACTION_TYPES = frozenset({"TRANSFER", "CAPTAIN", "CHIP"})
PREPARED = "PREPARED"
CONFIRMED_FOR_BROWSER = "CONFIRMED_FOR_BROWSER"
COMPLETED_BY_MANAGER = "COMPLETED_BY_MANAGER"
CANCELLED = "CANCELLED"
CONFIRMATION_PHRASE = "I confirm this action"


class AssistedActionError(ValueError):
    """An assisted action is incomplete or tries to skip confirmation."""


@dataclass(frozen=True)
class AssistedAction:
    id: str
    action_type: str
    summary: str
    deadline: str | None
    status: str
    created_at: str
    confirmed_at: str | None = None
    completed_at: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(path: Path) -> list[AssistedAction]:
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text())
        return [AssistedAction(**row) for row in rows]
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise AssistedActionError(f"cannot read assisted-action ledger: {exc}") from exc


def _save(path: Path, actions: list[AssistedAction]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(action) for action in actions], indent=2) + "\n")


def prepare(path: Path, *, action_type: str, summary: str, deadline: str | None) -> AssistedAction:
    action_type = action_type.upper()
    summary = summary.strip()
    if action_type not in ACTION_TYPES:
        raise AssistedActionError("action type must be TRANSFER, CAPTAIN or CHIP")
    if not summary:
        raise AssistedActionError("describe the action before preparing browser assistance")
    action = AssistedAction(str(uuid4()), action_type, summary, deadline, PREPARED, _now())
    actions = load(path)
    actions.append(action)
    _save(path, actions)
    return action


def confirm_for_browser(path: Path, *, action_id: str, confirmation: str) -> AssistedAction:
    if confirmation.strip() != CONFIRMATION_PHRASE:
        raise AssistedActionError(f"type exactly: {CONFIRMATION_PHRASE}")
    actions = load(path)
    for index, action in enumerate(actions):
        if action.id != action_id:
            continue
        if action.status != PREPARED:
            raise AssistedActionError("only a prepared action can be confirmed for browser assistance")
        updated = AssistedAction(**{**asdict(action), "status": CONFIRMED_FOR_BROWSER, "confirmed_at": _now()})
        actions[index] = updated
        _save(path, actions)
        return updated
    raise AssistedActionError("assisted action was not found")


def complete_by_manager(path: Path, *, action_id: str) -> AssistedAction:
    actions = load(path)
    for index, action in enumerate(actions):
        if action.id != action_id:
            continue
        if action.status != CONFIRMED_FOR_BROWSER:
            raise AssistedActionError("only a browser-confirmed action can be marked complete")
        updated = AssistedAction(**{**asdict(action), "status": COMPLETED_BY_MANAGER, "completed_at": _now()})
        actions[index] = updated
        _save(path, actions)
        return updated
    raise AssistedActionError("assisted action was not found")


def payload(path: Path) -> dict:
    actions = load(path)
    return {
        "mode": "LOCAL_BROWSER_ASSISTED",
        "actions": [asdict(action) for action in reversed(actions)],
        "message": "No FPL credentials or session data are stored. Browser assistance needs a fresh, per-action confirmation.",
    }
