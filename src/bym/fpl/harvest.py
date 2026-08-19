"""Resumable historical-fixture harvest for `fpl_player_match_stats`
(docs/SPEC_FPL.md §5.2, §5.6; Phase 1B corpus build, human-approved
2026-08-17 scope: `historical_player_match_stats`, 2022/23-2024/25
Premier League only — see docs/MANIFEST_FPL.md's corpus section and
`HARVEST_SEASONS` below for why it stops at 2024/25 rather than tracking
"current season minus N".

`api_football`'s free tier is 100 requests/day (`sources.quota`), and a
three-season Premier League corpus is ~1140 fixtures — many quota windows,
not one sitting. This module is built around that reality rather than
against it: `fpl_fixture_harvest_state` is the resumability ledger,
seeded once with every target fixture as PENDING, then worked down a
`max_fixtures`-bounded batch at a time. A batch STOPS the moment
`sources.quota` reports the budget is spent (never treated as a per-
fixture failure) and leaves everything unfinished exactly as PENDING, so
tomorrow's run picks up where today's left off with no special-casing.

`content_hash`/`row_count` exist so a later inspection can tell whether a
FETCHED fixture's underlying data has changed without re-parsing it, and
so "FETCHED but the row_count doesn't match what re-ingesting now would
find" is a detectable drift, not a silent one.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime

from ..db import to_iso, utcnow
from ..sources import api_football
from ..sources import quota as quota_mod
from ..sources.quota import QuotaExceededError, RateLimitError
from . import ingest

# api_football's free tier hard-rejects seasons from 2025/26 onward (the
# registry's 2026-08-08 live probe: "current seasons are paywalled, 2025+
# rejected, 'try from 2022 to 2024'" — see config/source_registry.json's
# api_football notes). That is a provider limit, not a policy choice, and
# it does NOT move with the calendar the way `current_season()` below
# does — a real subscription upgrade or provider change is what would
# revise it, so it stays a separate, explicit constant.
PROVIDER_MAX_FETCHABLE_SEASON = 2024

# The human-approved 2026-08-17 scope (docs/MANIFEST_FPL.md): 2022/23,
# 2023/24, 2024/25 Premier League — every season this provider's free tier
# actually serves (see `PROVIDER_MAX_FETCHABLE_SEASON`). 2025/26 and later
# are never targeted by this module, not filtered out at run time, simply
# never named here, so there is no code path that could reach them by
# accident.
#
# Deliberately NOT derived as "current_season() - N": that would make an
# already-frozen-in-spirit historical corpus silently reshuffle every
# August as the calendar rolls over, while the provider's own cutoff
# stays fixed — exactly the kind of silent corpus drift CLAUDE.md's
# "corpus changes are never silent" rule exists to prevent. Changing this
# tuple is a deliberate human edit, same discipline as `DataConfig.divisions`.
HARVEST_SEASONS = (2022, 2023, 2024)
HARVEST_COMPETITION = "E0"


def current_season(now: datetime | None = None) -> int:
    """English top-flight season start year for `now` (Aug-Dec belongs to
    the season starting that year; Jan-Jul belongs to the previous one) —
    the same month>=7 convention `web._lms_fd_season` already uses.

    Exists purely so nothing in this module (logs, the corpus report, a
    future caller) has to hardcode which season is "current" — the mistake
    that produced this function in the first place was code and docs
    silently assuming 2025/26 after 2026/27 had already started. It does
    NOT drive `HARVEST_SEASONS` — see that constant's docstring for why.
    """
    now = now or utcnow()
    return now.year if now.month >= 7 else now.year - 1


MAX_ATTEMPTS = 3

# api_football.QUOTA allows 10 requests/minute — a per-minute RateLimitError
# is a pacing signal, not a budget exhaustion, so a batch waits out the
# window and retries the SAME fixture rather than stopping entirely (that
# would waste the rest of a whole day's remaining quota on nothing).
RATE_LIMIT_PAUSE_SECONDS = 7.0
MAX_RATE_LIMIT_PAUSES = 20  # generous ceiling so a genuine outage still gives up

PENDING = "PENDING"
FETCHED = "FETCHED"
NO_DATA = "NO_DATA"
RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
FINAL_FAILURE = "FINAL_FAILURE"

# A fixture in any of these needs no further attempt — FETCHED/NO_DATA are
# settled outcomes and FINAL_FAILURE has exhausted its retries. A season
# is "done" once nothing outside this set (and no RETRYABLE_FAILURE still
# under MAX_ATTEMPTS) remains — see `is_season_complete`.
TERMINAL_STATUSES = (FETCHED, NO_DATA, FINAL_FAILURE)


def seed_harvest_targets(
    conn: sqlite3.Connection,
    fixtures: list[api_football.ApiFootballFixture],
    competition: str,
) -> int:
    """Register fixtures as harvest targets. Idempotent per (fixture_id,
    competition, season) — re-seeding an already-known fixture leaves its
    status/attempt history untouched, never resets it back to PENDING."""
    added = 0
    for f in fixtures:
        existing = conn.execute(
            "SELECT id FROM fpl_fixture_harvest_state "
            "WHERE fixture_id = ? AND competition = ? AND season = ?",
            (f.fixture_id, competition, f.season),
        ).fetchone()
        if existing is not None:
            continue
        conn.execute(
            "INSERT INTO fpl_fixture_harvest_state "
            "(fixture_id, season, competition, home_team, away_team, "
            "kickoff_at, player_stats_status, attempt_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (f.fixture_id, f.season, competition, f.home_team, f.away_team,
             f.kickoff_at, PENDING),
        )
        added += 1
    return added


def harvest_status_counts(
    conn: sqlite3.Connection, season: int, competition: str
) -> dict[str, int]:
    rows = conn.execute(
        "SELECT player_stats_status, COUNT(*) AS n FROM fpl_fixture_harvest_state "
        "WHERE season = ? AND competition = ? GROUP BY player_stats_status",
        (season, competition),
    ).fetchall()
    return {row["player_stats_status"]: row["n"] for row in rows}


def ensure_season_seeded(
    conn: sqlite3.Connection,
    season: int,
    competition: str,
    api_key: str,
    now: datetime | None = None,
    **fetch_kwargs,
) -> int:
    """Seed a season's fixture list the first time anything tries to
    harvest it, so adding a season to `HARVEST_SEASONS` (as 2024/25 was)
    is enough on its own — no separate manual seeding step to remember.
    `api_football.fetch_fixtures` returns a whole season in one request,
    the same call 2022/23 and 2023/24 were originally seeded from; a
    season that already has rows is left untouched (0 requests spent),
    matching `seed_harvest_targets`'s own idempotence.

    Returns the number of api_football requests spent (0 or 1) so a
    caller tracking a request budget (`run_daily_harvest`) can account
    for it precisely rather than re-reading quota state to find out.
    """
    if harvest_status_counts(conn, season, competition):
        return 0
    now = now or utcnow()
    fixtures = api_football.fetch_fixtures(competition, season, api_key, now=now, **fetch_kwargs)
    seed_harvest_targets(conn, fixtures, competition)
    return 1


def _pending_rows(
    conn: sqlite3.Connection, season: int, competition: str, limit: int | None
) -> list[sqlite3.Row]:
    sql = (
        "SELECT * FROM fpl_fixture_harvest_state WHERE season = ? AND competition = ? "
        f"AND (player_stats_status = '{PENDING}' "
        f"OR (player_stats_status = '{RETRYABLE_FAILURE}' AND attempt_count < ?)) "
        "ORDER BY kickoff_at, fixture_id"
    )
    params: tuple = (season, competition, MAX_ATTEMPTS)
    if limit is not None:
        sql += " LIMIT ?"
        params += (limit,)
    return conn.execute(sql, params).fetchall()


def _fetch_with_rate_limit_pacing(
    conn: sqlite3.Connection,
    fixture_id: str,
    season: int,
    api_key: str,
    kickoff_at: str,
    now: datetime | None,
    **fetch_kwargs,
) -> list:
    """Retry the SAME fixture across a per-minute `RateLimitError`,
    pacing rather than treating a minute-window cap as budget exhaustion
    — re-raises `QuotaExceededError` immediately (that one really does
    mean stop) and re-raises `RateLimitError` only after
    `MAX_RATE_LIMIT_PAUSES` — a genuine outage, not a pacing wait.
    `**fetch_kwargs` (e.g. `quota_path`, `registry_path`) passes straight
    through to `ingest_fixture_player_stats`, the same override point
    `api_football.fetch_coverage`/`fetch_fixtures` already expose for
    tests to isolate quota state from the real project files.

    `now` stays possibly-`None` all the way to here rather than arriving
    pre-resolved: `sources.quota`'s per-minute check filters
    `recent_calls` against a 60-second window ending at `now`, so this
    loop's whole POINT — sleeping real seconds so that window can clear —
    only works if `now` actually advances across the sleeps. Resolving it
    fresh (`utcnow()`) on every attempt does that in production. A caller
    that explicitly pins `now` (tests, wanting a deterministic quota
    day-bucket) gets that same frozen value on every attempt instead,
    exactly as before — `now or utcnow()` only re-evaluates the `utcnow()`
    side when `now` was `None` to begin with."""
    for _ in range(MAX_RATE_LIMIT_PAUSES):
        attempt_now = now or utcnow()
        try:
            return ingest.ingest_fixture_player_stats(
                conn, fixture_id, season, api_key, kickoff_at=kickoff_at, now=attempt_now,
                **fetch_kwargs,
            )
        except RateLimitError:
            time.sleep(RATE_LIMIT_PAUSE_SECONDS)
    # One last attempt whose exception (if any) propagates to the caller.
    attempt_now = now or utcnow()
    return ingest.ingest_fixture_player_stats(
        conn, fixture_id, season, api_key, kickoff_at=kickoff_at, now=attempt_now, **fetch_kwargs,
    )


def _content_hash(stats: list) -> str:
    """Hash of the PERSISTED rows (not the raw provider payload, which
    §13.4 data minimisation means this corpus never retains) — detects
    whether a FETCHED fixture's stored data has drifted from what
    re-ingesting it now would produce, without re-parsing anything."""
    canonical = json.dumps(
        sorted((s.player_id, s.minutes, s.goals, s.assists) for s in stats)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HarvestBatchResult:
    attempted: int
    fetched: int
    no_data: int
    retryable_failures: int
    final_failures: int
    stopped_reason: str | None  # None = ran out of pending work, not quota


def harvest_next_batch(
    conn: sqlite3.Connection,
    season: int,
    competition: str,
    api_key: str,
    max_fixtures: int | None = None,
    now: datetime | None = None,
    **fetch_kwargs,
) -> HarvestBatchResult:
    """Work down PENDING (and retryable) fixtures for one season/
    competition, stopping the instant the shared quota machinery reports
    the day's budget is spent — never mid-fixture, never as a fixture-
    level failure. `**fetch_kwargs` (e.g. `quota_path`) passes through to
    every fetch, so tests (and any caller wanting an isolated quota file)
    never have to touch the real project quota state.

    `now` is passed straight through to `_fetch_with_rate_limit_pacing`
    WITHOUT resolving it here first — a batch is dozens of real HTTP calls
    over several real minutes, and freezing one instant for all of them
    made every successful call record an identical `recent_calls`
    timestamp, guaranteeing a per-minute RateLimitError at request 11 that
    real elapsed time (from `time.sleep`) could never actually clear. Only
    `last_attempt_at` (informational ledger bookkeeping, not quota-
    critical) uses a value resolved once for the whole batch."""
    bookkeeping_now = now or utcnow()
    rows = _pending_rows(conn, season, competition, max_fixtures)

    attempted = fetched = no_data = retryable = final_failure = 0
    stopped_reason: str | None = None

    for row in rows:
        attempt_count = row["attempt_count"] + 1
        last_attempt_at = to_iso(bookkeeping_now)
        try:
            stats = _fetch_with_rate_limit_pacing(
                conn, row["fixture_id"], season, api_key, row["kickoff_at"], now,
                **fetch_kwargs,
            )
        except QuotaExceededError as exc:
            stopped_reason = str(exc)
            break
        except RateLimitError as exc:
            # Paced out MAX_RATE_LIMIT_PAUSES times without the window
            # clearing — treat as a stop, same as quota exhaustion, rather
            # than spinning forever against a genuine outage.
            stopped_reason = str(exc)
            break
        except Exception:
            attempted += 1
            new_status = FINAL_FAILURE if attempt_count >= MAX_ATTEMPTS else RETRYABLE_FAILURE
            if new_status == FINAL_FAILURE:
                final_failure += 1
            else:
                retryable += 1
            conn.execute(
                "UPDATE fpl_fixture_harvest_state SET player_stats_status = ?, "
                "attempt_count = ?, last_attempt_at = ? WHERE id = ?",
                (new_status, attempt_count, last_attempt_at, row["id"]),
            )
            continue

        attempted += 1
        # `stats` is the persisted (non-zero-minute) rows; content_hash is
        # computed from those rather than the raw provider payload, since
        # that is what this corpus actually retains (§13.4 minimisation).
        row_count = len(stats)
        if row_count == 0:
            no_data += 1
            new_status = NO_DATA
        else:
            fetched += 1
            new_status = FETCHED
        content_hash = _content_hash(stats)
        conn.execute(
            "UPDATE fpl_fixture_harvest_state SET player_stats_status = ?, "
            "attempt_count = ?, last_attempt_at = ?, content_hash = ?, "
            "row_count = ? WHERE id = ?",
            (new_status, attempt_count, last_attempt_at, content_hash, row_count, row["id"]),
        )

    return HarvestBatchResult(
        attempted=attempted, fetched=fetched, no_data=no_data,
        retryable_failures=retryable, final_failures=final_failure,
        stopped_reason=stopped_reason,
    )


def is_season_complete(
    conn: sqlite3.Connection, season: int, competition: str = HARVEST_COMPETITION
) -> bool:
    """A season is done when nothing PENDING or RETRYABLE_FAILURE remains
    — every seeded fixture has reached a terminal status. A stored
    RETRYABLE_FAILURE row always has `attempt_count < MAX_ATTEMPTS` by
    construction (`harvest_next_batch` reclassifies to FINAL_FAILURE the
    instant a retry would reach the ceiling), so counting its mere
    presence is sufficient — no attempt_count check needed here too.

    A season with NO rows seeded at all is explicitly NOT complete —
    without this, an empty `harvest_status_counts` (all `.get(...)`
    defaulting to 0) would satisfy the condition above and report a
    season "done" before `seed_harvest_targets` had ever run for it.
    """
    counts = harvest_status_counts(conn, season, competition)
    if not counts:
        return False
    return counts.get(PENDING, 0) == 0 and counts.get(RETRYABLE_FAILURE, 0) == 0


def harvest_complete(
    conn: sqlite3.Connection,
    seasons: tuple[int, ...] = HARVEST_SEASONS,
    competition: str = HARVEST_COMPETITION,
) -> bool:
    return all(is_season_complete(conn, s, competition) for s in seasons)


@dataclass(frozen=True)
class DailyHarvestResult:
    season_results: dict[int, HarvestBatchResult]
    requests_used_today_before: int
    requests_used_today_after: int
    all_seasons_complete: bool


def run_daily_harvest(
    conn: sqlite3.Connection,
    api_key: str,
    daily_cap: int = 85,
    seasons: tuple[int, ...] = HARVEST_SEASONS,
    competition: str = HARVEST_COMPETITION,
    now: datetime | None = None,
    quota_path=None,
    **fetch_kwargs,
) -> DailyHarvestResult:
    """One day's worth of harvesting, in `seasons` order, never spending
    more than `daily_cap` api_football requests TOTAL today — a ceiling
    on the whole day's usage (shared with whatever else, e.g. the live
    availability worker, already spent quota today), not an allowance
    reserved on top of it. Finishes each season before starting the
    next; a season that completes with daily budget still available lets
    the next season start the same day rather than waiting for tomorrow.
    Runs no model evaluation and freezes nothing — `all_seasons_complete`
    only reports the fact, it does not act on it.

    `quota_path` (default `None` -> the real project quota file,
    `sources.quota`'s own default) is read directly for the budget check
    AND passed through to every fetch, so the two never disagree about
    which quota file they mean — the same override point tests use to
    stay isolated from real project state (see `test_api_football.py`'s
    `quota_path=path` convention).

    `now`, if the CALLER pins it (tests wanting a deterministic quota
    day-bucket), is reused as-is for every `quota_mod.used()` check and
    every fetch below — `quota.used()` buckets by calendar day
    (`period_key`), so a test asserting on a specific day's usage needs
    every call to agree on which day that is.

    If the caller leaves `now` as `None` (the real driver script's actual
    call), this function does NOT synthesize one fixed instant and reuse
    it for the whole run: `reference_now` below is resolved once purely
    for THIS function's OWN budget-check/reporting reads, but the
    original (still-`None`) `now` is passed straight through to
    `ensure_season_seeded`/`harvest_next_batch`, letting each individual
    HTTP request resolve its own real time. A batch is dozens of requests
    over several real minutes; freezing one instant across all of them
    used to make every successful request record an identical
    `recent_calls` timestamp, so the per-minute rate limit tripped at
    request 11 every single day and real elapsed sleep time could never
    clear it (`sources.quota`'s 60-second window never advanced because
    `now` never did). The residual risk — a request genuinely crossing a
    real midnight mid-run and landing in a different day's bucket than
    this function's own pre-check assumed — is a rare, small
    mis-accounting, not a guaranteed daily failure like the one above.
    """
    reference_now = now or utcnow()
    used_before = quota_mod.used(api_football.QUOTA, quota_path, reference_now)
    season_results: dict[int, HarvestBatchResult] = {}

    for season in seasons:
        if is_season_complete(conn, season, competition):
            continue
        remaining_budget = daily_cap - quota_mod.used(api_football.QUOTA, quota_path, reference_now)
        if remaining_budget <= 0:
            break
        try:
            # A season newly added to HARVEST_SEASONS (e.g. 2024/25) has
            # no rows yet — seed it here, on budget, rather than requiring
            # a separate manual step every time the scope grows.
            ensure_season_seeded(
                conn, season, competition, api_key, now=now,
                quota_path=quota_path, **fetch_kwargs,
            )
        except (QuotaExceededError, RateLimitError) as exc:
            season_results[season] = HarvestBatchResult(
                attempted=0, fetched=0, no_data=0, retryable_failures=0,
                final_failures=0, stopped_reason=str(exc),
            )
            break
        remaining_budget = daily_cap - quota_mod.used(api_football.QUOTA, quota_path, reference_now)
        if remaining_budget <= 0:
            break
        result = harvest_next_batch(
            conn, season, competition, api_key, max_fixtures=remaining_budget, now=now,
            quota_path=quota_path, **fetch_kwargs,
        )
        season_results[season] = result
        if result.stopped_reason is not None:
            break  # real quota (or a stuck rate limit) is spent for today

    return DailyHarvestResult(
        season_results=season_results,
        requests_used_today_before=used_before,
        requests_used_today_after=quota_mod.used(api_football.QUOTA, quota_path, reference_now),
        all_seasons_complete=harvest_complete(conn, seasons, competition),
    )
