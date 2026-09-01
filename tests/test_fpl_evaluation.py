import pandas as pd
import pytest

from bym import db
from bym.backtest import fpl_evaluation
from bym.fpl import player_store
from bym.fpl.player_match_stats import PlayerMatchStats, record_match_stats
from bym.fpl.players import FplPlayerSeason, Position

SEASON = 2022


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "bym.db") as c:
        yield c


def _matches():
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2022-08-01"]),
            "season": [SEASON],
            "division": ["E0"],
            "home_team": ["Man City"],
            "away_team": ["Chelsea"],
            "home_goals": [2],
            "away_goals": [0],
        }
    )


def _seed_fixture(conn):
    conn.execute(
        "INSERT INTO fpl_fixture_harvest_state "
        "(fixture_id, season, competition, home_team, away_team, kickoff_at, player_stats_status) "
        "VALUES ('fx', ?, 'E0', 'Manchester City', 'Chelsea', "
        "'2022-08-07T14:00:00+00:00', 'FETCHED')",
        (SEASON,),
    )


def _player(conn, name, team):
    player, _ = player_store.get_or_create_player(conn, name)
    player_store.record_player_season(
        conn, FplPlayerSeason(player.id, SEASON, team, Position.MID.value)
    )
    return player


def _stat(conn, player_id, team):
    record_match_stats(
        conn,
        PlayerMatchStats(
            player_id=player_id, fixture_id="fx", season=SEASON,
            source_id="api_football", source_record_version="fixture:fx",
            observed_at="2026-08-31T00:00:00+00:00",
            kickoff_at="2022-08-07T14:00:00+00:00", team=team,
            minutes=90,
        ),
    )


def test_prepare_corpus_maps_provider_fixture_names_to_model_names(conn):
    _seed_fixture(conn)
    stable = _player(conn, "Stable Player", "Manchester City")
    _stat(conn, stable.id, "Manchester City")

    corpus = fpl_evaluation.prepare_corpus(conn, _matches(), (SEASON,))

    assert len(corpus.fixtures) == 1
    fixture = corpus.fixtures[0]
    assert fixture.home == "Manchester City"
    assert fixture.model_home == "Man City"
    assert fixture.model_away == "Chelsea"
    assert corpus.excluded_transfer_players == frozenset()


def test_prepare_corpus_excludes_a_player_with_different_fixture_time_club(conn):
    _seed_fixture(conn)
    transferred = _player(conn, "Transferred Player", "Chelsea")
    _stat(conn, transferred.id, "Manchester City")

    corpus = fpl_evaluation.prepare_corpus(conn, _matches(), (SEASON,))

    assert corpus.excluded_transfer_players == frozenset({(transferred.id, SEASON)})


def test_prepare_corpus_refuses_unmapped_provider_teams(conn):
    _seed_fixture(conn)
    conn.execute(
        "UPDATE fpl_fixture_harvest_state SET home_team = 'Unmapped FC' WHERE fixture_id = 'fx'"
    )

    with pytest.raises(fpl_evaluation.EvaluationCorpusError, match="unresolved"):
        fpl_evaluation.prepare_corpus(conn, _matches(), (SEASON,))


def test_run_builds_metrics_from_the_prepared_fixture_corpus(conn):
    _seed_fixture(conn)
    home = _player(conn, "Home Player", "Manchester City")
    away = _player(conn, "Away Player", "Chelsea")
    _stat(conn, home.id, "Manchester City")
    _stat(conn, away.id, "Chelsea")
    dates = pd.date_range("2020-01-01", periods=200, freq="D")
    matches = pd.DataFrame(
        {
            "date": dates,
            "season": [2020] * 200,
            "division": ["E0"] * 200,
            "home_team": ["Man City"] * 200,
            "away_team": ["Chelsea"] * 200,
            "home_goals": [2] * 200,
            "away_goals": [1] * 200,
        }
    )

    result = fpl_evaluation.run(
        conn, matches, fpl_evaluation.prepare_corpus(conn, matches, (SEASON,))
    )

    assert result.fixtures_evaluated == 1
    assert result.overall.n == 2
