"""football-data.org client (SPEC.md §18, §2.2).

Scope, established by live probing rather than documentation:

    HAS   current-season fixtures (380 PL + Championship for 2026/27)
    HAS   results and match status
    HAS   referees, venue, matchday
    LACKS lineups (team objects carry crest/id/name/shortName/tla only)
    LACKS injuries
    LACKS odds on the free tier (a paid add-on, §25)

So this does **not** replace API-Football for availability. What it does
provide is the fixture/result/status feed the system has never had:
§2.2's VOID handling has existed since Phase 1 with nothing to feed it,
and prospective ledger scoring has been manual JSON entry.

Free tier: 10 requests/minute across a limited competition set.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..scoring import FixtureStatus
from .quota import QuotaSpec, check, record
from .registry import DEFAULT_REGISTRY_PATH, assert_approved

SOURCE_ID = "football_data_org"
BASE_URL = "https://api.football-data.org/v4"

COMPETITION_CODES = {"E0": "PL", "E1": "ELC"}

# Free tier is rate-limited per minute rather than capped per month, so
# the daily budget here is a generous guard against runaway loops rather
# than a provider limit.
QUOTA = QuotaSpec(
    source_id=SOURCE_ID, limit=500, period="day", per_minute=10
)

# football-data.org status -> our fixture status (§2.2). A fixture that is
# postponed, cancelled, suspended or awarded did not produce a real
# 90-minute result, so it is VOID: no points, excluded from performance
# statistics, but still logged.
STATUS_MAP = {
    "SCHEDULED": FixtureStatus.SCHEDULED,
    "TIMED": FixtureStatus.SCHEDULED,
    "IN_PLAY": FixtureStatus.ACTIVE,
    "PAUSED": FixtureStatus.ACTIVE,
    "FINISHED": FixtureStatus.COMPLETED,
    "POSTPONED": FixtureStatus.POSTPONED,
    "SUSPENDED": FixtureStatus.ABANDONED,
    "CANCELLED": FixtureStatus.VOID,
    "AWARDED": FixtureStatus.VOID,
}


@dataclass
class Match:
    match_id: str
    home_team: str
    away_team: str
    kickoff_at: str
    status: str            # our FixtureStatus value
    raw_status: str        # what the provider said
    matchday: int | None = None
    home_goals: int | None = None
    away_goals: int | None = None

    @property
    def is_void(self) -> bool:
        """§2.2 — contributes no points and no performance statistics."""
        return self.status in {
            FixtureStatus.VOID.value,
            FixtureStatus.POSTPONED.value,
            FixtureStatus.ABANDONED.value,
        }

    @property
    def has_result(self) -> bool:
        return (
            self.status == FixtureStatus.COMPLETED.value
            and self.home_goals is not None
            and self.away_goals is not None
        )


def _request(
    path: str,
    api_key: str,
    params: dict | None = None,
    quota_path: Path | None = None,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    now: datetime | None = None,
    timeout: int = 25,
) -> dict:
    assert_approved(SOURCE_ID, registry_path)
    check(QUOTA, 1, quota_path, now)
    response = requests.get(
        f"{BASE_URL}/{path}",
        headers={"X-Auth-Token": api_key},
        params=params or {},
        timeout=timeout,
    )
    record(
        QUOTA, 1,
        response.headers.get("X-Requests-Available-Minute"),
        quota_path, now,
    )
    response.raise_for_status()
    return response.json()


def parse_matches(payload: dict) -> list[Match]:
    matches = []
    for item in payload.get("matches", []) or []:
        raw_status = str(item.get("status") or "")
        mapped = STATUS_MAP.get(raw_status, FixtureStatus.SCHEDULED)
        full_time = (item.get("score") or {}).get("fullTime") or {}
        matches.append(
            Match(
                match_id=str(item.get("id", "")),
                home_team=(item.get("homeTeam") or {}).get("name", ""),
                away_team=(item.get("awayTeam") or {}).get("name", ""),
                kickoff_at=item.get("utcDate", ""),
                status=mapped.value,
                raw_status=raw_status,
                matchday=item.get("matchday"),
                home_goals=full_time.get("home"),
                away_goals=full_time.get("away"),
            )
        )
    return matches


def fetch_matches(
    division: str,
    season: int,
    api_key: str,
    date_from: str | None = None,
    date_to: str | None = None,
    **kwargs,
) -> list[Match]:
    """Fixtures and results for a competition season."""
    if division not in COMPETITION_CODES:
        raise ValueError(
            f"No competition code for division '{division}'. "
            f"Known: {sorted(COMPETITION_CODES)}"
        )
    params: dict = {"season": season}
    if date_from and date_to:
        params.update({"dateFrom": date_from, "dateTo": date_to})
    payload = _request(
        f"competitions/{COMPETITION_CODES[division]}/matches",
        api_key, params, **kwargs,
    )
    return parse_matches(payload)


def results_for_round(
    matches: list[Match],
    fixtures: list[tuple[str, str]],
    resolve_team,
) -> dict[int, tuple[int, int] | None]:
    """Map a round's six fixtures onto results for ledger scoring.

    Returns fixture_index -> (home_goals, away_goals), or None for a VOID
    fixture (§2.2). A fixture with no completed result is simply absent,
    so a half-finished round scores nothing rather than scoring zeros.
    """
    known = {t for pair in fixtures for t in pair}
    by_pair: dict[tuple[str, str], Match] = {}
    for match in matches:
        home = resolve_team(match.home_team, known)
        away = resolve_team(match.away_team, known)
        if home and away:
            by_pair[(home, away)] = match

    out: dict[int, tuple[int, int] | None] = {}
    for index, pair in enumerate(fixtures):
        match = by_pair.get(pair)
        if match is None:
            continue
        if match.is_void:
            out[index] = None
        elif match.has_result:
            out[index] = (int(match.home_goals), int(match.away_goals))
    return out
