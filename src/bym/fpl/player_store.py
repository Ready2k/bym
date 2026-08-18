"""Persistence for `FplPlayer` / `FplPlayerSeason` (docs/SPEC_FPL.md §4).

Idempotent the same way `availability/store.py` is: re-ingesting the same
player or the same season snapshot is a no-op or a plain update, never a
duplicate row, so a source that gets polled repeatedly can't manufacture
two identities for one player.
"""

from __future__ import annotations

import json
import sqlite3

from .players import FplPlayer, FplPlayerSeason

SEASON_COLUMNS = (
    "player_id", "season", "fpl_element_id", "team", "position",
    "start_price", "external_ids",
)


def _row_to_player(row: sqlite3.Row) -> FplPlayer:
    return FplPlayer(id=row["id"], canonical_name=row["canonical_name"])


def _row_to_season(row: sqlite3.Row) -> FplPlayerSeason:
    return FplPlayerSeason(
        id=row["id"],
        player_id=row["player_id"],
        season=row["season"],
        fpl_element_id=row["fpl_element_id"],
        team=row["team"],
        position=row["position"],
        start_price=row["start_price"],
        external_ids=json.loads(row["external_ids"]),
    )


def find_player_by_name(conn: sqlite3.Connection, canonical_name: str) -> FplPlayer | None:
    row = conn.execute(
        "SELECT * FROM fpl_players WHERE canonical_name = ? COLLATE NOCASE",
        (canonical_name,),
    ).fetchone()
    return _row_to_player(row) if row is not None else None


def get_player(conn: sqlite3.Connection, player_id: int) -> FplPlayer | None:
    row = conn.execute("SELECT * FROM fpl_players WHERE id = ?", (player_id,)).fetchone()
    return _row_to_player(row) if row is not None else None


def get_or_create_player(
    conn: sqlite3.Connection, canonical_name: str
) -> tuple[FplPlayer, bool]:
    """Resolve a player by name, creating a new stable identity only if
    none exists. Returns (player, was_new)."""
    existing = find_player_by_name(conn, canonical_name)
    if existing is not None:
        return existing, False
    cursor = conn.execute(
        "INSERT INTO fpl_players (canonical_name) VALUES (?)", (canonical_name,)
    )
    return FplPlayer(id=int(cursor.lastrowid), canonical_name=canonical_name), True


def get_player_season(
    conn: sqlite3.Connection, player_id: int, season: int
) -> FplPlayerSeason | None:
    row = conn.execute(
        "SELECT * FROM fpl_player_seasons WHERE player_id = ? AND season = ?",
        (player_id, season),
    ).fetchone()
    return _row_to_season(row) if row is not None else None


def record_player_season(
    conn: sqlite3.Connection, player_season: FplPlayerSeason
) -> tuple[FplPlayerSeason, bool]:
    """Insert a new season snapshot, or update the mutable fields of an
    existing one (team/position/price/external_ids legitimately change
    across a season — price moves, position reclassification). Returns
    (record, was_new). Never uses INSERT OR REPLACE (db.py's portability
    rule) since that would also reset the surrogate id."""
    existing = get_player_season(conn, player_season.player_id, player_season.season)
    values = (
        player_season.player_id,
        player_season.season,
        player_season.fpl_element_id,
        player_season.team,
        player_season.position,
        player_season.start_price,
        json.dumps(player_season.external_ids),
    )
    if existing is None:
        cursor = conn.execute(
            f"INSERT INTO fpl_player_seasons ({', '.join(SEASON_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in SEASON_COLUMNS)})",
            values,
        )
        player_season.id = int(cursor.lastrowid)
        return player_season, True

    conn.execute(
        "UPDATE fpl_player_seasons SET fpl_element_id = ?, team = ?, "
        "position = ?, start_price = ?, external_ids = ? WHERE id = ?",
        (
            player_season.fpl_element_id,
            player_season.team,
            player_season.position,
            player_season.start_price,
            json.dumps(player_season.external_ids),
            existing.id,
        ),
    )
    player_season.id = existing.id
    return player_season, False


def player_seasons_for_season(
    conn: sqlite3.Connection, season: int
) -> list[FplPlayerSeason]:
    rows = conn.execute(
        "SELECT * FROM fpl_player_seasons WHERE season = ? ORDER BY team, id", (season,)
    ).fetchall()
    return [_row_to_season(r) for r in rows]


def find_player_by_external_id(
    conn: sqlite3.Connection, provider: str, external_id: str, season: int
) -> FplPlayer | None:
    """Resolve a stable player identity from a provider's season-scoped id
    (e.g. api_football's player id, or a future FPL `fpl_element_id`).

    Scans in Python rather than a JSON path operator, deliberately: §26
    requires the same query to work unchanged on SQLite or PostgreSQL, and
    at FPL's scale (~600-700 players/season) this is not a hot path.
    """
    for ps in player_seasons_for_season(conn, season):
        if ps.external_ids.get(provider) == external_id:
            return get_player(conn, ps.player_id)
    return None
