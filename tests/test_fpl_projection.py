import pytest

from bym import db
from bym.fpl import player_store, projection
from bym.fpl.player_match_stats import PlayerMatchStats, record_match_stats
from bym.fpl.players import FplPlayerSeason, Position
from bym.lms import FixtureOutlook, GameweekFixture

SEASON = 2022
GW = 1


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "bym.db") as c:
        yield c


def _make_player(conn, name, team, position, **season_kwargs):
    player, _ = player_store.get_or_create_player(conn, name)
    player_store.record_player_season(
        conn,
        FplPlayerSeason(player_id=player.id, season=SEASON, team=team, position=position, **season_kwargs),
    )
    return player


def _stat(player_id, fixture_id, minutes, goals=0, assists=0, saves=None, kickoff_at=None):
    return PlayerMatchStats(
        player_id=player_id, fixture_id=fixture_id, season=SEASON,
        source_id="api_football", source_record_version=f"fixture:{fixture_id}",
        observed_at="2026-08-17T00:00:00+00:00", kickoff_at=kickoff_at,
        minutes=minutes, goals=goals, assists=assists, saves=saves,
    )


@pytest.fixture
def chelsea_squad(conn):
    """Two Chelsea fixtures sampled (fx1, fx2): Palmer appears in both,
    Sterling only in fx1 — team_fixtures_sampled=2, team_goals_sampled=2."""
    palmer = _make_player(conn, "Cole Palmer", "Chelsea", Position.MID.value)
    sterling = _make_player(conn, "Raheem Sterling", "Chelsea", Position.MID.value)
    keeper = _make_player(conn, "Robert Sanchez", "Chelsea", Position.GK.value)

    record_match_stats(conn, _stat(palmer.id, "fx1", minutes=90, goals=1, assists=1))
    record_match_stats(conn, _stat(sterling.id, "fx1", minutes=90, goals=1, assists=0))
    record_match_stats(conn, _stat(keeper.id, "fx1", minutes=90, saves=3))
    record_match_stats(conn, _stat(palmer.id, "fx2", minutes=60, goals=0, assists=1))
    record_match_stats(conn, _stat(keeper.id, "fx2", minutes=90, saves=3))
    return {"palmer": palmer, "sterling": sterling, "keeper": keeper}


OUTLOOK = FixtureOutlook(
    GameweekFixture("Chelsea", "Everton"),
    p_home_win=0.55, p_draw=0.25, p_away_win=0.20,
    lambda_home=1.8, lambda_away=1.1,
    p_home_clean_sheet=0.35, p_away_clean_sheet=0.25,
)


def test_historical_profile_before_cutoff_excludes_later_fixtures(conn):
    """The walk-forward causal-cutoff path (§12.1): a fixture played AFTER
    the evaluation point must not leak into that evaluation's profile,
    even though it is the same player/team/season."""
    palmer = _make_player(conn, "Cole Palmer", "Chelsea", Position.MID.value)
    sterling = _make_player(conn, "Raheem Sterling", "Chelsea", Position.MID.value)
    record_match_stats(
        conn, _stat(palmer.id, "fx1", minutes=90, goals=1, assists=1,
                    kickoff_at="2022-08-06T14:00:00+00:00")
    )
    record_match_stats(
        conn, _stat(sterling.id, "fx1", minutes=90, goals=1,
                    kickoff_at="2022-08-06T14:00:00+00:00")
    )
    # A LATER fixture (fx2) where Palmer scores heavily — must not count
    # toward a profile evaluated as of a cutoff between fx1 and fx2.
    record_match_stats(
        conn, _stat(palmer.id, "fx2", minutes=90, goals=5, assists=5,
                    kickoff_at="2022-08-13T14:00:00+00:00")
    )

    cutoff = "2022-08-10T00:00:00+00:00"  # strictly between fx1 and fx2
    profile = projection.historical_profile(
        conn, palmer.id, SEASON, "Chelsea", before=cutoff
    )
    assert profile.appearances == 1  # only fx1
    assert profile.total_goals == 1  # not fx2's 5
    assert profile.team_fixtures_sampled == 1


def test_historical_profile_computes_unconditional_rates(conn, chelsea_squad):
    profile = projection.historical_profile(conn, chelsea_squad["palmer"].id, SEASON, "Chelsea")
    assert profile.team_fixtures_sampled == 2
    assert profile.team_goals_sampled == 2  # Palmer 1 + Sterling 1 + Palmer(fx2) 0
    assert profile.appearances == 2
    assert profile.p_appearance == 1.0
    assert profile.expected_minutes == 75.0  # (90 + 60) / 2
    assert profile.goal_share == 0.5  # Palmer's 1 goal / team's 2
    assert profile.assist_share == 1.0  # Palmer's 2 assists / team's 2 goals


