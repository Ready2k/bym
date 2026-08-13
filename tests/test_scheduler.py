from datetime import datetime, timedelta, timezone

import pytest

from bym import scheduler

NOW = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)
DEADLINE = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path):
    with scheduler.connect(tmp_path / "sched.db") as c:
        yield c


def test_schedule_and_claim(conn):
    scheduler.schedule_round(conn, 1, "rounds/r1.json", DEADLINE, first_run_at=NOW)
    due = scheduler.claim_due_jobs(conn, NOW)
    assert len(due) == 1
    assert due[0].status == "PENDING"  # snapshot taken before the update
    assert scheduler.get_job(conn, due[0].id).status == "RUNNING"


def test_job_not_claimed_before_due(conn):
    scheduler.schedule_round(
        conn, 1, "rounds/r1.json", DEADLINE, first_run_at=NOW + timedelta(hours=1)
    )
    assert scheduler.claim_due_jobs(conn, NOW) == []


def test_claim_is_not_repeated(conn):
    scheduler.schedule_round(conn, 1, "rounds/r1.json", DEADLINE, first_run_at=NOW)
    assert len(scheduler.claim_due_jobs(conn, NOW)) == 1
    assert scheduler.claim_due_jobs(conn, NOW) == []  # already RUNNING


def test_complete_reschedules(conn):
    job = scheduler.schedule_round(conn, 1, "rounds/r1.json", DEADLINE, first_run_at=NOW)
    scheduler.claim_due_jobs(conn, NOW)
    updated = scheduler.complete_job(conn, job.id, NOW + timedelta(hours=1))
    assert updated.status == "PENDING"
    assert updated.due_at == NOW + timedelta(hours=1)
    assert updated.attempt_count == 1


def test_complete_with_none_retires_job(conn):
    """Deadline passed — nothing left to decide, so polling stops."""
    job = scheduler.schedule_round(conn, 1, "rounds/r1.json", DEADLINE, first_run_at=NOW)
    scheduler.claim_due_jobs(conn, NOW)
    assert scheduler.complete_job(conn, job.id, None).status == "DONE"
    assert scheduler.claim_due_jobs(conn, DEADLINE + timedelta(hours=1)) == []


def test_restart_reclaims_crashed_jobs(tmp_path):
    """A container dying mid-poll must not strand the job in RUNNING."""
    db = tmp_path / "sched.db"
    with scheduler.connect(db) as c:
        scheduler.schedule_round(c, 1, "rounds/r1.json", DEADLINE, first_run_at=NOW)
        scheduler.claim_due_jobs(c, NOW)  # simulate crash: never completed

    with scheduler.connect(db) as c:  # process restarts
        assert scheduler.reclaim_stuck_jobs(c) == 1
        assert len(scheduler.claim_due_jobs(c, NOW)) == 1


def test_state_survives_reconnect(tmp_path):
    db = tmp_path / "sched.db"
    with scheduler.connect(db) as c:
        job = scheduler.schedule_round(c, 7, "rounds/r7.json", DEADLINE, first_run_at=NOW)
        job_id = job.id
    with scheduler.connect(db) as c:
        reloaded = scheduler.get_job(c, job_id)
        assert reloaded.round_number == 7
        assert reloaded.round_path == "rounds/r7.json"


def test_rescheduling_a_round_replaces_its_job(conn):
    scheduler.schedule_round(conn, 1, "rounds/r1.json", DEADLINE, first_run_at=NOW)
    scheduler.schedule_round(conn, 1, "rounds/r1.json", DEADLINE, first_run_at=NOW)
    assert len(scheduler.list_jobs(conn)) == 1


def test_failure_records_error_and_retries(conn):
    job = scheduler.schedule_round(conn, 1, "rounds/r1.json", DEADLINE, first_run_at=NOW)
    scheduler.claim_due_jobs(conn, NOW)
    updated = scheduler.complete_job(
        conn, job.id, NOW + timedelta(minutes=30), error="timeout"
    )
    assert updated.status == "PENDING"
    assert updated.last_error == "timeout"
