"""Expected vs confirmed lineup comparison (Increment 3.2).

Confirmed team news supersedes expected-team assumptions, and the
*difference* between them is the useful deadline-day signal — an expected
starter who turns out to be absent is exactly the information that arrives
too late to be in anyone's model but not too late to act on.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Importance, Lineup, LineupKind
from .players import normalise


@dataclass(frozen=True)
class LineupDifference:
    team: str
    expected_starters_absent: tuple[str, ...]
    unexpected_starters: tuple[str, ...]
    material: bool
    key_players_absent: tuple[str, ...] = ()

    @property
    def change_count(self) -> int:
        return len(self.expected_starters_absent) + len(self.unexpected_starters)

    def describe(self) -> str:
        if not self.change_count:
            return "confirmed XI matches the expected lineup"
        parts = []
        if self.expected_starters_absent:
            parts.append(
                "expected starters absent: "
                + ", ".join(self.expected_starters_absent)
            )
        if self.unexpected_starters:
            parts.append(
                "unexpected starters: " + ", ".join(self.unexpected_starters)
            )
        return "; ".join(parts)


def compare_lineups(
    expected: Lineup,
    confirmed: Lineup,
    key_players: set[str] | None = None,
    material_threshold: int = 2,
) -> LineupDifference:
    """Difference between an expected lineup and the confirmed XI.

    Material when several changes appear, or when any single absent player
    is flagged KEY — one key absence matters more than three squad-rotation
    swaps, so a raw count alone would miss the case that counts.
    """
    if expected.team != confirmed.team:
        raise ValueError("Cannot compare lineups for different teams")
    if confirmed.kind != LineupKind.CONFIRMED.value:
        raise ValueError("Second argument must be a CONFIRMED lineup")

    key_players = {normalise(p) for p in (key_players or set())}
    exp = {normalise(p): p for p in expected.players}
    con = {normalise(p): p for p in confirmed.players}

    absent = tuple(exp[k] for k in exp if k not in con)
    unexpected = tuple(con[k] for k in con if k not in exp)
    key_absent = tuple(exp[k] for k in exp if k not in con and k in key_players)

    material = bool(key_absent) or (len(absent) + len(unexpected)) >= material_threshold
    return LineupDifference(
        team=expected.team,
        expected_starters_absent=absent,
        unexpected_starters=unexpected,
        material=material,
        key_players_absent=key_absent,
    )


def latest(lineups: list[Lineup], team: str, kind: LineupKind) -> Lineup | None:
    candidates = [
        l for l in lineups if l.team == team and l.kind == kind.value
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda l: l.observed_at)
