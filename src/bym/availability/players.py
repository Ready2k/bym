"""Deterministic player resolution (Increment 3.3).

Same philosophy as team resolution: exact -> registered alias ->
normalised exact -> unresolved. **Never fuzzy.** "J. Williams" must not be
resolved by whichever Williams scores highest on a string-similarity
function; if a squad contains two players it could mean, the answer is
AMBIGUOUS, which is treated exactly like unresolved.

An unresolved player yields `PLAYER_UNRESOLVED` and no player-specific
inference is drawn. The event is still recorded — we know *something* was
reported about that team — but it never attaches to a player identity.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .model import Resolution


@dataclass(frozen=True)
class PlayerRef:
    player_id: str
    full_name: str
    aliases: tuple[str, ...] = ()

    @property
    def surname(self) -> str:
        return self.full_name.split()[-1] if self.full_name.split() else ""

    @property
    def initial(self) -> str:
        parts = self.full_name.split()
        return parts[0][0].lower() if parts else ""


@dataclass(frozen=True)
class PlayerResolution:
    status: str
    player_id: str | None = None
    matched_name: str | None = None
    candidates: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.status == Resolution.RESOLVED.value


def normalise(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_initial_form(name: str) -> tuple[str, str] | None:
    """'J. Williams' / 'J Williams' -> ('j', 'williams'); else None."""
    match = re.match(r"^\s*([A-Za-z])\.?\s+(.+)$", name.strip())
    if not match:
        return None
    initial, rest = match.group(1).lower(), normalise(match.group(2))
    return (initial, rest) if rest else None


def resolve_player(name: str, squad: list[PlayerRef]) -> PlayerResolution:
    """Resolve a reported name against a known squad.

    Ambiguity is a failure, not a tie-break: two plausible matches means
    AMBIGUOUS, and the caller must not guess.
    """
    if not name or not name.strip():
        return PlayerResolution(Resolution.UNRESOLVED.value)

    target = normalise(name)

    exact = [p for p in squad if normalise(p.full_name) == target]
    if len(exact) == 1:
        return PlayerResolution(
            Resolution.RESOLVED.value, exact[0].player_id, exact[0].full_name
        )
    if len(exact) > 1:
        return PlayerResolution(
            Resolution.AMBIGUOUS.value,
            candidates=tuple(p.full_name for p in exact),
        )

    aliased = [
        p for p in squad if any(normalise(a) == target for a in p.aliases)
    ]
    if len(aliased) == 1:
        return PlayerResolution(
            Resolution.RESOLVED.value, aliased[0].player_id, aliased[0].full_name
        )
    if len(aliased) > 1:
        return PlayerResolution(
            Resolution.AMBIGUOUS.value,
            candidates=tuple(p.full_name for p in aliased),
        )

    # Surname alone, only when it is unique in the squad.
    by_surname = [p for p in squad if normalise(p.surname) == target]
    if len(by_surname) == 1:
        return PlayerResolution(
            Resolution.RESOLVED.value,
            by_surname[0].player_id,
            by_surname[0].full_name,
        )
    if len(by_surname) > 1:
        return PlayerResolution(
            Resolution.AMBIGUOUS.value,
            candidates=tuple(p.full_name for p in by_surname),
        )

    # Initial + surname, only when that pair is unique.
    parsed = _parse_initial_form(name)
    if parsed:
        initial, surname = parsed
        matches = [
            p
            for p in squad
            if normalise(p.surname) == surname and p.initial == initial
        ]
        if len(matches) == 1:
            return PlayerResolution(
                Resolution.RESOLVED.value,
                matches[0].player_id,
                matches[0].full_name,
            )
        if len(matches) > 1:
            return PlayerResolution(
                Resolution.AMBIGUOUS.value,
                candidates=tuple(p.full_name for p in matches),
            )
        # Surname matched but the initial rules every candidate out — that
        # is a contradiction, not a near-miss to be resolved optimistically.
        surname_only = [p for p in squad if normalise(p.surname) == surname]
        if surname_only:
            return PlayerResolution(
                Resolution.UNRESOLVED.value,
                candidates=tuple(p.full_name for p in surname_only),
            )

    return PlayerResolution(Resolution.UNRESOLVED.value)
