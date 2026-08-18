"""FPL 2026/27 scoring rules (docs/SPEC_FPL.md §2), applied to a
`FixtureProjection`'s components to get expected points.

Point values verified against current Premier League/FPL sources during
Phase 0 (2026-08-17) — most consequentially that **a goal scored BY a
goalkeeper is worth 10 points, not 6** like a defender's. This is
unrelated to an OWN goal (§2: -2, any position, unmodelled here — see
`OMITTED_COMPONENTS`); confusing "goalkeeper scores" with "goalkeeper
own-goals" would silently misvalue every keeper who might score, which is
exactly the kind of subtly-wrong FPL maths CLAUDE.md and §7.2 warn about
for the budget/transfer maths, restated here for scoring.

This module only converts already-computed components into an expected
points *estimate* — it never re-derives a component itself (that is
`fpl.projection`'s job) and it never claims to model bonus, DefCon,
cards, own goals, goals-conceded deductions or penalties, because this
baseline's `FixtureProjection` has no components for them yet (§6.1: "the
model must state that they are omitted rather than hide them in a
generic residual"). `ExpectedPointsBreakdown.omitted_components` names
exactly what is missing so a caller can never mistake "not modelled" for
"modelled and zero".
"""

from __future__ import annotations

from dataclasses import dataclass

from .players import Position
from .projection import FixtureProjection

# Points per goal, by position — GK is not a typo: a goalkeeper's goal is
# the rarest scoring event in the game and FPL rewards it accordingly.
GOAL_POINTS = {
    Position.GK.value: 10,
    Position.DEF.value: 6,
    Position.MID.value: 5,
    Position.FWD.value: 4,
}

ASSIST_POINTS = 3

# Clean sheet points require 60+ minutes played (§2) — the projection's
# raw p_clean_sheet is the TEAM's probability, unconditioned on this
# player's participation, so it is multiplied by p_60_plus here rather
# than baked into the projection itself.
CLEAN_SHEET_POINTS = {
    Position.GK.value: 4,
    Position.DEF.value: 4,
    Position.MID.value: 1,
    Position.FWD.value: 0,
}

APPEARANCE_POINTS_60_PLUS = 2
APPEARANCE_POINTS_UNDER_60 = 1
SAVES_PER_POINT = 3  # +1 per 3 saves, GK only

# Components this baseline's FixtureProjection has no data for. Listed
# once here so `omitted_components` and any future audition of one of
# them (bonus, DefCon, ...) share the same vocabulary rather than each
# inventing its own label for "not modelled yet".
OMITTED_COMPONENTS = frozenset(
    {"bonus", "defcon", "cards", "own_goals", "goals_conceded_deduction", "penalties"}
)


@dataclass(frozen=True)
class ExpectedPointsBreakdown:
    """Expected points for one player in one fixture, split by the
    components that produced them — a caller that only reads `.total`
    reconstructs the same silent-collapse problem §16 warns against for
    the UI ("never collapse [xP, ownership, confidence...] into one
    pseudo-precision score"), so every addend stays inspectable."""

    appearance_points: float
    goal_points: float
    assist_points: float
    clean_sheet_points: float
    saves_points: float
    omitted_components: frozenset[str] = OMITTED_COMPONENTS

    @property
    def total(self) -> float:
        """Sum of the MODELLED components only. Not a full expected-points
        claim — see `omitted_components`."""
        return (
            self.appearance_points
            + self.goal_points
            + self.assist_points
            + self.clean_sheet_points
            + self.saves_points
        )


def fixture_expected_points(
    proj: FixtureProjection, position: str
) -> ExpectedPointsBreakdown:
    """Convert one fixture's projected components into expected FPL
    points for the given position. Pure function of its arguments — no DB
    access, so it can be called once per fixture per player without
    re-deriving anything `fpl.projection` already computed."""
    if position not in GOAL_POINTS:
        raise ValueError(f"unknown position {position!r}")

    appearance_points = (
        proj.p_60_plus * APPEARANCE_POINTS_60_PLUS
        + max(0.0, proj.p_appearance - proj.p_60_plus) * APPEARANCE_POINTS_UNDER_60
    )
    goal_points = proj.goal_lambda * GOAL_POINTS[position]
    assist_points = proj.assist_lambda * ASSIST_POINTS
    clean_sheet_points = (
        proj.p_clean_sheet * proj.p_60_plus * CLEAN_SHEET_POINTS[position]
    )
    saves_points = (
        (proj.expected_saves / SAVES_PER_POINT) if proj.expected_saves is not None else 0.0
    )

    return ExpectedPointsBreakdown(
        appearance_points=appearance_points,
        goal_points=goal_points,
        assist_points=assist_points,
        clean_sheet_points=clean_sheet_points,
        saves_points=saves_points,
    )
