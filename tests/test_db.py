"""The four §26 persistence requirements, asserted rather than assumed."""

from datetime import datetime, timezone

import pytest

from bym import db


def test_foreign_keys_enforced(tmp_path):
    with db.connect(tmp_path / "t.db") as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO availability_events (home_team, away_team, team, "
                "player_name, resolution, event_type, claim_status, source_id, "
                "source_reliability, source_timestamp, observed_at, "
                "supersedes_event_id, dedupe_key) "
                "VALUES ('A','B','A','P','RESOLVED','INJURY','REPORTED','s',"
                "'HIGH','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00',"
                "9999,'k')"
            )


def test_wal_mode(tmp_path):
    with db.connect(tmp_path / "t.db") as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_migrations_are_versioned_and_idempotent(tmp_path):
    path = tmp_path / "t.db"
    with db.connect(path) as conn:
        version = db.schema_version(conn)
        assert version == len(db.MIGRATIONS)
    with db.connect(path) as conn:  # reopening must not re-apply
        assert db.schema_version(conn) == version
        assert db.migrate(conn) == version


def test_migrations_apply_in_order_from_empty(tmp_path):
    import sqlite3

    path = tmp_path / "fresh.db"
    conn = sqlite3.connect(path)
    assert db.schema_version(conn) == 0
    db.migrate(conn)
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"jobs", "availability_events", "lineups"} <= tables
    conn.close()


def test_timestamps_are_utc_with_explicit_offset():
    naive = datetime(2026, 8, 15, 14, 0)
    assert db.to_iso(naive).endswith("+00:00")

    from datetime import timedelta

    other_zone = datetime(
        2026, 8, 15, 15, 0, tzinfo=timezone(timedelta(hours=1))
    )
    # converted to UTC, not merely relabelled
    assert db.to_iso(other_zone) == "2026-08-15T14:00:00+00:00"


def test_iso_roundtrip():
    now = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
    assert db.from_iso(db.to_iso(now)) == now


def test_no_sqlite_specific_syntax_in_migrations():
    """§26: application persistence must not depend on SQLite-specific
    semantics, so the migration DDL stays portable."""
    forbidden = ["INSERT OR REPLACE", "WITHOUT ROWID", "PRAGMA table_info"]
    for migration in db.MIGRATIONS:
        upper = migration.upper()
        for token in forbidden:
            assert token not in upper
