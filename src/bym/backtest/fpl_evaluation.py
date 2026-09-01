"""Runnable Phase 1B evaluation for the frozen FPL baseline.

This is the orchestration layer deliberately absent from
``fpl_walk_forward``: it turns the completed API-Football harvest plus the
frozen Football-Data match corpus into a reproducible, *causal* player
projection report.  It does not tune a model, mutate the corpus, or produce
an FPL recommendation.

There are two provider identities in play.  The player-stat harvest uses
API-Football club names while the frozen Dixon-Coles core is fitted on
Football-Data names.  Fixtures therefore carry both identities, resolving
the latter explicitly through ``sources.teams`` rather than accidentally
forecasting known clubs as league-average unknowns.

Player-season records are season snapshots, not transfer timelines.  Any
player whose fixture-time club is unknown or differs from that snapshot is
excluded from this first, stable-club evaluation cohort.  The exclusion is
reported; silently assigning a transfer to the wrong club would be worse
than a slightly smaller sample.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pandas as pd

from ..config import FROZEN_PHASE1_V1
from ..fpl import player_store, projection, scoring
from ..fpl.player_match_stats import stats_for_fixture
from ..model import dixon_coles
from ..sources import teams
from ..lms import Gameweek, GameweekFixture, fixture_outlooks
from . import fpl_metrics
from .fpl_walk_forward import WalkForwardFixture, WalkForwardPrediction, actual_points


class EvaluationCorpusError(RuntimeError):
    """The harvested data cannot support an honest Phase 1B evaluation."""


@dataclass(frozen=True)
class EvaluationCorpus:
    fixtures: list[WalkForwardFixture]
    excluded_transfer_players: frozenset[tuple[int, int]]  # (player_id, season)


@dataclass(frozen=True)
class EvaluationResult:
    fixtures_evaluated: int
    predictions: list[WalkForwardPrediction]
    excluded_transfer_players: int
    overall: fpl_metrics.PlayerForecastMetrics
    by_position: dict[str, fpl_metrics.PlayerForecastMetrics]
    minutes: fpl_metrics.MinutesCalibration
    calibration_bands: list[fpl_metrics.CalibrationBand]


def _fixture_rows(conn: sqlite3.Connection, seasons: tuple[int, ...]) -> list[sqlite3.Row]:
    placeholders = ", ".join("?" for _ in seasons)
    return conn.execute(
        "SELECT fixture_id, season, home_team, away_team, kickoff_at "
        "FROM fpl_fixture_harvest_state "
        "WHERE competition = 'E0' AND player_stats_status = 'FETCHED' "
        f"AND season IN ({placeholders}) AND kickoff_at IS NOT NULL "
        "ORDER BY kickoff_at, fixture_id",
        seasons,
    ).fetchall()


def _transferred_or_unknown_players(
    conn: sqlite3.Connection, seasons: tuple[int, ...]
) -> frozenset[tuple[int, int]]:
    """Players unsafe to evaluate with the one-row-per-season roster model.

    A NULL team is a legacy row which predates migration 0013 and therefore
    cannot be reconstructed without a provider re-fetch.  A non-NULL team
    different from the season snapshot is a genuine mid-season transfer.
    Either case is excluded across that season's evaluation cohort.
    """
    placeholders = ", ".join("?" for _ in seasons)
    rows = conn.execute(
        "SELECT DISTINCT s.player_id, s.season "
        "FROM fpl_player_match_stats s "
        "JOIN fpl_player_seasons ps "
        "  ON ps.player_id = s.player_id AND ps.season = s.season "
        f"WHERE s.season IN ({placeholders}) "
        "  AND (s.team IS NULL OR s.team <> ps.team)",
        seasons,
    ).fetchall()
    return frozenset((int(row["player_id"]), int(row["season"])) for row in rows)


def prepare_corpus(
    conn: sqlite3.Connection,
    matches: pd.DataFrame,
    seasons: tuple[int, ...],
) -> EvaluationCorpus:
    """Resolve each completed player-stat fixture to frozen model names.

    Gameweek is deliberately recorded as ``0``: API-Football's approved
    historical fixture response contains kickoff time but not a stable FPL
    Gameweek identifier.  This Phase 1B run evaluates *fixture* projections;
    it must not invent a Gameweek mapping and present it as FPL state.
    """
    rows = _fixture_rows(conn, seasons)
    if not rows:
        raise EvaluationCorpusError("no fetched Premier League player-stat fixtures found")

    known_teams = set(matches["home_team"]) | set(matches["away_team"])
    unresolved: list[str] = []
    fixtures: list[WalkForwardFixture] = []
    for row in rows:
        model_home = teams.resolve(row["home_team"], known_teams)
        model_away = teams.resolve(row["away_team"], known_teams)
        if model_home is None or model_away is None:
            unresolved.append(f"{row['home_team']} v {row['away_team']} ({row['fixture_id']})")
            continue
        fixtures.append(
            WalkForwardFixture(
                fixture_id=row["fixture_id"],
                season=int(row["season"]),
                gameweek=0,
                home=row["home_team"],
                away=row["away_team"],
                kickoff_at=row["kickoff_at"],
                model_home=model_home,
                model_away=model_away,
            )
        )
    if unresolved:
        examples = "; ".join(unresolved[:5])
        more = "" if len(unresolved) <= 5 else f" (+{len(unresolved) - 5} more)"
        raise EvaluationCorpusError(
            "unresolved API-Football → Football-Data team identities: " + examples + more
        )
    return EvaluationCorpus(fixtures, _transferred_or_unknown_players(conn, seasons))


def run(
    conn: sqlite3.Connection,
    matches: pd.DataFrame,
    corpus: EvaluationCorpus,
) -> EvaluationResult:
    """Walk forward through ``corpus`` using only pre-fixture team results.

    Football-Data timestamps have day precision whereas API-Football
    kickoffs include a time.  The train slice consequently excludes the
    *entire* evaluation calendar day, a conservative choice that prevents a
    same-day early kickoff leaking into a later kickoff.
    """
    e0_matches = matches[matches["division"] == "E0"].copy()
    if e0_matches.empty:
        raise EvaluationCorpusError("the frozen match corpus has no Premier League (E0) rows")

    cached_params = None
    last_refit_day: pd.Timestamp | None = None

    def params_for(fixture: WalkForwardFixture):
        nonlocal cached_params, last_refit_day
        cutoff_day = pd.Timestamp(fixture.kickoff_at).tz_localize(None).normalize()
        if (
            cached_params is None
            or last_refit_day is None
            or (cutoff_day - last_refit_day).days >= FROZEN_PHASE1_V1.refit_every_days
        ):
            train = e0_matches[e0_matches["date"] < cutoff_day]
            if len(train) < 200:
                raise EvaluationCorpusError(
                    f"only {len(train)} pre-fixture E0 matches before {cutoff_day.date()}; need 200"
                )
            cached_params = dixon_coles.fit(
                train,
                xi=FROZEN_PHASE1_V1.xi,
                reference_date=cutoff_day,
                season_decay=FROZEN_PHASE1_V1.season_decay,
                reference_season=fixture.season,
                cfg=FROZEN_PHASE1_V1,
            )
            last_refit_day = cutoff_day
        return cached_params

    # The direct `evaluate_fixture` helper deliberately reads profiles from
    # SQL, which is ideal for small/isolated checks. A full three-season
    # evaluation would repeat those same aggregate queries roughly 600,000
    # times. These rolling aggregates produce the identical pre-kickoff
    # HistoricalProfile inputs in O(player-fixtures), and every fixture at
    # one kickoff is predicted before *any* of that kickoff's rows update
    # state.
    players_by_season: dict[int, dict[str, list]] = {}
    for fixture in corpus.fixtures:
        if fixture.season not in players_by_season:
            by_team: dict[str, list] = {}
            for player in player_store.player_seasons_for_season(conn, fixture.season):
                if (player.player_id, fixture.season) not in corpus.excluded_transfer_players:
                    by_team.setdefault(player.team, []).append(player)
            players_by_season[fixture.season] = by_team

    predictions: list[WalkForwardPrediction] = []
    current_season: int | None = None
    team_fixtures: dict[str, int] = {}
    team_goals: dict[str, int] = {}
    player_totals: dict[int, list[int]] = {}

    def profile_for(player, team: str) -> projection.HistoricalProfile:
        appearances, starts, minutes, goals, assists, saves = player_totals.get(
            player.player_id, [0, 0, 0, 0, 0, 0]
        )
        return projection.HistoricalProfile(
            appearances=appearances,
            starts=starts,
            total_minutes=minutes,
            total_goals=goals,
            total_assists=assists,
            total_saves=saves,
            team_fixtures_sampled=team_fixtures.get(team, 0),
            team_goals_sampled=team_goals.get(team, 0),
        )

    index = 0
    while index < len(corpus.fixtures):
        fixture = corpus.fixtures[index]
        if fixture.season != current_season:
            current_season = fixture.season
            team_fixtures, team_goals, player_totals = {}, {}, {}

        # Strict causality is at the kickoff instant, not merely the date:
        # simultaneous fixtures cannot train one another's player profiles.
        same_kickoff: list[WalkForwardFixture] = []
        kickoff = fixture.kickoff_at
        while index < len(corpus.fixtures) and corpus.fixtures[index].kickoff_at == kickoff:
            same_kickoff.append(corpus.fixtures[index])
            index += 1

        stats_by_fixture = {
            f.fixture_id: stats_for_fixture(conn, f.fixture_id) for f in same_kickoff
        }
        for fixture in same_kickoff:
            outlook = fixture_outlooks(
                params_for(fixture),
                Gameweek(
                    fixture.gameweek,
                    (GameweekFixture(fixture.model_home or fixture.home, fixture.model_away or fixture.away),),
                ),
                FROZEN_PHASE1_V1,
            )[0]
            stats = stats_by_fixture[fixture.fixture_id]
            actual_by_player = {row.player_id: row for row in stats}
            conceded = {
                fixture.home: sum(row.goals for row in stats if row.team == fixture.away),
                fixture.away: sum(row.goals for row in stats if row.team == fixture.home),
            }
            for team, is_home in ((fixture.home, True), (fixture.away, False)):
                for player in players_by_season[fixture.season].get(team, []):
                    profile = profile_for(player, team)
                    fixture_projection = projection.project_fixture(
                        profile, outlook, is_home, player.position, player.player_id,
                        fixture.season, fixture.gameweek,
                    )
                    actual_row = actual_by_player.get(player.player_id)
                    actual = (
                        actual_points(actual_row, player.position, conceded[team])
                        if actual_row is not None
                        else scoring.ExpectedPointsBreakdown(0.0, 0.0, 0.0, 0.0, 0.0)
                    )
                    predictions.append(
                        WalkForwardPrediction(
                            player_id=player.player_id,
                            fixture_id=fixture.fixture_id,
                            season=fixture.season,
                            gameweek=fixture.gameweek,
                            position=player.position,
                            projection=fixture_projection,
                            predicted=scoring.fixture_expected_points(fixture_projection, player.position),
                            actual=actual,
                        )
                    )

        # Update only after every fixture at this kickoff has been evaluated.
        for fixture in same_kickoff:
            team_fixtures[fixture.home] = team_fixtures.get(fixture.home, 0) + 1
            team_fixtures[fixture.away] = team_fixtures.get(fixture.away, 0) + 1
            for row in stats_by_fixture[fixture.fixture_id]:
                if row.team is None:
                    continue
                team_goals[row.team] = team_goals.get(row.team, 0) + row.goals
                if (row.player_id, fixture.season) in corpus.excluded_transfer_players:
                    continue
                totals = player_totals.setdefault(row.player_id, [0, 0, 0, 0, 0, 0])
                totals[0] += 1
                totals[1] += int(row.minutes >= 60)
                totals[2] += row.minutes
                totals[3] += row.goals
                totals[4] += row.assists
                totals[5] += row.saves or 0

    return EvaluationResult(
        fixtures_evaluated=len(corpus.fixtures),
        predictions=predictions,
        excluded_transfer_players=len(corpus.excluded_transfer_players),
        overall=fpl_metrics.evaluate(predictions),
        by_position=fpl_metrics.evaluate_by_position(predictions),
        minutes=fpl_metrics.minutes_calibration(predictions),
        calibration_bands=fpl_metrics.calibration_by_predicted_band(predictions),
    )


def render_markdown(result: EvaluationResult) -> str:
    """A concise, provenance-safe Phase 1B report; no pass/fail claim yet."""
    overall = result.overall
    error = overall.error_decomposition
    lines = [
        "# FPL BASELINE_FPL_001 — Phase 1B Fixture Evaluation",
        "",
        "This is a causal, fixture-level diagnostic of the frozen baseline, not a "
        "claim of full official-FPL scoring calibration. Bonus, DefCon and other "
        "omitted components remain excluded by the baseline manifest.",
        "",
        f"- Fixtures evaluated: {result.fixtures_evaluated}",
        f"- Player-fixture predictions: {overall.n}",
        f"- Transfer/unknown-team players excluded: {result.excluded_transfer_players}",
        "- Same-calendar-day match results excluded from every training slice.",
        "",
        "## Overall",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| MAE | {overall.mae:.3f} |",
        f"| Mean predicted points | {overall.mean_predicted:.3f} |",
        f"| Mean actual component points | {overall.mean_actual:.3f} |",
        f"| Calibration gap | {overall.calibration_gap:+.3f} |",
        f"| Minutes-component MAE | {error.minutes_error:.3f} |",
        f"| Goal/assist/save-component MAE | {error.scoring_rate_error:.3f} |",
        f"| Clean-sheet-component MAE | {error.fixture_model_error:.3f} |",
        "",
        "## By position",
        "",
        "| Position | n | MAE | Calibration gap |",
        "|---|---:|---:|---:|",
    ]
    for position in sorted(result.by_position):
        metric = result.by_position[position]
        lines.append(f"| {position} | {metric.n} | {metric.mae:.3f} | {metric.calibration_gap:+.3f} |")
    lines += [
        "",
        "## Minutes calibration",
        "",
        "| Measure | Predicted | Actual | Gap |",
        "|---|---:|---:|---:|",
        f"| Appearance | {result.minutes.mean_predicted_p_appearance:.3f} | "
        f"{result.minutes.appearance_rate:.3f} | {result.minutes.appearance_calibration_gap:+.3f} |",
        f"| 60+ minutes | {result.minutes.mean_predicted_p_60_plus:.3f} | "
        f"{result.minutes.rate_60_plus:.3f} | {result.minutes.calibration_gap_60_plus:+.3f} |",
        "",
    ]
    return "\n".join(lines)
