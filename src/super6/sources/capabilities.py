"""Dynamic source capability detection (Increment 3B).

API-Football states that coverage varies by competition and season, that a
`true` coverage flag does not guarantee every fixture is populated, and
that flags for future competitions can stay false until the season
actually starts. So capabilities are **detected and persisted per (league,
season)**, never hard-coded as "Premier League supports lineups forever".

A missing capability is a state, not an error:

    MISSING  the source does not cover this feature for this competition
             -> the worker does not query the endpoint, the feature is
                absent from the forecast, and nothing goes looking for it
                somewhere unapproved (§18.1)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ..db import to_iso, utcnow

AVAILABLE = "AVAILABLE"
MISSING = "MISSING"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Capability:
    source_id: str
    league_id: int
    season: int
    feature: str
    available: bool
    checked_at: str

    @property
    def state(self) -> str:
        return AVAILABLE if self.available else MISSING


def record_capability(
    conn: sqlite3.Connection,
    source_id: str,
    league_id: int,
    season: int,
    feature: str,
    available: bool,
    now: datetime | None = None,
) -> Capability:
    """Upsert a detected capability.

    Uses standard `ON CONFLICT ... DO UPDATE` rather than SQLite's
    `INSERT OR REPLACE`, so the statement is portable to PostgreSQL (§26).
    """
    checked_at = to_iso(now or utcnow())
    conn.execute(
        "INSERT INTO source_capabilities "
        "(source_id, league_id, season, feature, available, checked_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (source_id, league_id, season, feature) DO UPDATE SET "
        "available = excluded.available, checked_at = excluded.checked_at",
        (source_id, league_id, season, feature, int(available), checked_at),
    )
    return Capability(source_id, league_id, season, feature, available, checked_at)


def get_capability(
    conn: sqlite3.Connection,
    source_id: str,
    league_id: int,
    season: int,
    feature: str,
) -> Capability | None:
    row = conn.execute(
        "SELECT * FROM source_capabilities WHERE source_id = ? AND league_id = ? "
        "AND season = ? AND feature = ?",
        (source_id, league_id, season, feature),
    ).fetchone()
    if row is None:
        return None
    return Capability(
        row["source_id"],
        row["league_id"],
        row["season"],
        row["feature"],
        bool(row["available"]),
        row["checked_at"],
    )


def capability_state(
    conn: sqlite3.Connection,
    source_id: str,
    league_id: int,
    season: int,
    feature: str,
) -> str:
    """AVAILABLE / MISSING / UNKNOWN.

    UNKNOWN means coverage has not been checked yet — distinct from
    MISSING, because "we haven't looked" and "the source doesn't have it"
    call for different actions.
    """
    capability = get_capability(conn, source_id, league_id, season, feature)
    return UNKNOWN if capability is None else capability.state


def may_query(
    conn: sqlite3.Connection,
    source_id: str,
    league_id: int,
    season: int,
    feature: str,
) -> bool:
    """A worker may query an endpoint only where coverage is confirmed."""
    return (
        capability_state(conn, source_id, league_id, season, feature) == AVAILABLE
    )


# ---- fixture identity mapping ------------------------------------------


def map_fixture(
    conn: sqlite3.Connection,
    source_id: str,
    external_id: str,
    home_team: str,
    away_team: str,
    kickoff_at: str | None = None,
    league_id: int | None = None,
    season: int | None = None,
    now: datetime | None = None,
) -> None:
    """Persist the link between a source's fixture id and our team names."""
    conn.execute(
        "INSERT INTO fixture_mappings (source_id, external_id, home_team, "
        "away_team, kickoff_at, league_id, season, mapped_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (source_id, external_id) DO UPDATE SET "
        "home_team = excluded.home_team, away_team = excluded.away_team, "
        "kickoff_at = excluded.kickoff_at, mapped_at = excluded.mapped_at",
        (
            source_id,
            str(external_id),
            home_team,
            away_team,
            kickoff_at,
            league_id,
            season,
            to_iso(now or utcnow()),
        ),
    )


def external_id_for(
    conn: sqlite3.Connection, source_id: str, home_team: str, away_team: str
) -> str | None:
    row = conn.execute(
        "SELECT external_id FROM fixture_mappings WHERE source_id = ? "
        "AND home_team = ? AND away_team = ? ORDER BY mapped_at DESC LIMIT 1",
        (source_id, home_team, away_team),
    ).fetchone()
    return row["external_id"] if row else None


def fixture_for_external_id(
    conn: sqlite3.Connection, source_id: str, external_id: str
) -> tuple[str, str] | None:
    row = conn.execute(
        "SELECT home_team, away_team FROM fixture_mappings "
        "WHERE source_id = ? AND external_id = ?",
        (source_id, str(external_id)),
    ).fetchone()
    return (row["home_team"], row["away_team"]) if row else None
