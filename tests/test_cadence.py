from datetime import datetime, timedelta, timezone

import pytest

from bym import cadence

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


# ---------------------------------------------------------------------------
# §8 knowledge research runs on a news cadence, not the odds one


def test_knowledge_research_is_six_sweeps_a_round_not_thirty_two():
    """Team news changes maybe twice a week per team and a sweep costs 36
    Tavily credits; odds move minute to minute and cost 1 for all six
    fixtures. On the odds ladder a single round cost 1,152 credits against
    a 1,000/month tier — one round exhausting a month."""
    from datetime import datetime, timedelta, timezone

    from bym import cadence

    deadline = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)

    def sweeps(interval_fn):
        now, count = deadline - timedelta(days=7), 0
        while (step := interval_fn(deadline, now)) is not None:
            count += 1
            now += step
            if count > 5000:
                break
        return count

    assert sweeps(cadence.knowledge_interval) == 6
    assert sweeps(cadence.poll_interval) == 32  # odds, unchanged


def test_a_round_of_research_fits_inside_the_monthly_tier():
    """Four to five rounds a month against Tavily's 1,000 free credits."""
    from bym import cadence

    per_round = 6 * cadence.KNOWLEDGE_CREDITS_PER_PASS
    assert per_round == 216
    assert per_round * 4 <= 1000, "four rounds must fit"


def test_knowledge_sweeps_are_weighted_toward_the_deadline():
    """Confirmed lineups land in the final hours — that is when a sweep is
    worth 36 credits, and a week out it is not."""
    from datetime import datetime, timedelta, timezone

    from bym import cadence

    deadline = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
    far = cadence.knowledge_interval(deadline, deadline - timedelta(days=6))
    near = cadence.knowledge_interval(deadline, deadline - timedelta(hours=5))
    assert far > near
    assert near <= timedelta(hours=2)


def test_a_low_budget_widens_the_knowledge_cadence_rather_than_stopping():
    """Going silent is the failure you notice last."""
    from datetime import datetime, timedelta, timezone

    from bym import cadence

    deadline = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
    now = deadline - timedelta(hours=5)
    normal = cadence.knowledge_interval(deadline, now)
    degraded = cadence.knowledge_interval(deadline, now, budget_constrained=True)
    assert degraded > normal
    assert degraded is not None, "degrade, never stop"


def test_the_odds_ladder_is_untouched():
    """This change must not alter odds polling, which is separately tuned
    and whose credits are a different order of magnitude."""
    from datetime import timedelta

    from bym import cadence

    assert cadence.LADDER[0] == (timedelta(minutes=90), timedelta(minutes=12))
    assert cadence.BASE_INTERVAL == timedelta(days=1)
    assert cadence.DEGRADED_MIN_INTERVAL == timedelta(minutes=30)
