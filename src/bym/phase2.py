"""Deterministic Phase 2 Super 6 intelligence arm.

Phase 2 is an *arm*, not a replacement for the frozen forecast.  It takes
the frozen recommendation and validated availability findings and may choose
another scoreline already present in that recommendation's score matrix.
It never asks an LLM for probabilities, goal rates, or a scoreline.

Contract
--------
* Inputs are ``FixtureRecommendation`` objects and promoted,
  reconciled ``AvailabilityEvent`` objects.  Candidate claims are not an
  input to this module.
* Only a resolved, confirmed key-player absence is actionable.  A rumour,
  doubt, unresolved player, retracted event, non-key player, or conflicting
  state is ignored.
* If the frozen pick names the affected team as the winner, that result is
  vetoed.  The highest-EV scoreline from the frozen matrix whose result is
  not that team is selected, with stable scoreline tie-breaking.
* All other fixtures retain the frozen pick.  The returned decisions include
  an audit reason so the caller can record why the arm differed.

This is deliberately conservative: validated knowledge changes a discrete
selection only when it meets the explicit contract; it does not alter the
probability distribution that produced the baseline recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

import numpy as np

from .availability.model import AvailabilityEvent
from .availability.reconcile import resolve_state
from .recommend import FixtureRecommendation, RoundRecommendation


@dataclass(frozen=True)
class Phase2Decision:
    """One auditable Phase 2 decision for a fixture."""

    home: str
    away: str
    pick: tuple[int, int]
    baseline_pick: tuple[int, int]
    changed: bool
    reason: str


def _result(score: tuple[int, int]) -> str:
    home, away = score
    if home > away:
        return "H"
    if away > home:
        return "A"
    return "D"


def _actionable_absent_teams(
    findings: Iterable[AvailabilityEvent],
) -> frozenset[str]:
    """Return teams with an unambiguous, actionable absence.

    Reconciliation is repeated here at the arm boundary so callers cannot
    accidentally pass a lower-priority historical event and have it change a
    pick.  ``resolve_state`` also exposes same-priority conflicts; those are
    not actionable until resolved upstream.
    """
    findings = tuple(findings)
    if any(not isinstance(item, AvailabilityEvent) for item in findings):
        raise TypeError("Phase 2 accepts validated AvailabilityEvent objects only")

    teams: set[str] = set()
    for state in resolve_state(list(findings)):
        event = state.current
        if (
            event.resolution == "RESOLVED"
            and event.claim_status == "CONFIRMED"
            and event.event_type == "CONFIRMED_ABSENT"
            and event.importance == "KEY"
            and not state.has_conflict
        ):
            teams.add(state.team)
    return frozenset(teams)


def _best_scoreline_avoiding(
    recommendation: FixtureRecommendation, forbidden_result: str
) -> tuple[int, int]:
    """Choose from the frozen matrix, without creating new probabilities."""
    ev = recommendation.matrix.ev_matrix()
    candidates: list[tuple[float, int, int]] = []
    for home, away in zip(*np.where(ev >= 0)):
        score = (int(home), int(away))
        if _result(score) != forbidden_result:
            candidates.append((float(ev[home, away]), score[0], score[1]))
    if not candidates:  # pragma: no cover - a score matrix always has draws
        return recommendation.pick_home, recommendation.pick_away
    _, home, away = max(candidates, key=lambda item: (item[0], -item[1], -item[2]))
    return home, away


def decide_fixture(
    recommendation: FixtureRecommendation,
    findings: Iterable[AvailabilityEvent] = (),
) -> Phase2Decision:
    """Apply the Phase 2 contract to one frozen fixture recommendation."""
    baseline = (recommendation.pick_home, recommendation.pick_away)
    absent = _actionable_absent_teams(findings)
    baseline_result = _result(baseline)
    affected_team = (
        recommendation.home if baseline_result == "H" else
        recommendation.away if baseline_result == "A" else None
    )
    if affected_team is None or affected_team not in absent:
        return Phase2Decision(
            recommendation.home, recommendation.away, baseline, baseline,
            False, "No actionable confirmed key absence against the baseline result.",
        )

    pick = _best_scoreline_avoiding(recommendation, baseline_result)
    return Phase2Decision(
        recommendation.home, recommendation.away, pick, baseline, pick != baseline,
        f"Vetoed baseline {baseline[0]}-{baseline[1]}: confirmed key absence for "
        f"{affected_team}; selected highest-EV frozen-matrix alternative.",
    )


def decide_round(
    recommendation: RoundRecommendation,
    findings_by_fixture: Mapping[tuple[str, str], Iterable[AvailabilityEvent]] | None = None,
) -> tuple[Phase2Decision, ...]:
    """Return the separate, pre-deadline Phase 2 decision set."""
    findings_by_fixture = findings_by_fixture or {}
    return tuple(
        decide_fixture(fixture, findings_by_fixture.get((fixture.home, fixture.away), ()))
        for fixture in recommendation.fixtures
    )


def picks_for_round(
    recommendation: RoundRecommendation,
    findings_by_fixture: Mapping[tuple[str, str], Iterable[AvailabilityEvent]] | None = None,
) -> list[tuple[int, int]]:
    """Return picks in the shape required by ``prospective.record_picks``."""
    return [decision.pick for decision in decide_round(recommendation, findings_by_fixture)]
