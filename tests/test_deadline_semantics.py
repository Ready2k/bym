"""Deadline-semantics regression tests.

The gate that stands between a historical availability feature and the
front door. If any of these fail, an oracle study has quietly become a
time-travel study.
"""

from datetime import datetime, timedelta, timezone

import pytest

from super6.research import deadline
from super6.research.deadline import Knowability

UTC = timezone.utc

# The worked example: round locks 15:00 Saturday, fixtures spread to Monday.
SATURDAY = datetime(2026, 8, 22, 15, 0, tzinfo=UTC)
ROUND = [
    ("Arsenal", "Everton", SATURDAY),
    ("Chelsea", "Newcastle", SATURDAY),
    ("Fulham", "Brentford", SATURDAY + timedelta(hours=2, minutes=30)),
    ("Leeds", "Tottenham", SATURDAY + timedelta(days=1, hours=-1)),
    ("Man City", "Bournemouth", SATURDAY + timedelta(days=1, hours=1, minutes=30)),
    ("West Ham", "Aston Villa", SATURDAY + timedelta(days=2, hours=5)),
]


def test_round_locks_at_the_first_kickoff_not_the_last():
    """Sky's rules: the whole round closes at the first fixture's
    kick-off. Using the last would licence hours of future information."""
    lock = deadline.round_lock([k for _, _, k in ROUND])
    assert lock == SATURDAY
    assert lock != max(k for _, _, k in ROUND)


def test_later_fixtures_confirmed_xi_is_future_information():
    assessed = deadline.assess_round(ROUND)
    by_team = {f.home_team: f for f in assessed.fixtures}

    # kicking off with the lock — XI presumed public ~20 min before
    assert by_team["Arsenal"].lineup == Knowability.PRESUMED_KNOWABLE.value
    assert by_team["Chelsea"].lineup == Knowability.PRESUMED_KNOWABLE.value

    # everything later is oracle-only
    for team in ("Fulham", "Leeds", "Man City", "West Ham"):
        assert by_team[team].lineup == Knowability.FUTURE_INFORMATION.value, team


def test_only_two_of_six_lineups_are_usable_in_the_worked_example():
    assessed = deadline.assess_round(ROUND)
    assert assessed.decision_valid_lineups == 2
    assert assessed.oracle_only_lineups == 4


def test_a_lineup_is_never_reported_as_proven_knowable():
    """A historical response proves the XI existed, not when it became
    public — so PRESUMED_KNOWABLE is the strongest honest verdict."""
    assessed = deadline.assess_round(ROUND)
    assert all(
        f.lineup != Knowability.KNOWABLE.value for f in assessed.fixtures
    )


def test_fixture_just_after_the_lock_is_still_future_information():
    """A fixture 25 minutes after the lock publishes its XI ~5 minutes
    after the round closed."""
    lock = SATURDAY
    kickoff = SATURDAY + timedelta(minutes=25)
    assert deadline.classify_lineup(kickoff, lock) == (
        Knowability.FUTURE_INFORMATION
    )


def test_fixture_exactly_at_the_lead_boundary_is_presumed_knowable():
    lock = SATURDAY
    kickoff = SATURDAY + timedelta(minutes=deadline.LINEUP_LEAD_MINUTES)
    assert deadline.classify_lineup(kickoff, lock) == (
        Knowability.PRESUMED_KNOWABLE
    )


def test_conservative_lead_time_is_used():
    """API-Football documents 20-40 minutes. Assuming 40 would
    manufacture knowability we cannot evidence."""
    assert deadline.LINEUP_LEAD_MINUTES == 20


def test_injuries_use_a_wider_and_weaker_window():
    assessed = deadline.assess_round(ROUND)
    by_team = {f.home_team: f for f in assessed.fixtures}
    # a 17:30 Saturday fixture is inside the 4h injury window...
    assert by_team["Fulham"].injury == Knowability.PRESUMED_KNOWABLE.value
    # ...but its confirmed XI is not
    assert by_team["Fulham"].lineup == Knowability.FUTURE_INFORMATION.value
    # next-day fixtures are outside even the injury window
    assert by_team["Leeds"].injury == Knowability.FUTURE_INFORMATION.value


def test_all_same_time_round_is_fully_usable():
    fixtures = [(f"H{i}", f"A{i}", SATURDAY) for i in range(6)]
    assessed = deadline.assess_round(fixtures)
    assert assessed.decision_valid_lineups == 6


def test_naive_datetimes_are_treated_as_utc():
    naive = [("H", "A", datetime(2026, 8, 22, 15, 0))]
    assessed = deadline.assess_round(naive)
    assert assessed.lock.tzinfo is not None


def test_empty_round_raises_rather_than_inventing_a_lock():
    with pytest.raises(deadline.NoFixturesError):
        deadline.round_lock([])


def test_description_names_the_oracle_fixtures():
    text = deadline.assess_round(ROUND).describe()
    assert "FUTURE INFORMATION" in text
    assert "first kick-off" in text


def test_research_package_is_not_imported_by_production():
    """Research may use oracle information; production may not import it."""
    import pkgutil

    import super6

    offenders = []
    for module in pkgutil.walk_packages(super6.__path__, "super6."):
        name = module.name
        if ".research" in name or name.endswith(".research"):
            continue
        try:
            source = __import__(name, fromlist=["_"]).__file__
        except Exception:
            continue
        if not source or "/research/" in source:
            continue
        with open(source) as handle:
            text = handle.read()
        if "research.deadline" in text or "from .research" in text:
            offenders.append(name)
    assert not offenders, f"production modules importing research: {offenders}"
