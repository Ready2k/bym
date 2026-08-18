"""API-Football client (SPEC.md §18.1, §25; Increment 3B).

Supplies structured availability: confirmed XIs (typically 20-40 minutes
before kickoff), injuries and suspensions. It does **not** supply
predicted/expected lineups — that gap is left deliberately open rather
than filled by a EUR159/month product for six score predictions among
mates (§25).

Free tier: 100 requests/day, 10/minute, all endpoints. Every request is
gated on the source registry (§18.1) and metered before dispatch (§25).

Coverage is detected per (league, season), never assumed — see
`capabilities`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests

from ..availability.model import ClaimStatus, EventType
from .quota import QuotaSpec, check, record
from .registry import DEFAULT_REGISTRY_PATH, assert_approved

SOURCE_ID = "api_football"
BASE_URL = "https://v3.football.api-sports.io"

# Competition ids for the two divisions Super 6 draws from.
LEAGUE_IDS = {"E0": 39, "E1": 40}

FEATURE_LINEUPS = "lineups"
FEATURE_INJURIES = "injuries"

QUOTA = QuotaSpec(
    source_id=SOURCE_ID, limit=100, period="day", per_minute=10
)

# Injuries endpoint `type` values, mapped conservatively.
#
# "Missing Fixture" is a statement that the player is out -> CONFIRMED_ABSENT.
# "Questionable" explicitly means the player may still play, so it must NOT
# become LIKELY_ABSENT. It is recorded as a DOUBT at REPORTED certainty and
# left to be superseded by the confirmed XI.
INJURY_TYPE_MAP: dict[str, tuple[EventType, ClaimStatus]] = {
    "missing fixture": (EventType.CONFIRMED_ABSENT, ClaimStatus.CONFIRMED),
    "questionable": (EventType.DOUBT, ClaimStatus.REPORTED),
}
DEFAULT_INJURY_MAPPING = (EventType.DOUBT, ClaimStatus.REPORTED)


@dataclass
class ApiFootballFixture:
    fixture_id: str
    home_team: str
    away_team: str
    kickoff_at: str
    league_id: int
    season: int


@dataclass
class InjuryRecord:
    fixture_id: str
    team: str
    player_id: str | None
    player_name: str
    raw_type: str
    reason: str
    event_type: EventType
    claim_status: ClaimStatus


@dataclass
class LineupRecord:
    fixture_id: str
    team: str
    formation: str
    start_xi: list[tuple[str | None, str]] = field(default_factory=list)
    substitutes: list[tuple[str | None, str]] = field(default_factory=list)


# api-football's own single-letter position codes (games.position), used
# only to seed FplPlayerSeason.position — the `Position` enum owns the
# canonical GK/DEF/MID/FWD values everything downstream reads.
POSITION_CODE_MAP = {"G": "GK", "D": "DEF", "M": "MID", "F": "FWD"}


@dataclass
class PlayerMatchStatsRecord:
    """One player's stats from one fixture (SPEC_FPL.md §4
    `fpl_player_match_stats`, FPL Phase 1A §5.2 historical scope).

    Fields the free-tier `/fixtures/players` response does not carry
    (xg, xa, own_goals, clearances, recoveries, cbit/cbirt, bps) are left
    for `fpl_player_match_stats`'s own nullable columns to hold `None` —
    this ingester never fabricates them.
    """

    fixture_id: str
    team: str
    provider_player_id: str | None
    player_name: str
    position_code: str | None  # api-football's G/D/M/F, or None if absent
    minutes: int
    goals: int
    assists: int
    shots: int | None
    saves: int | None
    yellow_cards: int
    red_cards: int
    tackles: int | None
    interceptions: int | None
    blocks: int | None
    penalties_scored: int
    penalties_missed: int
    penalties_saved: int | None

    @property
    def position(self) -> str | None:
        return POSITION_CODE_MAP.get(self.position_code or "")


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_player_match_stats(payload: list[dict]) -> list[PlayerMatchStatsRecord]:
    """Parse a `/fixtures/players` response — one entry per team, each
    carrying that team's player statistics for the fixture."""
    fixture_id = ""
    records: list[PlayerMatchStatsRecord] = []
    for team_entry in payload:
        team = (team_entry.get("team") or {}).get("name", "")
        for player_entry in team_entry.get("players") or []:
            player = player_entry.get("player") or {}
            stats_list = player_entry.get("statistics") or []
            if not stats_list:
                continue
            stats = stats_list[0]
            games = stats.get("games") or {}
            goals = stats.get("goals") or {}
            shots = stats.get("shots") or {}
            tackles = stats.get("tackles") or {}
            cards = stats.get("cards") or {}
            penalty = stats.get("penalty") or {}
            records.append(
                PlayerMatchStatsRecord(
                    fixture_id=fixture_id,
                    team=team,
                    provider_player_id=(
                        str(player["id"]) if player.get("id") else None
                    ),
                    player_name=player.get("name", ""),
                    position_code=games.get("position"),
                    minutes=_int(games.get("minutes")),
                    goals=_int(goals.get("total")),
                    assists=_int(goals.get("assists")),
                    shots=(shots.get("total") if shots.get("total") is not None else None),
                    saves=(goals.get("saves") if goals.get("saves") is not None else None),
                    yellow_cards=_int(cards.get("yellow")),
                    red_cards=_int(cards.get("red")),
                    tackles=(tackles.get("total") if tackles.get("total") is not None else None),
                    interceptions=(
                        tackles.get("interceptions")
                        if tackles.get("interceptions") is not None
                        else None
                    ),
                    blocks=(tackles.get("blocks") if tackles.get("blocks") is not None else None),
                    penalties_scored=_int(penalty.get("scored")),
                    penalties_missed=_int(penalty.get("missed")),
                    penalties_saved=(
                        penalty.get("saved") if penalty.get("saved") is not None else None
                    ),
                )
            )
    return records


