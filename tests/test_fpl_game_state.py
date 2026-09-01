"""FPL user/permitted game-state import (docs/SPEC_FPL.md §4, §13.3, §15).

§13.1/§13.3's fallback is a full FPL-state *import*, never a live read of
`fantasy.premierleague.com/api/*` — these tests exercise the parser and
the two append-only tables it writes to, not any network path (there is
none to exercise; `conftest.py`'s autouse socket block would fail loudly
if this module ever tried).
"""

import copy
import json

import pytest

from bym import db
from bym.fpl import game_state, player_store
from bym.fpl.game_state import ImportValidationError

SEASON = 2026


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "bym.db") as c:
        yield c


def _candidate(name, club, position, price):
    return {"name": name, "club": club, "position": position, "price": price}


def _squad_entry(name, purchase, current, selling):
    return {
        "name": name,
        "purchase_price": purchase,
        "current_price": current,
        "selling_price": selling,
    }


def sample_payload(**overrides) -> dict:
    """A minimal but complete 15-player import: 2 GK, 5 DEF, 5 MID, 3 FWD
    among the candidates, with a plausible XI/bench/captaincy split. FPL
    squad-legality (formation counts, budget, club limits) is deliberately
    not enforced by this module, so the numbers here are illustrative, not
    validated against those rules.
    """
    candidates = [
        _candidate("Keeper One", "ClubA", "GK", 5.0),
        _candidate("Keeper Two", "ClubB", "GK", 4.5),
        _candidate("Defender One", "ClubA", "DEF", 5.5),
        _candidate("Defender Two", "ClubB", "DEF", 5.0),
        _candidate("Defender Three", "ClubC", "DEF", 4.5),
        _candidate("Defender Four", "ClubD", "DEF", 4.0),
        _candidate("Defender Five", "ClubE", "DEF", 4.0),
        _candidate("Midfielder One", "ClubA", "MID", 9.5),
        _candidate("Midfielder Two", "ClubB", "MID", 8.0),
        _candidate("Midfielder Three", "ClubC", "MID", 6.5),
        _candidate("Midfielder Four", "ClubD", "MID", 5.5),
        _candidate("Midfielder Five", "ClubE", "MID", 5.0),
        _candidate("Forward One", "ClubA", "FWD", 15.0),
        _candidate("Forward Two", "ClubB", "FWD", 8.5),
        _candidate("Forward Three", "ClubC", "FWD", 5.5),
        # Extra candidates not owned by the user — the transfer universe
        # (§13.3) must be bigger than just the squad.
        _candidate("Bench Target One", "ClubF", "MID", 6.0),
        _candidate("Bench Target Two", "ClubG", "FWD", 6.5),
    ]
    squad_names = [c["name"] for c in candidates[:15]]
    squad = [_squad_entry(name, 5.0, 5.0, 5.0) for name in squad_names]
    starters = squad_names[:11]
    bench_order = squad_names[11:15]

    payload = {
        "gameweek": 3,
        "as_of": "2026-08-17T12:00:00+00:00",
        "deadline": "2026-08-20T17:30:00+00:00",
        "bank": 1.5,
        "free_transfers": 2,
        "active_chip": None,
        "chips_available": ["wildcard", "bench_boost", "triple_captain", "free_hit"],
        "candidate_universe_complete": True,
        "candidate_players": candidates,
        "squad": squad,
        "starters": starters,
        "bench_order": bench_order,
        "captain": "Forward One",
        "vice_captain": "Midfielder One",
    }
    payload.update(overrides)
    return payload


# ---- successful parse + persist round-trip -------------------------------


def test_parse_import_resolves_candidate_universe_and_squad(conn):
    universe, snapshot = game_state.parse_import(conn, sample_payload(), SEASON)

    assert len(universe) == 17  # 15 owned + 2 extra transfer targets
    assert len(snapshot.players) == 15
    assert len(snapshot.starter_ids) == 11
    assert len(snapshot.bench_order) == 4
    assert snapshot.source_kind == "IMPORT"
    assert snapshot.bank == 1.5
    assert snapshot.free_transfers == 2

    haaland_like = universe[snapshot.captain_id]
    assert haaland_like.name == "Forward One"
    assert haaland_like.position == "FWD"


