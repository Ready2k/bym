"""Sampler and harvester — determinism, resumability, quota discipline."""

from datetime import datetime, timedelta, timezone

import pytest
import requests

from bym.research import harvest, prevalence, sampling, store
from bym.research.sampling import UniverseFixture

UTC = timezone.utc
BASE = datetime(2022, 8, 6, 12, 0, tzinfo=UTC)


def universe(divisions=("E0", "E1"), seasons=(2022, 2023), per=120):
    out = []
    for division in divisions:
        for season in seasons:
            for i in range(per):
                out.append(
                    UniverseFixture(
                        fixture_id=f"{division}{season}{i:04d}",
                        division=division,
                        season=season,
                        matchday=None,
                        home_team=f"H{i%20}",
                        away_team=f"A{i%20}",
                        kickoff_at=(BASE + timedelta(days=i)).isoformat(),
                    )
                )
    return harvest.infer_matchdays(out)


@pytest.fixture
def conn(tmp_path):
    with store.connect(tmp_path / "r.db") as c:
        yield c


# ---- sampling -----------------------------------------------------------


def test_sample_is_deterministic():
    u = universe()
    a = sampling.build_sample(u, seed=sampling.SEED)
    b = sampling.build_sample(u, seed=sampling.SEED)
    assert [r["fixture_id"] for r in a] == [r["fixture_id"] for r in b]


def test_different_seed_gives_a_different_sample():
    u = universe()
    a = sampling.build_sample(u, seed=1)
    b = sampling.build_sample(u, seed=2)
    assert [r["fixture_id"] for r in a] != [r["fixture_id"] for r in b]


def test_sample_is_stratified_as_pre_registered():
    rows = sampling.build_sample(universe())
    counts = sampling.stratum_counts(rows)
    assert len(counts) == 12                      # 2 divisions x 2 seasons x 3 phases
    e0 = sum(v for k, v in counts.items() if k.startswith("E0"))
    e1 = sum(v for k, v in counts.items() if k.startswith("E1"))
    assert e0 == e1 == 100
    assert sum(counts.values()) == 200


def test_phases_scale_to_each_competition_length():
    """PL plays 38 matchdays, the Championship 46 — a fixed cutoff would
    put 'late season' in different places."""
    assert sampling.phase_for(5, 38) == "early"
    assert sampling.phase_for(5, 46) == "early"
    # matchday 28 is late in a 38-game season, mid in a 46-game one
    assert sampling.phase_for(28, 38) == "late"
    assert sampling.phase_for(28, 46) == "middle"
    assert sampling.phase_for(None, 38) == "middle"


def test_sample_persisted_before_any_api_call(conn):
    rows = sampling.build_sample(universe())
    stored = store.persist_sample(conn, rows, sampling.SEED, "2026-08-08T00:00:00+00:00")
    assert stored == 200
    assert store.sample_size(conn) == 200
    # requests are queued but nothing fetched
    assert store.seed_requests(conn, harvest.ENDPOINTS) == 400
    assert store.progress(conn) == {"PENDING": 400}


def test_sample_frame_is_immutable(conn):
    rows = sampling.build_sample(universe())
    store.persist_sample(conn, rows, sampling.SEED, "t")
    again = store.persist_sample(conn, rows, sampling.SEED, "t")
    assert again == 0
    assert store.sample_size(conn) == 200


def test_matchday_lock_is_earliest_kickoff():
    u = universe(divisions=("E0",), seasons=(2022,), per=24)
    locks = sampling.matchday_locks(u)
    for (division, season, matchday), lock in locks.items():
        same = [f.kickoff_at for f in u if f.matchday == matchday]
        assert lock == min(same)


# ---- harvesting ---------------------------------------------------------


def seeded(conn, n=4):
    rows = sampling.build_sample(universe())[:n]
    store.persist_sample(conn, rows, sampling.SEED, "t")
    store.seed_requests(conn, harvest.ENDPOINTS)
    return rows


def test_harvest_stores_and_marks_fetched(conn, monkeypatch):
    seeded(conn, 2)
    monkeypatch.setattr(
        harvest, "_fetch", lambda ep, fid, key, **kw: ([{"x": 1}], False)
    )
    monkeypatch.setattr(harvest, "remaining", lambda *a, **k: 100)
    result = harvest.harvest(conn, "key")
    assert result.fetched == 4
    assert store.progress(conn) == {"FETCHED": 4}


def test_empty_response_is_no_data_not_failure(conn, monkeypatch):
    seeded(conn, 1)
    monkeypatch.setattr(harvest, "_fetch", lambda *a, **k: ([], True))
    monkeypatch.setattr(harvest, "remaining", lambda *a, **k: 100)
    result = harvest.harvest(conn, "key")
    assert result.no_data == 2 and result.fetched == 0
    assert store.progress(conn) == {"NO_DATA": 2}


