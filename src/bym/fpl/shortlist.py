"""A deliberately bounded, no-cost FPL transfer comparison.

This is the permitted fallback when there is no complete current FPL market
feed.  It can answer only: *hold, or make one of the explicitly supplied
one-for-one changes?*  It never searches for players outside that shortlist,
and its result is therefore labelled ``SHORTLIST_ONLY`` rather than a
market-wide transfer recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .lineup import recommend_lineup
from .optimiser import MAX_PER_CLUB, SQUAD_COMPOSITION, TRANSFER_HIT_COST, OptimiserCandidate, SquadSelection

SHORTLIST_ONLY = "SHORTLIST_ONLY"


class ShortlistInputError(ValueError):
    """The current squad or the user-selected shortlist is not usable."""


@dataclass(frozen=True)
class ShortlistOption:
    """One feasible hold or one-for-one shortlist scenario."""

    selection: SquadSelection
    transfer_in_id: int | None
    transfer_out_id: int | None
    transfer_hit_cost: float
    net_expected_points: float

    @property
    def is_hold(self) -> bool:
        return self.transfer_in_id is None


@dataclass(frozen=True)
class ShortlistAdvice:
    """The best option among a manager-provided, incomplete candidate set."""

    scope: str
    recommended: ShortlistOption
    options: tuple[ShortlistOption, ...]


def _validate_current_squad(current_squad: list[OptimiserCandidate]) -> dict[int, OptimiserCandidate]:
    if len(current_squad) != 15:
        raise ShortlistInputError(f"shortlist advice needs exactly 15 owned players, got {len(current_squad)}")
    by_id = {player.player_id: player for player in current_squad}
    if len(by_id) != 15:
        raise ShortlistInputError("shortlist advice needs 15 distinct owned player IDs")
    counts = {position: 0 for position in SQUAD_COMPOSITION}
    clubs: dict[str, int] = {}
    for player in current_squad:
        if player.position not in counts:
            raise ShortlistInputError(f"unknown FPL position {player.position!r}")
        counts[player.position] += 1
        clubs[player.club] = clubs.get(player.club, 0) + 1
    if counts != SQUAD_COMPOSITION:
        raise ShortlistInputError(f"owned players do not have legal FPL composition: {counts}")
    if any(count > MAX_PER_CLUB for count in clubs.values()):
        raise ShortlistInputError("owned players exceed FPL's three-per-club limit")
    return by_id


def recommend_from_shortlist(
    current_squad: list[OptimiserCandidate],
    selling_prices: dict[int, float],
    shortlist: list[OptimiserCandidate],
    *,
    bank: float,
    free_transfers: int,
) -> ShortlistAdvice:
    """Compare holding with feasible one-for-one changes from ``shortlist``.

    ``shortlist`` contains only players the manager has personally nominated;
    an omitted player is deliberately never considered.  Each candidate is
    assessed only against same-position owned players, preserving the FPL 15
    composition.  Prices are required only for the owned squad and those few
    nominated targets.  The result includes XI/bench/captaincy for every
    feasible scenario and is always scoped as ``SHORTLIST_ONLY``.
    """
    if bank < 0:
        raise ShortlistInputError("bank cannot be negative")
    if free_transfers < 0:
        raise ShortlistInputError("free_transfers cannot be negative")

    owned = _validate_current_squad(current_squad)
    missing_selling = set(owned) - set(selling_prices)
    if missing_selling:
        raise ShortlistInputError(f"missing selling price for owned player ID(s) {sorted(missing_selling)}")
    if any(price < 0 for price in selling_prices.values()):
        raise ShortlistInputError("selling prices cannot be negative")

    shortlist_by_id = {player.player_id: player for player in shortlist}
    if len(shortlist_by_id) != len(shortlist):
        raise ShortlistInputError("shortlist player IDs must be distinct")
    overlap = set(shortlist_by_id) & set(owned)
    if overlap:
        raise ShortlistInputError(f"shortlist already contains owned player ID(s) {sorted(overlap)}")

    hold_selection = recommend_lineup(current_squad)
    options: list[ShortlistOption] = [
        ShortlistOption(
            selection=hold_selection,
            transfer_in_id=None,
            transfer_out_id=None,
            transfer_hit_cost=0.0,
            net_expected_points=hold_selection.net_expected_points,
        )
    ]
    club_counts: dict[str, int] = {}
    for player in current_squad:
        club_counts[player.club] = club_counts.get(player.club, 0) + 1
    hit_cost = 0.0 if free_transfers >= 1 else TRANSFER_HIT_COST

    for target_id in sorted(shortlist_by_id):
        target = shortlist_by_id[target_id]
        if target.position not in SQUAD_COMPOSITION:
            raise ShortlistInputError(f"unknown FPL position {target.position!r}")
        if target.price < 0:
            raise ShortlistInputError("shortlist prices cannot be negative")
        for outgoing_id in sorted(owned):
            outgoing = owned[outgoing_id]
            if outgoing.position != target.position:
                continue
            if target.price > bank + selling_prices[outgoing_id]:
                continue
            post_club_count = club_counts.get(target.club, 0) + 1 - int(outgoing.club == target.club)
            if post_club_count > MAX_PER_CLUB:
                continue

            proposed_squad = [
                target if player.player_id == outgoing_id else player
                for player in current_squad
            ]
            selection = recommend_lineup(proposed_squad)
            options.append(
                ShortlistOption(
                    selection=selection,
                    transfer_in_id=target_id,
                    transfer_out_id=outgoing_id,
                    transfer_hit_cost=hit_cost,
                    net_expected_points=selection.net_expected_points - hit_cost,
                )
            )

    # Retain list order on a tie: holding is first, so a marginal/neutral
    # shortlist candidate does not create churn merely due to tie-breaking.
    recommended = max(options, key=lambda option: option.net_expected_points)
    return ShortlistAdvice(
        scope=SHORTLIST_ONLY,
        recommended=recommended,
        options=tuple(options),
    )
