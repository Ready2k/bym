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

That ladder is for ODDS, and applying it to knowledge research was a
category error with a price. Odds move minute to minute and cost one
credit for all six fixtures. Team news changes maybe twice a week per
team, and a single sweep is 12 teams x 3 queries = 36 Tavily credits. On
the odds ladder a round costs 32 sweeps x 36 = 1,152 credits against a
1,000/month free tier — one round exhausting a month, for four or five
rounds a month. `KNOWLEDGE_LADDER` below is the news-shaped alternative:
six passes weighted toward the deadline, when lineups actually land.
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

# Knowledge research (§8). Six passes per round by design, weighted late:
# one a week out, one at three days, one at a day, then three inside the
# final six hours where confirmed lineups and late fitness news arrive.
# At 36 credits a sweep that is ~216 per round, so four to five rounds fit
# inside a 1,000/month tier with headroom for a manual refresh or two.
KNOWLEDGE_LADDER: tuple[tuple[timedelta, timedelta], ...] = (
    (timedelta(hours=6), timedelta(hours=2)),
    (timedelta(days=1), timedelta(hours=18)),
    (timedelta(days=3), timedelta(days=2)),
    (timedelta(days=7), timedelta(days=4)),
)
KNOWLEDGE_BASE_INTERVAL = timedelta(days=7)
KNOWLEDGE_DEGRADED_MIN_INTERVAL = timedelta(hours=12)
KNOWLEDGE_CREDITS_PER_PASS = 36  # 12 teams x 3 planned queries


def knowledge_interval(
    deadline: datetime,
    now: datetime | None = None,
    budget_constrained: bool = False,
) -> timedelta | None:
    """Interval until the next knowledge sweep, or None past the deadline.

    Separate from `poll_interval` because news and prices have different
    shapes and wildly different unit costs — see the module docstring.
    """
    return poll_interval(
        deadline,
        now,
        budget_constrained,
        ladder=KNOWLEDGE_LADDER,
        base=KNOWLEDGE_BASE_INTERVAL,
        degraded_min=KNOWLEDGE_DEGRADED_MIN_INTERVAL,
    )


def poll_interval(
    deadline: datetime,
    now: datetime | None = None,
    budget_constrained: bool = False,
    *,
    ladder: tuple[tuple[timedelta, timedelta], ...] | None = None,
    base: timedelta | None = None,
    degraded_min: timedelta | None = None,
) -> timedelta | None:
    """Interval until the next poll, or None once the deadline has passed."""
    ladder = ladder if ladder is not None else LADDER
    base = base if base is not None else BASE_INTERVAL
    degraded_min = degraded_min if degraded_min is not None else DEGRADED_MIN_INTERVAL
    now = now or datetime.now(timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    remaining = deadline - now
    if remaining <= timedelta(0):
        return None

    interval = base
    for bound, candidate in ladder:
        if remaining <= bound:
            interval = candidate
            break

    if budget_constrained and interval < degraded_min:
        return degraded_min
    return interval


def next_knowledge_at(
    deadline: datetime,
    now: datetime | None = None,
    budget_constrained: bool = False,
) -> datetime | None:
    """Timestamp of the next knowledge sweep, on the news ladder."""
    return next_poll_at(
        deadline, now, budget_constrained, interval_fn=knowledge_interval
    )


def next_poll_at(
    deadline: datetime,
    now: datetime | None = None,
    budget_constrained: bool = False,
    *,
    interval_fn=None,
) -> datetime | None:
    """Timestamp of the next poll, never later than the deadline itself."""
    now = now or datetime.now(timezone.utc)
    interval = (interval_fn or poll_interval)(deadline, now, budget_constrained)
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
