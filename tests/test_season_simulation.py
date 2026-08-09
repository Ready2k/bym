"""Season-length Monte Carlo simulator (SPEC.md §12-14)."""

from __future__ import annotations

from datetime import datetime

import pytest

from super6 import opponent_intelligence as oi
from super6 import prediction_history, season_simulation as ss
from super6 import strategy
from super6.league import LeagueStanding, OpponentRound, StrategyCandidate, opponent_profiles
from super6.model import dixon_coles
from super6.recommend import recommend_round
from super6.round import Fixture, Round

from test_dixon_coles import synthetic_matches

ROUND = Round(
    round_number=1,
    deadline_at=datetime(2026, 10, 18, 14, 0),
    fixtures=(
        Fixture("Strong", "Weak"), Fixture("Weak", "Strong"),
        Fixture("Middling A", "Middling B"), Fixture("Middling B", "Middling A"),
        Fixture("Strong", "Middling A"), Fixture("Weak", "Middling B"),
    ),
)


@pytest.fixture(scope="module")
def matches():
    return synthetic_matches()


@pytest.fixture(scope="module")
def params(matches):
    return dixon_coles.fit(matches, xi=0.0)


@pytest.fixture(scope="module")
def router(params):
    return lambda home, away: params


@pytest.fixture(scope="module")
def recommendation(router):
    from super6.config import FROZEN_PHASE1_V1

    return recommend_round(router, ROUND, FROZEN_PHASE1_V1)


def _standing(name, points=0, exact=0, rank=1):
    return LeagueStanding(rank=rank, participant=name, points=points, exact_scores=exact,
                          fixtures_scored=0, rounds_scored=0)


# ---------------------------------------------------------------------------
# SeasonState


def test_season_state_reports_gaps_relative_to_the_leader():
    standings = (_standing("Dave", points=40, exact=5, rank=1), _standing("You", points=33, exact=3, rank=2))
    state = ss.season_state(standings, "You", rounds_remaining=10)
    assert state.points_gap == -7
    assert state.exact_score_gap == -2
    assert state.rounds_remaining == 10
    assert state.position == 2


def test_season_state_for_the_leader_has_zero_gap():
    standings = (_standing("You", points=40, exact=5, rank=1), _standing("Dave", points=33, exact=3, rank=2))
    state = ss.season_state(standings, "You", rounds_remaining=5)
    assert state.points_gap == 0
    assert state.exact_score_gap == 0


def test_season_state_is_none_without_standings_or_participant():
    assert ss.season_state((), "You", 5) is None
    assert ss.season_state((_standing("Dave"),), "You", 5) is None


def test_season_state_rejects_negative_rounds_remaining():
    with pytest.raises(ValueError):
        ss.season_state((_standing("You"),), "You", -1)


# ---------------------------------------------------------------------------
# Round Opportunity Model


def test_sample_synthetic_round_is_forecastable_and_reproducible(matches, router):
    from super6.config import FROZEN_PHASE1_V1

    a = ss.sample_synthetic_round(matches, router, FROZEN_PHASE1_V1, seed=1, slot=0)
    b = ss.sample_synthetic_round(matches, router, FROZEN_PHASE1_V1, seed=1, slot=0)
    assert len(a.fixtures) == 6
    assert [(f.home, f.away) for f in a.fixtures] == [(f.home, f.away) for f in b.fixtures]
    other_slot = ss.sample_synthetic_round(matches, router, FROZEN_PHASE1_V1, seed=1, slot=1)
    assert [(f.home, f.away) for f in a.fixtures] != [(f.home, f.away) for f in other_slot.fixtures]


# ---------------------------------------------------------------------------
# Paired significance test (§13.3)


def test_paired_comparison_matches_manual_mean_and_interval():
    candidate = [1.0, 1.0, 0.0, 0.0]
    baseline = [0.0, 0.0, 0.0, 0.0]
    result = ss.paired_comparison(candidate, baseline)
    assert result.mean_gain == 0.5
    assert result.simulations == 4
    assert result.lower_bound < result.mean_gain < result.upper_bound


def test_paired_comparison_single_world_has_zero_width_interval():
    result = ss.paired_comparison([1.0], [0.0])
    assert result.mean_gain == 1.0
    assert result.lower_bound == result.upper_bound == 1.0


def test_paired_comparison_rejects_mismatched_lengths_and_bad_confidence():
    with pytest.raises(ValueError):
        ss.paired_comparison([1.0], [1.0, 0.0])
    with pytest.raises(ValueError):
        ss.paired_comparison([], [])
    with pytest.raises(ValueError):
        ss.paired_comparison([1.0], [0.0], confidence=0.5)


