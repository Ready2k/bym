from datetime import datetime, timedelta, timezone

import pytest

from bym.availability.model import (
    AvailabilityEvent,
    ClaimStatus,
    EventType,
    Reliability,
)
from bym.availability.reconcile import absentees, resolve_state

BASE = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)


def event(minutes, etype, status, reliability, source, player="Cole Palmer"):
    ts = (BASE + timedelta(minutes=minutes)).isoformat()
    return AvailabilityEvent(
        home_team="Chelsea",
        away_team="Newcastle",
        team="Chelsea",
        player_name=player,
        player_id="p1",
        event_type=etype.value,
        claim_status=status.value,
        source_id=source,
        source_reliability=reliability.value,
        source_timestamp=ts,
        observed_at=ts,
    )


def test_confirmation_beats_recency():
    """A 13:45 confirmed XI outranks a later low-status rumour."""
    events = [
        event(0, EventType.DOUBT, ClaimStatus.REPORTED, Reliability.MEDIUM, "journo"),
        event(225, EventType.CONFIRMED_STARTER, ClaimStatus.CONFIRMED, Reliability.HIGH, "club"),
        event(240, EventType.INJURY, ClaimStatus.RUMOUR, Reliability.LOW, "social"),
    ]
    state = resolve_state(events)[0]
    assert state.event_type == "CONFIRMED_STARTER"


def test_source_authority_breaks_ties_within_same_claim_level():
    events = [
        event(0, EventType.DOUBT, ClaimStatus.LIKELY, Reliability.LOW, "tabloid"),
        event(10, EventType.RETURN, ClaimStatus.LIKELY, Reliability.HIGH, "club"),
    ]
    assert resolve_state(events)[0].current.source_id == "club"


def test_recency_breaks_ties_last():
    events = [
        event(0, EventType.DOUBT, ClaimStatus.LIKELY, Reliability.HIGH, "club_am"),
        event(60, EventType.RETURN, ClaimStatus.LIKELY, Reliability.HIGH, "club_pm"),
    ]
    assert resolve_state(events)[0].current.source_id == "club_pm"


def test_spec_worked_example():
    """10:00 doubtful -> 12:30 club says available -> 13:45 confirmed XI."""
    events = [
        event(0, EventType.DOUBT, ClaimStatus.REPORTED, Reliability.MEDIUM, "journo"),
        event(150, EventType.RETURN, ClaimStatus.LIKELY, Reliability.HIGH, "club"),
        event(225, EventType.CONFIRMED_STARTER, ClaimStatus.CONFIRMED, Reliability.HIGH, "xi"),
    ]
    state = resolve_state(events)[0]
    assert state.event_type == "CONFIRMED_STARTER"
    assert len(state.history) == 3  # earlier evidence preserved


def test_history_is_preserved_not_overwritten():
    events = [
        event(0, EventType.DOUBT, ClaimStatus.REPORTED, Reliability.MEDIUM, "a"),
        event(60, EventType.INJURY, ClaimStatus.LIKELY, Reliability.MEDIUM, "b"),
        event(120, EventType.CONFIRMED_STARTER, ClaimStatus.CONFIRMED, Reliability.HIGH, "c"),
    ]
    state = resolve_state(events)[0]
    assert [e.source_id for e in state.history] == ["c", "b", "a"]


def test_conflicting_equal_authority_flagged():
    events = [
        event(0, EventType.CONFIRMED_ABSENT, ClaimStatus.CONFIRMED, Reliability.HIGH, "club"),
        event(5, EventType.CONFIRMED_STARTER, ClaimStatus.CONFIRMED, Reliability.HIGH, "league"),
    ]
    assert resolve_state(events)[0].has_conflict


def test_superseded_lower_claim_is_not_a_conflict():
    events = [
        event(0, EventType.INJURY, ClaimStatus.RUMOUR, Reliability.LOW, "social"),
        event(60, EventType.CONFIRMED_STARTER, ClaimStatus.CONFIRMED, Reliability.HIGH, "club"),
    ]
    assert not resolve_state(events)[0].has_conflict


def test_absentees_identified():
    events = [
        event(0, EventType.CONFIRMED_ABSENT, ClaimStatus.CONFIRMED, Reliability.HIGH, "club"),
        event(0, EventType.CONFIRMED_STARTER, ClaimStatus.CONFIRMED, Reliability.HIGH, "club",
              player="Enzo Fernandez"),
    ]
    events[1].player_id = "p2"
    out = absentees(resolve_state(events))
    assert [s.player_name for s in out] == ["Cole Palmer"]


def test_unresolved_players_do_not_merge_into_one_record():
    a = event(0, EventType.DOUBT, ClaimStatus.REPORTED, Reliability.LOW, "x", player="J. Williams")
    b = event(5, EventType.DOUBT, ClaimStatus.REPORTED, Reliability.LOW, "y", player="R. Sanchez")
    a.player_id = b.player_id = None
    assert len(resolve_state([a, b])) == 2
