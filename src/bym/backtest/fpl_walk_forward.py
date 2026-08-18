"""Walk-forward player-projection evaluation (SPEC_FPL.md §12.1-§12.2,
§18 Phase 1B).

Two causal boundaries must BOTH hold for an FPL walk-forward evaluation
to be honest, not just one:

- the TEAM-level match environment (`FixtureOutlook`'s lambda/clean-sheet
  figures) must come from a `DixonColesParams` fit only on matches
  strictly before the fixture being evaluated — the same §16 discipline
  `backtest.walk_forward` already enforces for Super 6/LMS. This module
  does not refit that model itself; callers pass in a `params` already
  fit on a causally-scoped slice (`dixon_coles.fit`), the same contract
  `lms.fixture_outlooks` already has.
- the PLAYER-level historical profile (`fpl.projection.historical_profile`)
  must be filtered to match-stat rows with `kickoff_at` strictly before
  the same instant — `fpl.projection`'s `before` parameter, itself
  defended in depth by an explicit `fpl.causality.assert_causal` check on
  every row, not merely a SQL `WHERE` clause trusted to be correct.

Missing either boundary produces an evaluation that LOOKS leak-free (the
other one holds) while silently cheating on the one that doesn't —
exactly the failure mode LMS's `resolve_round_teams` and Super 6's
deadline-causality tests exist to catch as an explicit, named check
rather than something "the code looks right" is trusted to guarantee.

This module only produces (predicted, actual) pairs, causally. Turning
those into calibration/MAE/error-decomposition metrics is a separate,
later step (§18's metrics table) — kept apart so either side can change
without the other, the same separation `fpl.projection`/`fpl.scoring`
already use.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..fpl import player_store, projection, scoring
from ..fpl.player_match_stats import PlayerMatchStats, stats_for_fixture
from ..fpl.players import Position
from ..fpl.projection import FixtureProjection
from ..fpl.scoring import ExpectedPointsBreakdown
from ..lms import Gameweek, GameweekFixture, fixture_outlooks
from ..model.dixon_coles import DixonColesParams

ZERO_BREAKDOWN = ExpectedPointsBreakdown(0.0, 0.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class WalkForwardFixture:
    """One historical fixture to walk forward through — enough to build a
    `FixtureOutlook` and to know exactly when it kicked off, the causal
    cutoff for both boundaries described in the module docstring."""

    fixture_id: str
    season: int
    gameweek: int
    home: str
    away: str
    kickoff_at: str


@dataclass(frozen=True)
class WalkForwardPrediction:
    """One player's predicted-vs-actual comparison for one fixture — the
    raw material Phase 1B's (separate) metrics module aggregates.

    `projection` carries the raw components (p_appearance, p_60_plus,
    p_clean_sheet, ...), not just `predicted`'s points-converted totals —
    minutes/appearance CALIBRATION needs the actual predicted probability
    to compare against the realised binary outcome, which the points
    figure alone cannot reconstruct (§18's "minutes: start/appearance/
    60+ calibration" metric)."""

    player_id: int
    fixture_id: str
    season: int
    gameweek: int
    position: str
    projection: FixtureProjection
    predicted: ExpectedPointsBreakdown
    actual: ExpectedPointsBreakdown


def actual_points(
    stats: PlayerMatchStats, position: str, team_conceded: int | None
) -> ExpectedPointsBreakdown:
    """Realised FPL points from what actually happened — the ground-truth
    counterpart to `fpl.scoring.fixture_expected_points`'s estimate, using
    the identical point-value constants (§2) so predicted and actual are
    comparable component by component, not just as a single total.

    `team_conceded=None` means the opponent's goals could not be
    determined (their stats were never ingested for this fixture) — clean
    sheet points are left at 0 rather than guessed, the same "omit rather
    than fabricate" discipline the projection side already follows for
    components it cannot support.
    """
    if position not in scoring.GOAL_POINTS:
        raise ValueError(f"unknown position {position!r}")

    if stats.minutes >= 60:
        appearance_points = scoring.APPEARANCE_POINTS_60_PLUS
    elif stats.minutes > 0:
        appearance_points = scoring.APPEARANCE_POINTS_UNDER_60
    else:
        appearance_points = 0.0

    clean_sheet_points = 0.0
    if stats.minutes >= 60 and team_conceded == 0:
        clean_sheet_points = scoring.CLEAN_SHEET_POINTS[position]

    saves_points = (
        (stats.saves or 0) / scoring.SAVES_PER_POINT
        if position == Position.GK.value
        else 0.0
    )

    return ExpectedPointsBreakdown(
        appearance_points=appearance_points,
        goal_points=stats.goals * scoring.GOAL_POINTS[position],
        assist_points=stats.assists * scoring.ASSIST_POINTS,
        clean_sheet_points=clean_sheet_points,
        saves_points=saves_points,
    )


def _team_goals_conceded(
    conn: sqlite3.Connection, fixture_id: str, team: str, season: int
) -> int | None:
    """Sum of goals scored by the OPPOSING team's players in this fixture,
    derived from already-ingested match-stat rows — there is no separate
    team-goals table, the same derivation `fpl.projection` already uses
    for goal/assist share. `None` if no opposing-team row was found at all
    (their stats were never ingested for this fixture), distinguished
    from a genuine 0 (ingested, and confirmed nobody scored)."""
    conceded = 0
    saw_opponent_row = False
    for row in stats_for_fixture(conn, fixture_id):
        player_season = player_store.get_player_season(conn, row.player_id, season)
        if player_season is None or player_season.team == team:
            continue
        saw_opponent_row = True
        conceded += row.goals
    return conceded if saw_opponent_row else None


def evaluate_fixture(
    conn: sqlite3.Connection,
    fixture: WalkForwardFixture,
    params: DixonColesParams,
) -> list[WalkForwardPrediction]:
    """Predict every rostered player for one historical fixture using only
    data strictly before its kickoff, then compare each to what actually
    happened. `params` must already be fit causally for this instant —
    see the module docstring; this function does not check that (it has
    no historical match corpus to check it against), only the player-side
    cutoff.
    """
    outlook = fixture_outlooks(
        params,
        Gameweek(fixture.gameweek, (GameweekFixture(fixture.home, fixture.away),)),
    )[0]

    actual_by_player = {
        row.player_id: row for row in stats_for_fixture(conn, fixture.fixture_id)
    }

    predictions: list[WalkForwardPrediction] = []
    for team, is_home in ((fixture.home, True), (fixture.away, False)):
        team_conceded = _team_goals_conceded(conn, fixture.fixture_id, team, fixture.season)
        for player_season in player_store.player_seasons_for_season(conn, fixture.season):
            if player_season.team != team:
                continue
            profile = projection.historical_profile(
                conn, player_season.player_id, fixture.season, team,
                before=fixture.kickoff_at,
            )
            proj = projection.project_fixture(
                profile, outlook, is_home, player_season.position,
                player_season.player_id, fixture.season, fixture.gameweek,
            )
            predicted = scoring.fixture_expected_points(proj, player_season.position)

            stats_row = actual_by_player.get(player_season.player_id)
            actual = (
                actual_points(stats_row, player_season.position, team_conceded)
                if stats_row is not None
                else ZERO_BREAKDOWN
            )

            predictions.append(
                WalkForwardPrediction(
                    player_id=player_season.player_id,
                    fixture_id=fixture.fixture_id,
                    season=fixture.season,
                    gameweek=fixture.gameweek,
                    position=player_season.position,
                    projection=proj,
                    predicted=predicted,
                    actual=actual,
                )
            )
    return predictions


def run_walk_forward(
    conn: sqlite3.Connection,
    fixtures: list[WalkForwardFixture],
    params_for: "callable[[WalkForwardFixture], DixonColesParams]",
) -> list[WalkForwardPrediction]:
    """Walk forward through `fixtures` IN THE ORDER GIVEN, calling
    `params_for(fixture)` to get a causally-fit team environment for each
    one. Callers are responsible for both the fixture order and for
    `params_for` actually being causal (e.g.
    `lambda f: dixon_coles.fit(matches_before(f.kickoff_at), xi=FROZEN_PHASE1_V1.xi)`)
    — this function only enforces the player-side cutoff itself."""
    predictions: list[WalkForwardPrediction] = []
    for fixture in fixtures:
        predictions.extend(evaluate_fixture(conn, fixture, params_for(fixture)))
    return predictions
