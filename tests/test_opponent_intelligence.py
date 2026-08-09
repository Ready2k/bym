"""Opponent behavioural modelling relative to our own forecasts (§11)."""

from __future__ import annotations

import pytest

from super6 import opponent_intelligence as oi
from super6 import prediction_history
from super6.league import OpponentRound


def opponent(participant, season, round_number, picks) -> OpponentRound:
    return OpponentRound(
        participant, season, round_number, picks, "2026-08-08T10:00:00+00:00"
    )


def record_round_forecast(path, season, round_number, results, teams=None):
    """Write a minimal forecast snapshot: six fixtures, each with a model result."""
    teams = teams or [(f"H{i}", f"A{i}") for i in range(6)]
    fixtures = [
        {"home_team": home, "away_team": away, "result": result, "score": "1-0"}
        for (home, away), result in zip(teams, results)
    ]
    prediction_history.record_snapshot(season, round_number, fixtures, path=path)


# ---------------------------------------------------------------------------
# opponent_influence_weight


def test_influence_weight_is_continuous_zero_evidence_to_near_certain():
    assert oi.opponent_influence_weight(0) == 0.0
    assert oi.opponent_influence_weight(20) == 0.5
    assert oi.opponent_influence_weight(180) == pytest.approx(0.9, abs=1e-9)
    # monotonic, no cliff
    weights = [oi.opponent_influence_weight(n) for n in range(0, 200, 5)]
    assert weights == sorted(weights)


def test_influence_weight_rejects_bad_input():
    with pytest.raises(ValueError):
        oi.opponent_influence_weight(-1)
    with pytest.raises(ValueError):
        oi.opponent_influence_weight(5, half_weight_at=0)


# ---------------------------------------------------------------------------
# join_picks_with_forecasts / favourite bias


def test_join_matches_by_fixture_index_and_skips_rounds_with_no_forecast(tmp_path):
    history_path = tmp_path / "opponents.json"
    forecast_path = tmp_path / "history.json"
    record_round_forecast(
        forecast_path, 2026, 1, ["HOME", "DRAW", "AWAY", "HOME", "HOME", "DRAW"]
    )
    history = (
        opponent("Dave", 2026, 1, ((2, 0), (1, 1), (0, 2), (1, 0), (3, 1), (2, 2))),
        # Round 2 was never forecast locally — must be silently skipped.
        opponent("Dave", 2026, 2, ((1, 0),) * 6),
    )
    joined = oi.join_picks_with_forecasts(history, prediction_path=forecast_path)
    assert len(joined) == 6
    assert all(item.round_number == 1 for item in joined)
    assert joined[0].home_team == "H0" and joined[0].away_team == "A0"
    assert [item.model_result for item in joined] == [
        "HOME", "DRAW", "AWAY", "HOME", "HOME", "DRAW"
    ]


def test_favourite_bias_counts_matches_against_our_recommended_result(tmp_path):
    forecast_path = tmp_path / "history.json"
    record_round_forecast(forecast_path, 2026, 1, ["HOME"] * 6)
    # Dave: 4 of 6 picks are HOME-result (matches), 2 are AWAY (upsets).
    history = (
        opponent(
            "Dave", 2026, 1,
            ((2, 0), (1, 0), (3, 1), (2, 1), (0, 1), (0, 2)),
        ),
    )
    joined = oi.join_picks_with_forecasts(history, prediction_path=forecast_path)
    profiles = oi.favourite_bias_profiles(joined)
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.participant == "Dave"
    assert profile.sample_size == 6
    assert profile.favourite_rate == pytest.approx(4 / 6, abs=1e-4)
    assert profile.upset_rate == pytest.approx(2 / 6, abs=1e-4)
    assert profile.influence_weight == oi.opponent_influence_weight(6)


def test_favourite_bias_is_none_with_no_joinable_evidence():
    profiles = oi.favourite_bias_profiles(())
    assert profiles == ()


def test_league_average_favourite_rate_pools_every_participant(tmp_path):
    forecast_path = tmp_path / "history.json"
    record_round_forecast(forecast_path, 2026, 1, ["HOME"] * 6)
    history = (
        opponent("Dave", 2026, 1, ((1, 0),) * 6),  # 6/6 HOME — all match
        opponent("Steve", 2026, 1, ((0, 1),) * 6),  # 6/6 AWAY — none match
    )
    joined = oi.join_picks_with_forecasts(history, prediction_path=forecast_path)
    assert oi.league_average_favourite_rate(joined) == pytest.approx(0.5, abs=1e-6)
    assert oi.league_average_favourite_rate(()) is None


# ---------------------------------------------------------------------------
# league_average_profile / predict_pick_distribution


def test_league_average_profile_pools_all_participants():
    history = (
        opponent("Dave", 2026, 1, ((2, 0),) * 6),
        opponent("Steve", 2026, 1, ((1, 1),) * 6),
    )
    average = oi.league_average_profile(history)
    assert average is not None
    assert average.picks == 12
    assert average.score_frequency["2-0"] == pytest.approx(0.5, abs=1e-6)
    assert average.score_frequency["1-1"] == pytest.approx(0.5, abs=1e-6)
    assert oi.league_average_profile(()) is None


def test_predict_pick_distribution_returns_empty_with_no_evidence_at_all():
    empty_history = ()
    average = oi.league_average_profile(empty_history)
    assert average is None  # nothing to predict from; caller must handle this


