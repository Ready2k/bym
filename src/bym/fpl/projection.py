"""Per-fixture player point-component projection (SPEC_FPL.md §6, Phase 1A).

Deliberately the simplest model that could work: minutes/appearance rate,
the team scoring environment from `FROZEN_PHASE1_V1` (via `FixtureOutlook`,
computed once per gameweek and never refit per player — CLAUDE.md's
existing discipline, restated for FPL in §6), historical goal/assist
share, clean-sheet probability, and basic GK saves where the ingested
data supports it. xG/xA, a bonus model, DefCon and further refinements
are deliberately NOT here — each is a candidate to audition later, kept
only if it beats this baseline, the same lesson the Super 6 ablation
ladder already taught this codebase.

This module produces point *components* only (goal_lambda, assist_lambda,
p_clean_sheet, expected_minutes, ...), not FPL points — turning a
component set into points is `fpl.scoring`'s job, kept separate so either
side can change without touching the other.

Every rate below is deliberately UNCONDITIONAL (already averaged across
the player's team's sampled fixtures, not just the fixtures the player
appeared in) rather than "per appearance" — a player who misses fixtures
contributes a real zero to that average, which is what a downstream sum
across a Gameweek needs, without a separate p_appearance multiplication
that would double-count absence.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..db import from_iso
from ..lms import FixtureOutlook
from . import causality, player_match_stats
from .player_store import player_seasons_for_season
from .players import Position

# The name is authoritative: this is the exact scoring/projection/
# decision combination frozen 2026-08-17 as BASELINE_FPL_001 (§18,
# docs/MANIFEST_FPL.md) — every FixtureProjection/GameweekProjection this
# module produces is stamped with it. Changing what this module computes
# without also bumping this string, and without a new §18-style decision
# log entry, is exactly the drift MANIFEST.md's "DO NOT EDIT" convention
# exists to prevent for the Super 6 core.
MODEL_VERSION = "BASELINE_FPL_001"


def fixture_id_for(season: int, gameweek: int, home: str, away: str) -> str:
    """A deterministic fixture identity from team names, matching this
    codebase's existing convention of treating club names as identity
    (round.py, availability, LMS `used_clubs`) rather than depending on
    any one provider's numeric fixture id."""
    return f"{season}:{gameweek}:{home}:{away}"


@dataclass(frozen=True)
class HistoricalProfile:
    """One player's empirical rates from their ingested match-stat sample
    for a season (§6.1's "historical starts/minutes" input), computed
    once per player rather than re-derived per fixture."""

    appearances: int
    starts: int  # minutes >= 60 — this baseline's start proxy; see p_start
    total_minutes: int
    total_goals: int
    total_assists: int
    total_saves: int
    team_fixtures_sampled: int
    team_goals_sampled: int

    @property
    def p_appearance(self) -> float:
        if self.team_fixtures_sampled == 0:
            return 0.0
        return min(1.0, self.appearances / self.team_fixtures_sampled)

    @property
    def p_60_plus(self) -> float:
        if self.team_fixtures_sampled == 0:
            return 0.0
        return min(1.0, self.starts / self.team_fixtures_sampled)

    @property
    def p_start(self) -> float:
        """No confirmed/expected lineup signal feeds this ingester yet
        (§6.1's minutes model is deliberately reduced for the baseline),
        so "started" is approximated by "played 60+ minutes" — the same
        proxy `p_60_plus` uses. A real starts signal is a later audition,
        not this baseline."""
        return self.p_60_plus

    @property
    def expected_minutes(self) -> float:
        if self.appearances == 0:
            return 0.0
        return self.total_minutes / self.appearances

    @property
    def goal_share(self) -> float:
        """Player goals as a share of the TEAM's sampled goals — derived
        by summing every player's goals for the team, since no separate
        team-goals table exists (§4 has no such entity; deriving it from
        already-ingested player rows avoids inventing one). Undercounts
        team goals scored via an opponent's own goal, since `own_goals`
        is not populated by this ingester (api_football's free-tier
        `/fixtures/players` does not expose it) — a known, small,
        documented gap rather than a silent one."""
        if self.team_goals_sampled == 0:
            return 0.0
        return self.total_goals / self.team_goals_sampled

    @property
    def assist_share(self) -> float:
        if self.team_goals_sampled == 0:
            return 0.0
        return self.total_assists / self.team_goals_sampled

    @property
    def saves_per_team_fixture(self) -> float:
        """Same unconditional convention as goal_share/assist_share: this
        goalkeeper's saves divided by the TEAM's sampled fixture count,
        not just this keeper's own appearances — a squad's second-choice
        keeper should project near zero, not their understudy rate."""
        if self.team_fixtures_sampled == 0:
            return 0.0
        return self.total_saves / self.team_fixtures_sampled


