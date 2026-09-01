import pytest

from bym import db
from bym.backtest.fpl_walk_forward import (
    WalkForwardFixture,
    _team_goals_conceded,
    actual_points,
    evaluate_fixture,
    run_walk_forward,
)
from bym.fpl import player_store, projection
from bym.fpl.player_match_stats import PlayerMatchStats, record_match_stats
from bym.fpl.players import FplPlayerSeason, Position
from bym.model import dixon_coles

from test_dixon_coles import synthetic_matches

SEASON = 2024
GW = 5
HOME, AWAY = "Strong", "Weak"


@pytest.fixture(scope="module")
def params():
    return dixon_coles.fit(synthetic_matches(), xi=0.0)


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "bym.db") as c:
        yield c


def _make_player(conn, name, team, position):
    player, _ = player_store.get_or_create_player(conn, name)
    player_store.record_player_season(
        conn, FplPlayerSeason(player_id=player.id, season=SEASON, team=team, position=position)
    )
    return player


def _stat(player_id, fixture_id, minutes, goals=0, assists=0, saves=None, kickoff_at=None):
    return PlayerMatchStats(
        player_id=player_id, fixture_id=fixture_id, season=SEASON,
        source_id="api_football", source_record_version=f"fixture:{fixture_id}",
        observed_at="2026-08-17T00:00:00+00:00", kickoff_at=kickoff_at,
        minutes=minutes, goals=goals, assists=assists, saves=saves,
    )


EARLY = "2024-08-06T14:00:00+00:00"
LATE = "2024-08-13T14:00:00+00:00"


@pytest.fixture
def rosters(conn):
    home_mid = _make_player(conn, "Home Mid", HOME, Position.MID.value)
    home_gk = _make_player(conn, "Home GK", HOME, Position.GK.value)
    away_def = _make_player(conn, "Away Def", AWAY, Position.DEF.value)
    # An earlier fixture for the SAME two teams, so a walk-forward profile
    # evaluated at LATE has genuine prior history to draw on.
    record_match_stats(conn, _stat(home_mid.id, "fx_early", 90, goals=1, kickoff_at=EARLY))
    record_match_stats(conn, _stat(home_gk.id, "fx_early", 90, saves=2, kickoff_at=EARLY))
    record_match_stats(conn, _stat(away_def.id, "fx_early", 90, kickoff_at=EARLY))
    return {"home_mid": home_mid, "home_gk": home_gk, "away_def": away_def}


@pytest.fixture
def fx_late():
    return WalkForwardFixture(
        fixture_id="fx_late", season=SEASON, gameweek=GW, home=HOME, away=AWAY,
        kickoff_at=LATE,
    )


def test_evaluate_fixture_predicts_every_rostered_player_on_both_teams(conn, rosters, fx_late, params):
    predictions = evaluate_fixture(conn, fx_late, params)
    player_ids = {p.player_id for p in predictions}
    assert rosters["home_mid"].id in player_ids
    assert rosters["home_gk"].id in player_ids
    assert rosters["away_def"].id in player_ids


def test_evaluate_fixture_excludes_the_fixtures_own_stats_from_its_profile(conn, rosters, fx_late, params):
    """fx_late is the fixture BEING predicted — recording a huge score for
    it must not leak into its own prediction (§12.1, §19)."""
    baseline = evaluate_fixture(conn, fx_late, params)
    baseline_pred = next(p for p in baseline if p.player_id == rosters["home_mid"].id).predicted

    record_match_stats(
        conn, _stat(rosters["home_mid"].id, "fx_late", 90, goals=5, assists=5, kickoff_at=LATE)
    )
    after = evaluate_fixture(conn, fx_late, params)
    after_pred = next(p for p in after if p.player_id == rosters["home_mid"].id).predicted

    assert after_pred.goal_points == pytest.approx(baseline_pred.goal_points)
    assert after_pred.assist_points == pytest.approx(baseline_pred.assist_points)


def test_evaluate_fixture_reports_the_actual_outcome_once_ingested(conn, rosters, fx_late, params):
    record_match_stats(
        conn, _stat(rosters["home_mid"].id, "fx_late", 90, goals=2, assists=1, kickoff_at=LATE)
    )
    predictions = evaluate_fixture(conn, fx_late, params)
    pred = next(p for p in predictions if p.player_id == rosters["home_mid"].id)
    assert pred.actual.goal_points == 2 * 5  # MID goal value
    assert pred.actual.assist_points == 1 * 3


def test_evaluate_fixture_reports_a_zero_actual_for_a_non_appearance(conn, rosters, fx_late, params):
    predictions = evaluate_fixture(conn, fx_late, params)
    pred = next(p for p in predictions if p.player_id == rosters["home_mid"].id)
    assert pred.actual.appearance_points == 0.0
    assert pred.actual.goal_points == 0.0


