"""Season-scoped FPL player identity (SPEC_FPL.md §4, Phase 1A)."""

import pytest

from bym import db
from bym.fpl import player_store as store
from bym.fpl.players import FplPlayerSeason, Position


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "bym.db") as c:
        yield c


def test_get_or_create_player_assigns_a_stable_id(conn):
    player, was_new = store.get_or_create_player(conn, "Cole Palmer")
    assert was_new
    assert player.id is not None
    assert player.canonical_name == "Cole Palmer"


def test_get_or_create_player_is_idempotent_by_name(conn):
    """Re-ingesting the same player from a second source must not
    manufacture a second identity."""
    first, _ = store.get_or_create_player(conn, "Cole Palmer")
    second, was_new = store.get_or_create_player(conn, "Cole Palmer")
    assert not was_new
    assert second.id == first.id


def test_get_or_create_player_matches_name_case_insensitively(conn):
    first, _ = store.get_or_create_player(conn, "Cole Palmer")
    second, was_new = store.get_or_create_player(conn, "cole palmer")
    assert not was_new
    assert second.id == first.id


def test_record_player_season_creates_a_new_season_snapshot(conn):
    player, _ = store.get_or_create_player(conn, "Cole Palmer")
    season = FplPlayerSeason(
        player_id=player.id,
        season=2026,
        team="Chelsea",
        position=Position.MID.value,
        fpl_element_id=123,
        start_price=10.5,
        external_ids={"api_football": "999"},
    )
    stored, was_new = store.record_player_season(conn, season)
    assert was_new
    assert stored.id is not None

    fetched = store.get_player_season(conn, player.id, 2026)
    assert fetched.team == "Chelsea"
    assert fetched.position == "MID"
    assert fetched.fpl_element_id == 123
    assert fetched.start_price == 10.5
    assert fetched.external_ids == {"api_football": "999"}


def test_record_player_season_updates_in_place_rather_than_duplicating(conn):
    """team/position/price legitimately change across a season (price
    moves, position reclassification) — this must update the one row for
    (player_id, season), not create a second."""
    player, _ = store.get_or_create_player(conn, "Cole Palmer")
    store.record_player_season(
        conn,
        FplPlayerSeason(
            player_id=player.id, season=2026, team="Chelsea",
            position=Position.MID.value, start_price=10.5,
        ),
    )
    updated, was_new = store.record_player_season(
        conn,
        FplPlayerSeason(
            player_id=player.id, season=2026, team="Chelsea",
            position=Position.MID.value, start_price=10.6,
        ),
    )
    assert not was_new

    all_for_season = store.player_seasons_for_season(conn, 2026)
    assert len(all_for_season) == 1
    assert all_for_season[0].start_price == 10.6


def test_player_seasons_for_season_only_returns_that_season(conn):
    player, _ = store.get_or_create_player(conn, "Cole Palmer")
    store.record_player_season(
        conn, FplPlayerSeason(player_id=player.id, season=2025, team="Chelsea", position="MID")
    )
    store.record_player_season(
        conn, FplPlayerSeason(player_id=player.id, season=2026, team="Chelsea", position="MID")
    )
    assert len(store.player_seasons_for_season(conn, 2025)) == 1
    assert len(store.player_seasons_for_season(conn, 2026)) == 1


def test_find_player_by_external_id_resolves_stable_identity(conn):
    """A provider's own player id is season-scoped; this is how ingestion
    (Phase 1A) reconciles a new source's record against an existing
    fpl_player without re-deriving identity from the name alone."""
    player, _ = store.get_or_create_player(conn, "Cole Palmer")
    store.record_player_season(
        conn,
        FplPlayerSeason(
            player_id=player.id, season=2026, team="Chelsea", position="MID",
            external_ids={"api_football": "999", "fpl": "42"},
        ),
    )
    found = store.find_player_by_external_id(conn, "api_football", "999", season=2026)
    assert found.id == player.id
    found_fpl = store.find_player_by_external_id(conn, "fpl", "42", season=2026)
    assert found_fpl.id == player.id


def test_find_player_by_external_id_returns_none_when_unresolved(conn):
    store.get_or_create_player(conn, "Cole Palmer")
    assert store.find_player_by_external_id(conn, "api_football", "no-such-id", season=2026) is None


def test_different_players_get_different_identities(conn):
    a, _ = store.get_or_create_player(conn, "Cole Palmer")
    b, _ = store.get_or_create_player(conn, "Nicolas Jackson")
    assert a.id != b.id
