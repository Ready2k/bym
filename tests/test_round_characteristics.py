"""§13.2 step 1: measuring what real Super 6 rounds actually look like."""

from __future__ import annotations

import pytest

from super6 import prediction_history, round_characteristics as rc


def fixture(home, away, home_p=None, draw_p=None, away_p=None):
    out = {"home_team": home, "away_team": away, "result": "HOME", "score": "1-0"}
    if home_p is not None:
        out["market"] = {
            "available": True,
            "home_probability": home_p,
            "draw_probability": draw_p,
            "away_probability": away_p,
        }
    return out


def snapshot_fixture(*args, **kwargs):
    """A fixture as `prediction_history` stores it, not as callers pass it."""
    return prediction_history.normalise_fixture(fixture(*args, **kwargs), 0)


# ---------------------------------------------------------------------------
# per fixture


def test_favourite_strength_and_competitiveness_come_from_the_market():
    measured = rc.characterise_fixture(snapshot_fixture("Arsenal", "Everton", 0.70, 0.20, 0.10))
    assert measured.favourite_strength == 0.70
    assert measured.competitiveness == pytest.approx(0.50)  # 0.70 - 0.20
    assert measured.favourite_at_home is True


def test_a_tight_fixture_reports_a_small_competitiveness_gap():
    measured = rc.characterise_fixture(snapshot_fixture("Fulham", "Brentford", 0.36, 0.34, 0.30))
    assert measured.competitiveness == pytest.approx(0.02)
    assert measured.favourite_at_home is True


def test_an_away_favourite_is_not_recorded_as_a_home_one():
    measured = rc.characterise_fixture(snapshot_fixture("Leeds", "Man City", 0.15, 0.20, 0.65))
    assert measured.favourite_strength == 0.65
    assert measured.favourite_at_home is False


def test_a_draw_favourite_takes_no_side():
    """The draw being most likely means neither team is favoured — which is
    not the same as the home team being favoured."""
    measured = rc.characterise_fixture(snapshot_fixture("Fulham", "Brentford", 0.30, 0.40, 0.30))
    assert measured.favourite_strength == 0.40
    assert measured.favourite_at_home is None


def test_a_fixture_without_odds_is_unknown_rather_than_assumed():
    measured = rc.characterise_fixture(snapshot_fixture("Arsenal", "Everton"))
    assert measured.favourite_strength is None
    assert measured.competitiveness is None
    assert measured.favourite_at_home is None


def test_division_comes_from_the_corpus_and_falls_back_to_the_away_team():
    divisions = {"Everton": "E0"}
    measured = rc.characterise_fixture(
        snapshot_fixture("Arsenal", "Everton"), divisions=divisions
    )
    assert measured.division == "E0"
    assert rc.characterise_fixture(snapshot_fixture("A", "B")).division is None


def test_expected_goals_is_supplied_by_the_caller_not_read_from_the_snapshot():
    """λ is not persisted on the forecast contract, so it is computed from
    the current fit — the same basis `sample_synthetic_round` uses."""
    measured = rc.characterise_fixture(
        snapshot_fixture("Arsenal", "Everton"),
        expected_goals=lambda home, away: 2.75,
    )
    assert measured.expected_goals == 2.75
    assert rc.characterise_fixture(snapshot_fixture("Arsenal", "Everton")).expected_goals is None


# ---------------------------------------------------------------------------
# per round


def round_fixtures():
    return [
        prediction_history.normalise_fixture(f, index)
        for index, f in enumerate([
            fixture("Arsenal", "Everton", 0.70, 0.20, 0.10),
            fixture("Man City", "Bournemouth", 0.75, 0.15, 0.10),
            fixture("Fulham", "Brentford", 0.36, 0.34, 0.30),
            fixture("Leeds", "Tottenham", 0.30, 0.25, 0.45),
            fixture("Chelsea", "Newcastle", 0.45, 0.30, 0.25),
            fixture("Birmingham", "Charlton", 0.40, 0.30, 0.30),
        ])
    ]


def test_round_shape_aggregates_mix_and_home_favourite_share():
    divisions = {
        "Arsenal": "E0", "Man City": "E0", "Fulham": "E0",
        "Leeds": "E0", "Chelsea": "E0", "Birmingham": "E1",
    }
    measured = rc.characterise_round(2026, 1, round_fixtures(), divisions=divisions)
    assert measured.division_mix == (("E0", 5), ("E1", 1))
    # Five of six have a home favourite; Leeds v Tottenham does not.
    assert measured.home_favourite_share == pytest.approx(5 / 6, abs=1e-4)
    assert measured.mean_favourite_strength == pytest.approx(0.5183, abs=1e-4)


def test_teams_absent_from_the_corpus_land_in_an_explicit_unknown_bucket():
    measured = rc.characterise_round(2026, 1, round_fixtures(), divisions={"Arsenal": "E0"})
    assert dict(measured.division_mix) == {"E0": 1, "UNKNOWN": 5}


