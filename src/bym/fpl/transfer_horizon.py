"""Season-versioned FPL transfer-state transitions for Phase 2.

This module is deliberately a *planning kernel*, not a market scraper or a
transfer recommender.  A caller must provide the manager's owned players,
their actual selling values, and every explicitly considered incoming player.
That makes it safe to use with the free manual-snapshot workflow while the
current full player market remains unavailable.

The rules encoded here are for the 2026/27 specification.  In particular, a
Wildcard or Free Hit neither spends nor wipes banked free transfers; both
preserve the banked amount and the following Gameweek earns the usual one
additional free transfer.  This is intentionally executable behaviour rather
than a comment that a future planner might accidentally ignore.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .optimiser import MAX_PER_CLUB, SQUAD_COMPOSITION, TRANSFER_HIT_COST

FPL_RULESET = "FPL_2026_27"
MAX_BANKED_FREE_TRANSFERS = 5
FIRST_HALF_FINAL_GAMEWEEK = 19

Chip = Literal[
    "wildcard_first_half",
    "free_hit_first_half",
    "bench_boost_first_half",
    "triple_captain_first_half",
    "wildcard_second_half",
    "free_hit_second_half",
    "bench_boost_second_half",
    "triple_captain_second_half",
]

WILDCARDS = frozenset({"wildcard_first_half", "wildcard_second_half"})
FREE_HITS = frozenset({"free_hit_first_half", "free_hit_second_half"})
CHIP_TOKENS = frozenset({
    *WILDCARDS,
    *FREE_HITS,
    "bench_boost_first_half",
    "triple_captain_first_half",
    "bench_boost_second_half",
    "triple_captain_second_half",
})


class TransferHorizonError(ValueError):
    """A proposed state or move is not legal under the selected ruleset."""


@dataclass(frozen=True)
class OwnedPlayer:
    """A player in the manager's current permanent squad.

    ``selling_price`` is deliberately supplied rather than derived from the
    current price: FPL's retained-player value is path dependent.
    """

    player_id: int
    position: str
    club: str
    selling_price: float


@dataclass(frozen=True)
class TransferTarget:
    """One explicitly considered incoming player and their buy price."""

    player_id: int
    position: str
    club: str
    buy_price: float


@dataclass(frozen=True)
class Transfer:
    player_out_id: int
    player_in: TransferTarget


@dataclass(frozen=True)
class TransferState:
    """Permanent squad and resources at a Gameweek decision deadline."""

    gameweek: int
    squad: tuple[OwnedPlayer, ...]
    bank: float
    free_transfers: int
    chips_available: frozenset[str]
    last_chip: str | None = None
    ruleset: str = FPL_RULESET


@dataclass(frozen=True)
class HorizonStep:
    """The nominated moves and, optionally, one chip for one Gameweek."""

    transfers: tuple[Transfer, ...] = ()
    chip: Chip | None = None


@dataclass(frozen=True)
class Transition:
    """One legal decision and the permanent state for the next Gameweek."""

    gameweek: int
    transfers: tuple[Transfer, ...]
    chip: Chip | None
    hit_cost: float
    state_after: TransferState


@dataclass(frozen=True)
class HorizonPlan:
    """A 2–4 Gameweek state path.  It contains no market-wide claim."""

    scope: str
    transitions: tuple[Transition, ...]
    total_hit_cost: float


def _validate_squad(squad: tuple[OwnedPlayer, ...]) -> dict[int, OwnedPlayer]:
    if len(squad) != 15:
        raise TransferHorizonError(f"a permanent squad needs 15 players, got {len(squad)}")
    by_id = {player.player_id: player for player in squad}
    if len(by_id) != len(squad):
        raise TransferHorizonError("permanent squad player IDs must be distinct")
    positions = {position: 0 for position in SQUAD_COMPOSITION}
    clubs: dict[str, int] = {}
    for player in squad:
        if player.position not in positions:
            raise TransferHorizonError(f"unknown FPL position {player.position!r}")
        if player.selling_price < 0:
            raise TransferHorizonError("selling prices cannot be negative")
        positions[player.position] += 1
        clubs[player.club] = clubs.get(player.club, 0) + 1
    if positions != SQUAD_COMPOSITION:
        raise TransferHorizonError(f"permanent squad has invalid composition: {positions}")
    if any(count > MAX_PER_CLUB for count in clubs.values()):
        raise TransferHorizonError("permanent squad exceeds the three-per-club limit")
    return by_id


def _validate_state(state: TransferState) -> dict[int, OwnedPlayer]:
    if state.ruleset != FPL_RULESET:
        raise TransferHorizonError(f"unsupported FPL ruleset {state.ruleset!r}")
    if state.gameweek < 1:
        raise TransferHorizonError("gameweek must be positive")
    if state.bank < 0:
        raise TransferHorizonError("bank cannot be negative")
    if not 0 <= state.free_transfers <= MAX_BANKED_FREE_TRANSFERS:
        raise TransferHorizonError(
            f"free transfers must be between 0 and {MAX_BANKED_FREE_TRANSFERS}"
        )
    unknown_chips = state.chips_available - CHIP_TOKENS
    if unknown_chips:
        raise TransferHorizonError(f"unknown chip token(s) {sorted(unknown_chips)}")
    return _validate_squad(state.squad)


def _validate_chip(state: TransferState, chip: Chip | None) -> None:
    if chip is None:
        return
    if chip not in state.chips_available:
        raise TransferHorizonError(f"chip {chip!r} is not available")
    first_half = chip.endswith("first_half")
    if first_half != (state.gameweek <= FIRST_HALF_FINAL_GAMEWEEK):
        raise TransferHorizonError(f"chip {chip!r} is unavailable in Gameweek {state.gameweek}")
    if state.gameweek == 1 and (chip in WILDCARDS or chip in FREE_HITS):
        raise TransferHorizonError("Wildcard and Free Hit are not usable in Gameweek 1")
    if chip in FREE_HITS and state.last_chip in FREE_HITS:
        raise TransferHorizonError("Free Hit cannot be used in consecutive Gameweeks")


def _apply_permanent_transfers(
    state: TransferState,
    owned: dict[int, OwnedPlayer],
    transfers: tuple[Transfer, ...],
) -> tuple[tuple[OwnedPlayer, ...], float]:
    outgoing_ids = [transfer.player_out_id for transfer in transfers]
    incoming_ids = [transfer.player_in.player_id for transfer in transfers]
    if len(set(outgoing_ids)) != len(outgoing_ids):
        raise TransferHorizonError("a player cannot be transferred out twice")
    if len(set(incoming_ids)) != len(incoming_ids):
        raise TransferHorizonError("a player cannot be transferred in twice")
    unknown_out = set(outgoing_ids) - set(owned)
    if unknown_out:
        raise TransferHorizonError(f"transfer out player ID(s) are not owned: {sorted(unknown_out)}")
    overlap = set(incoming_ids) & set(owned)
    if overlap:
        raise TransferHorizonError(f"transfer in player ID(s) are already owned: {sorted(overlap)}")
    if any(transfer.player_in.buy_price < 0 for transfer in transfers):
        raise TransferHorizonError("buy prices cannot be negative")

    proceeds = sum(owned[player_id].selling_price for player_id in outgoing_ids)
    spend = sum(transfer.player_in.buy_price for transfer in transfers)
    bank_after = state.bank + proceeds - spend
    if bank_after < -1e-9:
        raise TransferHorizonError("proposed transfers are not affordable at recorded selling values")

    post_squad = tuple(
        player for player in state.squad if player.player_id not in set(outgoing_ids)
    ) + tuple(
        OwnedPlayer(
            player_id=transfer.player_in.player_id,
            position=transfer.player_in.position,
            club=transfer.player_in.club,
            selling_price=transfer.player_in.buy_price,
        )
        for transfer in transfers
    )
    _validate_squad(post_squad)
    return post_squad, max(0.0, bank_after)


def _next_free_transfers(current: int, transfer_count: int, chip: Chip | None) -> int:
    """Return next-GW free transfers under the 2026/27 chip semantics."""
    if chip in WILDCARDS or chip in FREE_HITS:
        remaining = current
    else:
        remaining = max(0, current - transfer_count)
    return min(MAX_BANKED_FREE_TRANSFERS, remaining + 1)


def transition(state: TransferState, step: HorizonStep) -> Transition:
    """Apply one Gameweek's moves without making a recommendation.

    Wildcard moves permanently alter the squad, while Free Hit moves are a
    temporary Gameweek squad and therefore deliberately leave the permanent
    squad and bank untouched in ``state_after``.
    """
    owned = _validate_state(state)
    _validate_chip(state, step.chip)
    if step.chip in WILDCARDS or step.chip in FREE_HITS:
        hit_cost = 0.0
    else:
        hit_cost = TRANSFER_HIT_COST * max(0, len(step.transfers) - state.free_transfers)

    post_squad, bank_after = _apply_permanent_transfers(state, owned, step.transfers)
    state_after = TransferState(
        gameweek=state.gameweek + 1,
        squad=state.squad if step.chip in FREE_HITS else post_squad,
        bank=state.bank if step.chip in FREE_HITS else bank_after,
        free_transfers=_next_free_transfers(state.free_transfers, len(step.transfers), step.chip),
        chips_available=state.chips_available - ({step.chip} if step.chip else set()),
        last_chip=step.chip,
        ruleset=state.ruleset,
    )
    return Transition(
        gameweek=state.gameweek,
        transfers=step.transfers,
        chip=step.chip,
        hit_cost=hit_cost,
        state_after=state_after,
    )


def plan_shortlist_horizon(state: TransferState, steps: tuple[HorizonStep, ...]) -> HorizonPlan:
    """Build a bounded 2–4 GW path from only explicit, supplied candidates."""
    if not 2 <= len(steps) <= 4:
        raise TransferHorizonError("Phase 2 planning needs a horizon of 2 to 4 Gameweeks")
    transitions: list[Transition] = []
    current = state
    for step in steps:
        applied = transition(current, step)
        transitions.append(applied)
        current = applied.state_after
    return HorizonPlan(
        scope="SHORTLIST_ONLY_HORIZON",
        transitions=tuple(transitions),
        total_hit_cost=sum(applied.hit_cost for applied in transitions),
    )
