"""Persistence for `fpl_recommendation_snapshots` (docs/SPEC_FPL.md §11.2, §15).

Append-only, unlike `projection_store.py`'s idempotent-update tables:
"At the deadline, persist an immutable recommendation snapshot... Later
news, actual opponent picks or confirmed lineups create new observed
events but do not mutate the pre-deadline record" (§11.2). Every call to
`record_recommendation` inserts a new row — including repeated pre-
deadline reforecasts, which are each their own genuine observation, not
drafts of one mutable row. Nothing here decides which row is "the"
official frozen decision for a Gameweek (a worker/deadline-scheduling
concern outside Phase 1B's scope) — `recommendations_for` returns every
snapshot in recorded order and leaves that call to its reader.
"""

from __future__ import annotations

import json
import sqlite3

from ..db import to_iso, utcnow
from .optimiser import SquadSelection

COLUMNS = (
    "season", "gameweek", "game_state_snapshot_id", "computed_at",
    "model_version", "top_k", "squad_ids", "starter_ids", "bench_order",
    "captain_id", "vice_captain_id", "transfers_in", "transfers_out",
    "hit_cost", "xi_expected_points", "evaluated_expected_points",
    "net_expected_points",
)


def record_recommendation(
    conn: sqlite3.Connection,
    season: int,
    gameweek: int,
    selection: SquadSelection,
    model_version: str,
    top_k: int,
    game_state_snapshot_id: int | None = None,
    now=None,
) -> int:
    computed_at = to_iso(now or utcnow())
    values = (
        season, gameweek, game_state_snapshot_id, computed_at, model_version, top_k,
        json.dumps(sorted(selection.squad_ids)),
        json.dumps(sorted(selection.starter_ids)),
        json.dumps(list(selection.bench_order)),
        selection.captain_id, selection.vice_captain_id,
        json.dumps(sorted(selection.transfers_in)),
        json.dumps(sorted(selection.transfers_out)),
        selection.hit_cost, selection.xi_expected_points,
        selection.evaluated_expected_points, selection.net_expected_points,
    )
    cursor = conn.execute(
        f"INSERT INTO fpl_recommendation_snapshots ({', '.join(COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in COLUMNS)})",
        values,
    )
    return int(cursor.lastrowid)


def recommendations_for(
    conn: sqlite3.Connection, season: int, gameweek: int
) -> list[sqlite3.Row]:
    """Every recommendation snapshot recorded for this Gameweek, in the
    order they were made — including superseded pre-deadline reforecasts,
    since none of them are ever deleted or rewritten (§11.2)."""
    return conn.execute(
        "SELECT * FROM fpl_recommendation_snapshots WHERE season = ? AND gameweek = ? "
        "ORDER BY id",
        (season, gameweek),
    ).fetchall()