def test_accepts_requires_both_lower_bound_and_minimum_gain():
    # A confident but tiny gain must not clear a larger minimum_gain.
    comparison = ss.PairedComparison(mean_gain=0.02, lower_bound=0.01, upper_bound=0.03, simulations=5000)
    assert comparison.accepts(0.01) is True
    assert comparison.accepts(0.05) is False
    # A larger mean gain with a lower bound at or below zero is unresolved noise.
    noisy = ss.PairedComparison(mean_gain=0.05, lower_bound=-0.01, upper_bound=0.11, simulations=50)
    assert noisy.accepts(0.01) is False


# ---------------------------------------------------------------------------
# simulate_remaining_season


def test_simulate_remaining_season_returns_none_without_opponents(recommendation):
    standings = (_standing("You"),)
    candidates = (StrategyCandidate("MAX_EV", tuple((f.pick_home, f.pick_away) for f in recommendation.fixtures)),)
    assert ss.simulate_remaining_season(
        recommendation, candidates, standings, "You", (), {}, {}, None, None
    ) is None


def test_simulate_remaining_season_scores_actual_current_round_picks(recommendation):
    max_ev = tuple((f.pick_home, f.pick_away) for f in recommendation.fixtures)
    standings = (_standing("You", rank=1), _standing("Dave", rank=2))
    candidates = (StrategyCandidate("MAX_EV", max_ev),)
    # Dave mirrors our exact picks; with two players that guarantees a 50/50 split.
    current_picks = (OpponentRound("Dave", 2026, 1, max_ev, "t"),)
    result = ss.simulate_remaining_season(
        recommendation, candidates, standings, "You", current_picks, {}, {}, None, None,
        simulations=1500,
    )
    assert result is not None
    assert result.candidates[0].finish_first_probability == pytest.approx(0.5, abs=0.03)


def test_simulate_remaining_season_falls_back_to_predicted_picks_when_not_submitted(recommendation):
    max_ev = tuple((f.pick_home, f.pick_away) for f in recommendation.fixtures)
    standings = (_standing("You", rank=1), _standing("Dave", rank=2))
    candidates = (StrategyCandidate("MAX_EV", max_ev),)

    # Dave has prior history (matching our forecasts) but has not picked
    # *this* round yet — his contribution must come from prediction, not a
    # missing-data zero.
    forecast_path_history = (OpponentRound("Dave", 2026, 2, max_ev, "t"),)
    profiles = {p.participant: p for p in opponent_profiles(forecast_path_history)}
    average = oi.league_average_profile(forecast_path_history)
    bias = {"Dave": oi.FavouriteBiasProfile("Dave", 20, 0.8, 0.2, oi.opponent_influence_weight(20))}

    result = ss.simulate_remaining_season(
        recommendation, candidates, standings, "You", (), profiles, bias, average, 0.5,
        simulations=1500,
    )
    assert result is not None
    # Dave's simulated picks should vary world to world (not a fixed zero
    # contribution) — final points should show some spread across worlds.
    finals = {round(p, 2) for p in result.candidates[0].win_shares}
    assert finals != {0.0}


def test_simulate_remaining_season_includes_synthetic_future_rounds(recommendation, matches, router):
    from super6.config import FROZEN_PHASE1_V1

    max_ev = tuple((f.pick_home, f.pick_away) for f in recommendation.fixtures)
    standings = (_standing("You", rank=1), _standing("Dave", rank=2))
    candidates = (StrategyCandidate("MAX_EV", max_ev),)
    current_picks = (OpponentRound("Dave", 2026, 1, max_ev, "t"),)

    without_future = ss.simulate_remaining_season(
        recommendation, candidates, standings, "You", current_picks, {}, {}, None, None,
        simulations=800,
    )
    synthetic = [
        ss.sample_synthetic_round(matches, router, FROZEN_PHASE1_V1, seed=42, slot=slot)
        for slot in range(3)
    ]
    with_future = ss.simulate_remaining_season(
        recommendation, candidates, standings, "You", current_picks, {}, {}, None, None,
        synthetic_rounds=synthetic, simulations=800,
    )
    assert with_future.candidates[0].expected_final_points > without_future.candidates[0].expected_final_points