def fetch_player_match_stats(
    fixture_id: str, api_key: str, **kwargs
) -> list[PlayerMatchStatsRecord]:
    """Per-player stats for one historical fixture (2022-2024 on the free
    tier — see the registry's `historical_player_match_stats` purpose)."""
    payload = _request("fixtures/players", api_key, {"fixture": fixture_id}, **kwargs)
    records = parse_player_match_stats(payload)
    for r in records:
        r.fixture_id = fixture_id
    return records


def _request(
    path: str,
    api_key: str,
    params: dict,
    quota_path: Path | None = None,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    now: datetime | None = None,
    timeout: int = 20,
) -> list[dict]:
    """One metered, registry-gated GET."""
    assert_approved(SOURCE_ID, registry_path)
    check(QUOTA, 1, quota_path, now)

    response = requests.get(
        f"{BASE_URL}/{path}",
        headers={"x-apisports-key": api_key},
        params=params,
        timeout=timeout,
    )
    record(
        QUOTA,
        1,
        response.headers.get("x-ratelimit-requests-remaining"),
        quota_path,
        now,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError(f"API-Football error: {payload['errors']}")
    return payload.get("response", [])


def parse_coverage(payload: list[dict]) -> dict[str, bool]:
    """Extract the coverage flags we care about from a /leagues response."""
    if not payload:
        return {FEATURE_LINEUPS: False, FEATURE_INJURIES: False}
    seasons = payload[0].get("seasons") or []
    if not seasons:
        return {FEATURE_LINEUPS: False, FEATURE_INJURIES: False}
    coverage = seasons[0].get("coverage") or {}
    fixtures = coverage.get("fixtures") or {}
    return {
        FEATURE_LINEUPS: bool(fixtures.get("lineups", False)),
        FEATURE_INJURIES: bool(coverage.get("injuries", False)),
    }


def fetch_coverage(
    division: str, season: int, api_key: str, **kwargs
) -> dict[str, bool]:
    """Detect what this competition/season actually supports."""
    league_id = LEAGUE_IDS[division]
    payload = _request(
        "leagues", api_key, {"id": league_id, "season": season}, **kwargs
    )
    return parse_coverage(payload)


def parse_fixtures(payload: list[dict]) -> list[ApiFootballFixture]:
    fixtures = []
    for item in payload:
        fixture = item.get("fixture") or {}
        teams = item.get("teams") or {}
        league = item.get("league") or {}
        fixtures.append(
            ApiFootballFixture(
                fixture_id=str(fixture.get("id", "")),
                home_team=(teams.get("home") or {}).get("name", ""),
                away_team=(teams.get("away") or {}).get("name", ""),
                kickoff_at=fixture.get("date", ""),
                league_id=int(league.get("id", 0) or 0),
                season=int(league.get("season", 0) or 0),
            )
        )
    return fixtures


def fetch_fixtures(
    division: str, season: int, api_key: str, date: str | None = None, **kwargs
) -> list[ApiFootballFixture]:
    params = {"league": LEAGUE_IDS[division], "season": season}
    if date:
        params["date"] = date
    return parse_fixtures(_request("fixtures", api_key, params, **kwargs))


def parse_injuries(payload: list[dict]) -> list[InjuryRecord]:
    records = []
    for item in payload:
        player = item.get("player") or {}
        team = item.get("team") or {}
        fixture = item.get("fixture") or {}
        raw_type = str(player.get("type") or "").strip()
        event_type, claim = INJURY_TYPE_MAP.get(
            raw_type.lower(), DEFAULT_INJURY_MAPPING
        )
        records.append(
            InjuryRecord(
                fixture_id=str(fixture.get("id", "")),
                team=team.get("name", ""),
                player_id=str(player["id"]) if player.get("id") else None,
                player_name=player.get("name", ""),
                raw_type=raw_type,
                reason=str(player.get("reason") or ""),
                event_type=event_type,
                claim_status=claim,
            )
        )
    return records


def fetch_injuries(
    fixture_ids: list[str], api_key: str, **kwargs
) -> list[InjuryRecord]:
    """Injuries for up to 20 fixture ids in a single request."""
    if not fixture_ids:
        return []
    if len(fixture_ids) > 20:
        raise ValueError("API-Football accepts at most 20 fixture ids per call")
    return parse_injuries(
        _request("injuries", api_key, {"ids": "-".join(fixture_ids)}, **kwargs)
    )


def fetch_injuries_for_fixture(
    fixture_id: str, api_key: str, **kwargs
) -> list[InjuryRecord]:
    """Injuries for a single fixture.

    The batch `ids` parameter is paid-only (confirmed live: "Free plans do
    not have access to the Ids parameter"), so anything running on the
    free tier must ask one fixture at a time.
    """
    return parse_injuries(
        _request("injuries", api_key, {"fixture": fixture_id}, **kwargs)
    )


def parse_lineups(payload: list[dict], fixture_id: str) -> list[LineupRecord]:
    records = []
    for item in payload:
        team = (item.get("team") or {}).get("name", "")

        def names(key: str) -> list[tuple[str | None, str]]:
            out = []
            for entry in item.get(key) or []:
                player = entry.get("player") or {}
                name = player.get("name")
                if name:
                    out.append(
                        (str(player["id"]) if player.get("id") else None, name)
                    )
            return out

        records.append(
            LineupRecord(
                fixture_id=fixture_id,
                team=team,
                formation=item.get("formation") or "",
                start_xi=names("startXI"),
                substitutes=names("substitutes"),
            )
        )
    return records


def fetch_lineups(
    fixture_id: str, api_key: str, **kwargs
) -> list[LineupRecord]:
    """Confirmed XI for one fixture. Empty until the teams are announced —
    an empty response is 'not yet published', not an error."""
    payload = _request("fixtures/lineups", api_key, {"fixture": fixture_id}, **kwargs)
    return parse_lineups(payload, fixture_id)
