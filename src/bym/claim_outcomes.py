"""Pairing recorded team-news claims with what actually happened (§8, Increment 5).

The Evidence → λ model — turning "striker ruled out" into an adjustment to
`λ_home`/`λ_away` — is the largest unbuilt piece of the spec and the
riskiest to get wrong: a bad impact model degrades forecasts rather than
merely failing to help. `availability/impact.py` therefore has no route
from an event to a parameter at all
(`QUANTITATIVE_IMPACT_MODEL_ENABLED = False`).

Step 1 of unblocking it is "accumulate enough (claim, outcome) history to
fit something real — e.g. regressing goal output against confirmed
absences". Both halves were already being recorded and neither was joined
to the other: `candidate_claims` holds what was claimed about a fixture,
`prediction_history` holds which fixture that was, and the recorded
results hold what the fixture finished. This module is the join, and
therefore the thing that makes the data start accruing.

It fits nothing and adjusts nothing. There is no model here, and this
module must never grow one: a fitted impact model needs its own
versioning and walk-forward backtesting (§29-32) and must beat "no
adjustment" out of sample (§28) before it goes anywhere near a forecast.
What this gives you is the dataset that argument will eventually be had
over, plus an honest count of how far away that is.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from . import prediction_history
from .league import ResultKey, Scoreline

# A claim the extractor recorded but the validator rejected says nothing
# about the fixture, so it is excluded by default rather than padding the
# sample. Pass `verdict=None` to pull everything, e.g. to audit rejections.
DEFAULT_VERDICT = "ACCEPTED"

# Confirmed absences before regressing goal output against them is worth
# attempting. Deliberately large: the effect being estimated is small, the
# outcome (goals) is noisy, and §28 requires beating "no adjustment" out of
# sample — an underpowered fit that clears an in-sample threshold is
# exactly the failure mode that would degrade forecasts. Nothing enforces
# this number; it exists so "is there enough yet?" has a written answer.
ABSENCES_FOR_FITTING = 200


@dataclass(frozen=True)
class ClaimOutcome:
    """One recorded claim, matched to the result of the fixture it was about."""

    claim_id: int
    season: int
    round_number: int
    fixture_index: int
    home_team: str
    away_team: str
    team: str | None  # the team the claim concerns
    player_name: str | None
    claim_type: str
    availability: str | None
    certainty: str | None
    verdict: str
    observed_at: str
    team_was_home: bool | None  # None: the claim names no team, or not one of these two
    team_goals: int | None
    opponent_goals: int | None

    @property
    def is_confirmed_absence(self) -> bool:
        """The signal §8's worked example is about — a player confirmed out,
        as opposed to a doubt, a rumour or a return."""
        return self.availability == "CONFIRMED_ABSENT"


def _fixture_lookup(
    prediction_path: Path,
) -> dict[tuple[int, str, str], tuple[int, int] | None]:
    """(round_number, home, away) → (season, fixture_index).

    Claims carry a round number but no season, so the season is recovered
    from the forecast that named the same fixture in the same round. A key
    that occurs in more than one season maps to `None` and is dropped at
    join time: guessing which season a claim belonged to would silently
    attach team news to the wrong match.
    """
    store = prediction_history.load_history(prediction_path)
    lookup: dict[tuple[int, str, str], tuple[int, int] | None] = {}
    for data in store.get("rounds", {}).values():
        snapshots = data.get("snapshots") or []
        if not snapshots:
            continue
        season, round_number = int(data["season"]), int(data["round_number"])
        for fixture in snapshots[-1]["fixtures"]:
            home, away = fixture.get("home_team"), fixture.get("away_team")
            if not home or not away:
                continue
            key = (round_number, str(home), str(away))
            found = (season, int(fixture["fixture_index"]))
            if lookup.get(key, found) != found:
                lookup[key] = None  # same fixture, two seasons — ambiguous
            else:
                lookup.setdefault(key, found)
    return lookup


def claim_outcomes(
    conn: sqlite3.Connection,
    results: Mapping[ResultKey, Scoreline | None],
    *,
    prediction_path: Path = prediction_history.DEFAULT_HISTORY_PATH,
    verdict: str | None = DEFAULT_VERDICT,
) -> tuple[ClaimOutcome, ...]:
    """Every recorded claim whose fixture has since been scored.

    A claim contributes nothing until all three of its parts exist — the
    claim, a forecast naming that fixture, and a recorded result. Claims
    still waiting on any of those are skipped rather than carried with
    blanks, so the returned length is exactly "pairs available to fit on".
    """
    lookup = _fixture_lookup(prediction_path)
    sql = "SELECT * FROM candidate_claims WHERE home_team IS NOT NULL AND round_number IS NOT NULL"
    params: tuple = ()
    if verdict is not None:
        sql += " AND verdict = ?"
        params = (verdict,)
    rows = conn.execute(sql + " ORDER BY observed_at, id", params).fetchall()

    outcomes: list[ClaimOutcome] = []
    for row in rows:
        located = lookup.get(
            (int(row["round_number"]), str(row["home_team"]), str(row["away_team"]))
        )
        if located is None:  # never forecast, or ambiguous across seasons
            continue
        season, fixture_index = located
        score = results.get((season, int(row["round_number"]), fixture_index))
        if score is None:
            continue  # not played, or not recorded yet
        home_goals, away_goals = score
        team = row["team"]
        if team == row["home_team"]:
            was_home, team_goals, opponent_goals = True, home_goals, away_goals
        elif team == row["away_team"]:
            was_home, team_goals, opponent_goals = False, away_goals, home_goals
        else:
            # A claim with no team, or naming a team not in this fixture.
            # Kept — it is still evidence about the fixture — but with no
            # side attributed, because inventing one would corrupt exactly
            # the regression this data exists to support.
            was_home = team_goals = opponent_goals = None
        outcomes.append(
            ClaimOutcome(
                claim_id=int(row["id"]),
                season=season,
                round_number=int(row["round_number"]),
                fixture_index=fixture_index,
                home_team=str(row["home_team"]),
                away_team=str(row["away_team"]),
                team=team,
                player_name=row["player_name"],
                claim_type=str(row["claim_type"]),
                availability=row["availability"],
                certainty=row["certainty"],
                verdict=str(row["verdict"]),
                observed_at=str(row["observed_at"]),
                team_was_home=was_home,
                team_goals=team_goals,
                opponent_goals=opponent_goals,
            )
        )
    return tuple(outcomes)


@dataclass(frozen=True)
class ImpactEvidence:
    """How much there is to fit an impact model on — the honest headline."""

    pairs: int  # claims matched to a played fixture
    attributed: int  # ...of those, ones naming a team in the fixture
    confirmed_absences: int  # ...of those, the CONFIRMED_ABSENT subset
    rounds: int
    teams: int
    mean_goals_with_absence: float | None
    mean_goals_without_absence: float | None


def impact_evidence(outcomes: tuple[ClaimOutcome, ...]) -> ImpactEvidence:
    """Summarise the accumulated pairs.

    The two mean-goals figures are descriptive only, and will be noise for
    a long time. They are *not* an effect estimate: no controls, no
    opponent strength, no per-player weighting, and the teams with recorded
    absences are not a random sample of teams. They are here so the shape
    of the eventual regression is visible while the data accrues.
    """
    attributed = [item for item in outcomes if item.team_goals is not None]
    absent = [item for item in attributed if item.is_confirmed_absence]
    present = [item for item in attributed if not item.is_confirmed_absence]

    def mean_goals(items: list[ClaimOutcome]) -> float | None:
        return (
            round(sum(item.team_goals for item in items) / len(items), 4)
            if items
            else None
        )

    return ImpactEvidence(
        pairs=len(outcomes),
        attributed=len(attributed),
        confirmed_absences=len(absent),
        rounds=len({(item.season, item.round_number) for item in outcomes}),
        teams=len({item.team for item in attributed}),
        mean_goals_with_absence=mean_goals(absent),
        mean_goals_without_absence=mean_goals(present),
    )


def fitting_readiness(evidence: ImpactEvidence) -> str:
    """A plain-language answer to "can Increment 5 be attempted yet?"."""
    if evidence.confirmed_absences >= ABSENCES_FOR_FITTING:
        return (
            f"{evidence.confirmed_absences} confirmed absences across "
            f"{evidence.rounds} rounds — enough to attempt a fit, which still "
            "needs its own versioning, walk-forward backtesting (§29-32) and "
            "an out-of-sample win over no adjustment (§28) before it may "
            "touch a forecast."
        )
    return (
        f"{evidence.confirmed_absences} of ~{ABSENCES_FOR_FITTING} confirmed "
        f"absences ({evidence.pairs} claim/outcome pairs across "
        f"{evidence.rounds} rounds). Accumulating; fitting an impact model on "
        "this would produce a number nobody could defend, which is worse than "
        "the current no-adjustment default (§22, §28)."
    )