def test_actual_points_awards_clean_sheet_at_60_plus_minutes_and_no_goals_conceded():
    stats = _stat(1, "fx", minutes=90, goals=0, assists=0, saves=None)
    breakdown = actual_points(stats, Position.DEF.value, team_conceded=0)
    assert breakdown.clean_sheet_points == 4  # DEF clean sheet value


def test_actual_points_withholds_clean_sheet_under_60_minutes():
    stats = _stat(1, "fx", minutes=45)
    breakdown = actual_points(stats, Position.DEF.value, team_conceded=0)
    assert breakdown.clean_sheet_points == 0.0


def test_actual_points_omits_clean_sheet_when_conceded_is_unknown():
    """team_conceded=None means the opponent's stats were never ingested —
    clean sheet points must be withheld, not guessed as 0 (§4's "omit
    rather than fabricate" discipline)."""
    stats = _stat(1, "fx", minutes=90)
    breakdown = actual_points(stats, Position.DEF.value, team_conceded=None)
    assert breakdown.clean_sheet_points == 0.0


def test_actual_points_computes_gk_saves():
    stats = _stat(1, "fx", minutes=90, saves=6)
    breakdown = actual_points(stats, Position.GK.value, team_conceded=1)
    assert breakdown.saves_points == pytest.approx(2.0)  # 6 saves / 3


def test_actual_points_rejects_an_unknown_position():
    with pytest.raises(ValueError, match="unknown position"):
        actual_points(_stat(1, "fx", 90), "STRIKER", team_conceded=0)


def test_team_goals_conceded_is_none_when_the_opponent_was_never_ingested(conn, rosters):
    # Only the HOME side has a row for this fixture — the away side's
    # stats were never ingested, so "0 conceded" cannot be claimed.
    record_match_stats(
        conn, _stat(rosters["home_mid"].id, "fx_no_opponent", 90, kickoff_at=EARLY)
    )
    assert _team_goals_conceded(conn, "fx_no_opponent", HOME, SEASON) is None


def test_team_goals_conceded_sums_the_opposing_teams_goals(conn):
    home = _make_player(conn, "H", "TeamA", Position.MID.value)
    away1 = _make_player(conn, "A1", "TeamB", Position.MID.value)
    away2 = _make_player(conn, "A2", "TeamB", Position.FWD.value)
    record_match_stats(conn, _stat(home.id, "fx", 90, goals=0, kickoff_at=EARLY))
    record_match_stats(conn, _stat(away1.id, "fx", 90, goals=1, kickoff_at=EARLY))
    record_match_stats(conn, _stat(away2.id, "fx", 90, goals=2, kickoff_at=EARLY))
    assert _team_goals_conceded(conn, "fx", "TeamA", SEASON) == 3
    assert _team_goals_conceded(conn, "fx", "TeamB", SEASON) == 0


def test_run_walk_forward_processes_every_fixture_in_the_given_order(conn, rosters, params):
    fixtures = [
        WalkForwardFixture("fx_a", SEASON, 1, HOME, AWAY, EARLY),
        WalkForwardFixture("fx_b", SEASON, 2, HOME, AWAY, LATE),
    ]
    seen_order = []

    def params_for(fixture):
        seen_order.append(fixture.fixture_id)
        return params

    predictions = run_walk_forward(conn, fixtures, params_for)
    assert seen_order == ["fx_a", "fx_b"]
    assert {p.fixture_id for p in predictions} == {"fx_a", "fx_b"}


def test_evaluate_fixture_uses_resolved_model_team_names_not_provider_names(conn, rosters, params):
    """API-Football's names need not be the names Dixon-Coles was fitted on.
    The evaluator must consume the explicit mapping rather than silently
    treating a known provider-side name as an unseen league-average club."""
    renamed_matches = synthetic_matches().replace(
        {"home_team": {HOME: "Model Strong", AWAY: "Model Weak"},
         "away_team": {HOME: "Model Strong", AWAY: "Model Weak"}}
    )
    model_params = dixon_coles.fit(renamed_matches, xi=0.0)
    fixture = WalkForwardFixture(
        fixture_id="fx_mapped", season=SEASON, gameweek=GW,
        home=HOME, away=AWAY, kickoff_at=LATE,
        model_home="Model Strong", model_away="Model Weak",
    )
    predictions = evaluate_fixture(conn, fixture, model_params)
    home_mid = next(p for p in predictions if p.player_id == rosters["home_mid"].id)
    profile = projection.historical_profile(
        conn, rosters["home_mid"].id, SEASON, HOME, before=LATE
    )
    expected_lambda, _ = model_params.lambdas("Model Strong", "Model Weak")
    assert home_mid.projection.goal_lambda == pytest.approx(expected_lambda * profile.goal_share)