def test_restart_never_refetches_settled_requests(conn, monkeypatch):
    seeded(conn, 2)
    calls = []

    def fake(ep, fid, key, **kw):
        calls.append(fid)
        return [{"x": 1}], False

    monkeypatch.setattr(harvest, "_fetch", fake)
    monkeypatch.setattr(harvest, "remaining", lambda *a, **k: 100)
    harvest.harvest(conn, "key")
    first = len(calls)
    harvest.harvest(conn, "key")          # simulate restart
    assert len(calls) == first            # nothing re-fetched


def test_transient_failure_is_retryable(conn, monkeypatch):
    seeded(conn, 1)
    monkeypatch.setattr(harvest, "remaining", lambda *a, **k: 100)

    def boom(*a, **k):
        raise requests.ConnectionError("network blip")

    monkeypatch.setattr(harvest, "_fetch", boom)
    harvest.harvest(conn, "key")
    assert store.progress(conn) == {"FAILED_RETRYABLE": 2}
    assert len(store.pending_requests(conn)) == 2   # picked up again


def test_plan_error_is_final_and_never_retried(conn, monkeypatch):
    seeded(conn, 1)
    monkeypatch.setattr(harvest, "remaining", lambda *a, **k: 100)

    def plan_error(*a, **k):
        raise RuntimeError("API error: {'plan': 'Free plans do not have access'}")

    monkeypatch.setattr(harvest, "_fetch", plan_error)
    harvest.harvest(conn, "key")
    assert store.progress(conn) == {"FAILED_FINAL": 2}
    assert store.pending_requests(conn) == []


def test_stops_cleanly_at_quota_reserve(conn, monkeypatch):
    seeded(conn, 4)
    monkeypatch.setattr(harvest, "_fetch", lambda *a, **k: ([{"x": 1}], False))
    monkeypatch.setattr(harvest, "remaining", lambda *a, **k: 3)
    result = harvest.harvest(conn, "key", quota_reserve=5)
    assert result.attempted == 0
    assert "quota reserve" in result.stopped_reason


def test_max_requests_caps_a_run(conn, monkeypatch):
    seeded(conn, 4)
    monkeypatch.setattr(harvest, "_fetch", lambda *a, **k: ([{"x": 1}], False))
    monkeypatch.setattr(harvest, "remaining", lambda *a, **k: 100)
    result = harvest.harvest(conn, "key", max_requests=3)
    assert result.attempted == 3
    assert len(store.pending_requests(conn)) == 5   # rest still queued


# ---- information content ------------------------------------------------


def test_ordinary_fixture_is_not_informative():
    informative, reasons, counts = harvest.classify_information([], [{"team": "A"}])
    assert not informative and reasons == []
    assert counts["absent_count"] == 0


def test_confirmed_absence_is_informative():
    injuries = [{"raw_type": "Missing Fixture"}, {"raw_type": "Questionable"}]
    informative, reasons, counts = harvest.classify_information(injuries, [])
    assert informative
    assert counts["absent_count"] == 1 and counts["doubt_count"] == 1


def test_multiple_absences_flagged_separately():
    injuries = [{"raw_type": "Missing Fixture"}] * 3
    informative, reasons, _ = harvest.classify_information(injuries, [])
    assert informative
    assert any("multiple regular starters" in r for r in reasons)


def test_future_information_is_labelled_not_filtered(conn, monkeypatch):
    """Stage A measures the perfect-information ceiling, so oracle rows
    stay in the dataset."""
    rows = sampling.build_sample(universe())[:2]
    store.persist_sample(conn, rows, sampling.SEED, "t")
    store.seed_requests(conn, harvest.ENDPOINTS)
    for row in rows:
        store.store_observation(
            conn, row["fixture_id"], "injuries",
            [{"raw_type": "Missing Fixture"}], "t",
        )
    harvest.derive_facts(conn)
    facts = conn.execute("SELECT * FROM research_fixture_facts").fetchall()
    assert len(facts) == 2                      # nothing dropped
    assert all(f["would_change_feature"] for f in facts)
    assert all(f["lineup_knowability"] for f in facts)   # labelled


# ---- prevalence + freeze ------------------------------------------------


def test_prevalence_counts_without_modelling(conn):
    rows = sampling.build_sample(universe())[:3]
    store.persist_sample(conn, rows, sampling.SEED, "t")
    store.seed_requests(conn, harvest.ENDPOINTS)
    store.store_observation(
        conn, rows[0]["fixture_id"], "injuries",
        [{"raw_type": "Missing Fixture"}], "t",
    )
    store.store_observation(conn, rows[1]["fixture_id"], "injuries", [], "t")
    harvest.derive_facts(conn)
    report = prevalence.compute(conn)
    assert report.sampled == 3
    assert report.informative == 1
    assert "PASS A" in report.describe()
    assert "Pass B (predictive value) is NOT run" in report.describe()


def test_freeze_records_checksums(conn):
    rows = sampling.build_sample(universe())[:5]
    store.persist_sample(conn, rows, sampling.SEED, "t")
    record = store.freeze(conn, "AVAILABILITY_ORACLE_RESEARCH_V1")
    assert record["fixture_count"] == 5
    assert len(record["sample_sha"]) == 32
    assert store.freezes(conn)[0]["study"] == "AVAILABILITY_ORACLE_RESEARCH_V1"