def test_store_import_persists_a_provenance_row_and_a_squad_row(conn):
    game_row, squad_row = game_state.store_import(conn, sample_payload(), SEASON)

    assert game_row.id is not None
    assert game_row.source_id == "user_import"
    assert game_row.raw_payload_location is None
    assert game_row.content_hash

    assert squad_row.id is not None
    assert squad_row.game_state_snapshot_id == game_row.id
    assert squad_row.season == SEASON
    assert squad_row.source_kind == "IMPORT"

    persisted = conn.execute(
        "SELECT * FROM fpl_game_state_snapshots WHERE id = ?", (game_row.id,)
    ).fetchone()
    assert persisted is not None
    normalised = json.loads(persisted["normalised_state"])
    assert normalised["candidate_universe_complete"] is True
    assert len(normalised["candidate_players"]) == 17
    assert normalised["squad_snapshot"]["captain_id"] == squad_row.captain_id


def test_candidate_universe_reads_back_out_of_a_loaded_snapshot(conn):
    game_row, _ = game_state.store_import(conn, sample_payload(), SEASON)
    universe = game_state.candidate_universe(game_row)
    assert len(universe) == 17
    assert all(isinstance(p.price, float) for p in universe.values())
    assert game_state.candidate_universe_is_complete(game_row)


def test_partial_candidate_universe_is_stored_but_not_claimed_complete(conn):
    payload = sample_payload()
    payload["candidate_universe_complete"] = False

    game_row, _ = game_state.store_import(conn, payload, SEASON)

    assert not game_state.candidate_universe_is_complete(game_row)


# ---- idempotency ----------------------------------------------------------


def test_reimporting_identical_payload_is_idempotent(conn):
    payload = sample_payload()
    first_game, first_squad = game_state.store_import(conn, payload, SEASON)
    second_game, second_squad = game_state.store_import(conn, copy.deepcopy(payload), SEASON)

    assert second_game.id == first_game.id
    assert second_squad.id == first_squad.id

    game_rows = conn.execute("SELECT COUNT(*) AS n FROM fpl_game_state_snapshots").fetchone()
    squad_rows = conn.execute("SELECT COUNT(*) AS n FROM fpl_squad_snapshots").fetchone()
    assert game_rows["n"] == 1
    assert squad_rows["n"] == 1


def test_reimporting_changed_payload_creates_a_new_immutable_snapshot(conn):
    payload = sample_payload()
    first_game, _ = game_state.store_import(conn, payload, SEASON)

    changed = sample_payload(bank=2.0)
    second_game, _ = game_state.store_import(conn, changed, SEASON)

    assert second_game.id != first_game.id
    game_rows = conn.execute("SELECT COUNT(*) AS n FROM fpl_game_state_snapshots").fetchone()
    assert game_rows["n"] == 2

    original = conn.execute(
        "SELECT bank FROM fpl_squad_snapshots WHERE game_state_snapshot_id = ?",
        (first_game.id,),
    ).fetchone()
    assert original["bank"] == 1.5  # the original row is untouched, not overwritten


# ---- player identity resolution -------------------------------------------


def test_candidate_universe_resolves_against_existing_player_identity(conn):
    existing, _ = player_store.get_or_create_player(conn, "Forward One")

    universe, _ = game_state.parse_import(conn, sample_payload(), SEASON)

    assert universe[existing.id].name == "Forward One"
    total_players = conn.execute("SELECT COUNT(*) AS n FROM fpl_players").fetchone()
    assert total_players["n"] == 17  # no duplicate identity created for the pre-existing player


