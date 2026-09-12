from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bym.fpl import review


def _dashboard(deadline: str = "2026-09-04T18:30:00+01:00") -> dict:
    players = [
        {
            "name": f"Player {index}", "position": "MID", "club": f"Club {index}",
            "is_starter": index <= 11, "is_captain": index == 1,
            "is_vice_captain": index == 2,
        }
        for index in range(1, 16)
    ]
    return {
        "status": "LINEUP_ONLY",
        "squad": {"deadline": deadline, "gameweek": 3, "players": players, "candidate_universe_complete": False},
        "source": {"label": "Manager-entered screen", "observed_at": "2026-09-01T09:00:00+01:00"},
        "lineup_estimate": {"captain": "Player 1", "downside": [{"title": "Wide uncertainty", "detail": "No live feed."}]},
        "shortlist": {"candidates": []},
        "phase2": {"plan": None},
        "phase3": {"plan": None},
        "recommendation": None,
    }


def test_review_history_is_append_only_and_marks_the_matching_current_decision(tmp_path):
    path = tmp_path / "fpl_decision_history.json"
    now = datetime(2026, 9, 1, 9, tzinfo=timezone.utc)

    first = review.record(path, _dashboard(), now=now)
    second = review.record(path, _dashboard(), now=now)
    result = review.payload(path, _dashboard())

    assert first["id"] != second["id"]
    assert len(result["reviews"]) == 2
    assert result["current_reviewed_at"] == second["reviewed_at"]
    assert result["reviews"][0]["risks"][0]["detail"] == "No live feed."


def test_review_refuses_a_new_predeadline_record_after_the_deadline(tmp_path):
    with pytest.raises(review.DecisionReviewError, match="after the deadline"):
        review.record(
            tmp_path / "history.json", _dashboard(),
            now=datetime(2026, 9, 5, tzinfo=timezone.utc),
        )


def test_actual_rival_picks_are_separate_postdeadline_evaluation_evidence(tmp_path):
    path = tmp_path / "history.json"
    source = review.record(
        path, _dashboard(), now=datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
    )
    squad = [f"Rival {index}" for index in range(1, 16)]

    with pytest.raises(review.DecisionReviewError, match="only after the deadline"):
        review.add_actual_rival_evidence(
            path, review_id=source["id"], rival="Mate", squad=squad,
            captain="Rival 1", observed_gameweek=3,
            observed_at="2026-09-03T12:00:00+01:00",
            now=datetime(2026, 9, 3, 12, tzinfo=timezone.utc),
        )

    entry = review.add_actual_rival_evidence(
        path, review_id=source["id"], rival="Mate", squad=squad,
        captain="Rival 1", observed_gameweek=3,
        observed_at="2026-09-04T19:00:00+01:00",
        now=datetime(2026, 9, 4, 19, tzinfo=timezone.utc),
    )
    result = review.payload(path, _dashboard())

    assert entry["use"] == "POST_DEADLINE_EVALUATION_ONLY"
    assert len(result["reviews"]) == 1
    assert result["actual_rival_evidence"][0]["review_id"] == source["id"]
