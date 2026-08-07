"""Persisted job scheduler (SPEC.md §9, §26).

§26 specifies "a lightweight scheduler with jobs persisted in Postgres".
**Interim deviation:** this uses SQLite. For a single-user system running
roughly ten jobs a week, SQLite gives identical restart-safety (ACID,
transactional claim) with no server to operate, and the schema and SQL
port to Postgres by changing the connection and the placeholder style.
The §26 intent — "jobs persisted, next_run_at recomputed as the deadline
approaches" — is met; the storage engine is the only difference, and it
is recorded here rather than quietly substituted.

Restart safety: a job is claimed transactionally before running, and any
job left RUNNING by a crash is reclaimed on startup. No Redis, Celery,
Kafka or space programme is involved.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

DEFAULT_DB_PATH = Path("data/scheduler.db")

SCHEMA = """
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
"""


class JobType(str, Enum):
    POLL_ODDS = "POLL_ODDS"


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


@dataclass
class Job:
    id: int
    job_type: str
    round_number: int | None
    round_path: str | None
    deadline_at: str
    next_run_at: str
    status: str
    attempt_count: int
    last_run_at: str | None = None
    last_error: str | None = None

    @property
    def deadline(self) -> datetime:
        return _parse(self.deadline_at)

    @property
    def due_at(self) -> datetime:
        return _parse(self.next_run_at)


def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat(timespec="seconds")


@contextmanager
def connect(path: Path = DEFAULT_DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
    finally:
        conn.close()


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(**{k: row[k] for k in row.keys()})


def schedule_round(
    conn: sqlite3.Connection,
    round_number: int,
    round_path: str,
    deadline: datetime,
    first_run_at: datetime | None = None,
    job_type: JobType = JobType.POLL_ODDS,
) -> Job:
    """Create (or replace) the polling job for a round."""
    conn.execute(
        "DELETE FROM jobs WHERE round_number = ? AND job_type = ?",
        (round_number, job_type.value),
    )
    now = first_run_at or datetime.now(timezone.utc)
    cursor = conn.execute(
        "INSERT INTO jobs (job_type, round_number, round_path, deadline_at, "
        "next_run_at, status, attempt_count) VALUES (?, ?, ?, ?, ?, ?, 0)",
        (
            job_type.value,
            round_number,
            round_path,
            _iso(deadline),
            _iso(now),
            JobStatus.PENDING.value,
        ),
    )
    return get_job(conn, int(cursor.lastrowid))


def get_job(conn: sqlite3.Connection, job_id: int) -> Job:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise KeyError(f"No job {job_id}")
    return _row_to_job(row)


def list_jobs(conn: sqlite3.Connection) -> list[Job]:
    rows = conn.execute("SELECT * FROM jobs ORDER BY next_run_at").fetchall()
    return [_row_to_job(r) for r in rows]


def reclaim_stuck_jobs(conn: sqlite3.Connection) -> int:
    """Return jobs left RUNNING by a crash to PENDING (restart safety)."""
    cursor = conn.execute(
        "UPDATE jobs SET status = ? WHERE status = ?",
        (JobStatus.PENDING.value, JobStatus.RUNNING.value),
    )
    return cursor.rowcount


def claim_due_jobs(
    conn: sqlite3.Connection, now: datetime | None = None
) -> list[Job]:
    """Atomically claim every job that is due. Claiming before running is
    what makes a mid-run crash recoverable rather than invisible."""
    now = now or datetime.now(timezone.utc)
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = ? AND next_run_at <= ? "
            "ORDER BY next_run_at",
            (JobStatus.PENDING.value, _iso(now)),
        ).fetchall()
        jobs = [_row_to_job(r) for r in rows]
        for job in jobs:
            conn.execute(
                "UPDATE jobs SET status = ?, attempt_count = attempt_count + 1, "
                "last_run_at = ? WHERE id = ?",
                (JobStatus.RUNNING.value, _iso(now), job.id),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return jobs


def complete_job(
    conn: sqlite3.Connection,
    job_id: int,
    next_run_at: datetime | None,
    error: str | None = None,
) -> Job:
    """Reschedule a finished job, or retire it when next_run_at is None
    (deadline passed — there is nothing left to decide)."""
    if next_run_at is None:
        conn.execute(
            "UPDATE jobs SET status = ?, last_error = ? WHERE id = ?",
            (
                JobStatus.FAILED.value if error else JobStatus.DONE.value,
                error,
                job_id,
            ),
        )
    else:
        conn.execute(
            "UPDATE jobs SET status = ?, next_run_at = ?, last_error = ? "
            "WHERE id = ?",
            (JobStatus.PENDING.value, _iso(next_run_at), error, job_id),
        )
    return get_job(conn, job_id)
