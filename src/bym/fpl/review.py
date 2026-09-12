"""Append-only local FPL decision reviews and post-deadline evidence.

Reviews freeze the exact manual inputs and model outputs visible before a
deadline.  Later rival observations are stored separately as evaluation
evidence, so they cannot rewrite the recommendation that was actually made.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class DecisionReviewError(ValueError):
    """A review/evidence write would blur the pre/post-deadline boundary."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str | None, *, label: str) -> datetime:
    if not value:
        raise DecisionReviewError(f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DecisionReviewError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise DecisionReviewError(f"{label} must include a timezone")
    return parsed


def _load(path: Path) -> dict:
    if not path.exists():
        return {"reviews": [], "actual_rival_evidence": []}
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise DecisionReviewError(f"cannot read decision history at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DecisionReviewError("decision history must be a JSON object")
    value.setdefault("reviews", [])
    value.setdefault("actual_rival_evidence", [])
    return value


def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def _decision_snapshot(dashboard: dict) -> dict:
    squad = dashboard.get("squad") or {}
    phase2 = dashboard.get("phase2") or {}
    phase3 = dashboard.get("phase3") or {}
    estimate = dashboard.get("lineup_estimate") or {}
    risks = list(estimate.get("downside") or [])
    transfer_plan = (phase2.get("plan") or {}).get("best_transfer_plan") or {}
    risks.extend(transfer_plan.get("price_risks") or [])
    risks.extend(transfer_plan.get("fixture_alerts") or [])
    return {
        "deadline": squad.get("deadline"),
        "status": dashboard.get("status"),
        "scope": (
            (phase2.get("plan") or {}).get("scope")
            or ("FULL MARKET" if squad.get("candidate_universe_complete") else "CURRENT SQUAD ONLY")
        ),
        "source": dashboard.get("source"),
        "squad": squad,
        "shortlist": dashboard.get("shortlist"),
        "lineup_estimate": dashboard.get("lineup_estimate"),
        "recommendation": dashboard.get("recommendation"),
        "phase2": phase2.get("plan"),
        "phase3": phase3.get("plan"),
        "risks": risks,
    }


def _fingerprint(snapshot: dict) -> str:
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def record(path: Path, dashboard: dict, *, now: datetime | None = None) -> dict:
    """Append an immutable review of the currently displayed decision."""
    if not dashboard.get("squad"):
        raise DecisionReviewError("save a squad before reviewing a decision")
    snapshot = _decision_snapshot(dashboard)
    deadline = _parse_time(snapshot.get("deadline"), label="squad deadline")
    reviewed_at = now or _now()
    if reviewed_at >= deadline:
        raise DecisionReviewError("pre-deadline decisions cannot be reviewed after the deadline")
    entry = {
        "id": uuid4().hex,
        "reviewed_at": reviewed_at.isoformat(),
        "fingerprint": _fingerprint(snapshot),
        **snapshot,
    }
    ledger = _load(path)
    ledger["reviews"].append(entry)
    _save(path, ledger)
    return entry


def add_actual_rival_evidence(
    path: Path, *, review_id: str, rival: str, squad: list[str], captain: str,
    observed_gameweek: int, observed_at: str, now: datetime | None = None,
) -> dict:
    """Append a post-deadline observation without mutating its source review."""
    ledger = _load(path)
    source = next((item for item in ledger["reviews"] if item["id"] == review_id), None)
    if source is None:
        raise DecisionReviewError("review_id does not identify a saved decision")
    deadline = _parse_time(source.get("deadline"), label="review deadline")
    recorded_at = now or _now()
    observed = _parse_time(observed_at, label="observed_at")
    if recorded_at < deadline or observed < deadline:
        raise DecisionReviewError("actual rival picks are evaluation evidence only after the deadline")
    cleaned = [str(name).strip() for name in squad if str(name).strip()]
    if len(cleaned) != 15 or len({name.casefold() for name in cleaned}) != 15:
        raise DecisionReviewError("actual rival evidence needs 15 distinct players")
    if not rival.strip() or captain not in cleaned or observed_gameweek < 1:
        raise DecisionReviewError("rival, observed Gameweek and a squad captain are required")
    entry = {
        "id": uuid4().hex,
        "review_id": review_id,
        "recorded_at": recorded_at.isoformat(),
        "observed_at": observed.isoformat(),
        "observed_gameweek": observed_gameweek,
        "rival": rival.strip(),
        "squad": cleaned,
        "captain": captain,
        "use": "POST_DEADLINE_EVALUATION_ONLY",
    }
    ledger["actual_rival_evidence"].append(entry)
    _save(path, ledger)
    return entry


def payload(path: Path, dashboard: dict | None = None) -> dict:
    ledger = _load(path)
    fingerprint = _fingerprint(_decision_snapshot(dashboard)) if dashboard and dashboard.get("squad") else None
    matching = next(
        (item for item in reversed(ledger["reviews"]) if item.get("fingerprint") == fingerprint),
        None,
    )
    return {
        "current_reviewed_at": matching.get("reviewed_at") if matching else None,
        "reviews": list(reversed(ledger["reviews"])),
        "actual_rival_evidence": list(reversed(ledger["actual_rival_evidence"])),
    }
