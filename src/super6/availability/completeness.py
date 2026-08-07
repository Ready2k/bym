"""feature_completeness — the §20 CONF ingredient (Increment 3.8).

Team-news availability can now feed a real completeness assessment, and
the §62 data-quality states follow from it:

    GOOD       confirmed lineups in hand
    DEGRADED   expected lineup only, or market missing
    LIMITED    historical/team data only
    STALE      required data has not refreshed within tolerance

Still categorical. There is no `83.7 confidence units`, and there is not
going to be.
"""

from __future__ import annotations

from dataclasses import dataclass

from .lineups import latest
from .model import Lineup, LineupKind


GOOD = "GOOD"
DEGRADED = "DEGRADED"
LIMITED = "LIMITED"
STALE = "STALE"


@dataclass(frozen=True)
class CompletenessReport:
    state: str
    has_market: bool
    confirmed_lineups: int  # 0, 1 or 2 of the fixture's teams
    expected_lineups: int
    stale_market: bool
    notes: tuple[str, ...] = ()

    def describe(self) -> str:
        parts = [f"data quality {self.state}"]
        if self.confirmed_lineups:
            parts.append(f"{self.confirmed_lineups}/2 confirmed lineups")
        elif self.expected_lineups:
            parts.append(f"{self.expected_lineups}/2 expected lineups only")
        else:
            parts.append("no lineup information")
        parts.append("market available" if self.has_market else "no market view")
        return ", ".join(parts)


def assess(
    home_team: str,
    away_team: str,
    lineups: list[Lineup],
    has_market: bool,
    stale_market: bool = False,
) -> CompletenessReport:
    """Assess how complete this fixture's feature set is right now."""
    confirmed = sum(
        1
        for team in (home_team, away_team)
        if latest(lineups, team, LineupKind.CONFIRMED) is not None
    )
    expected = sum(
        1
        for team in (home_team, away_team)
        if latest(lineups, team, LineupKind.EXPECTED) is not None
    )

    notes = []
    if stale_market:
        state = STALE
        notes.append("market data has not refreshed within tolerance")
    elif not has_market and confirmed == 0 and expected == 0:
        state = LIMITED
        notes.append("historical/team data only")
    elif confirmed == 2 and has_market:
        state = GOOD
    else:
        state = DEGRADED
        if not has_market:
            notes.append("no market consensus available")
        if confirmed < 2:
            notes.append(
                "confirmed lineups incomplete"
                if confirmed
                else "no confirmed lineups yet"
            )
    return CompletenessReport(
        state=state,
        has_market=has_market,
        confirmed_lineups=confirmed,
        expected_lineups=expected,
        stale_market=stale_market,
        notes=tuple(notes),
    )


def completeness_factor(report: CompletenessReport) -> str:
    """The §20 `feature_completeness` CONF ingredient, as a category.

    HIGH means the forecast rests on everything we expect to have; LOW
    means it is running on partial information and CONF should say so
    (§59: system failures affect feature_completeness and therefore CONF).
    """
    return {GOOD: "HIGH", DEGRADED: "MEDIUM", LIMITED: "LOW", STALE: "LOW"}[
        report.state
    ]
