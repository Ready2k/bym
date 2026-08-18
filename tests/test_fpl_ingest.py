from datetime import datetime, timezone

import pytest

from bym import db
from bym.fpl import ingest, player_store
from bym.fpl.player_match_stats import stats_for_fixture
from bym.sources.api_football import PlayerMatchStatsRecord

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
KICKOFF = "2022-08-06T14:00:00+00:00"
SEASON = 2022


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "bym.db") as c:
        yield c


def palmer_record(**overrides) -> PlayerMatchStatsRecord:
    defaults = dict(
        fixture_id="fx1",
        team="Chelsea",
        provider_player_id="909",
        player_name="Cole Palmer",
        position_code="M",
        minutes=90,
        goals=1,
        assists=1,
        shots=3,
        saves=None,
        yellow_cards=0,
        red_cards=0,
        tackles=1,
        interceptions=0,
        blocks=0,
        penalties_scored=0,
        penalties_missed=0,
        penalties_saved=None,
    )
    defaults.update(overrides)
    return PlayerMatchStatsRecord(**defaults)


def test_resolve_identity_creates_a_new_player_and_season_snapshot(conn):
    player = ingest.resolve_identity(conn, palmer_record(), SEASON)
    season = player_store.get_player_season(conn, player.id, SEASON)
    assert season.team == "Chelsea"
    assert season.position == "MID"
    assert season.external_ids == {"api_football": "909"}


def test_resolve_identity_reuses_provider_id_across_records(conn):
    """A second fixture's record for the same provider id must resolve to
    the same stable player, not a fresh identity from a name re-match."""
    first = ingest.resolve_identity(conn, palmer_record(fixture_id="fx1"), SEASON)
    second = ingest.resolve_identity(
        conn, palmer_record(fixture_id="fx2", minutes=45, goals=0), SEASON
    )
    assert first.id == second.id


def test_resolve_identity_preserves_position_when_a_later_record_omits_it(conn):
    """A record with no position code (rare, but seen for unused
    substitutes) must not blank out a position already on file."""
    first = ingest.resolve_identity(conn, palmer_record(), SEASON)
    second = ingest.resolve_identity(
        conn, palmer_record(fixture_id="fx2", position_code=None), SEASON
    )
    assert first.id == second.id
    season = player_store.get_player_season(conn, second.id, SEASON)
    assert season.position == "MID"


def test_ingest_fixture_player_stats_persists_match_stats(conn, monkeypatch):
    monkeypatch.setattr(
        ingest.api_football,
        "fetch_player_match_stats",
        lambda fixture_id, api_key, **kw: [palmer_record()],
    )
    stored = ingest.ingest_fixture_player_stats(conn, "fx1", SEASON, "fake-key", kickoff_at=KICKOFF, now=NOW)
    assert len(stored) == 1
    assert stored[0].goals == 1
    assert stored[0].source_id == "api_football"

    rows = stats_for_fixture(conn, "fx1")
    assert len(rows) == 1
    assert rows[0].minutes == 90


def test_ingest_fixture_player_stats_skips_zero_minute_records(conn, monkeypatch):
    """A player who didn't appear has no match-stat evidence to persist —
    a stored zero-minute row would claim '0 goals in 0 minutes' as fact."""
    monkeypatch.setattr(
        ingest.api_football,
        "fetch_player_match_stats",
        lambda fixture_id, api_key, **kw: [palmer_record(minutes=0)],
    )
    stored = ingest.ingest_fixture_player_stats(conn, "fx1", SEASON, "fake-key", kickoff_at=KICKOFF, now=NOW)
    assert stored == []
    assert stats_for_fixture(conn, "fx1") == []


def test_ingest_fixture_player_stats_is_idempotent_on_re_ingest(conn, monkeypatch):
    monkeypatch.setattr(
        ingest.api_football,
        "fetch_player_match_stats",
        lambda fixture_id, api_key, **kw: [palmer_record()],
    )
    ingest.ingest_fixture_player_stats(conn, "fx1", SEASON, "fake-key", kickoff_at=KICKOFF, now=NOW)
    ingest.ingest_fixture_player_stats(conn, "fx1", SEASON, "fake-key", kickoff_at=KICKOFF, now=NOW)
    assert len(stats_for_fixture(conn, "fx1")) == 1


def test_ingest_fixture_player_stats_covers_multiple_players(conn, monkeypatch):
    jackson = palmer_record(
        provider_player_id="910", player_name="Nicolas Jackson",
        position_code="F", goals=2, assists=0,
    )
    monkeypatch.setattr(
        ingest.api_football,
        "fetch_player_match_stats",
        lambda fixture_id, api_key, **kw: [palmer_record(), jackson],
    )
    stored = ingest.ingest_fixture_player_stats(conn, "fx1", SEASON, "fake-key", kickoff_at=KICKOFF, now=NOW)
    assert len(stored) == 2
    assert {s.player_id for s in stored} == {
        p.id for p in (
            player_store.find_player_by_name(conn, "Cole Palmer"),
            player_store.find_player_by_name(conn, "Nicolas Jackson"),
        )
    }
