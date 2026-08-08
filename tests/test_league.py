from __future__ import annotations

import pytest

from super6 import league


PICKS_A = ((2, 1), (1, 1), (0, 1), (2, 0), (1, 0), (1, 2))
PICKS_B = ((1, 0), (0, 0), (1, 2), (2, 0), (1, 1), (0, 2))
RESULTS = ((2, 1), (1, 1), (0, 1), (2, 0), (2, 1), (1, 2))


def opponent(name: str, picks=PICKS_A, round_number: int = 1) -> league.OpponentRound:
    return league.OpponentRound(name, 2026, round_number, picks, "2026-08-08T10:00:00+00:00")


def test_opponent_round_is_six_picks_and_history_is_append_only(tmp_path):
    path = tmp_path / "opponents.json"
    recorded = league.record_opponent_round("Alex", 2026, 1, PICKS_A, path=path)
    assert league.load_opponent_history(path) == (recorded,)
    with pytest.raises(ValueError, match="already recorded"):
        league.record_opponent_round("Alex", 2026, 1, PICKS_B, path=path)
    with pytest.raises(ValueError, match="6 picks"):
        league.record_opponent_round("Bea", 2026, 1, PICKS_A[:5], path=path)


def test_profiles_keep_per_round_history_and_describe_observed_picks_only():
    history = (opponent("Alex"), opponent("Alex", PICKS_B, 2), opponent("Bea", PICKS_B))
    alex_history = league.history_for_participant(history, "alex")
    assert [item.round_number for item in alex_history] == [1, 2]
    profile = league.opponent_profiles(history)[0]
    assert profile.participant == "Alex"
    assert profile.rounds == 2
    assert profile.picks == 12
    assert profile.home_result_rate + profile.draw_result_rate + profile.away_result_rate == 1
    assert profile.common_scores[0][1] == 2


def test_private_league_standings_score_only_known_non_void_results():
    history = (opponent("You"), opponent("Alex", PICKS_B))
    results = {
        (2026, 1, 0): (2, 1),
        (2026, 1, 1): (1, 1),
        (2026, 1, 2): (0, 1),
        (2026, 1, 3): None,  # VOID — excluded, not a zero point miss
        (2026, 1, 4): (2, 1),
        # fixture 5 is still pending, so it must also be excluded
    }
    table = league.league_standings(history, results)
    assert [(row.participant, row.points, row.fixtures_scored) for row in table] == [
        ("You", 17, 4),
        ("Alex", 6, 4),
    ]
    assert table[0].exact_scores == 3
    assert table[0].rounds_scored == 1


def test_finish_first_returns_none_without_complete_adequate_scenarios():
    table = (
        league.LeagueStanding("You", 1, 10, 2, 6, 1),
        league.LeagueStanding("Alex", 2, 8, 1, 6, 1),
    )
    assert league.estimate_finish_first(
        table, [league.PointsScenario("one", {"You": 1, "Alex": 0})], participant="You"
    ) is None
    assert league.estimate_finish_first(
        table,
        [
            league.PointsScenario("one", {"You": 1, "Alex": 0}),
            league.PointsScenario("two", {"You": 1}),
        ],
        participant="You",
        minimum_scenarios=2,
    ) is None


def test_finish_first_is_transparent_and_splits_tied_first_places():
    table = (
        league.LeagueStanding("You", 1, 10, 2, 6, 1),
        league.LeagueStanding("Alex", 2, 8, 1, 6, 1),
    )
    estimate = league.estimate_finish_first(
        table,
        [
            league.PointsScenario("win", {"You": 1, "Alex": 0}),
            league.PointsScenario("tie", {"You": 0, "Alex": 2}),
            league.PointsScenario("lose", {"You": 0, "Alex": 3}),
        ],
        participant="You",
        minimum_scenarios=3,
    )
    assert estimate is not None
    assert estimate.probability == 0.5  # win + half of a tie, divided by 3
    assert estimate.expected_final_points == 10.33
    assert estimate.simulations == 3


def test_strategy_requires_complete_opponent_round_and_simulations():
    table = (
        league.LeagueStanding("You", 1, 10, 2, 6, 1),
        league.LeagueStanding("Alex", 2, 9, 1, 6, 1),
    )
    candidate = league.StrategyCandidate("model", PICKS_A)
    scenarios = [league.ScorelineScenario("one", RESULTS)]
    assert league.recommend_strategy(
        table, (), [candidate], scenarios, participant="You", season=2026, round_number=2,
        minimum_scenarios=1,
    ) is None
    assert league.recommend_strategy(
        table, (opponent("Alex", PICKS_B, 2),), [candidate], scenarios,
        participant="You", season=2026, round_number=2, minimum_scenarios=2,
    ) is None


def test_strategy_compares_actual_candidates_against_simulated_fixture_results():
    table = (
        league.LeagueStanding("You", 1, 10, 2, 6, 1),
        league.LeagueStanding("Alex", 2, 10, 2, 6, 1),
    )
    exact = league.StrategyCandidate("exact", RESULTS)
    contrarian = league.StrategyCandidate("contrarian", ((0, 0),) * 6)
    recommendation = league.recommend_strategy(
        table,
        (opponent("Alex", PICKS_B, 2),),
        (contrarian, exact),
        (league.ScorelineScenario("one", RESULTS), league.ScorelineScenario("two", RESULTS)),
        participant="You",
        season=2026,
        round_number=2,
        minimum_scenarios=2,
    )
    assert recommendation is not None
    assert recommendation.recommended.name == "exact"
    assert recommendation.recommended.finish_first_probability == 1.0
    assert recommendation.recommended.expected_round_points == 30.0
    assert recommendation.alternatives[0].name == "contrarian"
