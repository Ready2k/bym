from __future__ import annotations

import pytest

from bym import league


PICKS_A = ((2, 1), (1, 1), (0, 1), (2, 0), (1, 0), (1, 2))
PICKS_B = ((1, 0), (0, 0), (1, 2), (2, 0), (1, 1), (0, 2))
RESULTS = ((2, 1), (1, 1), (0, 1), (2, 0), (2, 1), (1, 2))


def opponent(
    name: str, picks=PICKS_A, round_number: int = 1, golden_goal: int | None = None
) -> league.OpponentRound:
    return league.OpponentRound(
        name, 2026, round_number, picks, "2026-08-08T10:00:00+00:00", golden_goal
    )


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


def test_golden_goal_rejects_out_of_range_minutes():
    with pytest.raises(ValueError, match="between 1 and 90"):
        opponent("Alex", golden_goal=0)
    with pytest.raises(ValueError, match="between 1 and 90"):
        opponent("Alex", golden_goal=91)
    opponent("Alex", golden_goal=1)
    opponent("Alex", golden_goal=90)


def test_golden_goal_round_trips_through_persisted_history(tmp_path):
    path = tmp_path / "opponents.json"
    recorded = league.record_opponent_round(
        "Alex", 2026, 1, PICKS_A, golden_goal=9, path=path
    )
    assert recorded.golden_goal == 9
    (loaded,) = league.load_opponent_history(path)
    assert loaded.golden_goal == 9

    # Historical rounds recorded before this field existed must still load.
    path.write_text(path.read_text().replace('"golden_goal": 9,', ""))
    (legacy,) = league.load_opponent_history(path)
    assert legacy.golden_goal is None


def test_standings_break_a_points_and_exact_tie_by_golden_goal_closeness():
    """§27: the private league's confirmed order is points, then exact
    scores, then Golden Goal distance — never a blind split while a real
    tiebreak still has evidence to use."""
    tied_picks = ((2, 1), (1, 1), (0, 1), (2, 0), (1, 0), (1, 2))
    history = (
        opponent("Dave", tied_picks, golden_goal=10),
        opponent("Steve", tied_picks, golden_goal=25),
    )
    results = {(2026, 1, i): tied_picks[i] for i in range(6)}
    golden_goal_actual = {(2026, 1): 12}  # Dave (|10-12|=2) closer than Steve (|25-12|=13)

    table = league.league_standings(history, results, golden_goal_actual)
    assert [row.participant for row in table] == ["Dave", "Steve"]
    assert table[0].rank == 1
    assert table[1].rank == 2
    assert table[0].golden_goal_average_distance == 2
    assert table[1].golden_goal_average_distance == 13


def test_standings_rank_golden_goal_evidence_ahead_of_no_evidence():
    tied_picks = ((2, 1), (1, 1), (0, 1), (2, 0), (1, 0), (1, 2))
    history = (
        opponent("Dave", tied_picks, golden_goal=50),  # miles off, but a real guess
        opponent("Steve", tied_picks, golden_goal=None),  # no guess recorded at all
    )
    results = {(2026, 1, i): tied_picks[i] for i in range(6)}
    golden_goal_actual = {(2026, 1): 12}

    table = league.league_standings(history, results, golden_goal_actual)
    assert [row.participant for row in table] == ["Dave", "Steve"]
    assert table[1].golden_goal_average_distance is None


def test_standings_still_split_when_golden_goal_also_ties():
    tied_picks = ((2, 1), (1, 1), (0, 1), (2, 0), (1, 0), (1, 2))
    history = (
        opponent("Ann", tied_picks, golden_goal=10),
        opponent("Bea", tied_picks, golden_goal=14),
    )
    results = {(2026, 1, i): tied_picks[i] for i in range(6)}
    golden_goal_actual = {(2026, 1): 12}  # both |10-12| == |14-12| == 2

    table = league.league_standings(history, results, golden_goal_actual)
    assert table[0].rank == table[1].rank == 1
    assert table[0].golden_goal_average_distance == table[1].golden_goal_average_distance == 2


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
