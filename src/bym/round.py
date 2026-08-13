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
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
    return round_from_dict(json.loads(path.read_text()))


def resolve_round_teams(
    rnd: Round, known_teams: Iterable[str]
) -> tuple[Round, list[tuple[str, str]]]:
    """Rewrite a round's club names to the corpus's canonical forms.

    The round file holds whatever name the user typed — often Sky's full
    club name ("Manchester City") — while the training corpus uses
    football-data.co.uk's short forms ("Man City"). An unresolved name is
    not a cosmetic problem: the fixture silently falls back to a
    league-average stand-in, which can invert a recommendation outright.

    Returns the rewritten round and the (typed, canonical) pairs that
    changed, so callers can report what they did. Every entry point that
    reads a round file should go through here — `predict` and `engine-run`
    disagreeing about the same file is a bug, not a difference of opinion.
    """
    from .sources.teams import resolve

    known = list(known_teams)
    renamed: list[tuple[str, str]] = []
    resolved_fixtures = []
    for fixture in rnd.fixtures:
        home = resolve(fixture.home, known) or fixture.home
        away = resolve(fixture.away, known) or fixture.away
        if home != fixture.home:
            renamed.append((fixture.home, home))
        if away != fixture.away:
            renamed.append((fixture.away, away))
        resolved_fixtures.append(replace(fixture, home=home, away=away))
    return replace(rnd, fixtures=tuple(resolved_fixtures)), renamed


def round_from_dict(raw: dict) -> Round:
    """Validate a manually-entered round payload and build its domain model.

    Keeping validation here gives the CLI and the web UI the exact same
    definition of a valid Super 6 round: six distinct fixtures and a
    deliberate deadline supplied by the user.
    """

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
    deadline = datetime.fromisoformat(raw["deadline_at"].replace("Z", "+00:00"))
    # The manual-entry flow presents deadlines in UK time. Existing round
    # files used a local ISO value without an offset, so interpret that
    # unambiguously rather than letting the server's machine timezone decide.
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=ZoneInfo("Europe/London"))
    return Round(
        round_number=int(raw["round_number"]),
        deadline_at=deadline,
        fixtures=fixtures,
        source=raw.get("source", "MANUAL"),
    )


def round_to_dict(rnd: Round) -> dict:
    """Serialize a round in the documented, user-editable JSON format."""
    return {
        "round_number": rnd.round_number,
        "deadline_at": rnd.deadline_at.isoformat(),
        "fixtures": [
            {
                "home": fixture.home,
                "away": fixture.away,
                **({"odds": list(fixture.odds)} if fixture.odds is not None else {}),
            }
            for fixture in rnd.fixtures
        ],
        "source": rnd.source,
    }
