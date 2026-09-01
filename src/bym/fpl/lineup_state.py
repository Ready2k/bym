"""Small, explicit local FPL lineup snapshots for the free Phase 1C mode.

This is intentionally not a replacement for ``game_state``: it contains a
manager's visible fifteen and optional manager-selected shortlist evidence,
not a complete transfer market, purchase prices, or an implied FPL feed.
It exists so the UI can be useful without scraping FPL or asking for a
market-wide CSV.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .optimiser import SQUAD_COMPOSITION


class LineupStateError(ValueError):
    """The local, user-visible lineup snapshot is internally inconsistent."""


@dataclass(frozen=True)
class LineupPlayer:
    name: str
    club: str
    position: str
    current_price: float
    fixture: str
    is_starter: bool
    bench_order: int | None = None
    is_captain: bool = False
    is_vice_captain: bool = False


@dataclass(frozen=True)
class ShortlistCandidate:
    name: str
    club: str
    position: str
    current_price: float
    total_points: int | None
    fixture: str


@dataclass(frozen=True)
class LineupState:
    observed_at: str
    gameweek: int
    deadline: str | None
    bank: float
    free_transfers: int
    source_label: str
    players: tuple[LineupPlayer, ...]
    shortlist: tuple[ShortlistCandidate, ...] = ()
    shortlist_recommendation: str | None = None
    shortlist_reasoning: str | None = None
    chips_available: tuple[str, ...] = field(default_factory=tuple)


def _validate(state: LineupState) -> None:
    if len(state.players) != 15:
        raise LineupStateError(f"lineup state needs 15 players, got {len(state.players)}")
    names = [player.name.casefold() for player in state.players]
    if len(set(names)) != 15:
        raise LineupStateError("lineup state players must have distinct names")
    counts = {position: 0 for position in SQUAD_COMPOSITION}
    starters = []
    bench = []
    for player in state.players:
        if player.position not in counts:
            raise LineupStateError(f"unknown FPL position {player.position!r}")
        if player.current_price < 0:
            raise LineupStateError("player prices cannot be negative")
        counts[player.position] += 1
        if player.is_starter:
            starters.append(player)
            if player.bench_order is not None:
                raise LineupStateError("a starter cannot have a bench order")
        else:
            bench.append(player)
    if counts != SQUAD_COMPOSITION:
        raise LineupStateError(f"lineup state has invalid composition: {counts}")
    if len(starters) != 11 or len(bench) != 4:
        raise LineupStateError("lineup state needs 11 starters and 4 bench players")
    if sorted(player.bench_order for player in bench) != [1, 2, 3, 4]:
        raise LineupStateError("bench players need orders 1 through 4")
    captains = [player for player in starters if player.is_captain]
    vices = [player for player in starters if player.is_vice_captain]
    if len(captains) != 1 or len(vices) != 1 or captains == vices:
        raise LineupStateError("lineup state needs distinct starting captain and vice captain")


def parse(payload: dict) -> LineupState:
    """Validate one manually/user-visibly supplied lineup snapshot."""
    try:
        players = tuple(LineupPlayer(**row) for row in payload["players"])
        shortlist = tuple(ShortlistCandidate(**row) for row in payload.get("shortlist", []))
        state = LineupState(
            observed_at=payload["observed_at"],
            gameweek=payload["gameweek"],
            deadline=payload.get("deadline"),
            bank=float(payload["bank"]),
            free_transfers=int(payload["free_transfers"]),
            source_label=payload["source_label"],
            players=players,
            shortlist=shortlist,
            shortlist_recommendation=payload.get("shortlist_recommendation"),
            shortlist_reasoning=payload.get("shortlist_reasoning"),
            chips_available=tuple(payload.get("chips_available", [])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LineupStateError(f"invalid lineup-state payload: {exc}") from exc
    if state.gameweek < 1 or state.bank < 0 or state.free_transfers < 0:
        raise LineupStateError("gameweek, bank and free transfers must be non-negative")
    _validate(state)
    return state


def load(path: Path) -> LineupState | None:
    """Load the optional local snapshot. Missing is a normal empty state."""
    if not path.exists():
        return None
    try:
        return parse(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError) as exc:
        raise LineupStateError(f"cannot read lineup state at {path}: {exc}") from exc


def save(state: LineupState, path: Path) -> None:
    """Write validated user-visible state; never records raw FPL responses."""
    _validate(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2) + "\n")
