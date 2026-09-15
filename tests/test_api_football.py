"""Offline tests for the API-Football adapter (Increment 3B).

No live calls. The network path itself remains unverified until an API key
exists — that is the outstanding human task, not something these tests can
stand in for.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bym import db
from bym.availability import bbc_live, worker as avail_worker
from bym.availability.model import ClaimStatus, EventType, LineupKind
from bym.availability import store
from bym.round import Fixture, Round
from bym.sources import api_football, capabilities
from bym.sources import quota as quota_mod
from bym.sources import registry as reg

NOW = datetime(2026, 8, 15, 13, 0, tzinfo=timezone.utc)
KICKOFF = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)

ROUND = Round(
    round_number=1,
    deadline_at=KICKOFF,
    fixtures=(
        Fixture("Chelsea", "Newcastle"),
        Fixture("Arsenal", "Everton"),
        Fixture("Man City", "Bournemouth"),
        Fixture("Fulham", "Brentford"),
        Fixture("Leeds", "Tottenham"),
        Fixture("West Ham", "Aston Villa"),
    ),
)


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "s.db") as c:
        yield c


@pytest.fixture
def approved(tmp_path):
    path = tmp_path / "registry.json"
    reg.save_registry(reg.load_registry(path), path)
    reg.approve("api_football", path=path)
    return path


@pytest.fixture(autouse=True)
def no_live_bbc_requests(monkeypatch):
    """This module is intentionally offline; BBC adapter tests live elsewhere."""
    monkeypatch.setattr(bbc_live, "fetch_confirmed_xis", lambda teams: [])


# ---- registry gating ----------------------------------------------------


def test_registered_as_review_required(tmp_path):
    path = tmp_path / "registry.json"
    reg.save_registry(reg.load_registry(path), path)
    assert reg.load_registry(path)["api_football"].usage_status == "REVIEW_REQUIRED"
    with pytest.raises(reg.SourceNotApprovedError):
        api_football.fetch_coverage("E0", 2026, "key", registry_path=path)


# ---- coverage parsing and capability detection --------------------------


COVERAGE_PAYLOAD = [
    {
        "league": {"id": 39, "name": "Premier League"},
        "seasons": [
            {
                "year": 2026,
                "coverage": {
                    "fixtures": {"lineups": True, "events": True},
                    "injuries": True,
                },
            }
        ],
    }
]

NO_COVERAGE_PAYLOAD = [
    {
        "league": {"id": 40, "name": "Championship"},
        "seasons": [
            {
                "year": 2026,
                "coverage": {"fixtures": {"lineups": False}, "injuries": False},
            }
        ],
    }
]


def test_parse_coverage():
    assert api_football.parse_coverage(COVERAGE_PAYLOAD) == {
        "lineups": True,
        "injuries": True,
    }
    assert api_football.parse_coverage(NO_COVERAGE_PAYLOAD) == {
        "lineups": False,
        "injuries": False,
    }
    assert api_football.parse_coverage([]) == {"lineups": False, "injuries": False}


def test_capability_is_detected_not_hardcoded(conn, monkeypatch, approved):
    monkeypatch.setattr(
        api_football, "_request", lambda *a, **k: NO_COVERAGE_PAYLOAD
    )
    avail_worker.detect_coverage(conn, "E1", 2026, "key", now=NOW)
    assert not capabilities.may_query(conn, "api_football", 40, 2026, "lineups")
    assert capabilities.capability_state(conn, "api_football", 40, 2026, "lineups") == "MISSING"


def test_unknown_is_distinct_from_missing(conn):
    """'We haven't checked' and 'the source lacks it' call for different
    actions, so they are different states."""
    assert capabilities.capability_state(conn, "api_football", 39, 2026, "lineups") == "UNKNOWN"
    capabilities.record_capability(conn, "api_football", 39, 2026, "lineups", False, now=NOW)
    assert capabilities.capability_state(conn, "api_football", 39, 2026, "lineups") == "MISSING"


def test_capability_recheck_updates_in_place(conn):
    """Coverage flags can flip to true once a season starts."""
    capabilities.record_capability(conn, "api_football", 39, 2026, "lineups", False, now=NOW)
    capabilities.record_capability(conn, "api_football", 39, 2026, "lineups", True, now=NOW)
    assert capabilities.may_query(conn, "api_football", 39, 2026, "lineups")
    rows = conn.execute("SELECT COUNT(*) c FROM source_capabilities").fetchone()
    assert rows["c"] == 1  # upsert, not duplicate


# ---- injury mapping -----------------------------------------------------


INJURY_PAYLOAD = [
    {
        "player": {"id": 100, "name": "Cole Palmer", "type": "Missing Fixture",
                   "reason": "Hamstring"},
        "team": {"name": "Chelsea"},
        "fixture": {"id": 555},
    },
    {
        "player": {"id": 101, "name": "Enzo Fernandez", "type": "Questionable",
                   "reason": "Knock"},
        "team": {"name": "Chelsea"},
        "fixture": {"id": 555},
    },
]


def test_missing_fixture_maps_to_confirmed_absent():
    records = api_football.parse_injuries(INJURY_PAYLOAD)
    palmer = records[0]
    assert palmer.event_type == EventType.CONFIRMED_ABSENT
    assert palmer.claim_status == ClaimStatus.CONFIRMED


def test_questionable_stays_conservative():
    """Their docs say a Questionable player may still play, so it must not
    become LIKELY_ABSENT."""
    enzo = api_football.parse_injuries(INJURY_PAYLOAD)[1]
    assert enzo.event_type == EventType.DOUBT
    assert enzo.claim_status == ClaimStatus.REPORTED
    assert enzo.claim_status != ClaimStatus.LIKELY


def test_unknown_injury_type_defaults_conservatively():
    payload = [
        {
            "player": {"id": 1, "name": "X", "type": "Some New Category"},
            "team": {"name": "Chelsea"},
            "fixture": {"id": 555},
        }
    ]
    record = api_football.parse_injuries(payload)[0]
    assert record.event_type == EventType.DOUBT
    assert record.claim_status == ClaimStatus.REPORTED


def test_injuries_batch_limit():
    with pytest.raises(ValueError, match="at most 20"):
        api_football.fetch_injuries([str(i) for i in range(21)], "key")


def test_empty_fixture_list_makes_no_request():
    assert api_football.fetch_injuries([], "key") == []


# ---- lineup parsing -----------------------------------------------------


LINEUP_PAYLOAD = [
    {
        "team": {"name": "Chelsea"},
        "formation": "4-2-3-1",
        "startXI": [
            {"player": {"id": 1, "name": "Robert Sanchez"}},
            {"player": {"id": 2, "name": "Nicolas Jackson"}},
        ],
        "substitutes": [{"player": {"id": 3, "name": "Cole Palmer"}}],
    }
]


def test_parse_lineups():
    records = api_football.parse_lineups(LINEUP_PAYLOAD, "555")
    assert records[0].formation == "4-2-3-1"
    assert [n for _, n in records[0].start_xi] == ["Robert Sanchez", "Nicolas Jackson"]
    assert [n for _, n in records[0].substitutes] == ["Cole Palmer"]


def test_empty_lineup_response_is_not_an_error():
    """Before teams are announced the endpoint returns nothing — that is
    'not yet published', not a failure."""
    assert api_football.parse_lineups([], "555") == []


# ---- player match stats (FPL Phase 1A historical ingestion) -------------

PLAYER_STATS_PAYLOAD = [
    {
        "team": {"id": 49, "name": "Chelsea"},
        "players": [
            {
                "player": {"id": 909, "name": "Cole Palmer"},
                "statistics": [
                    {
                        "games": {"minutes": 90, "position": "M"},
                        "shots": {"total": 3, "on": 2},
                        "goals": {"total": 1, "assists": 1, "saves": None},
                        "tackles": {"total": 1, "blocks": 0, "interceptions": 0},
                        "cards": {"yellow": 0, "red": 0},
                        "penalty": {"scored": 0, "missed": 0, "saved": None},
                    }
                ],
            },
            {
                # Unused substitute: no statistics block at all.
                "player": {"id": 3, "name": "Benched Player"},
                "statistics": [],
            },
        ],
    },
    {
        "team": {"id": 1, "name": "Everton"},
        "players": [
            {
                "player": {"id": 100, "name": "Jordan Pickford"},
                "statistics": [
                    {
                        "games": {"minutes": 90, "position": "G"},
                        "shots": {"total": None, "on": None},
                        "goals": {"total": 0, "assists": 0, "saves": 4},
                        "tackles": {"total": None, "blocks": None, "interceptions": None},
                        "cards": {"yellow": 1, "red": 0},
                        "penalty": {"scored": 0, "missed": 0, "saved": 1},
                    }
                ],
            },
        ],
    },
]


def test_parse_player_match_stats():
    records = api_football.parse_player_match_stats(PLAYER_STATS_PAYLOAD)
    assert len(records) == 2  # the statistics-less substitute is skipped

    palmer = records[0]
    assert palmer.player_name == "Cole Palmer"
    assert palmer.provider_player_id == "909"
    assert palmer.team == "Chelsea"
    assert palmer.position == "MID"
    assert palmer.minutes == 90
    assert palmer.goals == 1
    assert palmer.assists == 1
    assert palmer.shots == 3

    keeper = records[1]
    assert keeper.position == "GK"
    assert keeper.saves == 4
    assert keeper.penalties_saved == 1
    assert keeper.tackles is None  # not defaulted to 0 — genuinely unknown


def test_empty_player_stats_response_is_not_an_error():
    assert api_football.parse_player_match_stats([]) == []


def test_fetch_player_match_stats_stamps_the_fixture_id(monkeypatch):
    monkeypatch.setattr(
        api_football, "_request", lambda *a, **k: PLAYER_STATS_PAYLOAD
    )
    records = api_football.fetch_player_match_stats("555", "fake-key")
    assert all(r.fixture_id == "555" for r in records)


# ---- quota --------------------------------------------------------------


def test_daily_quota_accounting(tmp_path):
    path = tmp_path / "q.json"
    spec = api_football.QUOTA
    assert quota_mod.used(spec, path, NOW) == 0
    quota_mod.record(spec, 1, "99", path, NOW)
    assert quota_mod.used(spec, path, NOW) == 1
    # a different day is a separate budget
    assert quota_mod.used(spec, path, NOW + timedelta(days=1)) == 0


def test_daily_quota_blocks_before_dispatch(tmp_path, monkeypatch, approved):
    path = tmp_path / "q.json"
    quota_mod.record(api_football.QUOTA, 100, None, path, NOW)

    def fail(*a, **k):
        raise AssertionError("request attempted despite exhausted quota")

    monkeypatch.setattr(api_football.requests, "get", fail)
    with pytest.raises(quota_mod.QuotaExceededError):
        api_football.fetch_coverage(
            "E0", 2026, "key", quota_path=path, registry_path=approved, now=NOW
        )


def test_per_minute_rate_limit(tmp_path):
    path = tmp_path / "q.json"
    spec = api_football.QUOTA
    for _ in range(10):
        quota_mod.record(spec, 1, None, path, NOW)
    with pytest.raises(quota_mod.RateLimitError):
        quota_mod.check(spec, 1, path, NOW)
    # a minute later the window has cleared
    quota_mod.check(spec, 1, path, NOW + timedelta(minutes=2))


# ---- worker integration -------------------------------------------------


def test_worker_skips_endpoints_without_coverage(conn, monkeypatch, approved):
    capabilities.map_fixture(conn, "api_football", "555", "Chelsea", "Newcastle", now=NOW)
    capabilities.record_capability(conn, "api_football", 39, 2026, "injuries", False, now=NOW)
    capabilities.record_capability(conn, "api_football", 39, 2026, "lineups", False, now=NOW)

    def fail(*a, **k):
        raise AssertionError("queried an endpoint without coverage")

    monkeypatch.setattr(api_football, "_request", fail)
    result = avail_worker.poll_availability(
        conn, ROUND, "E0", 2026, "key", now=NOW, kickoff_at=KICKOFF
    )
    assert result.requests_spent == 0
    assert any("MISSING" in s for s in result.skipped)


def test_worker_ingests_injuries(conn, monkeypatch, approved):
    capabilities.map_fixture(conn, "api_football", "555", "Chelsea", "Newcastle", now=NOW)
    capabilities.record_capability(conn, "api_football", 39, 2026, "injuries", True, now=NOW)
    capabilities.record_capability(conn, "api_football", 39, 2026, "lineups", False, now=NOW)
    monkeypatch.setattr(
        api_football, "fetch_injuries",
        lambda ids, key, **kw: api_football.parse_injuries(INJURY_PAYLOAD),
    )

    result = avail_worker.poll_availability(
        conn, ROUND, "E0", 2026, "key", now=NOW, kickoff_at=KICKOFF
    )
    assert result.injuries_ingested == 2
    events = store.events_for_fixture(conn, "Chelsea", "Newcastle")
    assert {e.event_type for e in events} == {"CONFIRMED_ABSENT", "DOUBT"}
    # source-provided ids bypass name resolution entirely
    assert all(e.resolution == "RESOLVED" for e in events)


def test_worker_ingests_confirmed_lineup(conn, monkeypatch, approved):
    capabilities.map_fixture(conn, "api_football", "555", "Chelsea", "Newcastle", now=NOW)
    capabilities.record_capability(conn, "api_football", 39, 2026, "injuries", False, now=NOW)
    capabilities.record_capability(conn, "api_football", 39, 2026, "lineups", True, now=NOW)
    monkeypatch.setattr(
        api_football, "fetch_lineups",
        lambda fid, key, **kw: api_football.parse_lineups(LINEUP_PAYLOAD, fid),
    )

    result = avail_worker.poll_availability(
        conn, ROUND, "E0", 2026, "key", now=NOW, kickoff_at=KICKOFF
    )
    assert result.lineups_ingested == 1
    assert any(e.event_type == "LINEUP_CONFIRMED" for e in result.events)
    lineups = store.lineups_for_fixture(conn, "Chelsea", "Newcastle")
    assert lineups[0].kind == "CONFIRMED"
    assert lineups[0].confidence is None


def test_lineup_polling_waits_for_the_window(conn, monkeypatch, approved):
    """Confirmed XIs appear 20-40 min before kickoff; polling hours early
    just spends quota on empty responses."""
    capabilities.map_fixture(conn, "api_football", "555", "Chelsea", "Newcastle", now=NOW)
    capabilities.record_capability(conn, "api_football", 39, 2026, "injuries", False, now=NOW)
    capabilities.record_capability(conn, "api_football", 39, 2026, "lineups", True, now=NOW)
    monkeypatch.setattr(
        api_football, "fetch_lineups",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("polled too early")),
    )
    result = avail_worker.poll_availability(
        conn, ROUND, "E0", 2026, "key",
        now=KICKOFF - timedelta(hours=5), kickoff_at=KICKOFF,
    )
    assert "outside confirmed-lineup window" in result.skipped


def test_unmapped_round_polls_nothing(conn, monkeypatch, approved):
    def fail(*a, **k):
        raise AssertionError("polled without fixture mapping")

    monkeypatch.setattr(api_football, "_request", fail)
    result = avail_worker.poll_availability(
        conn, ROUND, "E0", 2026, "key", now=NOW, kickoff_at=KICKOFF
    )
    assert result.requests_spent == 0
    assert "no fixtures mapped to source ids" in result.skipped


def test_fixture_mapping_uses_team_resolution(conn, monkeypatch, approved):
    """Source names are full club names; ours are Football-Data short
    forms."""
    payload = [
        {
            "fixture": {"id": 777, "date": "2026-08-15T14:00:00+00:00"},
            "teams": {"home": {"name": "Manchester City"},
                      "away": {"name": "AFC Bournemouth"}},
            "league": {"id": 39, "season": 2026},
        }
    ]
    monkeypatch.setattr(api_football, "_request", lambda *a, **k: payload)
    assert avail_worker.map_round_fixtures(conn, ROUND, "E0", 2026, "key", now=NOW) == 1
    assert capabilities.external_id_for(conn, "api_football", "Man City", "Bournemouth") == "777"


def test_repeat_lineup_poll_is_idempotent(conn, monkeypatch, approved):
    capabilities.map_fixture(conn, "api_football", "555", "Chelsea", "Newcastle", now=NOW)
    capabilities.record_capability(conn, "api_football", 39, 2026, "injuries", False, now=NOW)
    capabilities.record_capability(conn, "api_football", 39, 2026, "lineups", True, now=NOW)
    monkeypatch.setattr(
        api_football, "fetch_lineups",
        lambda fid, key, **kw: api_football.parse_lineups(LINEUP_PAYLOAD, fid),
    )
    avail_worker.poll_availability(conn, ROUND, "E0", 2026, "key", now=NOW, kickoff_at=KICKOFF)
    second = avail_worker.poll_availability(
        conn, ROUND, "E0", 2026, "key", now=NOW, kickoff_at=KICKOFF
    )
    assert second.lineups_ingested == 0  # already have that confirmed XI


def test_provider_header_overrides_drifted_local_count(tmp_path):
    """Local counting drifts — it includes calls the provider did not
    charge for. Refusing on a pessimistic local count while the provider
    reports credits left would silently throttle allowed work."""
    path = tmp_path / "q.json"
    spec = api_football.QUOTA
    quota_mod.record(spec, spec.limit, "18", path, NOW)   # local says exhausted
    assert quota_mod.used(spec, path, NOW) == spec.limit
    assert quota_mod.remaining(spec, path, NOW) == 18     # header wins
    # checked a couple of minutes on, so the separate per-minute window
    # (which this bulk record filled) is not what is under test here
    later = NOW + timedelta(minutes=2)
    assert quota_mod.remaining(spec, path, later) == 18
    quota_mod.check(spec, 1, path, later)                 # and check agrees


def test_header_exhaustion_is_respected(tmp_path):
    path = tmp_path / "q.json"
    spec = api_football.QUOTA
    quota_mod.record(spec, 1, "0", path, NOW)
    with pytest.raises(quota_mod.QuotaExceededError):
        quota_mod.check(spec, 1, path, NOW)
