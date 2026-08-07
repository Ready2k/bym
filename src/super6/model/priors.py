"""Promoted/relegated-team empirical-Bayes priors (SPEC.md §29.4).

A team newly arrived in a division has no (or little) current-division
history, so its α/β estimate is noisy. §29.4: treat α_i, β_i as random
effects with an empirical-Bayes prior N(prior_mean, prior_variance) fit
from the historical distribution of newly-arrived teams' first-season
parameters. In the MLE this is a Gaussian penalty on the arrival team's
parameters; because the data likelihood grows with every match played
while the penalty stays fixed, shrinkage fades continuously as evidence
accumulates — §29.4's proportional-shrinkage requirement for free, no
cliff.

Arrival direction matters and is kept separate: a team relegated INTO the
Championship (arrived from above) is historically strong there, while a
team promoted INTO it from League One (arrived from below, or absent from
the corpus entirely) is historically weak. Priors are estimated per
(division, direction) from full-season fits of historical seasons, with a
pooled per-direction fallback for thin cells.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Division hierarchy: lower number = higher division.
DIVISION_LEVEL = {"E0": 0, "E1": 1}
VAR_FLOOR = 0.02  # never let a thin sample produce an overconfident prior


@dataclass(frozen=True)
class PriorSpec:
    mean_alpha: float
    var_alpha: float
    mean_beta: float
    var_beta: float


@dataclass(frozen=True)
class PromotedPriors:
    by_key: dict[tuple[str, str], PriorSpec]  # (division, direction) → spec
    by_direction: dict[str, PriorSpec]  # pooled fallback

    def lookup(self, division: str, direction: str) -> PriorSpec | None:
        return self.by_key.get((division, direction)) or self.by_direction.get(
            direction
        )


def team_division_by_season(matches: pd.DataFrame) -> dict[tuple[str, int], str]:
    """(team, season) → division, from either home or away appearances."""
    out: dict[tuple[str, int], str] = {}
    for side in ("home_team", "away_team"):
        sub = matches[[side, "season", "division"]].drop_duplicates()
        for row in sub.itertuples(index=False):
            out[(getattr(row, side), int(row.season))] = row.division
    return out


def arrivals(
    matches: pd.DataFrame, division: str, season: int
) -> dict[str, str]:
    """Teams newly arrived in `division` for `season`, with direction:
    'above' = came from a higher division (relegated in),
    'below' = came from a lower division or absent from the corpus
    (promoted in). Uses division membership only — no results — so it is
    public pre-season information, never leakage."""
    membership = team_division_by_season(matches)
    season_slice = matches[
        (matches["division"] == division) & (matches["season"] == season)
    ]
    teams = set(season_slice["home_team"]) | set(season_slice["away_team"])
    result: dict[str, str] = {}
    for team in teams:
        prev = membership.get((team, season - 1))
        if prev == division:
            continue
        if prev is None:
            result[team] = "below"
        elif DIVISION_LEVEL.get(prev, 99) < DIVISION_LEVEL.get(division, 99):
            result[team] = "above"
        else:
            result[team] = "below"
    return result


def estimate(
    matches: pd.DataFrame,
    min_cell: int = 3,
    var_floor: float = VAR_FLOOR,
    min_matches: int = 100,
) -> PromotedPriors:
    """Fit each historical (division, season) alone and collect the fitted
    α/β of that season's arrival teams, grouped by (division, direction).

    The earliest season in `matches` is skipped: without the season before
    it, every team would look like an arrival. Pass only training-period
    seasons — priors must never be estimated on the evaluation season.
    """
    from . import dixon_coles  # local import to avoid a cycle

    seasons = sorted(matches["season"].unique())
    records: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for season in seasons[1:]:
        for division in sorted(matches["division"].unique()):
            sub = matches[
                (matches["season"] == season) & (matches["division"] == division)
            ]
            if len(sub) < min_matches:
                continue
            params = dixon_coles.fit(sub, xi=0.0)
            for team, direction in arrivals(matches, division, season).items():
                if team in params.alpha:
                    records.setdefault((division, direction), []).append(
                        (params.alpha[team], params.beta[team])
                    )

    def spec(pairs: list[tuple[float, float]]) -> PriorSpec:
        a = np.array([p[0] for p in pairs])
        b = np.array([p[1] for p in pairs])
        var_a = a.var(ddof=1) if len(a) > 1 else 0.0
        var_b = b.var(ddof=1) if len(b) > 1 else 0.0
        return PriorSpec(
            mean_alpha=float(a.mean()),
            var_alpha=float(max(var_a, var_floor)),
            mean_beta=float(b.mean()),
            var_beta=float(max(var_b, var_floor)),
        )

    by_key = {k: spec(v) for k, v in records.items() if len(v) >= min_cell}
    pooled: dict[str, list[tuple[float, float]]] = {}
    for (_, direction), pairs in records.items():
        pooled.setdefault(direction, []).extend(pairs)
    by_direction = {d: spec(v) for d, v in pooled.items() if len(v) >= min_cell}
    return PromotedPriors(by_key=by_key, by_direction=by_direction)
