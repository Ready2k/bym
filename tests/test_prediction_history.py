"""Append-only forecast snapshots support UI prediction history."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from super6 import prediction_history


START = datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc)


def fixtures(score="2-0", confidence="HIGH", expected_points=2.151, alternatives=None):
    return [
        {
            "home_team": "Arsenal",
            "away_team": "Everton",
            "pick": score,
            "confidence": confidence,
            "expected_points": expected_points,
            "alternatives": alternatives
            if alternatives is not None
            else [
                {"score": score, "score_probability": 0.15, "expected_points": expected_points},
                {"score": "1-0", "score_probability": 0.11, "expected_points": 1.42},
            ],
        }
    ]


@pytest.fixture
def path(tmp_path):
    return tmp_path / "prediction_history.json"


def test_snapshots_are_append_only_and_keep_full_fixture_values(path):
    first = prediction_history.record_snapshot(2026, 1, fixtures(), now=START, path=path)
    second = prediction_history.record_snapshot(
        2026, 1, fixtures("1-1", "MEDIUM", 1.999), now=START + timedelta(hours=1), path=path
    )

    snapshots = prediction_history.round_snapshots(2026, 1, path)
    assert [snapshot["version"] for snapshot in snapshots] == [1, 2]
    assert snapshots[0]["fixtures"][0]["score"] == "2-0"
    assert snapshots[1]["fixtures"][0]["score"] == "1-1"
    assert first["changes"] == []
    assert second["changes"][0]["kind"] == "CHANGED"


def test_diff_captures_result_score_confidence_ev_and_alternatives(path):
    prediction_history.record_snapshot(2026, 1, fixtures(), now=START, path=path)
    later = prediction_history.record_snapshot(
        2026,
        1,
        fixtures(
            "0-1",
            "LOW",
            1.877,
            alternatives=[{"score": "0-1", "score_probability": 0.17, "expected_points": 1.877}],
        ),
        now=START + timedelta(minutes=5),
        path=path,
    )

    fields = {change["field"]: change for change in later["changes"][0]["fields"]}
    assert fields["result"] == {"field": "result", "was": "HOME", "now": "AWAY"}
    assert fields["score"]["was"] == "2-0" and fields["score"]["now"] == "0-1"
    assert fields["confidence"]["now"] == "LOW"
    assert fields["expected_points"] == {"field": "expected_points", "was": 2.151, "now": 1.877}
    assert fields["alternatives"]["now"] == [
        {"score": "0-1", "score_probability": 0.17, "expected_points": 1.877}
    ]


def test_fixture_history_exposes_versions_and_relevant_diffs_only(path):
    prediction_history.record_snapshot(2026, 1, fixtures(), now=START, path=path)
    prediction_history.record_snapshot(
        2026, 1, fixtures("2-1"), now=START + timedelta(minutes=5), path=path
    )

    history = prediction_history.fixture_history(2026, 1, "Arsenal::Everton", path)
    assert [item["version"] for item in history] == [1, 2]
    assert history[0]["changes"] == []
    assert {field["field"] for field in history[1]["changes"][0]["fields"]} == {
        "score", "alternatives"
    }


def test_changes_since_last_visit_only_returns_new_snapshots(path):
    prediction_history.record_snapshot(2026, 1, fixtures(), now=START, path=path)
    assert prediction_history.changes_for_visit(2026, 1, now=START, path=path) == []

    prediction_history.record_snapshot(
        2026, 1, fixtures("1-1"), now=START + timedelta(minutes=20), path=path
    )
    changes = prediction_history.changes_since_last_visit(2026, 1, path=path)
    assert len(changes) == 1
    assert changes[0]["fixture_key"] == "Arsenal::Everton"
    assert prediction_history.changes_for_visit(
        2026, 1, now=START + timedelta(minutes=25), path=path
    ) == changes
    assert prediction_history.changes_since_last_visit(2026, 1, path=path) == []


def test_round_summary_contract_is_accepted_and_normalised(path):
    snapshot = prediction_history.capture_round_summary(
        2026,
        {
            "round_number": 4,
            "model_version": "PHASE1_MODEL_V1",
            "fixtures": fixtures("2-0"),
        },
        now=START,
        path=path,
    )
    assert snapshot["model_version"] == "PHASE1_MODEL_V1"
    assert snapshot["fixtures"][0]["expected_points"] == 2.151
    assert prediction_history.current_score_picks(2026, 4, path) == ["2-0"]


def test_duplicate_fixture_key_is_rejected(path):
    with pytest.raises(ValueError, match="duplicate fixture keys"):
        prediction_history.record_snapshot(
            2026, 1, fixtures() + fixtures(), now=START, path=path
        )


# ---------------------------------------------------------------------------
# §19 reproducibility: basis + market/availability inputs preserved per fixture


def _fixture_with_inputs(basis="PHASE1_MODEL_V1", market=None, availability=None):
    fixture = fixtures()[0]
    fixture["forecast_basis"] = basis
    if market is not None:
        fixture["market"] = market
    if availability is not None:
        fixture["availability"] = availability
    return [fixture]


def test_normalise_fixture_preserves_basis_and_market_and_availability_inputs(path):
    market = {
        "available": True, "home_probability": 0.62, "draw_probability": 0.24,
        "away_probability": 0.14, "bookmaker_count": 12, "market_agreement": "HIGH",
        "stale": False,
    }
    availability = {
        "confirmed_lineups": 1, "expected_lineups": 0, "data_quality": "GOOD",
        "feature_completeness": "HIGH", "items": [{"team": "Everton"}],
        "quantitative_impact": "NONE DIRECTLY",
    }
    snapshot = prediction_history.record_snapshot(
        2026, 1, _fixture_with_inputs(market=market, availability=availability),
        now=START, path=path,
    )
    fixture = snapshot["fixtures"][0]
    assert fixture["basis"] == "PHASE1_MODEL_V1"
    assert fixture["market_snapshot"] == {
        "home_probability": 0.62, "draw_probability": 0.24, "away_probability": 0.14,
        "bookmaker_count": 12, "market_agreement": "HIGH", "stale": False,
    }
    assert fixture["availability_snapshot"] == {
        "confirmed_lineups": 1, "expected_lineups": 0, "data_quality": "GOOD",
        "feature_completeness": "HIGH", "item_count": 1,
        "quantitative_impact": "NONE DIRECTLY",
    }


def test_normalise_fixture_records_unavailable_market_as_none_not_zeroes(path):
    snapshot = prediction_history.record_snapshot(
        2026, 1, _fixture_with_inputs(market={"available": False}), now=START, path=path,
    )
    assert snapshot["fixtures"][0]["market_snapshot"] is None


def test_a_newly_tracked_field_does_not_spuriously_diff_an_existing_round(path):
    """Regression: adding a field to _TRACKED_FIELDS must not make every
    already-running round report a one-off fake "changed" the next time it
    is recomputed, just because the field is new rather than the value
    having actually moved."""
    # Simulate a round recorded before "basis" existed in normalise_fixture
    # by writing a snapshot with no basis key at all, the way an old file
    # on disk would have it.
    plain_fixture = fixtures()[0]
    first = prediction_history.record_snapshot(2026, 1, [plain_fixture], now=START, path=path)
    stored = json.loads(path.read_text())
    del stored["rounds"]["2026:1"]["snapshots"][0]["fixtures"][0]["basis"]
    path.write_text(json.dumps(stored))

    second = prediction_history.record_snapshot(
        2026, 1, _fixture_with_inputs(basis="PHASE1_MODEL_V1"),
        now=START + timedelta(hours=1), path=path,
    )
    assert second["changes"] == []


def test_basis_change_alone_is_tracked_as_a_diff(path):
    prediction_history.record_snapshot(
        2026, 1, _fixture_with_inputs(basis="PHASE1_MODEL_V1"), now=START, path=path,
    )
    second = prediction_history.record_snapshot(
        2026, 1, _fixture_with_inputs(basis="MARKET_ONLY_FALLBACK"),
        now=START + timedelta(hours=1), path=path,
    )
    fields = {f["field"] for f in second["changes"][0]["fields"]}
    assert "basis" in fields
