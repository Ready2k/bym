"""Fixed-squad FPL lineup advice without a transfer-market data source.

The full FPL optimiser needs a complete, permitted candidate-price universe
before it can claim a transfer recommendation.  This module deliberately does
less: given exactly the manager's existing fifteen and their point/availability
projections, it chooses a legal XI, bench order and captaincy.  No player can
enter or leave the squad, so it remains useful when the market feed is absent
without pretending to identify the global best transfer.
"""

from __future__ import annotations

from .optimiser import OptimiserCandidate, SquadSelection, optimise_squad


class LineupInputError(ValueError):
    """The supplied fixed squad cannot be assessed as an FPL fifteen."""


def recommend_lineup(squad: list[OptimiserCandidate]) -> SquadSelection:
    """Choose XI, ordered bench, captain and vice from one fixed 15.

    Price is intentionally irrelevant: all fifteen are retained and every
    candidate is assigned a zero selling value solely to express that fixed
    constraint through the audited optimiser.  The returned selection is
    checked defensively so callers cannot accidentally present it as a
    transfer recommendation.
    """
    if len(squad) != 15:
        raise LineupInputError(f"lineup advice needs exactly 15 players, got {len(squad)}")
    player_ids = [player.player_id for player in squad]
    if len(set(player_ids)) != 15:
        raise LineupInputError("lineup advice needs 15 distinct player IDs")

    fixed_squad = {player_id: 0.0 for player_id in player_ids}
    selection = optimise_squad(
        squad,
        bank=0.0,
        current_squad=fixed_squad,
        free_transfers=0,
        top_k=1,
    )
    if selection.squad_ids != frozenset(player_ids):
        raise AssertionError("fixed-squad lineup solver changed the squad")
    if selection.transfers_in or selection.transfers_out or selection.hit_cost:
        raise AssertionError("fixed-squad lineup solver produced a transfer")
    return selection