def test_predict_pick_distribution_shrinks_toward_league_average_for_thin_history(tmp_path):
    from super6.league import opponent_profiles

    forecast_path = tmp_path / "history.json"
    record_round_forecast(forecast_path, 2026, 1, ["HOME"] * 6)
    # Dave has a single round of history (thin); the rest of the league has
    # a lot, and predicts differently to Dave's one data point.
    dave_round = opponent("Dave", 2026, 1, ((2, 0),) * 6)
    field_rounds = tuple(
        opponent(f"Rival{i}", 2026, 1, ((1, 1),) * 6) for i in range(10)
    )
    history = (dave_round,) + field_rounds

    dave_profile = opponent_profiles((dave_round,))[0]
    average_profile = oi.league_average_profile(history)
    joined = oi.join_picks_with_forecasts(history, prediction_path=forecast_path)
    dave_bias = oi.favourite_bias_profiles(joined)[0]
    average_rate = oi.league_average_favourite_rate(joined)

    prediction = oi.predict_pick_distribution(
        dave_profile, dave_bias, average_profile, average_rate,
        fixture_favoured_result=None,
    )
    # Dave's own history says 2-0 100% of the time, but with only 6 picks
    # against a much larger field, the blended prediction should sit well
    # below 100% and 1-1 (the field's overwhelming preference) should show
    # up at all, proving the shrink actually pulled toward the average.
    assert 0 < prediction["2-0"] < 1
    assert prediction.get("1-1", 0) > 0
    assert prediction["1-1"] > prediction["2-0"]  # field's evidence dominates a thin one


def test_predict_pick_distribution_reweights_toward_the_favoured_result(tmp_path):
    forecast_path = tmp_path / "history.json"
    record_round_forecast(forecast_path, 2026, 1, ["HOME"] * 6)
    # A rival with a strong, well-evidenced history of backing the favourite.
    history = tuple(
        opponent(f"Rival{i}", 2026, 1, ((2, 0),) * 3 + ((0, 2),) * 3) for i in range(30)
    )
    from super6.league import opponent_profiles

    profile = opponent_profiles(history)[0]
    average = oi.league_average_profile(history)
    joined = oi.join_picks_with_forecasts(history, prediction_path=forecast_path)
    bias = oi.favourite_bias_profiles(joined)[0]
    average_rate = oi.league_average_favourite_rate(joined)

    # Force a high favourite rate directly so the reweight step is exercised
    # even though the synthetic history above is 50/50 by construction.
    biased = oi.FavouriteBiasProfile(
        participant=bias.participant,
        sample_size=bias.sample_size,
        favourite_rate=0.9,
        upset_rate=0.1,
        influence_weight=bias.influence_weight,
    )
    prediction = oi.predict_pick_distribution(
        profile, biased, average, average_rate, fixture_favoured_result="HOME",
    )
    assert prediction["2-0"] > prediction["0-2"]  # HOME-result scoreline favoured


# ---------------------------------------------------------------------------
# chase_defend_tendencies


def test_chase_defend_flags_more_divergence_while_trailing(tmp_path):
    forecast_path = tmp_path / "history.json"
    record_round_forecast(forecast_path, 2026, 1, ["HOME"] * 6)
    record_round_forecast(forecast_path, 2026, 2, ["HOME"] * 6)
    record_round_forecast(forecast_path, 2026, 3, ["HOME"] * 6)

    results = {}
    for index in range(6):
        results[(2026, 1, index)] = (5, 0)

    history = [
        # Round 1 establishes standings; not itself scored for divergence.
        opponent("You", 2026, 1, ((5, 0),) * 5 + ((0, 0),)),   # 25 pts
        opponent("Dave", 2026, 1, ((0, 0),) * 6),               # 0 pts
        # Round 2: Dave trails, "chases" (diverges from HOME every time but
        # happens to nail the actual result for points).
        opponent("You", 2026, 2, ((1, 0),) * 6),                # leading, defends (HOME picks)
        opponent("Dave", 2026, 2, ((0, 5),) * 6),                # trailing, chases (AWAY picks)
    ]
    for index in range(6):
        results[(2026, 2, index)] = (0, 5)

    # After rounds 1-2: You=25, Dave=30 -> Dave leads entering round 3.
    history += [
        opponent("You", 2026, 3, ((0, 2),) * 6),   # now trailing, diverges (AWAY picks)
        opponent("Dave", 2026, 3, ((2, 0),) * 6),  # now leading, defends (HOME picks)
    ]
    history = tuple(history)

    joined = oi.join_picks_with_forecasts(history, prediction_path=forecast_path)
    profiles = {p.participant: p for p in oi.chase_defend_tendencies(history, joined, results)}

    assert profiles["Dave"].divergence_rate_when_trailing == pytest.approx(1.0)
    assert profiles["Dave"].divergence_rate_when_leading == pytest.approx(0.0)
    assert profiles["Dave"].chase_when_behind == pytest.approx(1.0)

    assert profiles["You"].divergence_rate_when_leading == pytest.approx(0.0)
    assert profiles["You"].divergence_rate_when_trailing == pytest.approx(1.0)
    assert profiles["You"].chase_when_behind == pytest.approx(1.0)


def test_chase_defend_is_empty_without_at_least_one_earlier_round(tmp_path):
    forecast_path = tmp_path / "history.json"
    record_round_forecast(forecast_path, 2026, 1, ["HOME"] * 6)
    history = (opponent("Dave", 2026, 1, ((1, 0),) * 6),)
    joined = oi.join_picks_with_forecasts(history, prediction_path=forecast_path)
    # Round 1 has no earlier round to compute standings from, so nothing
    # can be classified as trailing/leading yet.
    assert oi.chase_defend_tendencies(history, joined, {}) == ()
