from datetime import datetime, timedelta, timezone

import pytest

from super6 import cadence

DEADLINE = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)


def at(**kwargs):
    return DEADLINE - timedelta(**kwargs)


def test_cadence_ladder_matches_spec_table():
    assert cadence.poll_interval(DEADLINE, at(days=10)) == timedelta(days=1)
    assert cadence.poll_interval(DEADLINE, at(days=5)) == timedelta(hours=12)
    assert cadence.poll_interval(DEADLINE, at(hours=48)) == timedelta(hours=6)
    assert cadence.poll_interval(DEADLINE, at(hours=12)) == timedelta(hours=2, minutes=30)
    assert cadence.poll_interval(DEADLINE, at(hours=3)) == timedelta(hours=1)
    assert cadence.poll_interval(DEADLINE, at(minutes=45)) == timedelta(minutes=12)


def test_intervals_shorten_monotonically():
    offsets = [timedelta(days=10), timedelta(days=5), timedelta(hours=48),
               timedelta(hours=12), timedelta(hours=3), timedelta(minutes=45)]
    intervals = [cadence.poll_interval(DEADLINE, DEADLINE - o) for o in offsets]
    assert intervals == sorted(intervals, reverse=True)


def test_polling_stops_after_deadline():
    assert cadence.poll_interval(DEADLINE, DEADLINE) is None
    assert cadence.poll_interval(DEADLINE, DEADLINE + timedelta(minutes=1)) is None
    assert cadence.next_poll_at(DEADLINE, DEADLINE) is None


def test_budget_constraint_degrades_fine_cadence():
    """§25: degrade to 30-minute polling rather than going blind later."""
    normal = cadence.poll_interval(DEADLINE, at(minutes=45))
    degraded = cadence.poll_interval(DEADLINE, at(minutes=45), budget_constrained=True)
    assert normal == timedelta(minutes=12)
    assert degraded == timedelta(minutes=30)


def test_budget_constraint_does_not_lengthen_coarse_cadence():
    for offset in [timedelta(days=5), timedelta(hours=12), timedelta(hours=3)]:
        assert cadence.poll_interval(
            DEADLINE, DEADLINE - offset, budget_constrained=True
        ) == cadence.poll_interval(DEADLINE, DEADLINE - offset)


def test_next_poll_never_overshoots_deadline():
    nxt = cadence.next_poll_at(DEADLINE, at(minutes=5))
    assert nxt == DEADLINE


def test_round_credit_estimate_fits_free_tier():
    """A week of polling both divisions must fit comfortably in 500
    credits/month across several rounds (§25)."""
    per_round = cadence.estimate_credits(DEADLINE, DEADLINE - timedelta(days=7), divisions=2)
    assert per_round < 100
    assert per_round * 5 < 500  # five rounds in a busy month


def test_naive_datetimes_accepted():
    naive_deadline = DEADLINE.replace(tzinfo=None)
    assert cadence.poll_interval(naive_deadline, at(hours=3).replace(tzinfo=None))
