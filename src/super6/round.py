"""Super 6 round configuration — manual entry (SPEC.md §9.1).

Sky selects each round's six fixtures at its own discretion and its
channels have shown inconsistent deadlines, so round configuration is
manual input, not derived data. The user enters the fixtures and deadline
once, as JSON:

    {
      "round_number": 14,
      "deadline_at": "2026-10-18T14:00:00",
      "fixtures": [
        {"home": "Arsenal", "away": "Everton", "odds": [1.55, 4.2, 6.0]},
        ...six entries...
      ]
    }

"odds" ([home, draw, away] decimal odds, manually copied from any
bookmaker near prediction time) is optional per fixture. When present,
§32's market calibration blends them in; when absent the fixture is
scored by the pure model — graceful degradation, never imputation.
Automated odds ingestion (§6/§18) replaces this manual step in Phase 2.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

FIXTURES_PER_ROUND = 6


@dataclass(frozen=True)
class Fixture:
    home: str
    away: str
    odds: tuple[float, float, float] | None = None  # (H, D, A) decimal odds


@dataclass(frozen=True)
class Round:
    round_number: int
    deadline_at: datetime
    fixtures: tuple[Fixture, ...]
    source: str = "MANUAL"  # §9.1: round configuration source = MANUAL
    entered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def load_round(path: Path) -> Round:
    raw = json.loads(path.read_text())

    def parse_fixture(f: dict) -> Fixture:
        odds = f.get("odds")
        if odds is not None:
            if len(odds) != 3 or any(o <= 1.0 for o in odds):
                raise ValueError(
                    f"{f['home']} v {f['away']}: odds must be three decimal "
                    f"odds > 1.0 as [home, draw, away], got {odds}"
                )
            odds = (float(odds[0]), float(odds[1]), float(odds[2]))
        return Fixture(f["home"], f["away"], odds)

    fixtures = tuple(parse_fixture(f) for f in raw["fixtures"])
    if len(fixtures) != FIXTURES_PER_ROUND:
        raise ValueError(
            f"A Super 6 round has exactly {FIXTURES_PER_ROUND} fixtures, "
            f"got {len(fixtures)} (§9.1)."
        )
    seen = set()
    for f in fixtures:
        key = (f.home.lower(), f.away.lower())
        if key in seen:
            raise ValueError(f"Duplicate fixture {f.home} v {f.away}")
        seen.add(key)
    return Round(
        round_number=int(raw["round_number"]),
        deadline_at=datetime.fromisoformat(raw["deadline_at"]),
        fixtures=fixtures,
        source=raw.get("source", "MANUAL"),
    )