def historical_profile(
    conn: sqlite3.Connection, player_id: int, season: int, team: str,
    before: str | None = None,
) -> HistoricalProfile:
    """`before` (ISO8601, strict) restricts the sample to fixtures played
    strictly before that instant (`fpl.causality.is_causal`'s convention)
    — the walk-forward evaluator (`backtest.fpl_walk_forward`) always
    passes the Gameweek being evaluated's deadline here so a prediction
    can never see its own fixture, let alone a later one. `None` (the
    ordinary live/current-Gameweek path) uses every sample gathered so
    far, since there is no future fixture in that corpus to leak from."""
    player_rows = player_match_stats.stats_for_player_season(
        conn, player_id, season, before=before
    )
    team_rows = player_match_stats.stats_for_team_season(conn, team, season, before=before)

    if before is not None:
        # Defence in depth (§12.1, Phase 1B): the SQL `before` filter
        # above should already exclude every non-causal row, but a
        # walk-forward evaluation is exactly the place a silent regression
        # there (a timezone-format mismatch, a dropped WHERE clause) would
        # do the most damage without ever raising — so every row is
        # re-checked explicitly rather than trusted.
        cutoff = from_iso(before)
        for row in player_rows + team_rows:
            if row.kickoff_at is None:
                raise causality.LookaheadViolation(
                    f"match_stats row for fixture={row.fixture_id} has no "
                    "kickoff_at — cannot prove it is causal for a "
                    "before-cutoff query; ingest it with kickoff_at set."
                )
            causality.assert_causal(
                from_iso(row.kickoff_at), cutoff,
                context=f"historical_profile fixture={row.fixture_id}",
            )

    team_fixture_ids = {r.fixture_id for r in team_rows}
    return HistoricalProfile(
        appearances=len(player_rows),
        starts=sum(1 for r in player_rows if r.minutes >= 60),
        total_minutes=sum(r.minutes for r in player_rows),
        total_goals=sum(r.goals for r in player_rows),
        total_assists=sum(r.assists for r in player_rows),
        total_saves=sum(r.saves or 0 for r in player_rows),
        team_fixtures_sampled=len(team_fixture_ids),
        team_goals_sampled=sum(r.goals for r in team_rows),
    )


@dataclass(frozen=True)
class FixtureProjection:
    """§4 `fpl_fixture_projection`, components only (see module docstring
    for why expected_points/points_variance are not computed here)."""

    player_id: int
    fixture_id: str
    season: int
    gameweek: int
    p_appearance: float
    p_start: float
    p_60_plus: float
    expected_minutes: float
    goal_lambda: float
    assist_lambda: float
    p_clean_sheet: float
    expected_saves: float | None  # None for outfield players
    model_version: str = MODEL_VERSION


def project_fixture(
    profile: HistoricalProfile,
    outlook: FixtureOutlook,
    is_home: bool,
    position: str,
    player_id: int,
    season: int,
    gameweek: int,
) -> FixtureProjection:
    """One player's component projection for one fixture, from an
    already-computed `FixtureOutlook` — never refit here (§6, §19,
    CLAUDE.md's once-per-gameweek discipline)."""
    team_lambda = outlook.lambda_home if is_home else outlook.lambda_away
    p_clean_sheet = outlook.p_home_clean_sheet if is_home else outlook.p_away_clean_sheet
    if team_lambda is None or p_clean_sheet is None:
        raise ValueError(
            "FixtureOutlook is missing lambda/clean-sheet fields — build "
            "it with fixture_outlooks(), not a hand-constructed "
            "FixtureOutlook, to get them (SPEC_FPL.md §6)."
        )

    return FixtureProjection(
        player_id=player_id,
        fixture_id=fixture_id_for(season, gameweek, outlook.fixture.home, outlook.fixture.away),
        season=season,
        gameweek=gameweek,
        p_appearance=profile.p_appearance,
        p_start=profile.p_start,
        p_60_plus=profile.p_60_plus,
        expected_minutes=profile.expected_minutes,
        goal_lambda=team_lambda * profile.goal_share,
        assist_lambda=team_lambda * profile.assist_share,
        p_clean_sheet=p_clean_sheet,
        expected_saves=(
            profile.saves_per_team_fixture if position == Position.GK.value else None
        ),
    )


def project_gameweek(
    conn: sqlite3.Connection,
    season: int,
    gameweek: int,
    outlooks: list[FixtureOutlook],
    before: str | None = None,
) -> list[FixtureProjection]:
    """Project every rostered player across one Gameweek's fixtures,
    reusing the already-computed `outlooks` for every player rather than
    recomputing a score matrix per player (§6, §19; the FPL restatement
    of `test_the_model_runs_once_per_gameweek_not_once_per_entrant`).
    `before` — see `historical_profile`; the walk-forward evaluator always
    supplies it, the live/current-Gameweek path leaves it `None`."""
    projections: list[FixtureProjection] = []
    for outlook in outlooks:
        fixture = outlook.fixture
        for team, is_home in ((fixture.home, True), (fixture.away, False)):
            for player_season in player_seasons_for_season(conn, season):
                if player_season.team != team:
                    continue
                profile = historical_profile(
                    conn, player_season.player_id, season, team, before=before
                )
                projections.append(
                    project_fixture(
                        profile, outlook, is_home, player_season.position,
                        player_season.player_id, season, gameweek,
                    )
                )
    return projections
