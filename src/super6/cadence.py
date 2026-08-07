"""Reforecast/poll cadence (SPEC.md §9).

The published cadence, as a pure function of time-to-deadline so it can be
tested without a clock or a scheduler:

    7+ days      daily
    72 hours     every 6 hours
    24 hours     every 2-3 hours
    6 hours      hourly
    90 minutes   every 10-15 minutes

Two §25 budget rules are built in: polling stops once the deadline passes
(there is nothing left to decide), and the sub-90-minute cadence degrades
to 30-minute polling when the credit budget is close to exhausted, rather
than spending the last credits early and going blind at the deadline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# (upper bound on time-to-deadline, poll interval)
LADDER: tuple[tuple[timedelta, timedelta], ...] = (
    (timedelta(minutes=90), timedelta(minutes=12)),
    (timedelta(hours=6), timedelta(hours=1)),
    (timedelta(hours=24), timedelta(hours=2, minutes=30)),
    (timedelta(hours=72), timedelta(hours=6)),
    (timedelta(days=7), timedelta(hours=12)),
)
BASE_INTERVAL = timedelta(days=1)
DEGRADED_MIN_INTERVAL = timedelta(minutes=30)


def poll_interval(
    deadline: datetime,
    now: datetime | None = None,
    budget_constrained: bool = False,
) -> timedelta | None:
    """Interval until the next poll, or None once the deadline has passed."""
    now = now or datetime.now(timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    remaining = deadline - now
    if remaining <= timedelta(0):
        return None

    interval = BASE_INTERVAL
    for bound, candidate in LADDER:
        if remaining <= bound:
            interval = candidate
            break

    if budget_constrained and interval < DEGRADED_MIN_INTERVAL:
        return DEGRADED_MIN_INTERVAL
    return interval


def next_poll_at(
    deadline: datetime,
    now: datetime | None = None,
    budget_constrained: bool = False,
) -> datetime | None:
    """Timestamp of the next poll, never later than the deadline itself."""
    now = now or datetime.now(timezone.utc)
    interval = poll_interval(deadline, now, budget_constrained)
    if interval is None:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return min(now + interval, deadline)


def estimate_credits(
    deadline: datetime,
    start: datetime,
    divisions: int = 2,
    budget_constrained: bool = False,
) -> int:
    """Credits a single round would consume from `start` to its deadline,
    at `divisions` credits per cycle. Used to check a round's cost against
    the monthly budget before committing to it (§25)."""
    now = start
    cycles = 0
    while True:
        interval = poll_interval(deadline, now, budget_constrained)
        if interval is None:
            break
        cycles += 1
        now = now + interval
        if cycles > 10_000:  # guard against a pathological ladder change
            break
    return cycles * divisions
