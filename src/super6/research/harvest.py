"""AVAILABILITY_ORACLE_RESEARCH_V1 harvester.

Restart-safe, resumable, quota-aware. Elapsed time is deliberately not
part of the design: the harvester accumulates whatever the quota permits
and stops cleanly, because a half-finished dataset that gets treated as
"probably complete" is the failure mode worth engineering against.

    PENDING           not yet attempted
    FETCHED           data stored
    NO_DATA           endpoint answered, legitimately empty
    FAILED_RETRYABLE  transient; will be retried
    FAILED_FINAL      permanent; never retried

Terminal states are never re-fetched merely because the process
restarted.

**Deadline classification is applied but never used to filter.** Stage A
measures the perfect-information ceiling, so future information stays in
the dataset — labelled, not removed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..db import from_iso, to_iso, utcnow
from ..sources import api_football
from ..sources.quota import QuotaExceededError, RateLimitError, remaining
from . import deadline, store
from .sampling import UniverseFixture, build_sample, stratum_counts

STUDY = "AVAILABILITY_ORACLE_RESEARCH_V1"
ENDPOINTS = ("injuries", "lineups")

# Injury `type` values meaning the player did not feature.
ABSENT_TYPES = {"missing fixture"}
DOUBT_TYPES = {"questionable"}

# Leave a little quota unspent so an unrelated live operation is not
# starved by a background research harvest.
QUOTA_RESERVE = 5


@dataclass
class HarvestResult:
    attempted: int = 0
    fetched: int = 0
    no_data: int = 0
    failed_retryable: int = 0
    failed_final: int = 0
    stopped_reason: str = ""
    quota_remaining: int = 0
    notes: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return (
            f"attempted {self.attempted}, fetched {self.fetched}, "
            f"no_data {self.no_data}, retryable {self.failed_retryable}, "
            f"final {self.failed_final}; stopped: {self.stopped_reason}; "
            f"quota remaining {self.quota_remaining}"
        )


# ---- universe -----------------------------------------------------------


def build_universe(api_key: str, **kwargs) -> list[UniverseFixture]:
    """Every eligible fixture: PL + Championship, 2022/23 and 2023/24."""
    universe: list[UniverseFixture] = []
    for division in ("E0", "E1"):
        for season in (2022, 2023):
            for fixture in api_football.fetch_fixtures(
                division, season, api_key, **kwargs
            ):
                if not fixture.fixture_id:
                    continue
                universe.append(
                    UniverseFixture(
                        fixture_id=fixture.fixture_id,
                        division=division,
                        season=season,
                        matchday=None,
                        home_team=fixture.home_team,
                        away_team=fixture.away_team,
                        kickoff_at=fixture.kickoff_at,
                    )
                )
    return universe


def infer_matchdays(universe: list[UniverseFixture]) -> list[UniverseFixture]:
    """API-Football's fixture list does not expose a league matchday in the
    shape this adapter parses, so matchday is inferred from kick-off order
    within each competition-season — sufficient for phase stratification,
    which only needs early/middle/late."""
    out: list[UniverseFixture] = []
    grouped: dict[tuple[str, int], list[UniverseFixture]] = {}
    for f in universe:
        grouped.setdefault((f.division, f.season), []).append(f)

    for (division, season), fixtures in grouped.items():
        fixtures.sort(key=lambda f: f.kickoff_at)
        per_round = 10 if division == "E0" else 12
        for index, f in enumerate(fixtures):
            out.append(
                UniverseFixture(
                    fixture_id=f.fixture_id,
                    division=division,
                    season=season,
                    matchday=(index // per_round) + 1,
                    home_team=f.home_team,
                    away_team=f.away_team,
                    kickoff_at=f.kickoff_at,
                )
            )
    return out


def create_sample(
    conn: sqlite3.Connection, universe: list[UniverseFixture], seed: int | None = None
) -> dict:
    """Draw and persist the sample frame, then seed the request queue."""
    from .sampling import SEED

    seed = SEED if seed is None else seed
    if store.sample_size(conn):
        return {
            "created": 0,
            "existing": store.sample_size(conn),
            "note": "sample already frozen — the frame is immutable",
        }

    rows = build_sample(infer_matchdays(universe), seed=seed)
    stored = store.persist_sample(conn, rows, seed, to_iso(utcnow()))
    requests_created = store.seed_requests(conn, ENDPOINTS)
    return {
        "created": stored,
        "requests": requests_created,
        "strata": stratum_counts(rows),
        "seed": seed,
    }


# ---- harvesting ---------------------------------------------------------


def _fetch(endpoint: str, fixture_id: str, api_key: str, **kwargs):
    """One request. Returns (payload, is_empty)."""
    if endpoint == "injuries":
        records = api_football.fetch_injuries_for_fixture(
            fixture_id, api_key, **kwargs
        )
        return [r.__dict__ for r in records], not records
    records = api_football.fetch_lineups(fixture_id, api_key, **kwargs)
    payload = [
        {
            "team": r.team,
            "formation": r.formation,
            "start_xi": r.start_xi,
            "substitutes": r.substitutes,
        }
        for r in records
    ]
    return payload, not records


def harvest(
    conn: sqlite3.Connection,
    api_key: str,
    max_requests: int | None = None,
    quota_reserve: int = QUOTA_RESERVE,
    pace_seconds: float = 6.5,
    sleep=None,
    **kwargs,
) -> HarvestResult:
    """Process pending requests until quota, limit or work runs out.

    Paces itself under the 10/minute limit so one run can drain the daily
    budget rather than stopping after ten requests. This is throughput
    within a run, not elapsed time as part of the research design — the
    dataset is complete when the request queue is settled, whenever that
    happens to be.
    """
    import time

    sleep = sleep or time.sleep
    result = HarvestResult()
    first = True
    pending = store.pending_requests(conn)
    if not pending:
        result.stopped_reason = "no pending requests"
        result.quota_remaining = remaining(api_football.QUOTA)
        return result

    for request in pending:
        if max_requests is not None and result.attempted >= max_requests:
            result.stopped_reason = f"reached --max-requests {max_requests}"
            break
        if remaining(api_football.QUOTA) <= quota_reserve:
            result.stopped_reason = (
                f"quota reserve reached ({quota_reserve} left unspent); "
                "resume in the next window"
            )
            break

        if not first and pace_seconds > 0:
            sleep(pace_seconds)
        first = False

        now = to_iso(utcnow())
        result.attempted += 1
        try:
            payload, empty = _fetch(
                request["endpoint"], request["fixture_id"], api_key, **kwargs
            )
        except (QuotaExceededError, RateLimitError) as exc:
            result.stopped_reason = f"quota/rate limit: {exc}"
            result.attempted -= 1   # not actually attempted
            break
        except requests.RequestException as exc:
            store.mark_request(
                conn, request["id"], store.RequestState.FAILED_RETRYABLE,
                error=str(exc)[:300], attempted_at=now,
            )
            result.failed_retryable += 1
            continue
        except Exception as exc:
            # A plan/permission error will never succeed on retry.
            final = "plan" in str(exc).lower() or "not have access" in str(exc).lower()
            store.mark_request(
                conn, request["id"],
                store.RequestState.FAILED_FINAL if final
                else store.RequestState.FAILED_RETRYABLE,
                error=str(exc)[:300], attempted_at=now,
            )
            if final:
                result.failed_final += 1
                result.notes.append(str(exc)[:160])
            else:
                result.failed_retryable += 1
            continue

        if empty:
            store.mark_request(
                conn, request["id"], store.RequestState.NO_DATA,
                retrieved_at=now, attempted_at=now,
            )
            result.no_data += 1
        else:
            store.store_observation(
                conn, request["fixture_id"], request["endpoint"], payload, now
            )
            store.mark_request(
                conn, request["id"], store.RequestState.FETCHED,
                retrieved_at=now, attempted_at=now,
            )
            result.fetched += 1

    if not result.stopped_reason:
        result.stopped_reason = "all pending work processed"
    result.quota_remaining = remaining(api_football.QUOTA)
    return result


# ---- derived facts ------------------------------------------------------


def classify_information(
    injuries: list[dict] | None, lineups: list[dict] | None
) -> tuple[bool, list[str], dict]:
    """Does this fixture's availability data actually *contain* anything?

    Distinguishes "availability data exists" from "availability data is
    informative" — 200 perfectly ordinary starting elevens tell us very
    little.
    """
    reasons: list[str] = []
    absent = doubt = 0
    for record in injuries or []:
        raw = str(record.get("raw_type", "")).lower()
        if raw in ABSENT_TYPES:
            absent += 1
        elif raw in DOUBT_TYPES:
            doubt += 1

    if absent >= 1:
        reasons.append(f"{absent} confirmed absence(s)")
    if absent >= 3:
        reasons.append("multiple regular starters absent")
    if doubt >= 2:
        reasons.append(f"{doubt} doubtful player(s)")

    counts = {
        "injury_total": len(injuries or []),
        "absent_count": absent,
        "doubt_count": doubt,
        "lineup_teams": len(lineups or []),
    }
    return bool(reasons), reasons, counts


def derive_facts(conn: sqlite3.Connection) -> int:
    """Compute per-fixture research facts from stored observations.

    Deadline classification is recorded here and is **not** used to filter
    anything — Stage A measures the perfect-information ceiling.
    """
    import json

    written = 0
    for row in store.sample_rows(conn):
        fixture_id = row["fixture_id"]
        injuries = store.observation(conn, fixture_id, "injuries")
        lineups = store.observation(conn, fixture_id, "lineups")
        if injuries is None and lineups is None:
            continue

        informative, reasons, counts = classify_information(injuries, lineups)

        lineup_know = injury_know = "UNKNOWN"
        if row["matchday_lock"] and row["kickoff_at"]:
            try:
                kickoff = from_iso(row["kickoff_at"])
                lock = from_iso(row["matchday_lock"])
                lineup_know = deadline.classify_lineup(kickoff, lock).value
                injury_know = deadline.classify_injury_report(kickoff, lock).value
            except (ValueError, TypeError):
                pass

        formations = {l.get("team"): l.get("formation") for l in (lineups or [])}
        formation_values = list(formations.values())

        conn.execute(
            "INSERT INTO research_fixture_facts (fixture_id, has_injury_data, "
            "has_lineup_data, injury_total, absent_count, doubt_count, "
            "lineup_teams, formation_home, formation_away, "
            "lineup_knowability, injury_knowability, would_change_feature, "
            "change_reasons) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (fixture_id) DO UPDATE SET "
            "has_injury_data=excluded.has_injury_data, "
            "has_lineup_data=excluded.has_lineup_data, "
            "injury_total=excluded.injury_total, "
            "absent_count=excluded.absent_count, "
            "doubt_count=excluded.doubt_count, "
            "lineup_teams=excluded.lineup_teams, "
            "formation_home=excluded.formation_home, "
            "formation_away=excluded.formation_away, "
            "lineup_knowability=excluded.lineup_knowability, "
            "injury_knowability=excluded.injury_knowability, "
            "would_change_feature=excluded.would_change_feature, "
            "change_reasons=excluded.change_reasons",
            (
                fixture_id,
                int(injuries is not None),
                int(lineups is not None),
                counts["injury_total"],
                counts["absent_count"],
                counts["doubt_count"],
                counts["lineup_teams"],
                formation_values[0] if formation_values else None,
                formation_values[1] if len(formation_values) > 1 else None,
                lineup_know,
                injury_know,
                int(informative),
                json.dumps(reasons),
            ),
        )
        written += 1
    return written
