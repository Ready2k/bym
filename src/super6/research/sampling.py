"""Deterministic stratified sampling for AVAILABILITY_ORACLE_RESEARCH_V1.

The frame is generated and persisted **before** any availability call is
made, from a fixed seed, so the sample cannot drift toward whatever the
data turns out to say.

Strata: division x season x season-phase.

    2 divisions (E0, E1)
  x 2 seasons   (2022/23, 2023/24)
  x 3 phases    (early / middle / late)
  = 12 strata, ~17 fixtures each -> 200 total

Phase is computed as a fraction of each competition's own matchday count,
because the Premier League plays 38 and the Championship 46; a fixed
matchday cutoff would put "late season" in different places.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone

from ..db import from_iso, to_iso

SEED = 20260808
TARGET_TOTAL = 200
DIVISIONS = ("E0", "E1")
SEASONS = (2022, 2023)
PHASES = ("early", "middle", "late")


@dataclass(frozen=True)
class UniverseFixture:
    fixture_id: str
    division: str
    season: int
    matchday: int | None
    home_team: str
    away_team: str
    kickoff_at: str


def phase_for(matchday: int | None, max_matchday: int) -> str:
    """Season phase as a fraction of the competition's own length."""
    if not matchday or not max_matchday:
        return "middle"
    fraction = matchday / max_matchday
    if fraction <= 1 / 3:
        return "early"
    if fraction <= 2 / 3:
        return "middle"
    return "late"


def matchday_locks(fixtures: list[UniverseFixture]) -> dict[tuple, str]:
    """Earliest kick-off per (division, season, matchday).

    A proxy for the Super 6 round lock: Sky picks six fixtures from a
    matchday and the round closes at the first of them. We do not have
    Sky's historical selections, so the matchday's own earliest kick-off
    stands in — documented as a proxy rather than presented as the real
    lock.
    """
    locks: dict[tuple, str] = {}
    for f in fixtures:
        key = (f.division, f.season, f.matchday)
        current = locks.get(key)
        if current is None or f.kickoff_at < current:
            locks[key] = f.kickoff_at
    return locks


def _allocate(total: int, buckets: int) -> list[int]:
    """Spread `total` across `buckets` as evenly as possible."""
    base, remainder = divmod(total, buckets)
    return [base + (1 if i < remainder else 0) for i in range(buckets)]


def build_sample(
    universe: list[UniverseFixture],
    target_total: int = TARGET_TOTAL,
    seed: int = SEED,
) -> list[dict]:
    """Draw the stratified sample. Deterministic for a given seed and
    universe."""
    max_matchday: dict[tuple[str, int], int] = {}
    for f in universe:
        key = (f.division, f.season)
        if f.matchday:
            max_matchday[key] = max(max_matchday.get(key, 0), f.matchday)

    strata: dict[str, list[UniverseFixture]] = {}
    for f in universe:
        phase = phase_for(f.matchday, max_matchday.get((f.division, f.season), 0))
        strata.setdefault(f"{f.division}:{f.season}:{phase}", []).append(f)

    per_division = target_total // len(DIVISIONS)
    per_season = per_division // len(SEASONS)
    phase_quota = _allocate(per_season, len(PHASES))

    locks = matchday_locks(universe)
    rng = random.Random(seed)
    chosen: list[dict] = []

    for division in DIVISIONS:
        for season in SEASONS:
            for index, phase in enumerate(PHASES):
                key = f"{division}:{season}:{phase}"
                pool = sorted(strata.get(key, []), key=lambda f: f.fixture_id)
                quota = phase_quota[index]
                if not pool:
                    continue
                picked = (
                    pool if len(pool) <= quota else rng.sample(pool, quota)
                )
                for f in picked:
                    chosen.append(
                        {
                            "fixture_id": f.fixture_id,
                            "division": f.division,
                            "season": f.season,
                            "matchday": f.matchday,
                            "home_team": f.home_team,
                            "away_team": f.away_team,
                            "kickoff_at": f.kickoff_at,
                            "matchday_lock": locks.get(
                                (f.division, f.season, f.matchday)
                            ),
                            "stratum": key,
                        }
                    )
    return sorted(chosen, key=lambda r: r["fixture_id"])


def stratum_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["stratum"]] = counts.get(row["stratum"], 0) + 1
    return dict(sorted(counts.items()))
