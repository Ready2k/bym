"""Always-on loop integration: job dispatch and event-driven investigation."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from super6 import db, scheduler, worker
from super6.availability import worker as availability_worker
from super6.knowledge import worker as knowledge_worker
from super6.model import dixon_coles
from super6.sources import pipeline
from super6.sources.odds_api import BookmakerOdds, EventOdds

from test_dixon_coles import synthetic_matches

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
DEADLINE = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)

ROUND_JSON = """{
  "round_number": 1,
  "deadline_at": "2026-08-15T14:00:00+00:00",
  "fixtures": [
    {"home": "Strong", "away": "Weak"},
    {"home": "Middling A", "away": "Middling B"},
    {"home": "Middling B", "away": "Middling A"},
    {"home": "Weak", "away": "Strong"},
    {"home": "Strong", "away": "Middling A"},
    {"home": "Weak", "away": "Middling B"}
  ]
}"""

TIGHT = [(1.80, 3.50, 4.50), (1.82, 3.45, 4.40), (1.78, 3.55, 4.60)]
MOVED = [(1.40, 4.50, 7.00), (1.42, 4.40, 6.90), (1.38, 4.60, 7.10)]


@pytest.fixture(scope="module")
def router():
    params = dixon_coles.fit(synthetic_matches(), xi=0.0)
    return lambda home, away: params


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "s.db") as c:
        yield c


@pytest.fixture
def round_path(tmp_path):
    path = tmp_path / "round.json"
    path.write_text(ROUND_JSON)
    return path


def feed(prices):
    ts = (NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    return [
        EventOdds(
            "e1", "soccer_epl", "2026-08-15T14:00:00Z", "Strong", "Weak",
            [BookmakerOdds(f"b{i}", ts, h, d, a) for i, (h, d, a) in enumerate(prices)],
        )
    ]


class FakeExtractor:
    def __init__(self):
        self.calls = 0

    def extract_claims(self, evidence):
        self.calls += 1
        return []


# ---- job types ----------------------------------------------------------


def test_all_layers_scheduled(conn, round_path):
    jobs = scheduler.schedule_all_layers(
        conn, 1, str(round_path), DEADLINE, NOW, include_knowledge=True
    )
    assert {j.job_type for j in jobs} == {
        "POLL_ODDS", "POLL_AVAILABILITY", "KNOWLEDGE_RESEARCH"
    }


def test_knowledge_is_opt_in(conn, round_path):
    """Knowledge costs credits and is mostly event-driven, so routine
    research is not scheduled by default."""
    jobs = scheduler.schedule_all_layers(conn, 1, str(round_path), DEADLINE, NOW)
    assert {j.job_type for j in jobs} == {"POLL_ODDS", "POLL_AVAILABILITY"}


def test_layers_reschedule_independently(conn, round_path, monkeypatch, tmp_path):
    scheduler.schedule_all_layers(conn, 1, str(round_path), DEADLINE, NOW)
    monkeypatch.setattr(
        pipeline.odds_api, "fetch_odds", lambda d, k, **kw: feed(TIGHT)
    )
    monkeypatch.setattr(worker, "budget_constrained", lambda *a, **k: False)
    outcomes = worker.run_due_jobs(
        conn, "key", lambda: (lambda h, a: None), now=NOW,
        event_log=tmp_path / "e.jsonl",
        consensus_path=tmp_path / "c.csv", odds_snapshot_path=tmp_path / "o.csv",
        divisions=("E0",),
    )
    assert len(outcomes) == 2   # odds + availability both ran
    assert all(j.status == "PENDING" for j in scheduler.list_jobs(conn))


# ---- availability job ---------------------------------------------------


def test_availability_job_without_key_degrades(conn, round_path, tmp_path):
    scheduler.schedule_round(
        conn, 1, str(round_path), DEADLINE, NOW,
        scheduler.JobType.POLL_AVAILABILITY,
    )
    outcomes = worker.run_due_jobs(
        conn, "key", lambda: (lambda h, a: None), now=NOW,
        event_log=tmp_path / "e.jsonl", availability_key=None,
    )
    assert len(outcomes) == 1
    assert any("no API_FOOTBALL_KEY" in e.summary for e in outcomes[0].events)


def test_availability_job_never_triggers_a_reforecast(conn, round_path, tmp_path):
    """Structured availability informs; it does not move the model."""
    scheduler.schedule_round(
        conn, 1, str(round_path), DEADLINE, NOW,
        scheduler.JobType.POLL_AVAILABILITY,
    )
    outcomes = worker.run_due_jobs(
        conn, "key", lambda: (lambda h, a: None), now=NOW,
        event_log=tmp_path / "e.jsonl", availability_key=None,
    )
    assert outcomes[0].reforecast == 0


# ---- event-driven investigation ----------------------------------------


def test_unexplained_major_move_is_flagged(conn, router, monkeypatch, tmp_path):
    from super6.round import Fixture, Round

    monkeypatch.setattr(worker, "budget_constrained", lambda *a, **k: False)
    kwargs = dict(
        consensus_path=tmp_path / "c.csv", odds_snapshot_path=tmp_path / "o.csv",
        divisions=("E0",),
    )
    rnd = Round(1, DEADLINE, (Fixture("Strong", "Weak"),))

    monkeypatch.setattr(pipeline.odds_api, "fetch_odds", lambda d, k, **kw: feed(TIGHT))
    worker.poll_round(rnd, "key", router, conn=conn, now=NOW, **kwargs)

    monkeypatch.setattr(pipeline.odds_api, "fetch_odds", lambda d, k, **kw: feed(MOVED))
    outcome = worker.poll_round(
        rnd, "key", router, conn=conn, now=NOW + timedelta(minutes=30), **kwargs
    )
    assert outcome.unexplained_moves
    assert outcome.unexplained_moves[0][0] == "Strong"


def test_known_news_means_the_move_is_not_unexplained(
    conn, router, monkeypatch, tmp_path
):
    from super6.availability import ingest
    from super6.availability.model import ClaimStatus, EventType
    from super6.round import Fixture, Round

    monkeypatch.setattr(worker, "budget_constrained", lambda *a, **k: False)
    kwargs = dict(
        consensus_path=tmp_path / "c.csv", odds_snapshot_path=tmp_path / "o.csv",
        divisions=("E0",),
    )
    rnd = Round(1, DEADLINE, (Fixture("Strong", "Weak"),))

    monkeypatch.setattr(pipeline.odds_api, "fetch_odds", lambda d, k, **kw: feed(TIGHT))
    worker.poll_round(rnd, "key", router, conn=conn, now=NOW, **kwargs)

    later = NOW + timedelta(minutes=30)
    ingest.ingest_availability(
        conn, "Strong", "Weak", "Strong", "Star Player",
        EventType.CONFIRMED_ABSENT, ClaimStatus.CONFIRMED,
        "club", "OFFICIAL_CLUB", later, now=later,
    )

    monkeypatch.setattr(pipeline.odds_api, "fetch_odds", lambda d, k, **kw: feed(MOVED))
    outcome = worker.poll_round(
        rnd, "key", router, conn=conn, now=later, **kwargs
    )
    assert outcome.unexplained_moves == []


def test_investigation_runs_only_with_key_and_extractor(
    conn, router, round_path, monkeypatch, tmp_path
):
    """No credentials means no investigation, not a crash."""
    scheduler.schedule_round(conn, 1, str(round_path), DEADLINE, NOW)
    monkeypatch.setattr(pipeline.odds_api, "fetch_odds", lambda d, k, **kw: feed(MOVED))
    monkeypatch.setattr(worker, "budget_constrained", lambda *a, **k: False)
    extractor = FakeExtractor()
    worker.run_due_jobs(
        conn, "key", lambda: router, now=NOW, event_log=tmp_path / "e.jsonl",
        tavily_key=None, extractor=extractor,
        consensus_path=tmp_path / "c.csv", odds_snapshot_path=tmp_path / "o.csv",
        divisions=("E0",),
    )
    assert extractor.calls == 0


def test_knowledge_job_without_credentials_degrades(conn, round_path, tmp_path):
    scheduler.schedule_round(
        conn, 1, str(round_path), DEADLINE, NOW,
        scheduler.JobType.KNOWLEDGE_RESEARCH,
    )
    outcomes = worker.run_due_jobs(
        conn, "key", lambda: (lambda h, a: None), now=NOW,
        event_log=tmp_path / "e.jsonl",
    )
    assert any("Knowledge research skipped" in e.summary for e in outcomes[0].events)


def test_one_failing_layer_does_not_stop_the_others(
    conn, round_path, router, monkeypatch, tmp_path
):
    scheduler.schedule_all_layers(conn, 1, str(round_path), DEADLINE, NOW)

    def boom(*a, **k):
        raise RuntimeError("odds API down")

    monkeypatch.setattr(pipeline.odds_api, "fetch_odds", boom)
    outcomes = worker.run_due_jobs(
        conn, "key", lambda: router, now=NOW, event_log=tmp_path / "e.jsonl",
        availability_key=None,
    )
    # availability still produced an outcome despite odds failing
    assert len(outcomes) >= 1
    jobs = {j.job_type: j for j in scheduler.list_jobs(conn)}
    assert jobs["POLL_ODDS"].last_error is not None
    assert jobs["POLL_ODDS"].status == "PENDING"   # retried, not abandoned
