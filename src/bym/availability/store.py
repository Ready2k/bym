"""Availability persistence (Increment 3B).

Append-only and idempotent. Re-ingesting the same source report is a
no-op via `dedupe_key`; a *correction* is a new event that supersedes the
old one, and a *retraction* marks the original retracted without deleting
it. History is never mutated (§19, §22) — "the source withdrew this at
14:10" is itself evidence.
"""

from __future__ import annotations

import json
import sqlite3

from ..db import to_iso, utcnow
from .model import AvailabilityEvent, Lineup

EVENT_COLUMNS = (
    "round_number", "home_team", "away_team", "team", "player_name",
    "player_id", "resolution", "event_type", "claim_status", "source_id",
    "source_reliability", "source_timestamp", "observed_at", "role",
    "importance", "expires_at", "supersedes_event_id", "retracted_by_id",
    "note",
)


def _row_to_event(row: sqlite3.Row) -> AvailabilityEvent:
    return AvailabilityEvent(**{k: row[k] for k in ("id", *EVENT_COLUMNS)})


def record_event(
    conn: sqlite3.Connection, event: AvailabilityEvent
) -> tuple[AvailabilityEvent, bool]:
    """Store an event. Returns (stored_event, was_new).

    An identical claim from the same source at the same timestamp is
    idempotent — polling a feed twice must not manufacture evidence.
    """
    key = event.dedupe_key()
    existing = conn.execute(
        "SELECT * FROM availability_events WHERE dedupe_key = ?", (key,)
    ).fetchone()
    if existing is not None:
        return _row_to_event(existing), False

    placeholders = ", ".join("?" for _ in EVENT_COLUMNS)
    cursor = conn.execute(
        f"INSERT INTO availability_events ({', '.join(EVENT_COLUMNS)}, dedupe_key) "
        f"VALUES ({placeholders}, ?)",
        tuple(getattr(event, c) for c in EVENT_COLUMNS) + (key,),
    )
    event.id = int(cursor.lastrowid)
    return event, True


def supersede(
    conn: sqlite3.Connection, new_event: AvailabilityEvent, old_event_id: int
) -> AvailabilityEvent:
    """Record a correction: the new event points at what it replaces, and
    the old row stays exactly as it was."""
    new_event.supersedes_event_id = old_event_id
    stored, _ = record_event(conn, new_event)
    return stored


def retract(
    conn: sqlite3.Connection, event_id: int, retraction: AvailabilityEvent
) -> AvailabilityEvent:
    """Record a retraction as a new event, then mark the original as
    retracted by it. The original's own fields are untouched."""
    stored, _ = record_event(conn, retraction)
    conn.execute(
        "UPDATE availability_events SET retracted_by_id = ? WHERE id = ?",
        (stored.id, event_id),
    )
    return stored


def events_for_fixture(
    conn: sqlite3.Connection, home: str, away: str, include_retracted: bool = False
) -> list[AvailabilityEvent]:
    sql = (
        "SELECT * FROM availability_events WHERE home_team = ? AND away_team = ?"
    )
    if not include_retracted:
        sql += " AND retracted_by_id IS NULL"
    rows = conn.execute(sql + " ORDER BY source_timestamp", (home, away)).fetchall()
    return [_row_to_event(r) for r in rows]


def history_for_player(
    conn: sqlite3.Connection, home: str, away: str, player_name: str
) -> list[AvailabilityEvent]:
    """Full evidence chain, retractions included — this is the audit view."""
    rows = conn.execute(
        "SELECT * FROM availability_events WHERE home_team = ? AND away_team = ? "
        "AND lower(player_name) = lower(?) ORDER BY source_timestamp",
        (home, away, player_name),
    ).fetchall()
    return [_row_to_event(r) for r in rows]


# ---- lineups ------------------------------------------------------------

LINEUP_COLUMNS = (
    "round_number", "home_team", "away_team", "team", "kind", "source_id",
    "generated_at", "observed_at", "confidence", "players",
)


def record_lineup(conn: sqlite3.Connection, lineup: Lineup) -> Lineup:
    values = tuple(
        json.dumps(lineup.players) if c == "players" else getattr(lineup, c)
        for c in LINEUP_COLUMNS
    )
    cursor = conn.execute(
        f"INSERT INTO lineups ({', '.join(LINEUP_COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in LINEUP_COLUMNS)})",
        values,
    )
    lineup.id = int(cursor.lastrowid)
    return lineup


def lineups_for_fixture(
    conn: sqlite3.Connection, home: str, away: str
) -> list[Lineup]:
    rows = conn.execute(
        "SELECT * FROM lineups WHERE home_team = ? AND away_team = ? "
        "ORDER BY observed_at",
        (home, away),
    ).fetchall()
    return [
        Lineup(
            id=r["id"],
            round_number=r["round_number"],
            home_team=r["home_team"],
            away_team=r["away_team"],
            team=r["team"],
            kind=r["kind"],
            source_id=r["source_id"],
            generated_at=r["generated_at"],
            observed_at=r["observed_at"],
            confidence=r["confidence"],
            players=json.loads(r["players"]),
        )
        for r in rows
    ]
