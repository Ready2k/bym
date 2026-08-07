"""Relational persistence (SPEC.md §19, §26).

SQLite is the single-node default; PostgreSQL is the migration target for
multiple instances/workers, multi-user access, meaningful concurrent
writes, or hosted HA. Application code must therefore avoid
SQLite-specific semantics — no `INSERT OR REPLACE`, no implicit rowid
dependence, no dynamic typing tricks.

The four requirements §26 imposes at either engine:

    foreign_keys = ON      referential integrity actually enforced
    WAL mode               readers don't block the writer
    schema migrations      versioned and applied in order
    UTC timestamps         stored as ISO8601 with an explicit offset

Timestamps are text ISO8601-with-offset rather than a native type,
because that is the one representation both engines read identically.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# Ordered, append-only. Never edit a migration that has shipped; add a new
# one. The index in this list is the schema version.
MIGRATIONS: list[str] = [
    # 0001 — scheduler jobs (§9)
    """
    CREATE TABLE IF NOT EXISTS jobs (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        job_type       TEXT NOT NULL,
        round_number   INTEGER,
        round_path     TEXT,
        deadline_at    TEXT NOT NULL,
        next_run_at    TEXT NOT NULL,
        status         TEXT NOT NULL,
        attempt_count  INTEGER NOT NULL DEFAULT 0,
        last_run_at    TEXT,
        last_error     TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs (status, next_run_at);
    """,
    # 0002 — team availability intelligence (Increment 3A)
    """
    CREATE TABLE IF NOT EXISTS availability_events (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        round_number        INTEGER,
        home_team           TEXT NOT NULL,
        away_team           TEXT NOT NULL,
        team                TEXT NOT NULL,
        player_name         TEXT NOT NULL,
        player_id           TEXT,
        resolution          TEXT NOT NULL,
        event_type          TEXT NOT NULL,
        claim_status        TEXT NOT NULL,
        source_id           TEXT NOT NULL,
        source_reliability  TEXT NOT NULL,
        source_timestamp    TEXT NOT NULL,
        observed_at         TEXT NOT NULL,
        role                TEXT NOT NULL DEFAULT 'UNKNOWN',
        importance          TEXT NOT NULL DEFAULT 'UNKNOWN',
        expires_at          TEXT,
        supersedes_event_id INTEGER,
        retracted_by_id     INTEGER,
        dedupe_key          TEXT NOT NULL UNIQUE,
        note                TEXT,
        FOREIGN KEY (supersedes_event_id) REFERENCES availability_events (id),
        FOREIGN KEY (retracted_by_id)     REFERENCES availability_events (id)
    );
    CREATE INDEX IF NOT EXISTS idx_avail_fixture
        ON availability_events (home_team, away_team, team, player_name);

    CREATE TABLE IF NOT EXISTS lineups (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        round_number  INTEGER,
        home_team     TEXT NOT NULL,
        away_team     TEXT NOT NULL,
        team          TEXT NOT NULL,
        kind          TEXT NOT NULL,          -- EXPECTED | CONFIRMED
        source_id     TEXT NOT NULL,
        generated_at  TEXT NOT NULL,
        observed_at   TEXT NOT NULL,
        confidence    TEXT,                   -- EXPECTED lineups only
        players       TEXT NOT NULL           -- JSON array of names
    );
    CREATE INDEX IF NOT EXISTS idx_lineups_fixture
        ON lineups (home_team, away_team, team, kind);
    """,
    # 0003 — source capability detection and fixture mapping (Increment 3B)
    """
    CREATE TABLE IF NOT EXISTS source_capabilities (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id   TEXT NOT NULL,
        league_id   INTEGER NOT NULL,
        season      INTEGER NOT NULL,
        feature     TEXT NOT NULL,
        available   INTEGER NOT NULL,
        checked_at  TEXT NOT NULL,
        UNIQUE (source_id, league_id, season, feature)
    );

    CREATE TABLE IF NOT EXISTS fixture_mappings (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id    TEXT NOT NULL,
        external_id  TEXT NOT NULL,
        home_team    TEXT NOT NULL,
        away_team    TEXT NOT NULL,
        kickoff_at   TEXT,
        league_id    INTEGER,
        season       INTEGER,
        mapped_at    TEXT NOT NULL,
        UNIQUE (source_id, external_id)
    );
    CREATE INDEX IF NOT EXISTS idx_fixture_map_teams
        ON fixture_mappings (source_id, home_team, away_team);
    """,
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime) -> str:
    """UTC ISO8601 with an explicit offset (§26)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    """Apply outstanding migrations in order. Returns the new version."""
    current = schema_version(conn)
    for index in range(current, len(MIGRATIONS)):
        conn.executescript(MIGRATIONS[index])
        conn.execute(f"PRAGMA user_version = {index + 1}")
    return schema_version(conn)


@contextmanager
def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        migrate(conn)
        yield conn
    finally:
        conn.close()