def test_unrecognised_player_name_gets_a_new_identity_not_an_error(conn):
    """An FPL candidate list will include players with no historical
    stats yet — that is not an import error."""
    before = conn.execute("SELECT COUNT(*) AS n FROM fpl_players").fetchone()["n"]
    game_state.parse_import(conn, sample_payload(), SEASON)
    after = conn.execute("SELECT COUNT(*) AS n FROM fpl_players").fetchone()["n"]
    assert after == before + 17


# ---- shape validation -------------------------------------------------


def test_missing_top_level_key_is_rejected_with_a_clear_error(conn):
    payload = sample_payload()
    del payload["free_transfers"]
    with pytest.raises(ImportValidationError, match="free_transfers"):
        game_state.parse_import(conn, payload, SEASON)


def test_squad_not_exactly_15_players_is_rejected(conn):
    payload = sample_payload()
    payload["squad"] = payload["squad"][:14]
    with pytest.raises(ImportValidationError, match="exactly 15"):
        game_state.parse_import(conn, payload, SEASON)


def test_starters_not_exactly_11_players_is_rejected(conn):
    payload = sample_payload()
    payload["starters"] = payload["starters"][:10]
    with pytest.raises(ImportValidationError, match="exactly 11"):
        game_state.parse_import(conn, payload, SEASON)


def test_bench_order_not_exactly_4_players_is_rejected(conn):
    payload = sample_payload()
    payload["bench_order"] = payload["bench_order"][:3]
    with pytest.raises(ImportValidationError, match="exactly 4"):
        game_state.parse_import(conn, payload, SEASON)


def test_missing_captain_is_rejected_with_a_clear_error(conn):
    payload = sample_payload()
    payload["captain"] = None
    with pytest.raises(ImportValidationError, match="captain"):
        game_state.parse_import(conn, payload, SEASON)


def test_captain_and_vice_captain_being_the_same_player_is_rejected(conn):
    payload = sample_payload()
    payload["vice_captain"] = payload["captain"]
    with pytest.raises(ImportValidationError, match="different players"):
        game_state.parse_import(conn, payload, SEASON)


def test_missing_bank_is_rejected(conn):
    payload = sample_payload()
    del payload["bank"]
    with pytest.raises(ImportValidationError, match="bank"):
        game_state.parse_import(conn, payload, SEASON)


def test_missing_free_transfers_is_rejected(conn):
    payload = sample_payload()
    payload["free_transfers"] = None
    with pytest.raises(ImportValidationError, match="free_transfers"):
        game_state.parse_import(conn, payload, SEASON)


def test_squad_player_absent_from_candidate_universe_is_rejected(conn):
    """The candidate list is where a squad player's position/club/current
    price live — a squad name with no matching candidate is unparseable,
    not just incomplete."""
    payload = sample_payload()
    payload["squad"][0] = _squad_entry("Nobody Imported", 5.0, 5.0, 5.0)
    payload["starters"][0] = "Nobody Imported"
    with pytest.raises(ImportValidationError, match="Nobody Imported"):
        game_state.parse_import(conn, payload, SEASON)


def test_starter_and_bench_overlap_is_rejected(conn):
    payload = sample_payload()
    payload["bench_order"][0] = payload["starters"][0]
    with pytest.raises(ImportValidationError, match="both starters and bench_order"):
        game_state.parse_import(conn, payload, SEASON)


def test_unknown_position_is_rejected(conn):
    payload = sample_payload()
    payload["candidate_players"][0]["position"] = "SWEEPER"
    with pytest.raises(ImportValidationError, match="position"):
        game_state.parse_import(conn, payload, SEASON)


# ---- boundary --------------------------------------------------------


def test_this_module_never_imports_a_live_fpl_client():
    """Static check that nothing in this module reaches for the
    prohibited endpoint — §13.1/§13.3's fallback is an import, never a
    live read of fantasy.premierleague.com."""
    import inspect

    from bym.fpl import game_state as module

    source = inspect.getsource(module)
    forbidden_imports = ("import requests", "import urllib", "import http.client", "import httpx")
    assert not any(needle in source for needle in forbidden_imports)
