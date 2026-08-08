"""Super 6 deadline semantics (SPEC.md §9.1, §16, §22).

**The whole round locks at the first fixture's kick-off**, and nothing can
be changed afterwards. That single fact governs what any availability
feature is allowed to know.

Worked through:

```text
Round locks          15:00 Saturday   (first kick-off)

Fixture A  15:00 Sat   XI public ~14:30   -> KNOWABLE
Fixture B  15:00 Sat   XI public ~14:30   -> KNOWABLE
Fixture C  17:30 Sat   XI public ~17:00   -> FUTURE INFORMATION
Fixture D  14:00 Sun   XI public ~13:30   -> FUTURE INFORMATION
Fixture E  16:30 Sun   XI public ~16:00   -> FUTURE INFORMATION
Fixture F  20:00 Mon   XI public ~19:30   -> FUTURE INFORMATION
```

So a historical confirmed XI is decision-valid for roughly the fixtures
kicking off with the first one, and oracle-only for everything later.
Using all six and reporting "lineups improve predictions" would be time
travel wearing a tracksuit.

A second, subtler trap: a historical API response proves a lineup or
injury *exists*, not *when it became public*. API-Football supplies no
historical `published_at` for either. So even a same-kick-off fixture's
XI is only presumed knowable, on a documented lead time, never proven —
and this module labels that presumption rather than hiding it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

# API-Football documents confirmed XIs appearing 20-40 minutes before
# kick-off. The conservative end is used: assuming the earliest possible
# publication would manufacture knowability we cannot evidence.
LINEUP_LEAD_MINUTES = 20

# Injury reports refresh roughly every four hours and have no historical
# publication timestamp at all, so the same presumption applies with a
# much wider, and much less trustworthy, window.
INJURY_ASSUMED_LEAD_HOURS = 4


class Knowability(str, Enum):
    """Whether a piece of information could have been known at the lock."""

    KNOWABLE = "KNOWABLE"              # public before the round locked
    PRESUMED_KNOWABLE = "PRESUMED_KNOWABLE"  # within lead time, unproven
    FUTURE_INFORMATION = "FUTURE_INFORMATION"  # oracle only


class NoFixturesError(ValueError):
    """A round with no fixtures has no lock time."""


def round_lock(kickoffs: list[datetime]) -> datetime:
    """The round closes at the EARLIEST kick-off, not the latest."""
    if not kickoffs:
        raise NoFixturesError("a round needs at least one fixture to have a lock")
    normalised = [
        k.replace(tzinfo=timezone.utc) if k.tzinfo is None else k for k in kickoffs
    ]
    return min(normalised)


def lineup_public_at(
    kickoff: datetime, lead_minutes: int = LINEUP_LEAD_MINUTES
) -> datetime:
    """Earliest time a confirmed XI is assumed to have been public."""
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    return kickoff - timedelta(minutes=lead_minutes)


def classify_lineup(
    kickoff: datetime,
    lock: datetime,
    lead_minutes: int = LINEUP_LEAD_MINUTES,
) -> Knowability:
    """Could this fixture's confirmed XI have been known at the lock?

    Never returns KNOWABLE: without a historical publication timestamp the
    strongest honest verdict is PRESUMED_KNOWABLE.
    """
    if lock.tzinfo is None:
        lock = lock.replace(tzinfo=timezone.utc)
    public_at = lineup_public_at(kickoff, lead_minutes)
    if public_at <= lock:
        return Knowability.PRESUMED_KNOWABLE
    return Knowability.FUTURE_INFORMATION


def classify_injury_report(
    kickoff: datetime,
    lock: datetime,
    assumed_lead_hours: int = INJURY_ASSUMED_LEAD_HOURS,
) -> Knowability:
    """Injury reports carry no historical publication timestamp either."""
    if lock.tzinfo is None:
        lock = lock.replace(tzinfo=timezone.utc)
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    if kickoff - timedelta(hours=assumed_lead_hours) <= lock:
        return Knowability.PRESUMED_KNOWABLE
    return Knowability.FUTURE_INFORMATION


@dataclass(frozen=True)
class FixtureKnowability:
    home_team: str
    away_team: str
    kickoff: datetime
    lineup: str
    injury: str

    @property
    def lineup_is_oracle(self) -> bool:
        return self.lineup == Knowability.FUTURE_INFORMATION.value


@dataclass(frozen=True)
class RoundKnowability:
    lock: datetime
    fixtures: tuple[FixtureKnowability, ...]

    @property
    def decision_valid_lineups(self) -> int:
        return sum(1 for f in self.fixtures if not f.lineup_is_oracle)

    @property
    def oracle_only_lineups(self) -> int:
        return sum(1 for f in self.fixtures if f.lineup_is_oracle)

    def describe(self) -> str:
        lines = [
            f"Round locks at {self.lock.isoformat()} (first kick-off).",
            f"Confirmed XIs presumed knowable for "
            f"{self.decision_valid_lineups}/{len(self.fixtures)} fixtures; "
            f"{self.oracle_only_lineups} are FUTURE INFORMATION.",
        ]
        for f in self.fixtures:
            lines.append(
                f"  {f.home_team} v {f.away_team} @ "
                f"{f.kickoff.strftime('%a %H:%M')}: lineup={f.lineup}, "
                f"injury={f.injury}"
            )
        return "\n".join(lines)


def assess_round(
    fixtures: list[tuple[str, str, datetime]],
    lead_minutes: int = LINEUP_LEAD_MINUTES,
) -> RoundKnowability:
    """Classify every fixture in a round against that round's own lock."""
    lock = round_lock([k for _, _, k in fixtures])
    assessed = tuple(
        FixtureKnowability(
            home_team=home,
            away_team=away,
            kickoff=kickoff if kickoff.tzinfo else kickoff.replace(tzinfo=timezone.utc),
            lineup=classify_lineup(kickoff, lock, lead_minutes).value,
            injury=classify_injury_report(kickoff, lock).value,
        )
        for home, away, kickoff in fixtures
    )
    return RoundKnowability(lock=lock, fixtures=assessed)
