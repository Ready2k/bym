"""Deadline-semantics regression tests.

The gate that stands between a historical availability feature and the
front door. If any of these fail, an oracle study has quietly become a
time-travel study.
"""

from datetime import datetime, timedelta, timezone

import pytest

from bym.research import deadline
from bym.research.deadline import Knowability

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


    # The forecasting core. Research may import these; none of them may
    # import research. The CLI is deliberately excluded — it is a
    # top-level entry point that orchestrates both, not part of the core.
FORECAST_CORE = (
    "src/bym/engine.py",
    "src/bym/recommend.py",
    "src/bym/worker.py",
    "src/bym/config.py",
    "src/bym/model/dixon_coles.py",
    "src/bym/model/matrix.py",
    "src/bym/model/market.py",
    "src/bym/model/golden_goal.py",
    "src/bym/backtest/walk_forward.py",
    "src/bym/api/service.py",
    "src/bym/api/contracts.py",
)


def test_forecast_core_never_imports_research():
    """Research may use oracle information and may import the frozen
    core. The core must never import research, or future information
    acquires a path into a live forecast."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    offenders = []
    for relative in FORECAST_CORE:
        path = root / relative
        assert path.exists(), f"protected module missing: {relative}"
        text = path.read_text()
        if "research" in text and (
            "from .research" in text
            or "from ..research" in text
            or "import research" in text
            or "bym.research" in text
        ):
            offenders.append(relative)
    assert not offenders, f"forecast core importing research: {offenders}"


def test_research_may_import_the_frozen_core():
    """The permitted direction — asserted so nobody 'fixes' it later."""
    from bym.config import FROZEN_PHASE1_V1
    from bym.research import deadline

    assert FROZEN_PHASE1_V1.xi == 0.003
    assert deadline.LINEUP_LEAD_MINUTES == 20
