"""Opponent-aware strategy: scenario generation and candidate selection."""

from datetime import datetime

import pytest

from bym import strategy
from bym.config import FROZEN_PHASE1_V1
from bym.league import LeagueStanding, OpponentRound, recommend_strategy
from bym.model import dixon_coles
from bym.recommend import recommend_round
from bym.round import Fixture, Round

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
def recommendation():
    params = dixon_coles.fit(synthetic_matches(), xi=0.0)
    return recommend_round(params, ROUND, FROZEN_PHASE1_V1)


def test_scenarios_are_drawn_from_the_models_own_distribution(recommendation):
    scenarios = strategy.simulate_scenarios(recommendation, count=4000)
    assert len(scenarios) == 4000
    assert all(len(s.results) == 6 for s in scenarios)

    # Simulated home-win rate for fixture 0 must track its matrix.
    home_wins = sum(1 for s in scenarios if s.results[0][0] > s.results[0][1])
    expected = recommendation.fixtures[0].matrix.p_home_win
    assert home_wins / 4000 == pytest.approx(expected, abs=0.025)


def test_scenarios_are_reproducible(recommendation):
    """A refresh must not silently re-rank statistically tied candidates."""
    a = strategy.simulate_scenarios(recommendation, count=200)
    b = strategy.simulate_scenarios(recommendation, count=200)
    assert [s.results for s in a] == [s.results for s in b]
    other = strategy.simulate_scenarios(recommendation, count=200, seed=99)
    assert [s.results for s in a] != [s.results for s in other]


def test_max_ev_is_always_offered_as_a_candidate(recommendation):
    candidates = strategy.build_candidates(recommendation, [])
    assert candidates[0].name == "MAX_EV"
    assert candidates[0].picks == tuple(
        (f.pick_home, f.pick_away) for f in recommendation.fixtures
    )


def test_phase3_falls_back_to_max_ev_without_opponent_evidence(recommendation):
    result = strategy.generate_phase3_picks(
        recommendation,
        season=2026,
        round_number=1,
    )

    assert result.fallback is True
    assert result.status == "FALLBACK_MAX_EV"
    assert result.selected_candidate == "MAX_EV"
    assert result.picks == tuple(
        (fixture.pick_home, fixture.pick_away)
        for fixture in recommendation.fixtures
    )
    assert "no league standings" in result.reason


def test_phase3_is_reproducible_and_returns_recordable_picks(recommendation):
    baseline = tuple((f.pick_home, f.pick_away) for f in recommendation.fixtures)
    rival = OpponentRound("Twin", 2026, 1, baseline, "test")
    standings = [_standing("You"), _standing("Twin")]

    first = strategy.generate_phase3_picks(
        recommendation,
        standings=standings,
        round_picks=[rival],
        participant="You",
        season=2026,
        round_number=1,
        simulations=200,
        seed=17,
    )
    second = strategy.generate_phase3_picks(
        recommendation,
        standings=standings,
        round_picks=[rival],
        participant="You",
        season=2026,
        round_number=1,
        simulations=200,
        seed=17,
    )

    assert first.fallback is False
    assert first.status == "MAX_EV"
    assert first.simulations == 200
    assert len(first.picks) == 6
    assert first.picks == second.picks
    assert first.selected_candidate == second.selected_candidate


def test_phase3_falls_back_when_opponent_round_is_incomplete(recommendation):
    result = strategy.generate_phase3_picks(
        recommendation,
        standings=[_standing("You"), _standing("Twin")],
        round_picks=[],
        season=2026,
        round_number=1,
    )

    assert result.fallback
    assert result.picks == tuple(
        (fixture.pick_home, fixture.pick_away)
        for fixture in recommendation.fixtures
    )
    assert "complete current round" in result.reason


def test_deviations_prefer_fixtures_where_the_field_is_crowded(recommendation):
    """Separation is only worth buying where rivals are concentrated."""
    crowded_index = 3
    picks = [(f.pick_home, f.pick_away) for f in recommendation.fixtures]
    rivals = [
        OpponentRound(name, 2026, 1, tuple(picks), "test")
        for name in ("Dave", "Priya", "Sam")
    ]
    ranked = strategy.rank_alternatives(recommendation, rivals)
    assert ranked, "no alternatives generated"
    # Every rival holds the max-EV pick everywhere, so all shares are 1.0
    # and ordering falls through to the cheapest EV sacrifice.
    assert all(a.opponent_share == 1.0 for a in ranked)
    assert ranked == sorted(ranked, key=lambda a: (-a.opponent_share, a.ev_cost))
    assert ranked[0].ev_cost <= ranked[-1].ev_cost
    del crowded_index


def test_candidates_deviate_on_distinct_fixtures_and_stack(recommendation):
    rivals = [OpponentRound("Dave", 2026, 1, tuple(
        (f.pick_home, f.pick_away) for f in recommendation.fixtures), "t")]
    candidates = strategy.build_candidates(recommendation, rivals, max_candidates=4)
    base = candidates[0].picks
    changed_counts = [
        sum(1 for a, b in zip(base, candidate.picks) if a != b)
        for candidate in candidates
    ]
    assert changed_counts[0] == 0
    # Each successive candidate gambles one more fixture than the last.
    assert changed_counts == sorted(changed_counts)
    assert changed_counts[-1] == len(candidates) - 1


