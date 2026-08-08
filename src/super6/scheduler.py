"""Persisted job scheduler (SPEC.md §9, §26).

Persistence per §26: SQLite as the single-node default, with schema and
connection handling in `db` (foreign keys, WAL, migrations, UTC).

Restart safety: a job is claimed transactionally before running, and any
job left RUNNING by a crash is reclaimed on startup. No Redis, Celery,
Kafka or space programme is involved.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from .db import connect as _db_connect
from .db import from_iso as _parse_iso
from .db import to_iso

DEFAULT_DB_PATH = Path("data/super6.db")


class JobType(str, Enum):
    """The three intelligence layers, scheduled independently.

    Separate job types rather than one do-everything poll, because they
    have genuinely different cadences and different budgets: odds follow
    the §9 deadline ladder, confirmed lineups only exist in a narrow
    window before kickoff, and knowledge research is expensive enough to
    be event-driven rather than routine.
    """

    POLL_ODDS = "POLL_ODDS"
    POLL_AVAILABILITY = "POLL_AVAILABILITY"
    KNOWLEDGE_RESEARCH = "KNOWLEDGE_RESEARCH"


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


_parse = _parse_iso
_iso = to_iso


@contextmanager
def connect(path: Path = DEFAULT_DB_PATH):
    with _db_connect(path) as conn:
        yield conn


JOB_COLUMNS = (
    "id", "job_type", "round_number", "round_path", "deadline_at",
    "next_run_at", "status", "attempt_count", "last_run_at", "last_error",
)


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(**{k: row[k] for k in JOB_COLUMNS})


def schedule_all_layers(
    conn: sqlite3.Connection,
    round_number: int,
    round_path: str,
    deadline: datetime,
    first_run_at: datetime | None = None,
    include_knowledge: bool = False,
) -> list[Job]:
    """Schedule every intelligence layer for a round.

    Knowledge research is opt-in: it costs search credits and LLM time,
    and most of its value is event-driven (a market move to explain)
    rather than routine polling.
    """
    types = [JobType.POLL_ODDS, JobType.POLL_AVAILABILITY]
    if include_knowledge:
        types.append(JobType.KNOWLEDGE_RESEARCH)
    return [
        schedule_round(
            conn, round_number, round_path, deadline, first_run_at, job_type
        )
        for job_type in types
    ]


def schedule_round(
    conn: sqlite3.Connection,
    round_number: int,
    round_path: str,
    deadline: datetime,
    first_run_at: datetime | None = None,
    job_type: JobType = JobType.POLL_ODDS,
) -> Job:
    """Create (or replace) one polling job for a round."""
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