def test_observed_rounds_reads_every_stored_round_oldest_first(tmp_path):
    path = tmp_path / "history.json"
    prediction_history.record_snapshot(2026, 2, [fixture("A", "B", 0.5, 0.3, 0.2)], path=path)
    prediction_history.record_snapshot(2026, 1, [fixture("C", "D", 0.5, 0.3, 0.2)], path=path)
    observed = rc.observed_rounds(prediction_path=path)
    assert [item.round_number for item in observed] == [1, 2]


def test_observed_rounds_uses_the_latest_snapshot_of_a_reforecast_round(tmp_path):
    path = tmp_path / "history.json"
    prediction_history.record_snapshot(2026, 1, [fixture("A", "B", 0.5, 0.3, 0.2)], path=path)
    prediction_history.record_snapshot(
        2026, 1, [fixture("A", "B", 0.8, 0.1, 0.1)], path=path, model_version="v2"
    )
    observed = rc.observed_rounds(prediction_path=path)
    assert len(observed) == 1
    assert observed[0].fixtures[0].favourite_strength == 0.8


# ---------------------------------------------------------------------------
# the accumulating target


def test_profile_pools_at_fixture_level_not_by_averaging_round_averages():
    """A round with one priced fixture must not weigh as much as a full one."""
    full = rc.characterise_round(2026, 1, round_fixtures())
    thin = rc.characterise_round(2026, 2, [
        prediction_history.normalise_fixture(fixture("X", "Y", 0.9, 0.05, 0.05), 0)
    ])
    profile = rc.observed_profile((full, thin))
    assert profile.rounds == 2
    assert profile.fixtures == 7
    strengths = [0.70, 0.75, 0.36, 0.45, 0.45, 0.40, 0.90]
    assert profile.mean_favourite_strength == pytest.approx(
        sum(strengths) / len(strengths), abs=1e-4
    )
    assert profile.favourite_strength_range == (0.36, 0.9)


def test_profile_is_empty_rather_than_fabricated_with_nothing_observed():
    profile = rc.observed_profile(())
    assert profile.rounds == 0 and profile.fixtures == 0
    assert profile.mean_favourite_strength is None
    assert profile.favourite_strength_range is None


def test_readiness_says_plainly_that_one_round_is_not_enough():
    profile = rc.observed_profile((rc.characterise_round(2026, 1, round_fixtures()),))
    message = rc.matching_readiness(profile)
    assert "1 of ~20 rounds" in message
    assert "no power" in message


def test_readiness_flips_once_enough_rounds_have_accumulated():
    rounds = tuple(
        rc.characterise_round(2026, n, round_fixtures())
        for n in range(1, rc.ROUNDS_FOR_MATCHING + 1)
    )
    message = rc.matching_readiness(rc.observed_profile(rounds))
    assert "enough to build a stratified sampler" in message


def test_nothing_here_changes_how_synthetic_rounds_are_drawn():
    """§28: measuring the target must not silently become adopting it."""
    import inspect

    from super6 import season_simulation

    source = inspect.getsource(season_simulation.sample_synthetic_round)
    assert "round_characteristics" not in source


def test_readiness_command_reports_both_counters(tmp_path, capsys):
    """`super6 readiness` is the surface that makes "not yet" a number."""
    from super6.cli import main

    prediction_path = tmp_path / "history.json"
    prediction_history.record_snapshot(2026, 1, round_fixtures(), path=prediction_path)
    exit_code = main([
        "readiness",
        "--data", str(tmp_path),  # no corpus here — must degrade, not crash
        "--db", str(tmp_path / "s.db"),
        "--ui-state", str(tmp_path / "ui_state.json"),
        "--prediction-history", str(prediction_path),
    ])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "No corpus found" in out
    assert "1 of ~20 rounds observed" in out
    assert "0 of ~200 confirmed absences" in out


def test_readiness_flags_missing_state_rather_than_reporting_a_bare_zero(tmp_path, capsys):
    """A zero because a file is missing must not look like a zero because
    nothing has happened yet — `db.connect` would even create the database
    it could not find, making the wrong answer look authoritative."""
    from super6.cli import main

    prediction_path = tmp_path / "history.json"
    prediction_history.record_snapshot(2026, 1, round_fixtures(), path=prediction_path)
    main([
        "readiness",
        "--data", str(tmp_path),
        "--db", str(tmp_path / "absent.db"),
        "--ui-state", str(tmp_path / "absent.json"),
        "--prediction-history", str(prediction_path),
    ])
    err = capsys.readouterr().err
    assert "UI state not found" in err
    assert "database not found" in err
    assert "incomplete, not merely empty" in err
    # The path that *does* exist must not be reported as missing.
    assert "prediction history not found" not in err
