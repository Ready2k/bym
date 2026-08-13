"""Deterministic event reconciliation (Increment 3.5).

Priority is **confirmation level, then source authority, then recency** —
explicitly not "latest article wins". A 13:45 confirmed XI beats a 12:30
club statement beats a 10:00 journalist's doubt, and that ordering holds
regardless of which arrived last.

Superseded evidence is never deleted. The chain is the point: it is what
lets the system answer "why did we think he was out?" three weeks later
(§19, §22).
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import AvailabilityEvent, ClaimStatus, EventType, Resolution


@dataclass(frozen=True)
class PlayerState:
    """Current best understanding of one player, plus its evidence."""

    team: str
    player_name: str
    player_id: str | None
    current: AvailabilityEvent
    history: tuple[AvailabilityEvent, ...]
    conflicting: tuple[AvailabilityEvent, ...] = ()

    @property
    def event_type(self) -> str:
        return self.current.event_type

    @property
    def claim_status(self) -> str:
        return self.current.claim_status

    @property
    def is_absent(self) -> bool:
        return self.current.implies_absent

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicting)

    def describe(self) -> str:
        return (
            f"{self.player_name}: {self.event_type} "
            f"({self.claim_status}, {self.current.source_id})"
        )


def _key(event: AvailabilityEvent) -> tuple[str, str]:
    """Group by resolved identity where available, else by reported name —
    an unresolved player still groups with itself but never merges into a
    real player's record."""
    return (event.team, event.player_id or f"UNRESOLVED::{event.player_name.lower()}")


def resolve_state(events: list[AvailabilityEvent]) -> list[PlayerState]:
    """Reduce an evidence set to one current state per player."""
    grouped: dict[tuple[str, str], list[AvailabilityEvent]] = {}
    for event in events:
        if event.retracted_by_id is not None:
            continue  # retracted, but still present in the stored history
        grouped.setdefault(_key(event), []).append(event)

    states = []
    for (team, _), group in grouped.items():
        ordered = sorted(group, key=lambda e: e.sort_key, reverse=True)
        winner = ordered[0]
        # Conflicts worth surfacing: same top priority band, contradictory
        # availability. A superseded lower-priority claim is not a conflict.
        conflicting = tuple(
            e
            for e in ordered[1:]
            if e.sort_key[0] == winner.sort_key[0]
            and e.implies_absent != winner.implies_absent
        )
        states.append(
            PlayerState(
                team=team,
                player_name=winner.player_name,
                player_id=winner.player_id,
                current=winner,
                history=tuple(ordered),
                conflicting=conflicting,
            )
        )
    return sorted(states, key=lambda s: (s.team, s.player_name))


def absentees(states: list[PlayerState]) -> list[PlayerState]:
    return [s for s in states if s.is_absent]


def unresolved(states: list[PlayerState]) -> list[PlayerState]:
    return [
        s for s in states if s.current.resolution != Resolution.RESOLVED.value
    ]
