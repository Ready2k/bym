"""Persistence for `fpl_player_match_stats` (docs/SPEC_FPL.md §4).

One row per (player, fixture, source) — the `source_id` is part of the key
deliberately: two sources describing the same fixture are corroborating
evidence, not duplicates to merge, since §5's four source roles (live,
historical development, research-only, benchmark) are never meant to
collapse into one blended number.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

COLUMNS = (
    "player_id", "fixture_id", "season", "source_id", "source_record_version",
    "dataset_manifest_id", "observed_at", "kickoff_at", "team", "minutes", "goals",
    "assists", "shots", "xg", "xa", "saves", "yellow_cards", "red_cards",
    "own_goals", "penalties_missed", "penalties_saved", "tackles",
    "interceptions", "blocks", "clearances", "recoveries", "cbit_actions",
    "cbirt_actions", "bps",
)


@dataclass
class PlayerMatchStats:
    player_id: int
    fixture_id: str
    season: int
    source_id: str
    source_record_version: str
    observed_at: str
    kickoff_at: str | None = None  # when the fixture was actually played —
    # walk-forward's causal cutoff key (fpl.causality); distinct from
    # observed_at, which is when this row was ingested (always later).
    team: str | None = None  # club represented in this fixture, not a season-end snapshot
    minutes: int = 0
    goals: int = 0
    assists: int = 0
    shots: int | None = None
    xg: float | None = None
    xa: float | None = None
    saves: int | None = None
    yellow_cards: int = 0
    red_cards: int = 0
    own_goals: int | None = None
    penalties_missed: int | None = None
    penalties_saved: int | None = None
    tackles: int | None = None
    interceptions: int | None = None
    blocks: int | None = None
    clearances: int | None = None
    recoveries: int | None = None
    cbit_actions: int | None = None
    cbirt_actions: int | None = None
    bps: int | None = None
    dataset_manifest_id: str | None = None
    id: int | None = None


def _row_to_stats(row: sqlite3.Row) -> PlayerMatchStats:
    return PlayerMatchStats(**{k: row[k] for k in ("id", *COLUMNS)})


def record_match_stats(
    conn: sqlite3.Connection, stats: PlayerMatchStats
) -> tuple[PlayerMatchStats, bool]:
    """Insert a stats row, or update it in place if this exact (player,
    fixture, source) has already been recorded — re-ingesting the same
    fixture from the same source is idempotent, not a duplicate."""
    # New ingestion passes the provider's fixture-time club explicitly.
    # Tests and old one-off callers may not, so retain the only safe
    # fallback available to them: the currently recorded season snapshot.
    # The Phase 1B migration intentionally leaves transferred-player
    # history NULL instead of applying this fallback retrospectively.
    if stats.team is None:
        player_season = conn.execute(
            "SELECT team FROM fpl_player_seasons WHERE player_id = ? AND season = ?",
            (stats.player_id, stats.season),
        ).fetchone()
        if player_season is not None:
            stats.team = player_season["team"]

    existing = conn.execute(
        "SELECT * FROM fpl_player_match_stats WHERE player_id = ? "
        "AND fixture_id = ? AND source_id = ?",
        (stats.player_id, stats.fixture_id, stats.source_id),
    ).fetchone()
    values = tuple(getattr(stats, c) for c in COLUMNS)

    if existing is None:
        cursor = conn.execute(
            f"INSERT INTO fpl_player_match_stats ({', '.join(COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in COLUMNS)})",
            values,
        )
        stats.id = int(cursor.lastrowid)
        return stats, True

    set_clause = ", ".join(f"{c} = ?" for c in COLUMNS)
    conn.execute(
        f"UPDATE fpl_player_match_stats SET {set_clause} WHERE id = ?",
        values + (existing["id"],),
    )
    stats.id = existing["id"]
    return stats, False


def stats_for_fixture(
    conn: sqlite3.Connection, fixture_id: str
) -> list[PlayerMatchStats]:
    rows = conn.execute(
        "SELECT * FROM fpl_player_match_stats WHERE fixture_id = ? ORDER BY id",
        (fixture_id,),
    ).fetchall()
    return [_row_to_stats(r) for r in rows]


def stats_for_player_season(
    conn: sqlite3.Connection, player_id: int, season: int, before: str | None = None
) -> list[PlayerMatchStats]:
    """`before`, when given, restricts to rows with `kickoff_at < before`
    (an ISO8601 string, strict — matching `fpl.causality.is_causal`'s
    "strictly before", never at-or-before) — the causal cutoff walk-
    forward evaluation needs so a fixture cannot see itself or anything
    after it. `None` means "no cutoff", the ordinary (non-walk-forward)
    read every other caller already uses."""
    sql = "SELECT * FROM fpl_player_match_stats WHERE player_id = ? AND season = ?"
    params: tuple = (player_id, season)
    if before is not None:
        sql += " AND kickoff_at < ?"
        params += (before,)
    rows = conn.execute(sql + " ORDER BY fixture_id", params).fetchall()
    return [_row_to_stats(r) for r in rows]


def stats_for_team_season(
    conn: sqlite3.Connection, team: str, season: int, before: str | None = None
) -> list[PlayerMatchStats]:
    """Every ingested match-stat row for a club's season. `team` is the
    provider-recorded club for this specific fixture, not the player's
    latest season snapshot — a player can transfer mid-season. Used to
    derive a team's sampled fixture count and total goals for the projection baseline
    (`fpl.projection`) — there is no separate team-fixtures table.
    `before` — see `stats_for_player_season`."""
    sql = "SELECT fms.* FROM fpl_player_match_stats fms WHERE fms.team = ? AND fms.season = ?"
    params: tuple = (team, season)
    if before is not None:
        sql += " AND fms.kickoff_at < ?"
        params += (before,)
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_stats(r) for r in rows]


def stats_for_season(conn: sqlite3.Connection, season: int) -> list[PlayerMatchStats]:
    """Every ingested match-stat row for a season, across every team —
    the field-completeness/identity gate's input (`fpl.corpus_report`),
    which needs the whole harvested corpus, not one player's or one
    team's slice of it."""
    rows = conn.execute(
        "SELECT * FROM fpl_player_match_stats WHERE season = ? ORDER BY fixture_id, player_id",
        (season,),
    ).fetchall()
    return [_row_to_stats(r) for r in rows]
