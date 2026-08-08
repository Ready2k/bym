"""Research persistence — deliberately separate from production.

Its own database file, its own migrations, its own tables. Raw research
observations never share storage with the production availability tables,
so oracle information cannot reach the forecast path by a careless join.

Same §26 discipline as production: foreign keys on, WAL, versioned
migrations, UTC timestamps.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_RESEARCH_DB = Path("research/data/oracle_v1.db")

MIGRATIONS: list[str] = [
    # 0001 — sample frame, request state machine, raw observations
    """
    CREATE TABLE IF NOT EXISTS research_sample (
        fixture_id   TEXT PRIMARY KEY,
        division     TEXT NOT NULL,
        season       INTEGER NOT NULL,
        matchday     INTEGER,
        home_team    TEXT NOT NULL,
        away_team    TEXT NOT NULL,
        kickoff_at   TEXT NOT NULL,
        matchday_lock TEXT,
        stratum      TEXT NOT NULL,
        seed         INTEGER NOT NULL,
        selected_at  TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS research_requests (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id      TEXT NOT NULL,
        endpoint        TEXT NOT NULL,
        state           TEXT NOT NULL,
        attempts        INTEGER NOT NULL DEFAULT 0,
        last_attempt_at TEXT,
        retrieved_at    TEXT,
        error           TEXT,
        UNIQUE (fixture_id, endpoint),
        FOREIGN KEY (fixture_id) REFERENCES research_sample (fixture_id)
    );
    CREATE INDEX IF NOT EXISTS idx_research_requests_state
        ON research_requests (state);

    CREATE TABLE IF NOT EXISTS research_observations (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id   TEXT NOT NULL,
        endpoint     TEXT NOT NULL,
        payload      TEXT NOT NULL,
        retrieved_at TEXT NOT NULL,
        UNIQUE (fixture_id, endpoint),
        FOREIGN KEY (fixture_id) REFERENCES research_sample (fixture_id)
    );

    CREATE TABLE IF NOT EXISTS research_fixture_facts (
        fixture_id          TEXT PRIMARY KEY,
        home_goals          INTEGER,
        away_goals          INTEGER,
        status              TEXT,
        has_injury_data     INTEGER NOT NULL DEFAULT 0,
        has_lineup_data     INTEGER NOT NULL DEFAULT 0,
        injury_total        INTEGER NOT NULL DEFAULT 0,
        absent_count        INTEGER NOT NULL DEFAULT 0,
        doubt_count         INTEGER NOT NULL DEFAULT 0,
        lineup_teams        INTEGER NOT NULL DEFAULT 0,
        formation_home      TEXT,
        formation_away      TEXT,
        lineup_knowability  TEXT,
        injury_knowability  TEXT,
        would_change_feature INTEGER NOT NULL DEFAULT 0,
        change_reasons      TEXT,
        FOREIGN KEY (fixture_id) REFERENCES research_sample (fixture_id)
    );

    CREATE TABLE IF NOT EXISTS research_freeze (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        frozen_at     TEXT NOT NULL,
        study         TEXT NOT NULL,
        seed          INTEGER NOT NULL,
        fixture_count INTEGER NOT NULL,
        request_count INTEGER NOT NULL,
        sample_sha    TEXT NOT NULL,
        payload_sha   TEXT NOT NULL,
        notes         TEXT
    );
    """,
]


class RequestState:
    PENDING = "PENDING"
    FETCHED = "FETCHED"
    NO_DATA = "NO_DATA"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"


# States that must never be re-fetched merely because the process restarted.
TERMINAL_STATES = {
    RequestState.FETCHED,
    RequestState.NO_DATA,
    RequestState.FAILED_FINAL,
}


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    for index in range(schema_version(conn), len(MIGRATIONS)):
        conn.executescript(MIGRATIONS[index])
        conn.execute(f"PRAGMA user_version = {index + 1}")
    return schema_version(conn)


@contextmanager
def connect(path: Path = DEFAULT_RESEARCH_DB):
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


# ---- sample -------------------------------------------------------------


def persist_sample(conn: sqlite3.Connection, rows: list[dict], seed: int,
                   selected_at: str) -> int:
    """Store the sample frame BEFORE any API call is made."""
    stored = 0
    for row in rows:
        try:
            conn.execute(
                "INSERT INTO research_sample (fixture_id, division, season, "
                "matchday, home_team, away_team, kickoff_at, matchday_lock, "
                "stratum, seed, selected_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["fixture_id"], row["division"], row["season"],
                    row.get("matchday"), row["home_team"], row["away_team"],
                    row["kickoff_at"], row.get("matchday_lock"),
                    row["stratum"], seed, selected_at,
                ),
            )
            stored += 1
        except sqlite3.IntegrityError:
            continue  # already sampled — the frame is immutable
    return stored


def sample_size(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) c FROM research_sample").fetchone()["c"])


def sample_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM research_sample ORDER BY fixture_id"
    ).fetchall()


# ---- requests -----------------------------------------------------------


def seed_requests(conn: sqlite3.Connection, endpoints: tuple[str, ...]) -> int:
    created = 0
    for row in sample_rows(conn):
        for endpoint in endpoints:
            try:
                conn.execute(
                    "INSERT INTO research_requests (fixture_id, endpoint, state) "
                    "VALUES (?, ?, ?)",
                    (row["fixture_id"], endpoint, RequestState.PENDING),
                )
                created += 1
            except sqlite3.IntegrityError:
                continue
    return created


def pending_requests(
    conn: sqlite3.Connection, limit: int | None = None
) -> list[sqlite3.Row]:
    """Work still to do. Retryable failures are picked up again; terminal
    states never are."""
    sql = (
        "SELECT * FROM research_requests WHERE state IN (?, ?) "
        "ORDER BY attempts, id"
    )
    params: tuple = (RequestState.PENDING, RequestState.FAILED_RETRYABLE)
    if limit:
        sql += " LIMIT ?"
        params += (limit,)
    return conn.execute(sql, params).fetchall()


def mark_request(
    conn: sqlite3.Connection,
    request_id: int,
    state: str,
    retrieved_at: str | None = None,
    error: str | None = None,
    attempted_at: str | None = None,
) -> None:
    conn.execute(
        "UPDATE research_requests SET state = ?, attempts = attempts + 1, "
        "last_attempt_at = ?, retrieved_at = COALESCE(?, retrieved_at), "
        "error = ? WHERE id = ?",
        (state, attempted_at, retrieved_at, error, request_id),
    )


def store_observation(
    conn: sqlite3.Connection,
    fixture_id: str,
    endpoint: str,
    payload,
    retrieved_at: str,
) -> None:
    conn.execute(
        "INSERT INTO research_observations (fixture_id, endpoint, payload, "
        "retrieved_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (fixture_id, endpoint) DO UPDATE SET "
        "payload = excluded.payload, retrieved_at = excluded.retrieved_at",
        (fixture_id, endpoint, json.dumps(payload), retrieved_at),
    )


def observation(conn: sqlite3.Connection, fixture_id: str, endpoint: str):
    row = conn.execute(
        "SELECT payload FROM research_observations WHERE fixture_id = ? "
        "AND endpoint = ?",
        (fixture_id, endpoint),
    ).fetchone()
    return json.loads(row["payload"]) if row else None


def freeze(
    conn: sqlite3.Connection, study: str, notes: str = ""
) -> dict:
    """Freeze the harvested sample with checksums.

    Recorded so the analysis can prove it ran against this exact dataset,
    and so a later top-up is visible as a different freeze rather than
    silently changing what was analysed.
    """
    import hashlib

    sample = sample_rows(conn)
    sample_blob = "\n".join(
        f"{r['fixture_id']}|{r['stratum']}|{r['kickoff_at']}" for r in sample
    )
    payloads = conn.execute(
        "SELECT fixture_id, endpoint, payload FROM research_observations "
        "ORDER BY fixture_id, endpoint"
    ).fetchall()
    payload_blob = "\n".join(
        f"{r['fixture_id']}|{r['endpoint']}|{len(r['payload'])}" for r in payloads
    )

    record = {
        "frozen_at": __import__("super6.db", fromlist=["to_iso", "utcnow"]).to_iso(
            __import__("super6.db", fromlist=["utcnow"]).utcnow()
        ),
        "study": study,
        "seed": int(sample[0]["seed"]) if sample else 0,
        "fixture_count": len(sample),
        "request_count": len(payloads),
        "sample_sha": hashlib.sha256(sample_blob.encode()).hexdigest()[:32],
        "payload_sha": hashlib.sha256(payload_blob.encode()).hexdigest()[:32],
        "notes": notes,
    }
    conn.execute(
        "INSERT INTO research_freeze (frozen_at, study, seed, fixture_count, "
        "request_count, sample_sha, payload_sha, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(record[k] for k in (
            "frozen_at", "study", "seed", "fixture_count", "request_count",
            "sample_sha", "payload_sha", "notes",
        )),
    )
    return record


def freezes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM research_freeze ORDER BY frozen_at"
    ).fetchall()


def progress(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT state, COUNT(*) c FROM research_requests GROUP BY state"
    ).fetchall()
    return {r["state"]: int(r["c"]) for r in rows}
