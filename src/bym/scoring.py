"""Super 6 scoring rules (SPEC.md §2) and fixture status (§2.2)."""

from __future__ import annotations

from enum import Enum

EXACT_POINTS = 5
RESULT_POINTS = 2


class FixtureStatus(str, Enum):
    """§2.2 — a VOID fixture contributes neither points nor performance
    statistics, but is logged rather than silently dropped."""

    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    POSTPONED = "POSTPONED"
    ABANDONED = "ABANDONED"
    VOID = "VOID"


def result_of(home_goals: int, away_goals: int) -> str:
    """Return 'H', 'D' or 'A' for a scoreline."""
    if home_goals > away_goals:
        return "H"
    if home_goals < away_goals:
        return "A"
    return "D"


def super6_points(
    pred_home: int, pred_away: int, actual_home: int, actual_away: int
) -> int:
    """§2: exact score = 5, correct result wrong score = 2, else 0."""
    if pred_home == actual_home and pred_away == actual_away:
        return EXACT_POINTS
    if result_of(pred_home, pred_away) == result_of(actual_home, actual_away):
        return RESULT_POINTS
    return 0
