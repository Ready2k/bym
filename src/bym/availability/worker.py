"""Availability polling worker (Increment 3B).

    detect coverage (per league/season, persisted)
        -> map Super 6 fixtures to source fixture ids
        -> poll injuries (one call covers up to 20 fixtures)
        -> poll confirmed lineups near kickoff
        -> reconcile, emit structured events
        -> attribute against recent market movement

Still no λ impact — `availability.impact` continues to police that, and
the architectural test asserts the forecast path has no availability
input at all.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .. import events as ev
from ..db import to_iso, utcnow
from ..round import Round
from ..sources import api_football, capabilities
from ..sources.teams import match_event, resolve
from . import ingest, store
from .model import ClaimStatus, EventType, Importance, Lineup, LineupKind

# Confirmed XIs typically appear 20-40 minutes before kickoff, so polling
# for them earlier than this simply spends quota on empty responses.
LINEUP_WINDOW = timedelta(minutes=75)


@dataclass
class AvailabilityPollResult:
    injuries_ingested: int = 0
    lineups_ingested: int = 0
    requests_spent: int = 0
    events: list[ev.Event] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def detect_coverage(
    conn: sqlite3.Connection,
    division: str,
    season: int,
    api_key: str,
    now: datetime | None = None,
    **kwargs,
) -> dict[str, bool]:
    """Detect and persist what this competition/season actually supports."""
    coverage = api_football.fetch_coverage(division, season, api_key, **kwargs)
    league_id = api_football.LEAGUE_IDS[division]
    for feature, available in coverage.items():
        capabilities.record_capability(
            conn, api_football.SOURCE_ID, league_id, season, feature,
            available, now=now,
        )
    return coverage


def map_round_fixtures(
    conn: sqlite3.Connection,
    rnd: Round,
    division: str,
    season: int,
    api_key: str,
    now: datetime | None = None,
    **kwargs,
) -> int:
    """Map this round's fixtures to source fixture ids. Returns how many
    of the six were matched; unmatched fixtures are simply not polled."""
    fixtures = api_football.fetch_fixtures(division, season, api_key, **kwargs)
    round_teams = {t for f in rnd.fixtures for t in (f.home, f.away)}
    matched = 0
    for fixture in fixtures:
        pair = match_event(fixture.home_team, fixture.away_team, round_teams)
        if pair is None:
            continue
        capabilities.map_fixture(
            conn, api_football.SOURCE_ID, fixture.fixture_id, pair[0], pair[1],
            kickoff_at=fixture.kickoff_at, league_id=fixture.league_id,
            season=fixture.season, now=now,
        )
        matched += 1
    return matched


def poll_availability(
    conn: sqlite3.Connection,
    rnd: Round,
    division: str,
    season: int,
    api_key: str,
    now: datetime | None = None,
    kickoff_at: datetime | None = None,
    key_players: set[str] | None = None,
    **kwargs,
) -> AvailabilityPollResult:
    """One availability polling cycle for a round."""
    now = now or utcnow()
    result = AvailabilityPollResult()
    league_id = api_football.LEAGUE_IDS[division]

    fixture_ids: list[str] = []
    id_to_fixture: dict[str, tuple[str, str]] = {}
    for fixture in rnd.fixtures:
        external = capabilities.external_id_for(
            conn, api_football.SOURCE_ID, fixture.home, fixture.away
        )
        if external:
            fixture_ids.append(external)
            id_to_fixture[external] = (fixture.home, fixture.away)

    if not fixture_ids:
        result.skipped.append("no fixtures mapped to source ids")
        return result

    # ---- injuries -------------------------------------------------------
    if capabilities.may_query(
        conn, api_football.SOURCE_ID, league_id, season, api_football.FEATURE_INJURIES
    ):
        records = api_football.fetch_injuries(fixture_ids[:20], api_key, **kwargs)
        result.requests_spent += 1
        for record in records:
            fixture = id_to_fixture.get(record.fixture_id)
            if fixture is None:
                continue
            team = resolve(record.team, {fixture[0], fixture[1]}) or record.team
            outcome = ingest.ingest_availability(
                conn,
                fixture[0],
                fixture[1],
                team,
                record.player_name,
                record.event_type,
                record.claim_status,
                source_id=api_football.SOURCE_ID,
                source_tier="STRUCTURED_PROVIDER",
                source_timestamp=now,
                round_number=rnd.round_number,
                now=now,
                source_player_id=record.player_id,
            )
            if outcome.was_new:
                result.injuries_ingested += 1
            result.events.extend(outcome.events)
    else:
        state = capabilities.capability_state(
            conn, api_football.SOURCE_ID, league_id, season,
            api_football.FEATURE_INJURIES,
        )
        result.skipped.append(f"injuries {state} for league {league_id}/{season}")

    # ---- confirmed lineups ---------------------------------------------
    within_window = kickoff_at is None or (kickoff_at - now) <= LINEUP_WINDOW
    if not within_window:
        result.skipped.append("outside confirmed-lineup window")
        return result

    if not capabilities.may_query(
        conn, api_football.SOURCE_ID, league_id, season, api_football.FEATURE_LINEUPS
    ):
        state = capabilities.capability_state(
            conn, api_football.SOURCE_ID, league_id, season,
            api_football.FEATURE_LINEUPS,
        )
        result.skipped.append(f"lineups {state} for league {league_id}/{season}")
        return result

    for external_id in fixture_ids:
        fixture = id_to_fixture[external_id]
        existing = {
            (l.team, l.kind)
            for l in store.lineups_for_fixture(conn, fixture[0], fixture[1])
        }
        records = api_football.fetch_lineups(external_id, api_key, **kwargs)
        result.requests_spent += 1
        for record in records:
            team = resolve(record.team, {fixture[0], fixture[1]}) or record.team
            if (team, LineupKind.CONFIRMED.value) in existing:
                continue  # already have this team's confirmed XI
            lineup = Lineup(
                home_team=fixture[0],
                away_team=fixture[1],
                team=team,
                kind=LineupKind.CONFIRMED.value,
                source_id=api_football.SOURCE_ID,
                generated_at=to_iso(now),
                observed_at=to_iso(now),
                players=[name for _, name in record.start_xi],
                round_number=rnd.round_number,
            )
            result.events.extend(
                ingest.ingest_lineup(conn, lineup, key_players=key_players, now=now)
            )
            result.lineups_ingested += 1

            # Starters and bench are both facts worth recording, and a
            # CONFIRMED_BENCH supersedes an expectation of starting.
            for player_id, name in record.start_xi:
                outcome = ingest.ingest_availability(
                    conn, fixture[0], fixture[1], team, name,
                    EventType.CONFIRMED_STARTER, ClaimStatus.CONFIRMED,
                    source_id=api_football.SOURCE_ID,
                    source_tier="STRUCTURED_PROVIDER",
                    source_timestamp=now, round_number=rnd.round_number,
                    now=now, source_player_id=player_id,
                )
                result.events.extend(outcome.events)
            for player_id, name in record.substitutes:
                outcome = ingest.ingest_availability(
                    conn, fixture[0], fixture[1], team, name,
                    EventType.CONFIRMED_BENCH, ClaimStatus.CONFIRMED,
                    source_id=api_football.SOURCE_ID,
                    source_tier="STRUCTURED_PROVIDER",
                    source_timestamp=now, round_number=rnd.round_number,
                    now=now, source_player_id=player_id,
                )
                result.events.extend(outcome.events)
    return result
