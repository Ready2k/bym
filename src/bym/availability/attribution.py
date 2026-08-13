"""Market-movement attribution (Increment 3C).

Associates availability events with market movements **temporally**. This
is attribution, not causality, and the vocabulary says so: the strongest
verdict available is PLAUSIBLE.

The honest phrasing the system is allowed to use:

    "The market move began shortly before Chelsea confirmed an attacking
    absence. That is a plausible explanation, but the system cannot
    establish that the team news caused the repricing."

Note the awkward direction that makes this necessarily weak: markets
routinely move *before* news is public, because someone knew first. So
news arriving shortly after a move is at least as suggestive as news
arriving before it — and neither is proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from ..db import from_iso
from ..movement import MovementReport
from .model import AvailabilityEvent

# Beyond this, an event and a move are not treated as related at all.
DEFAULT_WINDOW = timedelta(minutes=45)
# Within this, the association is considered tight enough to report.
TIGHT_WINDOW = timedelta(minutes=15)


class Relationship(str, Enum):
    PLAUSIBLE = "PLAUSIBLE"
    POSSIBLE = "POSSIBLE"
    UNRELATED = "UNRELATED"


@dataclass(frozen=True)
class Attribution:
    event: AvailabilityEvent
    movement: MovementReport
    relationship: str
    temporal_distance_minutes: float
    news_after_move: bool

    def describe(self) -> str:
        order = (
            "the move began shortly before"
            if self.news_after_move
            else "the news arrived shortly before the move:"
        )
        return (
            f"{self.movement.describe()} — {order} "
            f"{self.event.team} {self.event.event_type} "
            f"({self.event.player_name}, {self.event.source_id}), "
            f"{self.temporal_distance_minutes:.0f} minutes apart. "
            "Plausible explanation only; causality is not established."
        )


def attribute(
    movement: MovementReport,
    events: list[AvailabilityEvent],
    window: timedelta = DEFAULT_WINDOW,
) -> list[Attribution]:
    """Candidate explanations for one movement, nearest first.

    Only events concerning a team in the fixture are considered — a
    Newcastle injury cannot explain a move in someone else's match.
    """
    detected = from_iso(movement.detected_at)
    teams = {movement.home_team, movement.away_team}
    results = []

    for event in events:
        if event.team not in teams:
            continue
        try:
            event_time = from_iso(event.source_timestamp)
        except ValueError:
            continue
        delta = event_time - detected
        minutes = abs(delta.total_seconds()) / 60.0
        if abs(delta) > window:
            continue
        results.append(
            Attribution(
                event=event,
                movement=movement,
                relationship=(
                    Relationship.PLAUSIBLE.value
                    if abs(delta) <= TIGHT_WINDOW
                    else Relationship.POSSIBLE.value
                ),
                temporal_distance_minutes=minutes,
                news_after_move=delta.total_seconds() > 0,
            )
        )
    return sorted(results, key=lambda a: a.temporal_distance_minutes)


def best_explanation(
    movement: MovementReport,
    events: list[AvailabilityEvent],
    window: timedelta = DEFAULT_WINDOW,
) -> Attribution | None:
    """Closest plausible explanation, or None — an unexplained move stays
    unexplained rather than being assigned the nearest available story
    (§21's 'REVIEW REQUIRED: no confirmed news detected')."""
    candidates = attribute(movement, events, window)
    return candidates[0] if candidates else None