def test_simulate_remaining_season_rejects_duplicate_candidate_names(recommendation):
    max_ev = tuple((f.pick_home, f.pick_away) for f in recommendation.fixtures)
    standings = (_standing("You"), _standing("Dave"))
    candidates = (StrategyCandidate("MAX_EV", max_ev), StrategyCandidate("MAX_EV", max_ev))
    with pytest.raises(ValueError, match="unique"):
        ss.simulate_remaining_season(
            recommendation, candidates, standings, "You", (), {}, {}, None, None,
        )


# ---------------------------------------------------------------------------
# §12 mode labelling


def _result(name, p):
    return ss.SeasonCandidateResult(name=name, finish_first_probability=p, expected_final_points=0.0,
                                    win_shares=tuple([1.0] * 40 if p > 0.5 else [0.0] * 40))


def test_recommend_season_strategy_is_normal_when_baseline_already_wins():
    baseline = _result("MAX_EV", 0.4)
    simulation = ss.SeasonSimulationResult("You", baseline, (baseline,), simulations=40)
    recommendation = ss.recommend_season_strategy(simulation)
    assert recommendation.mode == "NORMAL"
    assert recommendation.chosen.name == "MAX_EV"


def test_recommend_season_strategy_labels_attack_for_a_differentiate_win():
    baseline = _result("MAX_EV", 0.30)
    winner = ss.SeasonCandidateResult(
        "DIFFERENTIATE_1", 0.60, 0.0, tuple([1.0] * 35 + [0.0] * 5),
    )
    simulation = ss.SeasonSimulationResult("You", baseline, (winner, baseline), simulations=40)
    recommendation = ss.recommend_season_strategy(simulation, minimum_gain=0.01)
    assert recommendation.mode == "ATTACK"
    assert recommendation.chosen.name == "DIFFERENTIATE_1"


def test_recommend_season_strategy_labels_defend_for_a_correlate_win():
    baseline = _result("MAX_EV", 0.30)
    winner = ss.SeasonCandidateResult(
        "CORRELATE", 0.60, 0.0, tuple([1.0] * 35 + [0.0] * 5),
    )
    simulation = ss.SeasonSimulationResult("You", baseline, (winner, baseline), simulations=40)
    recommendation = ss.recommend_season_strategy(simulation, minimum_gain=0.01)
    assert recommendation.mode == "DEFEND"


def test_recommend_season_strategy_stays_normal_when_gain_is_inside_noise():
    # Baseline wins 16 of 40 worlds, the differentiate candidate wins 17 —
    # a one-world edge with only 40 draws, nowhere near enough to clear the
    # lower confidence bound even though the raw mean favours it.
    baseline_shares = tuple([1.0] * 16 + [0.0] * 24)
    marginal_shares = tuple([0.0] * 16 + [1.0] * 1 + [0.0] * 23)
    baseline = ss.SeasonCandidateResult("MAX_EV", 0.40, 0.0, baseline_shares)
    marginal = ss.SeasonCandidateResult("DIFFERENTIATE_1", 0.425, 0.0, marginal_shares)
    simulation = ss.SeasonSimulationResult("You", baseline, (marginal, baseline), simulations=40)
    recommendation = ss.recommend_season_strategy(simulation, minimum_gain=0.01)
    assert recommendation.mode == "NORMAL"
    assert recommendation.chosen.name == "MAX_EV"


# ---------------------------------------------------------------------------
# strategy.build_correlate_candidate


def test_build_correlate_candidate_picks_the_fields_most_likely_score(recommendation):
    from super6.league import OpponentProfile

    history = (
        OpponentRound("Dave", 2026, 1, ((3, 0), (3, 0), (1, 1), (1, 1), (3, 0), (0, 3)), "t"),
        OpponentRound("Priya", 2026, 1, ((3, 0), (3, 0), (1, 1), (1, 1), (3, 0), (0, 3)), "t"),
    )
    profiles = {p.participant: p for p in opponent_profiles(history)}
    average = oi.league_average_profile(history)
    bias = {
        name: oi.FavouriteBiasProfile(name, 12, 0.9, 0.1, oi.opponent_influence_weight(12))
        for name in profiles
    }
    candidate = strategy.build_correlate_candidate(recommendation, profiles, bias, average, 0.9)
    assert candidate is not None
    assert candidate.name == "CORRELATE"
    # First fixture is a clear HOME favourite in the model, and both rivals
    # only ever pick 3-0 for a HOME-favoured fixture in this history.
    assert candidate.picks[0] == (3, 0)


def test_build_correlate_candidate_is_none_without_any_opponent_evidence(recommendation):
    candidate = strategy.build_correlate_candidate(recommendation, {}, {}, None, None)
    assert candidate is None
