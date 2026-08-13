"""Submission state machine (§46)."""

from datetime import datetime, timedelta, timezone

import pytest

from bym import submission
from bym.submission import SubmissionState

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
DEADLINE = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
PICKS = ["2-0", "1-0", "2-1", "1-2", "3-0", "1-2"]


@pytest.fixture
def path(tmp_path):
    return tmp_path / "submissions.json"


def test_starts_not_ready(path):
    assert submission.get(2026, 1, path).state == SubmissionState.NOT_READY.value


def test_review_makes_it_ready(path):
    record = submission.mark_reviewed(2026, 1, PICKS, NOW, path)
    assert record.state == SubmissionState.READY.value
    assert record.reviewed_picks == PICKS


def test_change_after_review_detected(path):
    submission.mark_reviewed(2026, 1, PICKS, NOW, path)
    changed = ["2-0", "1-1", "2-1", "1-2", "3-0", "1-2"]
    record = submission.reconcile(2026, 1, changed, DEADLINE, NOW, path)
    assert record.state == SubmissionState.CHANGED_SINCE_REVIEW.value
    assert record.drift == [{"index": 1, "was": "1-0", "now": "1-1"}]


def test_unchanged_after_review_stays_ready(path):
    submission.mark_reviewed(2026, 1, PICKS, NOW, path)
    record = submission.reconcile(2026, 1, PICKS, DEADLINE, NOW, path)
    assert record.state == SubmissionState.READY.value
    assert record.drift == []


def test_mark_submitted(path):
    record = submission.mark_submitted(2026, 1, PICKS, 9, NOW, path)
    assert record.state == SubmissionState.MARKED_AS_SUBMITTED.value
    assert record.submitted_golden_goal == 9
    assert record.submitted_at is not None


def test_change_after_submission_is_the_critical_state(path):
    submission.mark_submitted(2026, 1, PICKS, 9, NOW, path)
    changed = ["2-0", "1-1", "2-1", "1-2", "3-0", "1-2"]
    record = submission.reconcile(
        2026, 1, changed, DEADLINE, NOW + timedelta(minutes=30), path
    )
    assert record.state == SubmissionState.CHANGED_AFTER_SUBMISSION.value
    text = submission.describe(record)
    assert "entered 1-0" in text and "now recommended 1-1" in text
    assert "update these predictions in Super 6 yourself" in text


def test_unchanged_after_submission_stays_submitted(path):
    submission.mark_submitted(2026, 1, PICKS, 9, NOW, path)
    record = submission.reconcile(2026, 1, PICKS, DEADLINE, NOW, path)
    assert record.state == SubmissionState.MARKED_AS_SUBMITTED.value


def test_deadline_closes_the_round(path):
    submission.mark_submitted(2026, 1, PICKS, 9, NOW, path)
    record = submission.reconcile(
        2026, 1, PICKS, DEADLINE, DEADLINE + timedelta(minutes=1), path
    )
    assert record.state == SubmissionState.ROUND_CLOSED.value


def test_submission_beats_review_when_both_exist(path):
    """Once submitted, drift is measured against what was entered — the
    review baseline is no longer the thing that matters."""
    submission.mark_reviewed(2026, 1, PICKS, NOW, path)
    submitted = ["2-0", "2-0", "2-1", "1-2", "3-0", "1-2"]
    submission.mark_submitted(2026, 1, submitted, 9, NOW, path)
    record = submission.reconcile(2026, 1, PICKS, DEADLINE, NOW, path)
    assert record.state == SubmissionState.CHANGED_AFTER_SUBMISSION.value
    assert record.drift == [{"index": 1, "was": "2-0", "now": "1-0"}]


def test_no_wording_implies_the_system_submitted(path):
    for state_setup in [
        lambda: submission.mark_reviewed(2026, 1, PICKS, NOW, path),
        lambda: submission.mark_submitted(2026, 1, PICKS, 9, NOW, path),
    ]:
        record = state_setup()
        text = submission.describe(record).lower()
        assert "we submitted" not in text
        assert "submitted for you" not in text


def test_state_persists_across_reload(path):
    submission.mark_submitted(2026, 1, PICKS, 9, NOW, path)
    reloaded = submission.get(2026, 1, path)
    assert reloaded.state == SubmissionState.MARKED_AS_SUBMITTED.value
    assert reloaded.submitted_picks == PICKS


def test_rounds_are_independent(path):
    submission.mark_submitted(2026, 1, PICKS, 9, NOW, path)
    assert submission.get(2026, 2, path).state == SubmissionState.NOT_READY.value
