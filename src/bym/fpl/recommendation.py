"""Run the frozen FPL optimiser from immutable state and projections.

This is deliberately a narrow Phase 1B orchestration layer.  It joins one
explicit user-imported game-state snapshot to projections bearing one model
version, refuses any missing coverage, then records the resulting selection
append-only.  It never fetches FPL data, chooses a "latest" input, or mutates
the manager's team.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ..db import from_iso, utcnow
from . import game_state, projection_store, recommendation_store
from .causality import LookaheadViolation, assert_causal
from .optimiser import OptimiserCandidate, SquadSelection, optimise_squad


class RecommendationPreflightError(RuntimeError):
    """The selected immutable inputs cannot support a trustworthy run."""


@dataclass(frozen=True)
class RecommendationRun:
    game_state_snapshot_id: int
    selection: SquadSelection
    recommendation_snapshot_id: int


def _appearance_probabilities(
    fixture_rows: list[sqlite3.Row], expected_fixture_counts: dict[int, int]
) -> dict[int, float]:
    """Probability of at least one appearance in the Gameweek.

    Each stored fixture projection carries the baseline's per-fixture
    appearance probability.  For a double this combines them as independent
    availability events; the baseline does not model cross-fixture injury
    correlation, so this is explicit rather than silently using a single
    fixture's value.  A genuine blank has probability zero.
    """
    by_player: dict[int, list[float]] = {}
    for row in fixture_rows:
        by_player.setdefault(int(row["player_id"]), []).append(float(row["p_appearance"]))

    probabilities: dict[int, float] = {}
    for player_id, expected_count in expected_fixture_counts.items():
        values = by_player.get(player_id, [])
        if expected_count == 0:
            probabilities[player_id] = 0.0
            continue
        if len(values) != expected_count:
            raise RecommendationPreflightError(
                f"player_id {player_id} has {expected_count} Gameweek fixture(s) but "
                f"{len(values)} fixture availability projection(s)"
            )
        probability_none = 1.0
        for value in values:
            probability_none *= 1.0 - value
        probabilities[player_id] = 1.0 - probability_none
    return probabilities


def recommend_from_snapshot(
    conn: sqlite3.Connection,
    game_state_snapshot_id: int,
    model_version: str,
    top_k: int,
    now: datetime | None = None,
) -> RecommendationRun:
    """Optimise and append one recommendation from exact frozen inputs.

    Every priced candidate in the imported transfer universe needs a matching
    Gameweek projection for the snapshot's season/Gameweek and model version.
    This blocks the tempting but invalid fallback of optimising only the
    current fifteen while calling the result a transfer recommendation.
    """
    state, squad = game_state.load_snapshot(conn, game_state_snapshot_id)
    if not game_state.candidate_universe_is_complete(state):
        raise RecommendationPreflightError(
            f"FPL game-state snapshot #{game_state_snapshot_id} is marked with an "
            "incomplete candidate universe; import every eligible player's current "
            "price before requesting a transfer recommendation"
        )
    if squad.deadline is None:
        raise RecommendationPreflightError(
            f"FPL game-state snapshot #{game_state_snapshot_id} has no deadline; "
            "cannot prove a recommendation is pre-deadline"
        )
    deadline = from_iso(squad.deadline)
    try:
        assert_causal(
            from_iso(state.observed_at), deadline,
            context=f"FPL game-state snapshot #{game_state_snapshot_id}",
        )
        assert_causal(
            now or utcnow(), deadline,
            context=f"FPL recommendation run for snapshot #{game_state_snapshot_id}",
        )
    except LookaheadViolation as exc:
        raise RecommendationPreflightError(str(exc)) from exc
    universe = game_state.candidate_universe(state)
    projections = projection_store.gameweek_projections_for(
        conn, squad.season, squad.gameweek, model_version
    )
    by_player = {int(row["player_id"]): row for row in projections}
    missing = sorted(set(universe) - set(by_player))
    if missing:
        raise RecommendationPreflightError(
            "missing Gameweek projections for candidate player_id(s): "
            + ", ".join(str(player_id) for player_id in missing)
        )

    appearances = _appearance_probabilities(
        projection_store.fixture_projections_for(conn, squad.season, squad.gameweek, model_version),
        {player_id: int(by_player[player_id]["fixtures_played"]) for player_id in universe},
    )
    candidates = [
        OptimiserCandidate(
            player_id=candidate.player_id,
            position=candidate.position,
            club=candidate.club,
            price=candidate.price,
            expected_points=float(by_player[player_id]["expected_points"]),
            p_appearance=appearances[player_id],
        )
        for player_id, candidate in universe.items()
    ]
    selling_prices = {player.player_id: player.selling_price for player in squad.players}
    selection = optimise_squad(
        candidates,
        bank=squad.bank,
        current_squad=selling_prices,
        free_transfers=squad.free_transfers,
        top_k=top_k,
    )
    recommendation_id = recommendation_store.record_recommendation(
        conn,
        season=squad.season,
        gameweek=squad.gameweek,
        selection=selection,
        model_version=model_version,
        top_k=top_k,
        game_state_snapshot_id=game_state_snapshot_id,
    )
    return RecommendationRun(game_state_snapshot_id, selection, recommendation_id)
