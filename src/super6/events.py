"""Structured activity events (SPEC.md §21, §53).

Events are emitted as structured records with their fields already
separated, so the eventual UI reads them directly rather than
reverse-engineering meaning from database timestamps. Append-only: an
event describes something that happened, and history is not edited.

Note that MARKET_MOVEMENT and PREDICTION_CHANGED are deliberately
distinct. Market movement is already consumed mechanically by the frozen
model's w=0.5 blend; the event records that it happened and whether it
mattered. It never applies a second adjustment (§32 double-counting).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

DEFAULT_EVENT_LOG = Path("data/events/activity.jsonl")


class EventType(str, Enum):
    MARKET_SNAPSHOT = "MARKET_SNAPSHOT"
    MARKET_MOVEMENT = "MARKET_MOVEMENT"
    MARKET_MAJOR_MOVEMENT = "MARKET_MAJOR_MOVEMENT"
    FORECAST_RECALCULATED = "FORECAST_RECALCULATED"
    PREDICTION_CHANGED = "PREDICTION_CHANGED"
    CONFIDENCE_CHANGED = "CONFIDENCE_CHANGED"
    POLL_FAILED = "POLL_FAILED"
    # Increment 3 — team availability intelligence
    AVAILABILITY_REPORTED = "AVAILABILITY_REPORTED"
    AVAILABILITY_CONFIRMED = "AVAILABILITY_CONFIRMED"
    PLAYER_RETURNED = "PLAYER_RETURNED"
    EXPECTED_LINEUP_UPDATED = "EXPECTED_LINEUP_UPDATED"
    LINEUP_CONFIRMED = "LINEUP_CONFIRMED"
    LINEUP_SURPRISE = "LINEUP_SURPRISE"
    TEAM_NEWS_CONFLICT = "TEAM_NEWS_CONFLICT"
    PLAYER_UNRESOLVED = "PLAYER_UNRESOLVED"
    INTELLIGENCE_EXPLAINS_MARKET_MOVE = "INTELLIGENCE_EXPLAINS_MARKET_MOVE"
    # Increment 4 — knowledge agent
    CLAIM_DISCOVERED = "CLAIM_DISCOVERED"
    CLAIM_CORROBORATED = "CLAIM_CORROBORATED"
    CLAIM_CONTRADICTED = "CLAIM_CONTRADICTED"
    CLAIM_REJECTED = "CLAIM_REJECTED"
    CLAIM_SUPERSEDED = "CLAIM_SUPERSEDED"
    KNOWLEDGE_EXTRACTION_FAILED = "KNOWLEDGE_EXTRACTION_FAILED"


class Severity(str, Enum):
    """§54 alert severity. INFORMATIONAL events stay in Activity and do
    not generate push notifications by default."""

    CRITICAL = "CRITICAL"
    IMPORTANT = "IMPORTANT"
    INFORMATIONAL = "INFORMATIONAL"


@dataclass
class Event:
    event_type: str
    severity: str
    occurred_at: str
    round_number: int | None = None
    home_team: str | None = None
    away_team: str | None = None
    summary: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def fixture(self) -> str:
        if self.home_team and self.away_team:
            return f"{self.home_team} v {self.away_team}"
        return ""


def make_event(
    event_type: EventType,
    severity: Severity,
    summary: str,
    round_number: int | None = None,
    home_team: str | None = None,
    away_team: str | None = None,
    detail: dict | None = None,
    now: datetime | None = None,
) -> Event:
    now = now or datetime.now(timezone.utc)
    return Event(
        event_type=event_type.value,
        severity=severity.value,
        occurred_at=now.isoformat(timespec="seconds"),
        round_number=round_number,
        home_team=home_team,
        away_team=away_team,
        summary=summary,
        detail=detail or {},
    )


def append(events: list[Event], path: Path = DEFAULT_EVENT_LOG) -> None:
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        for event in events:
            handle.write(json.dumps(asdict(event)) + "\n")


def load(path: Path = DEFAULT_EVENT_LOG) -> list[Event]:
    if not path.exists():
        return []
    return [
        Event(**json.loads(line))
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def format_event(event: Event) -> str:
    """§53-style rendering for the activity feed."""
    lines = [f"{event.event_type}", ""]
    if event.fixture:
        lines.append(f"Fixture:\n{event.fixture}")
    lines.append(f"Detected:\n{event.occurred_at}")
    if event.summary:
        lines.append(event.summary)
    for key, value in event.detail.items():
        lines.append(f"{key}: {value}")
    return "\n".join(lines)
