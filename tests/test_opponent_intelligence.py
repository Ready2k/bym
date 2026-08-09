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


# ---------------------------------------------------------------------------
# §11 team-identity bias: big clubs / promoted teams


def joined_pick(participant, index, home, away, pick, *, home_p, away_p, round_number=1):
    return oi.JoinedPick(
        participant=participant, season=2026, round_number=round_number,
        fixture_index=index, home_team=home, away_team=away, pick=pick,
        model_result="HOME", market_home_probability=home_p,
        market_away_probability=away_p,
    )


def test_market_reputation_centres_out_home_advantage():
    # X only ever plays at home, Y only ever away, and both are priced
    # exactly at the average for their venue — so both are average teams.
    # Pooling home and away probabilities together would instead rank X
    # far above Y purely on venue.
    joined = (
        joined_pick("Dave", 0, "X", "P", (1, 0), home_p=0.5, away_p=0.25),
        joined_pick("Dave", 1, "X", "Q", (1, 0), home_p=0.5, away_p=0.25),
        joined_pick("Dave", 2, "R", "Y", (1, 0), home_p=0.5, away_p=0.25),
        joined_pick("Dave", 3, "S", "Y", (1, 0), home_p=0.5, away_p=0.25),
    )
    strength = {r.team: r.market_strength for r in oi.team_market_reputations(joined)}
    assert strength["X"] == pytest.approx(0.0)
    assert strength["Y"] == pytest.approx(0.0)


def test_market_reputation_ranks_a_stronger_team_above_a_weaker_one():
    joined = (
        joined_pick("Dave", 0, "Arsenal", "Everton", (1, 0), home_p=0.70, away_p=0.15),
        joined_pick("Dave", 1, "Everton", "Arsenal", (1, 0), home_p=0.35, away_p=0.45),
    )
    reputations = oi.team_market_reputations(joined)
    assert [r.team for r in reputations] == ["Arsenal", "Everton"]
    assert reputations[0].market_strength > 0 > reputations[1].market_strength
    assert reputations[0].appearances == 2


def test_market_reputation_counts_each_fixture_once_however_many_rivals_picked_it():
    one_rival = (joined_pick("Dave", 0, "Arsenal", "Everton", (1, 0), home_p=0.7, away_p=0.15),)
    three_rivals = one_rival + (
        joined_pick("Sam", 0, "Arsenal", "Everton", (2, 0), home_p=0.7, away_p=0.15),
        joined_pick("Ann", 0, "Arsenal", "Everton", (0, 0), home_p=0.7, away_p=0.15),
    )
    assert oi.team_market_reputations(one_rival) == oi.team_market_reputations(three_rivals)


def test_market_reputation_ignores_fixtures_with_no_stored_odds():
    joined = (
        oi.JoinedPick("Dave", 2026, 1, 0, "Arsenal", "Everton", (1, 0), "HOME"),
    )
    assert oi.team_market_reputations(joined) == ()


def test_team_bias_measures_backing_against_the_price_not_the_raw_rate():
    # Every fixture prices Arsenal at 0.60 to win. Dave backs them in 3 of
    # 4, Sam in 1 of 4 — so Dave over-backs and Sam under-backs the same
    # club, which a raw "backs Arsenal 75% of the time" could not say.
    joined = tuple(
        joined_pick("Dave", index, "Arsenal", f"Opp{index}", pick, home_p=0.60, away_p=0.20)
        for index, pick in enumerate([(2, 0), (1, 0), (3, 1), (0, 1)])
    ) + tuple(
        joined_pick("Sam", index, "Arsenal", f"Opp{index}", pick, home_p=0.60, away_p=0.20)
        for index, pick in enumerate([(2, 0), (1, 1), (0, 1), (1, 2)])
    )
    profiles = {
        profile.participant: profile
        for profile in oi.team_bias_profiles(
            joined, reputations=(oi.TeamReputation("Arsenal", 4, 0.25),)
        )
    }
    dave = profiles["Dave"].big_clubs
    assert (dave.picks, dave.backed_rate, dave.market_rate) == (4, 0.75, 0.6)
    assert dave.bias == pytest.approx(0.15)  # favours big clubs
    assert profiles["Sam"].big_clubs.bias == pytest.approx(-0.35)  # avoids them
    # Nobody was flagged promoted, so that half stays honestly empty.
    assert profiles["Dave"].promoted == oi.NO_CATEGORY_EVIDENCE


def test_underrating_promoted_teams_shows_as_a_negative_bias():
    joined = tuple(
        joined_pick("Dave", index, f"Opp{index}", "Sunderland", (2, 0), home_p=0.5, away_p=0.20)
        for index in range(4)
    )
    profile = oi.team_bias_profiles(joined, promoted_teams=("Sunderland",))[0]
    assert profile.promoted.picks == 4
    assert profile.promoted.backed_rate == 0.0
    assert profile.promoted.bias == pytest.approx(-0.20)


def test_team_bias_skips_fixtures_without_exactly_one_team_in_the_category():
    # Big club v big club has no unambiguous subject; neither does a
    # fixture with no big club at all.
    joined = (
        joined_pick("Dave", 0, "Arsenal", "Man City", (2, 0), home_p=0.45, away_p=0.30),
        joined_pick("Dave", 1, "Burnley", "Everton", (1, 0), home_p=0.40, away_p=0.35),
    )
    reputations = (oi.TeamReputation("Arsenal", 1, 0.30), oi.TeamReputation("Man City", 1, 0.30))
    profile = oi.team_bias_profiles(joined, reputations=reputations)[0]
    assert profile.big_clubs == oi.NO_CATEGORY_EVIDENCE


def test_team_bias_reads_market_probabilities_off_the_stored_snapshot(tmp_path):
    forecast_path = tmp_path / "history.json"
    fixtures = [
        {
            "home_team": "Arsenal", "away_team": f"Opp{index}", "result": "HOME",
            "score": "1-0",
            "market": {
                "available": True, "home_probability": 0.6,
                "draw_probability": 0.2, "away_probability": 0.2,
            },
        }
        for index in range(6)
    ]
    prediction_history.record_snapshot(2026, 1, fixtures, path=forecast_path)
    history = (opponent("Dave", 2026, 1, ((1, 0),) * 6),)
    joined = oi.join_picks_with_forecasts(history, prediction_path=forecast_path)
    assert joined[0].market_home_probability == 0.6
    assert joined[0].market_away_probability == 0.2
    profile = oi.team_bias_profiles(
        joined, reputations=(oi.TeamReputation("Arsenal", 6, 0.3),)
    )[0]
    assert profile.big_clubs.picks == 6
    assert profile.big_clubs.bias == pytest.approx(0.4)


def test_team_bias_never_reads_the_fitted_model():
    """§11's club-reputation signal is market-sourced by design — this
    module must not acquire a dependency on the forecasting core."""
    import ast
    from pathlib import Path

    source = Path(oi.__file__).read_text()
    imported = {
        node.module or ""
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any("model" in name.split(".") for name in imported), imported
