"""Persistence for `fpl_fixture_projections` / `fpl_gameweek_projections`
(docs/SPEC_FPL.md §4, §15) — deliberately deferred from Phase 1A until
BASELINE_FPL_001's shape had actually been exercised (docs/MANIFEST_FPL.md).

`model_version` is part of the identity key on both tables, not just a
label: §12.3 forbids a later challenger version from silently overwriting
an earlier one's stored row, so a frozen baseline's projections stay
reconstructable even once `FPL_002` exists. Re-recording the SAME
(player, fixture-or-gameweek, model_version) is an idempotent update —
the projection legitimately changes as new evidence arrives before a
Gameweek's deadline, and this is not the immutable side of the pipeline
(`fpl_recommendation_snapshots`, `recommendation_store.py`, is).
"""

from __future__ import annotations

import sqlite3

from ..db import to_iso, utcnow
from .aggregation import GameweekProjection
from .projection import FixtureProjection
from .scoring import ExpectedPointsBreakdown

FIXTURE_COLUMNS = (
    "player_id", "fixture_id", "season", "gameweek", "p_appearance", "p_start",
    "p_60_plus", "expected_minutes", "goal_lambda", "assist_lambda",
    "p_clean_sheet", "expected_saves", "expected_defcon_points",
    "expected_bonus", "expected_points", "points_variance", "model_version",
    "computed_at",
)

GAMEWEEK_COLUMNS = (
    "player_id", "season", "gameweek", "expected_points", "points_variance",
    "quantiles", "simulation_key", "fixtures_played", "model_version",
    "computed_at",
)


def record_fixture_projection(
    conn: sqlite3.Connection,
    proj: FixtureProjection,
    breakdown: ExpectedPointsBreakdown,
    computed_at: str | None = None,
) -> tuple[int, bool]:
    """Persist one player's fixture-level projection plus the scoring
    engine's resulting expected points. `expected_defcon_points` and
    `expected_bonus` are always stored NULL here — this baseline has no
    component for either (§5.4, §18's OMITTED list); a future model
    version that adds one gets its own `model_version` row, not a
    retrofit of this one's."""
    computed_at = computed_at or to_iso(utcnow())
    existing = conn.execute(
        "SELECT id FROM fpl_fixture_projections WHERE player_id = ? "
        "AND fixture_id = ? AND model_version = ?",
        (proj.player_id, proj.fixture_id, proj.model_version),
    ).fetchone()
    values = (
        proj.player_id, proj.fixture_id, proj.season, proj.gameweek,
        proj.p_appearance, proj.p_start, proj.p_60_plus, proj.expected_minutes,
        proj.goal_lambda, proj.assist_lambda, proj.p_clean_sheet,
        proj.expected_saves, None, None, breakdown.total, None,
        proj.model_version, computed_at,
    )
    if existing is None:
        cursor = conn.execute(
            f"INSERT INTO fpl_fixture_projections ({', '.join(FIXTURE_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in FIXTURE_COLUMNS)})",
            values,
        )
        return int(cursor.lastrowid), True
    set_clause = ", ".join(f"{c} = ?" for c in FIXTURE_COLUMNS)
    conn.execute(
        f"UPDATE fpl_fixture_projections SET {set_clause} WHERE id = ?",
        values + (existing["id"],),
    )
    return int(existing["id"]), False


def record_gameweek_projection(
    conn: sqlite3.Connection,
    gw_proj: GameweekProjection,
    computed_at: str | None = None,
) -> tuple[int, bool]:
    computed_at = computed_at or gw_proj.computed_at or to_iso(utcnow())
    existing = conn.execute(
        "SELECT id FROM fpl_gameweek_projections WHERE player_id = ? "
        "AND season = ? AND gameweek = ? AND model_version = ?",
        (gw_proj.player_id, gw_proj.season, gw_proj.gameweek, gw_proj.model_version),
    ).fetchone()
    values = (
        gw_proj.player_id, gw_proj.season, gw_proj.gameweek, gw_proj.expected_points,
        gw_proj.points_variance, None, None, gw_proj.fixtures_played,
        gw_proj.model_version, computed_at,
    )
    if existing is None:
        cursor = conn.execute(
            f"INSERT INTO fpl_gameweek_projections ({', '.join(GAMEWEEK_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in GAMEWEEK_COLUMNS)})",
            values,
        )
        return int(cursor.lastrowid), True
    set_clause = ", ".join(f"{c} = ?" for c in GAMEWEEK_COLUMNS)
    conn.execute(
        f"UPDATE fpl_gameweek_projections SET {set_clause} WHERE id = ?",
        values + (existing["id"],),
    )
    return int(existing["id"]), False


def gameweek_projections_for(
    conn: sqlite3.Connection, season: int, gameweek: int, model_version: str
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM fpl_gameweek_projections WHERE season = ? AND gameweek = ? "
        "AND model_version = ? ORDER BY player_id",
        (season, gameweek, model_version),
    ).fetchall()


def fixture_projections_for(
    conn: sqlite3.Connection, season: int, gameweek: int, model_version: str
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM fpl_fixture_projections WHERE season = ? AND gameweek = ? "
        "AND model_version = ? ORDER BY player_id, fixture_id",
        (season, gameweek, model_version),
    ).fetchall()
