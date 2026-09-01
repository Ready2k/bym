"""Frontend-independent read model for the Phase 1C FPL dashboard.

The dashboard is intentionally read-only.  It shows only immutable FPL state
and recommendation snapshots already stored locally; it does not call FPL,
scrape an authenticated session, or imply that an incomplete shortlist is a
market-wide transfer recommendation.
"""

from __future__ import annotations

import json
import sqlite3

from pathlib import Path

from . import game_state, lineup_state, manual_lineup_estimate


def _selection_names(ids: list[int], names: dict[int, str]) -> list[dict[str, int | str]]:
    return [{"player_id": player_id, "name": names.get(player_id, f"Player {player_id}")} for player_id in ids]


def _lineup_only_payload(state: lineup_state.LineupState) -> dict:
    players = [
        {
            "player_id": None,
            "name": player.name,
            "club": player.club,
            "position": player.position,
            "purchase_price": None,
            "current_price": player.current_price,
            "selling_price": None,
            "fixture": player.fixture,
            "is_starter": player.is_starter,
            "bench_order": player.bench_order,
            "is_captain": player.is_captain,
            "is_vice_captain": player.is_vice_captain,
        }
        for player in state.players
    ]
    players.sort(key=lambda player: (not player["is_starter"], player["bench_order"] or 0, player["name"]))
    return {
        "status": "LINEUP_ONLY",
        "message": (
            "This is a local snapshot of the manager's user-visible squad. It supports "
            "lineup display and the named shortlist only; it is not a complete FPL market feed."
        ),
        "squad": {
            "snapshot_id": None,
            "observed_at": state.observed_at,
            "gameweek": state.gameweek,
            "deadline": state.deadline,
            "bank": state.bank,
            "free_transfers": state.free_transfers,
            "active_chip": None,
            "chips_available": list(state.chips_available),
            "candidate_universe_complete": False,
            "players": players,
        },
        "recommendation": None,
        "lineup_estimate": None,
        "source": {
            "kind": "MANUAL_SNAPSHOT",
            "label": state.source_label,
            "observed_at": state.observed_at,
            "current_player_feed": False,
        },
        "shortlist": {
            "source_label": state.source_label,
            "candidates": [
                {
                    "name": candidate.name,
                    "club": candidate.club,
                    "position": candidate.position,
                    "current_price": candidate.current_price,
                    "total_points": candidate.total_points,
                    "fixture": candidate.fixture,
                }
                for candidate in state.shortlist
            ],
            "recommendation": state.shortlist_recommendation,
            "reasoning": state.shortlist_reasoning,
        },
    }


def dashboard_payload(conn: sqlite3.Connection, lineup_state_path: Path | None = None) -> dict:
    """Return the latest stored squad and its latest immutable recommendation.

    Absence is a normal, explicit state.  A user can use chat-based fixed
    squad/shortlist help without saving a market snapshot, and this endpoint
    must never turn that absence into a misleading recommendation.
    """
    latest = conn.execute(
        "SELECT id FROM fpl_game_state_snapshots ORDER BY observed_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if latest is None:
        if lineup_state_path is not None:
            visible_state = lineup_state.load(lineup_state_path)
            if visible_state is not None:
                payload = _lineup_only_payload(visible_state)
                payload["lineup_estimate"] = manual_lineup_estimate.estimate_lineup(
                    conn, visible_state
                )
                return payload
        return {
            "status": "AWAITING_SQUAD",
            "message": (
                "No FPL squad snapshot is stored locally. The free chat-based "
                "lineup and shortlist workflow remains available; no FPL account "
                "is queried from this dashboard."
            ),
            "squad": None,
            "recommendation": None,
            "lineup_estimate": None,
            "source": None,
            "shortlist": None,
        }

    state, squad = game_state.load_snapshot(conn, int(latest["id"]))
    candidates = game_state.candidate_universe(state)
    names = {player_id: candidate.name for player_id, candidate in candidates.items()}
    starter_ids = set(squad.starter_ids)
    bench_positions = {player_id: index + 1 for index, player_id in enumerate(squad.bench_order)}
    owned = []
    for player in squad.players:
        candidate = candidates[player.player_id]
        owned.append(
            {
                "player_id": player.player_id,
                "name": candidate.name,
                "club": candidate.club,
                "position": candidate.position,
                "purchase_price": player.purchase_price,
                "current_price": player.current_price,
                "selling_price": player.selling_price,
                "is_starter": player.player_id in starter_ids,
                "bench_order": bench_positions.get(player.player_id),
                "is_captain": player.player_id == squad.captain_id,
                "is_vice_captain": player.player_id == squad.vice_captain_id,
            }
        )
    owned.sort(key=lambda player: (not player["is_starter"], player["bench_order"] or 0, player["name"]))

    complete_universe = game_state.candidate_universe_is_complete(state)
    rec_row = conn.execute(
        "SELECT * FROM fpl_recommendation_snapshots "
        "WHERE game_state_snapshot_id = ? ORDER BY id DESC LIMIT 1",
        (state.id,),
    ).fetchone()
    recommendation = None
    if rec_row is not None:
        recommendation = {
            "snapshot_id": int(rec_row["id"]),
            "computed_at": rec_row["computed_at"],
            "model_version": rec_row["model_version"],
            "top_k": int(rec_row["top_k"]),
            "starters": _selection_names(json.loads(rec_row["starter_ids"]), names),
            "bench": _selection_names(json.loads(rec_row["bench_order"]), names),
            "captain": {"player_id": rec_row["captain_id"], "name": names.get(rec_row["captain_id"], f"Player {rec_row['captain_id']}" )},
            "vice_captain": {"player_id": rec_row["vice_captain_id"], "name": names.get(rec_row["vice_captain_id"], f"Player {rec_row['vice_captain_id']}" )},
            "transfers_in": _selection_names(json.loads(rec_row["transfers_in"]), names),
            "transfers_out": _selection_names(json.loads(rec_row["transfers_out"]), names),
            "hit_cost": float(rec_row["hit_cost"]),
            "net_expected_points": float(rec_row["net_expected_points"]),
        }

    return {
        "status": "FULL_MARKET_READY" if complete_universe else "SHORTLIST_ONLY",
        "message": (
            "A complete candidate universe supports a market-wide recommendation."
            if complete_universe
            else "This snapshot supports squad display and shortlist-only advice, not a market-wide transfer claim."
        ),
        "squad": {
            "snapshot_id": state.id,
            "observed_at": state.observed_at,
            "gameweek": squad.gameweek,
            "deadline": squad.deadline,
            "bank": squad.bank,
            "free_transfers": squad.free_transfers,
            "active_chip": squad.active_chip,
            "chips_available": squad.chips_available,
            "candidate_universe_complete": complete_universe,
            "players": owned,
        },
        "recommendation": recommendation,
        "lineup_estimate": None,
        "source": {
            "kind": "IMPORTED_SNAPSHOT",
            "label": "Imported FPL game-state snapshot",
            "observed_at": state.observed_at,
            "current_player_feed": complete_universe,
        },
        "shortlist": None,
    }
