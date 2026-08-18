"""Per-fixture -> per-Gameweek aggregation (docs/SPEC_FPL.md §4, §6.2).

A player can score across multiple fixtures in one Gameweek (a Double
Gameweek) or none at all (a Blank Gameweek) — §4 splits `fpl_fixture_
projection` from `fpl_gameweek_projection` for exactly this reason.
Summing each fixture's expected points (via `fpl.scoring`) handles both
cases as the natural consequence of "however many fixtures this player
actually has this Gameweek," never as a special case: zero fixtures sums
to zero, two fixtures sums two independent scoring chances (each with its
own captain-multiplier eligibility per §4's note that "a captain's
multiplier applies to the points from each fixture they play").

This is a pure aggregation over already-computed `FixtureProjection`s —
no DB access, so a caller can aggregate a re-run projection without
touching persistence. Wiring `fpl_gameweek_projection`'s table is a
follow-on task once the projection/scoring maths it depends on has had a
chance to be exercised and, per the explicit build direction, audited one
refinement at a time rather than persisted prematurely.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from ..db import to_iso, utcnow
from . import scoring
from .projection import FixtureProjection, MODEL_VERSION
from .scoring import ExpectedPointsBreakdown


@dataclass(frozen=True)
class GameweekProjection:
    """§4 `fpl_gameweek_projection`. `points_variance`/`quantiles` are
    deliberately absent from this baseline (`None`) rather than
    fabricated — this model produces a point estimate only until a
    distributional refinement is audited in and beats it (§6.2, §12.4)."""

    player_id: int
    season: int
    gameweek: int
    expected_points: float
    fixtures_played: int  # 0 = blank, 2+ = double
    fixture_breakdowns: tuple[ExpectedPointsBreakdown, ...]
    points_variance: float | None = None
    model_version: str = MODEL_VERSION
    computed_at: str = ""


def aggregate_gameweek(
    fixture_projections: list[FixtureProjection],
    player_positions: dict[int, str],
    season: int,
    gameweek: int,
    player_ids: set[int] | None = None,
    now: datetime | None = None,
) -> list[GameweekProjection]:
    """Sum each player's fixture-level expected points into one Gameweek
    figure. `player_ids` — normally the full rostered set for the
    Gameweek — controls who gets an explicit zero-fixture (blank) row;
    without it, a blanking player is simply absent from the output rather
    than present with `fixtures_played=0`, which would make "no fixture"
    indistinguishable from "not asked about" to a caller building a
    squad. `season`/`gameweek` are required explicitly (not inferred from
    the projections) precisely because a blanking player has none to
    infer them from.
    """
    computed_at = to_iso(now or utcnow())
    by_player: dict[int, list[FixtureProjection]] = defaultdict(list)
    for proj in fixture_projections:
        if proj.season != season or proj.gameweek != gameweek:
            raise ValueError(
                f"fixture projection for player {proj.player_id} is for "
                f"season={proj.season} gameweek={proj.gameweek}, not the "
                f"requested season={season} gameweek={gameweek}"
            )
        by_player[proj.player_id].append(proj)

    all_ids = set(player_ids) if player_ids is not None else set(by_player)
    out: list[GameweekProjection] = []
    for player_id in all_ids:
        projs = by_player.get(player_id, [])
        position = player_positions.get(player_id)
        if projs and position is None:
            raise ValueError(
                f"player_id {player_id} has fixture projections but no "
                "entry in player_positions — cannot score without a "
                "position (goal/clean-sheet points are position-dependent)."
            )
        breakdowns = tuple(
            scoring.fixture_expected_points(p, position) for p in projs
        )
        out.append(
            GameweekProjection(
                player_id=player_id,
                season=season,
                gameweek=gameweek,
                expected_points=sum(b.total for b in breakdowns),
                fixtures_played=len(projs),
                fixture_breakdowns=breakdowns,
                computed_at=computed_at,
            )
        )
    return out
