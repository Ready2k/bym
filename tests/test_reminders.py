"""Deadline reminders: fire once, escalate, and stop once the user acted."""

from datetime import datetime, timedelta, timezone

from super6 import events as ev
from super6 import reminders

DEADLINE = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
ROUND = 14


def at(**delta):
    return DEADLINE - timedelta(**delta)


def emitted(now, state="READY", existing=None):
    return reminders.build_events(DEADLINE, now, state, existing or [], ROUND)


def test_no_reminder_while_the_deadline_is_distant():
    assert emitted(at(days=3)) == []


def test_each_threshold_fires_as_it_is_crossed():
    for hours in (24, 6, 1):
        events = emitted(at(hours=hours, minutes=-1))
        assert len(events) == 1, f"{hours}h threshold did not fire"
        assert events[0].detail["threshold_hours"] == hours


def test_a_threshold_is_never_announced_twice():
    first = emitted(at(hours=5))
    assert first
    again = emitted(at(hours=4), existing=first)
    assert again == [], "refreshing the page re-emitted a reminder"


def test_only_the_most_urgent_missed_threshold_is_raised():
    """Someone opening the app two hours out needs 'two hours left', not
    also the 24- and 6-hour notices they already slept through."""
    events = emitted(at(hours=2))
    assert len(events) == 1
    assert events[0].detail["threshold_hours"] == 6

    later = emitted(at(minutes=30), existing=events)
    assert len(later) == 1
    assert later[0].detail["threshold_hours"] == 1


def test_reminders_stop_once_the_round_is_entered():
    assert emitted(at(minutes=30), state="MARKED_AS_SUBMITTED") == []
    assert emitted(at(minutes=30), state="ROUND_CLOSED") == []


def test_nothing_fires_after_the_deadline_has_passed():
    assert emitted(DEADLINE) == []
    assert emitted(DEADLINE + timedelta(minutes=5)) == []


def test_the_final_hour_escalates_to_critical():
    assert emitted(at(hours=20))[0].severity == ev.Severity.IMPORTANT.value
    assert emitted(at(minutes=30))[0].severity == ev.Severity.CRITICAL.value


def test_wording_distinguishes_never_entered_from_drifted():
    never = emitted(at(hours=5))[0].summary
    assert "not yet marked as entered" in never

    drifted = emitted(at(hours=5), state="CHANGED_AFTER_SUBMISSION")[0].summary
    assert "no longer match" in drifted


def test_time_remaining_is_stated_not_just_the_threshold():
    event = emitted(at(minutes=42))[0]
    assert "42m" in event.summary
    assert event.detail["minutes_remaining"] == 42
