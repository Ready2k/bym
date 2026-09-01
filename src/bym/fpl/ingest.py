"""Historical player-match stat ingestion (docs/SPEC_FPL.md §4, §5.2, §5.6).

Ties two steps together, kept as separate functions because they answer
different questions: `resolve_identity` asks "which stable `fpl_player`
is this provider record about," `ingest_fixture_player_stats` asks
"fetch this fixture and persist what the provider says happened." Scoped
to `api_football`'s approved historical purpose only (2022-2024 on the
free tier — see the registry's 2026-08-17 `historical_player_match_stats`
decision); this module never reads a live/current-season endpoint.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from ..db import to_iso, utcnow
from ..sources import api_football
from ..sources.api_football import PlayerMatchStatsRecord
from . import player_store
from .player_match_stats import PlayerMatchStats, record_match_stats
from .players import FplPlayer, FplPlayerSeason

PROVIDER = api_football.SOURCE_ID


def resolve_identity(
    conn: sqlite3.Connection, record: PlayerMatchStatsRecord, season: int
) -> FplPlayer:
    """Resolve a provider record to a stable `fpl_player`, updating that
    player's season snapshot with what this record reveals (team,
    position, the provider's own id) — §4's "identity separated from
    season-specific identity" applied to ingestion.

    A provider id already seen this season wins over a fresh name lookup:
    it is a stronger identity claim than a string match, the same
    precedent `availability/ingest.py`'s `source_player_id` short-circuit
    already sets for this codebase.
    """
    existing = None
    if record.provider_player_id:
        existing = player_store.find_player_by_external_id(
            conn, PROVIDER, record.provider_player_id, season
        )
    if existing is not None:
        player = existing
    else:
        player, _ = player_store.get_or_create_player(conn, record.player_name)

    current = player_store.get_player_season(conn, player.id, season)
    external_ids = dict(current.external_ids) if current else {}
    if record.provider_player_id:
        external_ids[PROVIDER] = record.provider_player_id

    player_store.record_player_season(
        conn,
        FplPlayerSeason(
            player_id=player.id,
            season=season,
            team=record.team,
            # A record with no position code (rare — substitutes who never
            # appear sometimes omit it) must not blank out a position this
            # player is already known to play.
            position=record.position or (current.position if current else "") or "",
            fpl_element_id=current.fpl_element_id if current else None,
            start_price=current.start_price if current else None,
            external_ids=external_ids,
        ),
    )
    return player


def ingest_fixture_player_stats(
    conn: sqlite3.Connection,
    fixture_id: str,
    season: int,
    api_key: str,
    kickoff_at: str,
    now: datetime | None = None,
    **fetch_kwargs,
) -> list[PlayerMatchStats]:
    """Fetch one historical fixture's player stats from api_football,
    resolve each player's identity, and persist `fpl_player_match_stats`.

    `kickoff_at` is required, not defaulted — it is when the fixture was
    actually played (typically `ApiFootballFixture.kickoff_at` from a
    prior `api_football.fetch_fixtures()` call) and is the ONLY thing
    walk-forward evaluation's causal cutoff (`fpl.causality`,
    `fpl.projection`'s `before` parameter) can filter on. `observed_at`,
    by contrast, is the ingestion instant — always later, and useless for
    ordering fixtures chronologically. A caller that doesn't know the
    kickoff time yet should not be ingesting the fixture yet either,
    rather than persisting a row that silently drops out of every
    walk-forward query that filters on `kickoff_at`.
    """
    now = now or utcnow()
    observed_at = to_iso(now)
    # `now` must also reach api_football's own quota check/record calls —
    # quota.used()/record() bucket by calendar day, so a caller pinning
    # `now` (a test, or a harvest run spanning a UTC midnight) needs the
    # actual spend recorded against the SAME day it is budgeting against,
    # not whatever `datetime.now()` happens to be when the request fires.
    records = api_football.fetch_player_match_stats(fixture_id, api_key, now=now, **fetch_kwargs)

    stored: list[PlayerMatchStats] = []
    for record in records:
        if record.minutes <= 0:
            # Did not appear — no match-stat evidence to persist. Keeping
            # a zero-minute row would silently claim "0 goals in 0
            # minutes" as observed fact rather than "did not play".
            continue
        player = resolve_identity(conn, record, season)
        stats = PlayerMatchStats(
            player_id=player.id,
            fixture_id=fixture_id,
            season=season,
            source_id=PROVIDER,
            source_record_version=f"fixture:{fixture_id}",
            observed_at=observed_at,
            kickoff_at=kickoff_at,
            team=record.team,
            minutes=record.minutes,
            goals=record.goals,
            assists=record.assists,
            shots=record.shots,
            saves=record.saves,
            yellow_cards=record.yellow_cards,
            red_cards=record.red_cards,
            penalties_missed=record.penalties_missed,
            penalties_saved=record.penalties_saved,
            tackles=record.tackles,
            interceptions=record.interceptions,
            blocks=record.blocks,
        )
        recorded, _ = record_match_stats(conn, stats)
        stored.append(recorded)
    return stored
