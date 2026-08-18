"""Season-scoped FPL player identity (docs/SPEC_FPL.md §4).

Player identity must be separated from season-specific FPL identity:
`fpl_element_id`, club, position and price can all change between
seasons, but the player underneath is the same person a projection or a
squad slot needs to track across that change. `FplPlayer` is the stable
identity; `FplPlayerSeason` is one season's snapshot of it, keyed by
(player_id, season) — the same split §4 draws between `fpl_player` and
`fpl_player_season`.

`external_ids` on the season row, not the player, is deliberate: a
provider's own player id is itself often season-scoped (see
api_football's per-season fixture/player ids), so pinning it to one
season's record is more honest than pretending one external id identifies
a player for all time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Position(str, Enum):
    GK = "GK"
    DEF = "DEF"
    MID = "MID"
    FWD = "FWD"


@dataclass
class FplPlayer:
    canonical_name: str
    id: int | None = None


@dataclass
class FplPlayerSeason:
    player_id: int
    season: int  # start year, e.g. 2026 for 2026/27 — matches DataConfig's convention
    team: str
    position: str  # Position value
    fpl_element_id: int | None = None
    start_price: float | None = None
    external_ids: dict[str, str] = field(default_factory=dict)
    id: int | None = None