def _standing(name):
    return LeagueStanding(rank=1, participant=name, points=0, exact_scores=0,
                          fixtures_scored=0, rounds_scored=0)


def _evaluate(recommendation, rivals, names, count=2000):
    candidates = strategy.build_candidates(recommendation, rivals)
    scenarios = strategy.simulate_scenarios(recommendation, count=count)
    result = recommend_strategy(
        [_standing(n) for n in ("You", *names)], rivals, candidates, scenarios,
        participant="You", season=2026, round_number=1,
    )
    assert result is not None
    evaluations = (result.recommended, *result.alternatives)
    # The app applies this guard before acting on the raw ranking.
    chosen = strategy.preferred(evaluations, count)
    return chosen, {e.name: e for e in evaluations}


def test_a_gain_inside_sampling_noise_is_not_treated_as_an_improvement():
    """Deviating costs certain expected points. Buying that with a gap the
    simulation cannot resolve would be a guaranteed loss for a fictional
    gain, so the baseline must win ties and near-ties."""
    from bym.league import StrategyEvaluation

    floor = strategy.noise_floor(2000)
    assert 0.005 < floor < 0.03  # ~1.1pp at 2,000 draws

    baseline = StrategyEvaluation("MAX_EV", 7.8, 0.400, 2000)
    marginal = StrategyEvaluation("DIFFERENTIATE_1", 7.6, 0.400 + floor / 2, 2000)
    assert strategy.preferred([baseline, marginal], 2000).name == "MAX_EV"

    decisive = StrategyEvaluation("DIFFERENTIATE_1", 7.6, 0.400 + floor * 3, 2000)
    assert strategy.preferred([baseline, decisive], 2000).name == "DIFFERENTIATE_1"


def test_head_to_head_mirroring_is_a_guaranteed_split_and_stays_optimal(recommendation):
    """With one rival, mirroring locks in exactly half of first place.

    Deviating can only lower your score, so the engine must NOT chase
    differentiation here — the value of separating appears once a third
    player can overtake you both.
    """
    max_ev = tuple((f.pick_home, f.pick_away) for f in recommendation.fixtures)
    rivals = [OpponentRound("Twin", 2026, 1, max_ev, "t")]
    chosen, scored = _evaluate(recommendation, rivals, ["Twin"])
    assert scored["MAX_EV"].finish_first_probability == pytest.approx(0.5)
    assert chosen.name == "MAX_EV"


def test_tie_splitting_penalises_mirroring_once_others_can_overtake(recommendation):
    """The case that motivates the feature: sharing picks with one rival
    while two others play differently means splitting first whenever you
    win, without protecting you from being beaten."""
    max_ev = tuple((f.pick_home, f.pick_away) for f in recommendation.fixtures)
    varied_a = ((1, 1), (1, 1), (2, 0), (0, 0), (1, 2), (2, 2))
    varied_b = ((0, 0), (2, 2), (1, 0), (1, 1), (3, 1), (0, 1))
    mirrored = [
        OpponentRound("Twin", 2026, 1, max_ev, "t"),
        OpponentRound("Ann", 2026, 1, varied_a, "t"),
        OpponentRound("Bo", 2026, 1, varied_b, "t"),
    ]
    _, with_twin = _evaluate(recommendation, mirrored, ["Twin", "Ann", "Bo"])

    distinct = [
        OpponentRound("Cass", 2026, 1, varied_b, "t"),
        OpponentRound("Ann", 2026, 1, varied_a, "t"),
        OpponentRound("Bo", 2026, 1, varied_b, "t"),
    ]
    _, without_twin = _evaluate(recommendation, distinct, ["Cass", "Ann", "Bo"])

    assert with_twin["MAX_EV"].finish_first_probability < (
        without_twin["MAX_EV"].finish_first_probability
    ), "a rival mirroring your picks must reduce P(finish first)"


def test_the_recommended_candidate_is_the_one_that_maximises_p_first(recommendation):
    max_ev = tuple((f.pick_home, f.pick_away) for f in recommendation.fixtures)
    rivals = [
        OpponentRound("Twin", 2026, 1, max_ev, "t"),
        OpponentRound("Ann", 2026, 1, ((1, 1), (1, 1), (2, 0), (0, 0), (1, 2), (2, 2)), "t"),
    ]
    chosen, scored = _evaluate(recommendation, rivals, ["Twin", "Ann"])
    best = max(e.finish_first_probability for e in scored.values())
    # Either the clear winner, or the baseline when the gap is unresolvable.
    assert chosen.finish_first_probability == best or chosen.name == "MAX_EV"
    if chosen.name != "MAX_EV":
        gain = chosen.finish_first_probability - scored["MAX_EV"].finish_first_probability
        assert gain > strategy.noise_floor(2000)
