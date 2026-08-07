"""Availability ingestion and event emission (Increment 3B/3C).

Ties the pieces together: resolve the player, store the claim
idempotently, reconcile against existing evidence, and emit structured
activity events. Emits, never adjusts — see `impact`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .. import events as ev
from ..db import to_iso, utcnow
from . import store
from .lineups import compare_lineups, latest
from .model import (
    AvailabilityEvent,
    ClaimStatus,
    EventType,
    Importance,
    Lineup,
    LineupKind,
    Reliability,
    Resolution,
    SOURCE_TIER_RELIABILITY,
)
from .players import PlayerRef, PlayerResolution, resolve_player
from .reconcile import resolve_state


@dataclass
class IngestResult:
    event: AvailabilityEvent | None
    was_new: bool
    events: list[ev.Event]


def reliability_for_tier(tier: str) -> Reliability:
    return SOURCE_TIER_RELIABILITY.get(tier, Reliability.LOW)


def ingest_availability(
    conn: sqlite3.Connection,
    home_team: str,
    away_team: str,
    team: str,
    player_name: str,
    event_type: EventType,
    claim_status: ClaimStatus,
    source_id: str,
    source_tier: str,
    source_timestamp: datetime,
    squad: list[PlayerRef] | None = None,
    round_number: int | None = None,
    importance: Importance = Importance.UNKNOWN,
    now: datetime | None = None,
    source_player_id: str | None = None,
) -> IngestResult:
    """Ingest one availability claim.

    `source_player_id` short-circuits name resolution: when the source
    supplies a stable player identifier (API-Football does), that is a
    better identity than any string match we could compute, and the
    ambiguity problem simply does not arise.
    """
    now = now or utcnow()
    emitted: list[ev.Event] = []

    if source_player_id:
        resolution = PlayerResolution(
            Resolution.RESOLVED.value, source_player_id, player_name
        )
    else:
        resolution = resolve_player(player_name, squad or [])
    if not resolution.resolved:
        emitted.append(
            ev.make_event(
                ev.EventType.PLAYER_UNRESOLVED,
                ev.Severity.INFORMATIONAL,
                f"Could not resolve '{player_name}' for {team}; recorded "
                "without a player identity and no player-specific "
                "inference drawn.",
                round_number=round_number,
                home_team=home_team,
                away_team=away_team,
                detail={
                    "reported_name": player_name,
                    "resolution": resolution.status,
                    "candidates": list(resolution.candidates),
                },
                now=now,
            )
        )

    event = AvailabilityEvent(
        home_team=home_team,
        away_team=away_team,
        team=team,
        player_name=player_name,
        player_id=resolution.player_id,
        resolution=resolution.status,
        event_type=event_type.value,
        claim_status=claim_status.value,
        source_id=source_id,
        source_reliability=reliability_for_tier(source_tier).value,
        source_timestamp=to_iso(source_timestamp),
        observed_at=to_iso(now),
        round_number=round_number,
        importance=importance.value,
    )
    stored, was_new = store.record_event(conn, event)
    if not was_new:
        return IngestResult(stored, False, emitted)

    if event_type == EventType.RETURN:
        kind, severity = ev.EventType.PLAYER_RETURNED, ev.Severity.INFORMATIONAL
    elif claim_status == ClaimStatus.CONFIRMED:
        kind, severity = ev.EventType.AVAILABILITY_CONFIRMED, ev.Severity.IMPORTANT
    else:
        kind, severity = ev.EventType.AVAILABILITY_REPORTED, ev.Severity.INFORMATIONAL

    emitted.append(
        ev.make_event(
            kind,
            severity,
            f"{team}: {player_name} {event_type.value} "
            f"({claim_status.value}, source {source_id})",
            round_number=round_number,
            home_team=home_team,
            away_team=away_team,
            detail={
                "player": player_name,
                "player_resolution": resolution.status,
                "event_type": event_type.value,
                "claim_status": claim_status.value,
                "source_reliability": event.source_reliability,
                "importance": importance.value,
                # Route 2 (§3.7): recorded, may affect CONF, never λ.
                "quantitative_impact": "NONE DIRECTLY",
                "action": "Review required" if claim_status == ClaimStatus.CONFIRMED else "Recorded",
            },
            now=now,
        )
    )

    # Surface contradictions between equally-authoritative claims.
    states = resolve_state(store.events_for_fixture(conn, home_team, away_team))
    for state in states:
        if state.has_conflict and state.player_name == player_name:
            emitted.append(
                ev.make_event(
                    ev.EventType.TEAM_NEWS_CONFLICT,
                    ev.Severity.IMPORTANT,
                    f"Conflicting reports for {player_name}: "
                    f"{state.current.event_type} ({state.current.source_id}) vs "
                    + ", ".join(
                        f"{c.event_type} ({c.source_id})" for c in state.conflicting
                    ),
                    round_number=round_number,
                    home_team=home_team,
                    away_team=away_team,
                    detail={"resolved_as": state.event_type},
                    now=now,
                )
            )
    return IngestResult(stored, True, emitted)


def ingest_lineup(
    conn: sqlite3.Connection,
    lineup: Lineup,
    key_players: set[str] | None = None,
    now: datetime | None = None,
) -> list[ev.Event]:
    """Store a lineup and, for a confirmed XI, compare it against the
    latest expected lineup (§3.2)."""
    now = now or utcnow()
    existing = store.lineups_for_fixture(conn, lineup.home_team, lineup.away_team)
    store.record_lineup(conn, lineup)
    emitted: list[ev.Event] = []

    if lineup.kind == LineupKind.EXPECTED.value:
        emitted.append(
            ev.make_event(
                ev.EventType.EXPECTED_LINEUP_UPDATED,
                ev.Severity.INFORMATIONAL,
                f"{lineup.team}: expected lineup updated "
                f"({lineup.confidence or 'unrated'} confidence, {lineup.source_id})",
                round_number=lineup.round_number,
                home_team=lineup.home_team,
                away_team=lineup.away_team,
                detail={"players": lineup.players},
                now=now,
            )
        )
        return emitted

    emitted.append(
        ev.make_event(
            ev.EventType.LINEUP_CONFIRMED,
            ev.Severity.IMPORTANT,
            f"{lineup.team}: confirmed XI announced ({lineup.source_id})",
            round_number=lineup.round_number,
            home_team=lineup.home_team,
            away_team=lineup.away_team,
            detail={"players": lineup.players},
            now=now,
        )
    )

    expected = latest(existing, lineup.team, LineupKind.EXPECTED)
    if expected is None:
        return emitted

    diff = compare_lineups(expected, lineup, key_players=key_players)
    if diff.material:
        emitted.append(
            ev.make_event(
                ev.EventType.LINEUP_SURPRISE,
                ev.Severity.IMPORTANT,
                f"{lineup.team}'s confirmed XI differs materially from the "
                f"expected lineup: {diff.describe()}",
                round_number=lineup.round_number,
                home_team=lineup.home_team,
                away_team=lineup.away_team,
                detail={
                    "expected_starters_absent": list(diff.expected_starters_absent),
                    "unexpected_starters": list(diff.unexpected_starters),
                    "key_players_absent": list(diff.key_players_absent),
                    "quantitative_impact": "NONE DIRECTLY",
                    "action": "Review required",
                },
                now=now,
            )
        )
    return emitted
