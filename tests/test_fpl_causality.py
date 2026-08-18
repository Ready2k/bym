from datetime import datetime, timedelta, timezone

import pytest

from bym.fpl import causality as c

DEADLINE = datetime(2026, 9, 12, 10, 30, tzinfo=timezone.utc)


def test_observation_strictly_before_deadline_is_causal():
    assert c.is_causal(DEADLINE - timedelta(minutes=1), DEADLINE)


def test_observation_at_deadline_is_not_causal():
    """§2: information first published at the deadline instant itself is
    future information for that Gameweek, not a pre-deadline input."""
    assert not c.is_causal(DEADLINE, DEADLINE)


def test_observation_after_deadline_is_not_causal():
    assert not c.is_causal(DEADLINE + timedelta(minutes=1), DEADLINE)


def test_assert_causal_passes_silently_for_pre_deadline_observation():
    c.assert_causal(DEADLINE - timedelta(hours=1), DEADLINE, context="test")


def test_assert_causal_raises_for_post_deadline_observation():
    with pytest.raises(c.LookaheadViolation, match="post-deadline evidence"):
        c.assert_causal(DEADLINE + timedelta(minutes=30), DEADLINE, context="confirmed lineup")


def test_confirmed_lineup_published_after_deadline_cannot_back_that_gameweek():
    """§11.1: confirmed lineups are post-deadline evidence for FPL and must
    not alter the recommendation already frozen for that Gameweek."""
    published_after_deadline = DEADLINE + timedelta(minutes=20)
    with pytest.raises(c.LookaheadViolation):
        c.assert_causal(published_after_deadline, DEADLINE, context="confirmed lineup: Arsenal")


@pytest.mark.parametrize("field_name", sorted(c.LOOKAHEAD_RISK_FIELDS))
def test_lookahead_field_without_timestamp_is_refused(field_name):
    """An absent observation timestamp is not evidence a forward-looking
    field was available in time — the ordinary shape of a vaastav CSV."""
    with pytest.raises(c.LookaheadViolation, match="no observation timestamp"):
        c.assert_no_unproven_lookahead_field(field_name, None, DEADLINE)


def test_lookahead_field_with_pre_deadline_timestamp_is_permitted():
    c.assert_no_unproven_lookahead_field("xP", DEADLINE - timedelta(days=1), DEADLINE)


def test_lookahead_field_with_post_deadline_timestamp_is_refused():
    with pytest.raises(c.LookaheadViolation):
        c.assert_no_unproven_lookahead_field("ep_this", DEADLINE + timedelta(minutes=1), DEADLINE)


def test_non_lookahead_field_skips_the_check_even_without_a_timestamp():
    """Only the named forward-looking fields are lookahead risks; an
    observed match stat like `minutes` needs no timestamp proof here."""
    c.assert_no_unproven_lookahead_field("minutes", None, DEADLINE)


@pytest.mark.parametrize(
    "status", ["UPCOMING", "LOCKED", "IN_PROGRESS", "PROVISIONAL"]
)
def test_non_final_gameweek_refuses_permanent_evaluation_write(status):
    """§12.1: a PROVISIONAL result may be shown but is not ground truth —
    BPS/DefCon figures can still move during Opta's review window."""
    with pytest.raises(c.GameweekNotFinalError, match=status):
        c.assert_final(c.GameweekSettlement(status=status), context="ledger write")


def test_final_gameweek_permits_permanent_evaluation_write():
    c.assert_final(c.GameweekSettlement(status="FINAL"), context="ledger write")