def test_historical_profile_with_no_data_is_all_zero(conn):
    player, _ = player_store.get_or_create_player(conn, "Unseen Player")
    profile = projection.historical_profile(conn, player.id, SEASON, "Chelsea")
    assert profile.p_appearance == 0.0
    assert profile.expected_minutes == 0.0
    assert profile.goal_share == 0.0


def test_project_fixture_scales_by_team_lambda_for_the_players_side(conn, chelsea_squad):
    profile = projection.historical_profile(conn, chelsea_squad["palmer"].id, SEASON, "Chelsea")
    proj = projection.project_fixture(
        profile, OUTLOOK, is_home=True, position=Position.MID.value,
        player_id=chelsea_squad["palmer"].id, season=SEASON, gameweek=GW,
    )
    assert proj.goal_lambda == pytest.approx(1.8 * 0.5)
    assert proj.assist_lambda == pytest.approx(1.8 * 1.0)
    assert proj.p_clean_sheet == 0.35  # home side of the outlook
    assert proj.expected_saves is None  # outfield player
    assert proj.fixture_id == "2022:1:Chelsea:Everton"


def test_project_fixture_uses_away_lambda_when_not_home(conn, chelsea_squad):
    profile = projection.historical_profile(conn, chelsea_squad["palmer"].id, SEASON, "Chelsea")
    proj = projection.project_fixture(
        profile, OUTLOOK, is_home=False, position=Position.MID.value,
        player_id=chelsea_squad["palmer"].id, season=SEASON, gameweek=GW,
    )
    assert proj.goal_lambda == pytest.approx(1.1 * 0.5)
    assert proj.p_clean_sheet == 0.25  # away side of the outlook


def test_project_fixture_computes_expected_saves_only_for_goalkeepers(conn, chelsea_squad):
    profile = projection.historical_profile(conn, chelsea_squad["keeper"].id, SEASON, "Chelsea")
    proj = projection.project_fixture(
        profile, OUTLOOK, is_home=True, position=Position.GK.value,
        player_id=chelsea_squad["keeper"].id, season=SEASON, gameweek=GW,
    )
    assert proj.expected_saves == pytest.approx(3.0)  # (3+3) saves / 2 team fixtures


def test_project_fixture_rejects_an_outlook_without_lambda_fields(conn, chelsea_squad):
    """A hand-built FixtureOutlook (e.g. from an LMS test) has no
    lambda/clean-sheet fields — refuse rather than silently project zero."""
    bare = FixtureOutlook(GameweekFixture("Chelsea", "Everton"), 0.5, 0.25, 0.25)
    profile = projection.historical_profile(conn, chelsea_squad["palmer"].id, SEASON, "Chelsea")
    with pytest.raises(ValueError, match="fixture_outlooks"):
        projection.project_fixture(
            profile, bare, is_home=True, position=Position.MID.value,
            player_id=chelsea_squad["palmer"].id, season=SEASON, gameweek=GW,
        )


def test_project_gameweek_projects_every_rostered_player_on_both_sides(conn, chelsea_squad):
    _make_player(conn, "Jordan Pickford", "Everton", Position.GK.value)
    _make_player(conn, "Not Playing This Week", "Arsenal", Position.MID.value)

    projections = projection.project_gameweek(conn, SEASON, GW, [OUTLOOK])
    player_ids = {p.player_id for p in projections}

    assert chelsea_squad["palmer"].id in player_ids
    assert chelsea_squad["sterling"].id in player_ids
    assert chelsea_squad["keeper"].id in player_ids
    pickford = player_store.find_player_by_name(conn, "Jordan Pickford")
    assert pickford.id in player_ids
    # Arsenal has no fixture in this outlook list — must not appear.
    arsenal_player = player_store.find_player_by_name(conn, "Not Playing This Week")
    assert arsenal_player.id not in player_ids
    assert all(p.fixture_id == "2022:1:Chelsea:Everton" for p in projections)


def test_fixture_id_is_deterministic_from_season_gameweek_and_teams():
    assert projection.fixture_id_for(2022, 1, "Chelsea", "Everton") == "2022:1:Chelsea:Everton"
