"""§19 strategy-run snapshots and their link back to a forecast run."""

from __future__ import annotations

import json

import pytest

from super6 import prediction_history, strategy_history as sh


def record(path, **overrides):
    kwargs = {
        "scope": sh.SCOPE_ROUND,
        "mode": "NORMAL",
        "active": False,
        "raw_picks": ["1-0", "1-1"],
        "strategy_picks": ["1-0", "1-1"],
        "reason": "maximum-EV picks are already best",
        "path": path,
    }
    kwargs.update(overrides)
    return sh.record_strategy(2026, 1, **kwargs)


# ---------------------------------------------------------------------------
# the store


def test_a_decision_is_recorded_with_both_raw_and_strategy_picks(tmp_path):
    """§19 wants both sides: a strategy pick is only meaningful as a
    deviation from the raw one."""
    path = tmp_path / "strategy.json"
    snapshot = record(
        path, mode="DIFFERENTIATE", active=True,
        raw_picks=["1-0", "1-1"], strategy_picks=["2-0", "1-1"],
    )
    assert snapshot["version"] == 1
    assert snapshot["raw_picks"] == ["1-0", "1-1"]
    assert snapshot["strategy_picks"] == ["2-0", "1-1"]
    assert snapshot["active"] is True
    assert snapshot["changes"] == []  # nothing earlier to differ from


def test_scoreline_tuples_and_strings_are_stored_identically(tmp_path):
    """The UI passes "2-0"; the strategy modules pass (2, 0)."""
    path = tmp_path / "strategy.json"
    from_strings = record(path, strategy_picks=["2-0", "1-1"])
    from_tuples = record(
        path, scope=sh.SCOPE_SEASON, strategy_picks=[(2, 0), (1, 1)]
    )
    assert from_strings["strategy_picks"] == from_tuples["strategy_picks"] == ["2-0", "1-1"]


def test_an_unchanged_recommendation_does_not_create_a_new_version(tmp_path):
    """`_strategy_payload` runs on every page load; a refresh is not a
    decision and must not manufacture audit history."""
    path = tmp_path / "strategy.json"
    first = record(path)
    again = record(path)
    assert again["version"] == first["version"] == 1
    assert len(sh.round_snapshots(2026, 1, path)) == 1


def test_a_changed_recommendation_appends_a_version_with_a_diff(tmp_path):
    path = tmp_path / "strategy.json"
    record(path)
    changed = record(
        path, mode="DIFFERENTIATE", active=True, strategy_picks=["2-0", "1-1"]
    )
    assert changed["version"] == 2
    fields = {item["field"]: item for item in changed["changes"]}
    assert fields["mode"]["was"] == "NORMAL" and fields["mode"]["now"] == "DIFFERENTIATE"
    assert fields["active"]["now"] is True
    assert fields["strategy_picks"]["now"] == ["2-0", "1-1"]


def test_the_reason_text_alone_never_creates_a_version(tmp_path):
    """Reasons embed live probabilities, so they churn on every
    recomputation — versioning on them would bury real decision changes."""
    path = tmp_path / "strategy.json"
    record(path, reason="P(finish first) 41.2%")
    record(path, reason="P(finish first) 41.3%")
    assert len(sh.round_snapshots(2026, 1, path)) == 1


def test_round_and_season_scopes_version_independently(tmp_path):
    """The two horizons can legitimately disagree about the same round."""
    path = tmp_path / "strategy.json"
    record(path, scope=sh.SCOPE_ROUND)
    record(path, scope=sh.SCOPE_SEASON, mode="ATTACK", active=True,
           strategy_picks=["3-0", "1-1"])
    record(path, scope=sh.SCOPE_ROUND, mode="DIFFERENTIATE", active=True,
           strategy_picks=["2-0", "1-1"])

    rounds = sh.round_snapshots(2026, 1, path, scope=sh.SCOPE_ROUND)
    seasons = sh.round_snapshots(2026, 1, path, scope=sh.SCOPE_SEASON)
    assert [item["version"] for item in rounds] == [1, 2]
    assert [item["version"] for item in seasons] == [1]
    assert sh.latest(2026, 1, sh.SCOPE_SEASON, path)["mode"] == "ATTACK"
    assert sh.latest(2026, 1, sh.SCOPE_ROUND, path)["mode"] == "DIFFERENTIATE"


def test_an_unknown_scope_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="scope must be one of"):
        record(tmp_path / "s.json", scope="CUP")


def test_a_malformed_store_is_rejected_rather_than_silently_erased(tmp_path):
    path = tmp_path / "strategy.json"
    path.write_text(json.dumps({"schema_version": 999, "rounds": {}}))
    with pytest.raises(ValueError, match="schema version"):
        sh.load_history(path)


