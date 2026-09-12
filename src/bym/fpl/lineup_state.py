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
from datetime import datetime
from pathlib import Path

from .optimiser import MAX_PER_CLUB, SQUAD_COMPOSITION
from .transfer_horizon import MAX_BANKED_FREE_TRANSFERS

LINEUP_CHIPS = frozenset({"wildcard", "free_hit", "bench_boost", "triple_captain"})


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
    if not state.source_label.strip():
        raise LineupStateError("source label is required")
    for label, value in (("observed_at", state.observed_at), ("deadline", state.deadline)):
        if value is None and label == "deadline":
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise LineupStateError(f"{label} must be an ISO-8601 timestamp") from exc
        if parsed.tzinfo is None:
            raise LineupStateError(f"{label} must include a timezone")
    if set(state.chips_available) - LINEUP_CHIPS:
        raise LineupStateError("chips_available contains an unknown FPL chip")
    if len(state.players) != 15:
        raise LineupStateError(f"lineup state needs 15 players, got {len(state.players)}")
    names = [player.name.casefold() for player in state.players]
    if len(set(names)) != 15:
        raise LineupStateError("lineup state players must have distinct names")
    counts = {position: 0 for position in SQUAD_COMPOSITION}
    clubs: dict[str, int] = {}
    starters = []
    bench = []
    for player in state.players:
        if not player.name.strip() or not player.club.strip():
            raise LineupStateError("every player needs a name and club")
        if player.position not in counts:
            raise LineupStateError(f"unknown FPL position {player.position!r}")
        if player.current_price < 0:
            raise LineupStateError("player prices cannot be negative")
        counts[player.position] += 1
        clubs[player.club.casefold()] = clubs.get(player.club.casefold(), 0) + 1
        if player.is_starter:
            starters.append(player)
            if player.bench_order is not None:
                raise LineupStateError("a starter cannot have a bench order")
        else:
            bench.append(player)
    if counts != SQUAD_COMPOSITION:
        raise LineupStateError(f"lineup state has invalid composition: {counts}")
    if any(count > MAX_PER_CLUB for count in clubs.values()):
        raise LineupStateError("lineup state exceeds the three-per-club limit")
    if len(starters) != 11 or len(bench) != 4:
        raise LineupStateError("lineup state needs 11 starters and 4 bench players")
    starter_counts = {
        position: sum(player.position == position for player in starters)
        for position in SQUAD_COMPOSITION
    }
    if not (
        starter_counts["GK"] == 1
        and 3 <= starter_counts["DEF"] <= 5
        and 2 <= starter_counts["MID"] <= 5
        and 1 <= starter_counts["FWD"] <= 3
    ):
        raise LineupStateError(f"starting XI has an invalid formation: {starter_counts}")
    if sorted(player.bench_order for player in bench) != [1, 2, 3, 4]:
        raise LineupStateError("bench players need orders 1 through 4")
    captains = [player for player in starters if player.is_captain]
    vices = [player for player in starters if player.is_vice_captain]
    if len(captains) != 1 or len(vices) != 1 or captains == vices:
        raise LineupStateError("lineup state needs distinct starting captain and vice captain")
    shortlist_names = [candidate.name.casefold() for candidate in state.shortlist]
    if len(shortlist_names) != len(set(shortlist_names)) or set(shortlist_names) & set(names):
        raise LineupStateError("shortlist and owned player names must be distinct")
    for candidate in state.shortlist:
        if not candidate.name.strip() or not candidate.club.strip():
            raise LineupStateError("every shortlist candidate needs a name and club")
        if candidate.position not in SQUAD_COMPOSITION or candidate.current_price < 0:
            raise LineupStateError("shortlist candidates need a valid position and price")


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
    if state.gameweek < 1 or state.bank < 0 or not 0 <= state.free_transfers <= MAX_BANKED_FREE_TRANSFERS:
        raise LineupStateError("gameweek/bank must be non-negative and free transfers must be 0–5")
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