# ---------------------------------------------------------------------------
# §19's opponent_snapshot


def test_opponent_snapshot_records_what_was_known_at_decision_time():
    snapshot = sh.opponent_snapshot(
        ["Steve", "Dave", "dave"], field_complete=True,
        rounds_of_history=4, joined_picks=24,
    )
    assert snapshot["participants"] == 3
    assert snapshot["participant_names"] == ["Dave", "dave", "Steve"]  # casefold order
    assert snapshot["field_complete"] is True
    assert snapshot["joined_picks"] == 24


def test_a_partial_field_is_recorded_as_such(tmp_path):
    """A strategy computed against a partial field is a different claim
    from one computed against all of it."""
    path = tmp_path / "strategy.json"
    partial = sh.opponent_snapshot(["Dave"], field_complete=False)
    snapshot = record(path, opponent=partial)
    assert snapshot["opponent_snapshot"]["field_complete"] is False


def test_the_same_decision_against_new_opponent_data_is_a_new_version(tmp_path):
    """The field filling up changes what the decision *means*, even when
    the picks come out identical."""
    path = tmp_path / "strategy.json"
    record(path, opponent=sh.opponent_snapshot(["Dave"], field_complete=False))
    record(path, opponent=sh.opponent_snapshot(["Dave", "Steve"], field_complete=True))
    assert len(sh.round_snapshots(2026, 1, path)) == 2


# ---------------------------------------------------------------------------
# §19's forecast_run linkage


def test_a_decision_names_the_forecast_version_it_was_taken_against(tmp_path):
    path = tmp_path / "strategy.json"
    snapshot = record(path, forecast_version=3)
    assert snapshot["forecast_version"] == 3


def test_one_forecast_can_carry_several_strategy_decisions(tmp_path):
    """Rivals' picks arriving re-strategises the same forecast — the
    forecast has not moved, so the join must return both."""
    path = tmp_path / "strategy.json"
    record(path, forecast_version=2, opponent=sh.opponent_snapshot(["Dave"], field_complete=False))
    record(path, forecast_version=2, mode="DIFFERENTIATE", active=True,
           strategy_picks=["2-0", "1-1"],
           opponent=sh.opponent_snapshot(["Dave", "Steve"], field_complete=True))
    record(path, forecast_version=3, mode="DIFFERENTIATE", active=True,
           strategy_picks=["3-0", "1-1"])

    against_two = sh.for_forecast_version(2026, 1, 2, path)
    assert len(against_two) == 2
    assert [item["version"] for item in against_two] == [1, 2]
    assert len(sh.for_forecast_version(2026, 1, 3, path)) == 1
    assert sh.for_forecast_version(2026, 1, 99, path) == []


def test_the_link_resolves_a_real_forecast_snapshot(tmp_path):
    """End to end: §19's question — for this forecast run, what was the
    strategy pick? — answered by joining the two append-only stores."""
    forecast_path = tmp_path / "history.json"
    strategy_path = tmp_path / "strategy.json"
    prediction_history.record_snapshot(
        2026, 1,
        [{"home_team": "Arsenal", "away_team": "Everton", "result": "HOME", "score": "1-0"}],
        path=forecast_path,
    )
    forecast = prediction_history.round_snapshots(2026, 1, forecast_path)[-1]
    record(
        strategy_path, forecast_version=forecast["version"],
        mode="DIFFERENTIATE", active=True,
        raw_picks=["1-0"], strategy_picks=["2-0"],
    )

    linked = sh.for_forecast_version(2026, 1, forecast["version"], strategy_path)
    assert len(linked) == 1
    assert linked[0]["raw_picks"] == ["1-0"]  # what the model said
    assert linked[0]["strategy_picks"] == ["2-0"]  # what was recommended instead
    assert forecast["fixtures"][0]["score"] == "1-0"  # and the forecast agrees


def test_nothing_recorded_yet_reads_as_absent_not_as_a_default(tmp_path):
    path = tmp_path / "strategy.json"
    assert sh.round_snapshots(2026, 1, path) == []
    assert sh.latest(2026, 1, sh.SCOPE_ROUND, path) is None


def test_participant_order_is_stable_across_processes():
    """Names differing only by case tie under casefold, and `sorted` would
    then fall back to the set's hash-randomised iteration order. Since
    snapshots are compared for equality, that would append a spurious
    version on every rerun recording a decision nobody made."""
    import subprocess
    import sys

    program = (
        "from super6.strategy_history import opponent_snapshot;"
        "print(opponent_snapshot(['Steve','Dave','dave','ANN','ann'],"
        " field_complete=True)['participant_names'])"
    )
    seen = {
        subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, check=True
        ).stdout.strip()
        for _ in range(3)
    }
    assert len(seen) == 1, f"order varied across processes: {seen}"
