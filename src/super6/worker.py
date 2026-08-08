"""Poll worker: odds -> movement -> conditional reforecast -> events.

The §9 loop, assembled from the pieces:

    find due jobs
        -> poll odds (approved sources only)
        -> persist bookmaker + consensus snapshots
        -> detect movement against stored references
        -> reforecast ONLY where movement was material
        -> emit structured events
        -> recompute next_run_at from the cadence ladder

The forecast is never adjusted by the movement detector. The frozen model
already consumes the new consensus through its w=0.5 blend, so a moved
market changes P(score) on the next forecast run by itself. The detector
decides *whether to run that forecast* and what to tell the user — it
does not touch probabilities (§32 double-counting).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import cadence, divergence, events, movement, scheduler
from .config import DEFAULT_MOVEMENT, FROZEN_PHASE1_V1, ModelConfig, MovementConfig
from .model.matrix import score_matrix
from .recommend import FixtureRecommendation, recommend_fixture
from .round import Round, load_round
from .sources import pipeline
from .sources.odds_api import FREE_TIER_MONTHLY_CREDITS, credits_used_this_month

# Below this fraction of the monthly budget remaining, the fine-grained
# cadence degrades rather than exhausting credits before the deadline.
BUDGET_DEGRADE_AT = 0.15


@dataclass
class PollOutcome:
    round_number: int
    fixtures_covered: int
    credits_spent: int
    reforecast: int
    events: list[events.Event]
    next_run_at: datetime | None
    # Fixtures whose market moved materially with no availability event
    # nearby — the §21 "REVIEW REQUIRED: no confirmed news detected" case,
    # and the trigger for a targeted knowledge investigation.
    unexplained_moves: list[tuple[str, str, str]] = field(default_factory=list)


def budget_constrained(
    quota_path: Path | None = None, budget: int = FREE_TIER_MONTHLY_CREDITS
) -> bool:
    from .sources.odds_api import DEFAULT_QUOTA_PATH

    used = credits_used_this_month(quota_path or DEFAULT_QUOTA_PATH)
    return (budget - used) < budget * BUDGET_DEGRADE_AT


def _pick_of(rec: FixtureRecommendation) -> str:
    return rec.pick


def poll_round(
    rnd: Round,
    api_key: str,
    router,
    conn: sqlite3.Connection | None = None,
    now: datetime | None = None,
    model_cfg: ModelConfig = FROZEN_PHASE1_V1,
    move_cfg: MovementConfig = DEFAULT_MOVEMENT,
    previous_picks: dict[tuple[str, str], tuple[str, str]] | None = None,
    consensus_path: Path = pipeline.CONSENSUS_SNAPSHOT_PATH,
    **fetch_kwargs,
) -> PollOutcome:
    """One polling cycle for a round.

    `previous_picks` maps (home, away) -> (pick, confidence) from the last
    forecast, so a changed recommendation can be detected. `router` is the
    per-division parameter lookup used by the frozen model.
    """
    now = now or datetime.now(timezone.utc)

    # History must be read BEFORE the new snapshot is written, or the
    # "previous poll" reference would be the poll we just took.
    history = movement.ConsensusHistory.load(consensus_path)

    acquired = pipeline.acquire_round_odds(
        rnd, api_key, now=now, consensus_snapshot_path=consensus_path, **fetch_kwargs
    )

    emitted: list[events.Event] = []
    reforecast_count = 0
    unexplained: list[tuple[str, str, str]] = []
    previous_picks = previous_picks or {}

    for fixture_odds in acquired.fixtures:
        home, away = fixture_odds.home, fixture_odds.away
        consensus = fixture_odds.consensus
        if consensus is None:
            emitted.append(
                events.make_event(
                    events.EventType.POLL_FAILED,
                    events.Severity.INFORMATIONAL,
                    f"No market view: {fixture_odds.reason}. Using model only.",
                    round_number=rnd.round_number,
                    home_team=home,
                    away_team=away,
                    now=now,
                )
            )
            continue

        emitted.append(
            events.make_event(
                events.EventType.MARKET_SNAPSHOT,
                events.Severity.INFORMATIONAL,
                f"{consensus.bookmaker_count} bookmakers, "
                f"dispersion {consensus.dispersion:.3f}",
                round_number=rnd.round_number,
                home_team=home,
                away_team=away,
                detail={
                    "consensus_h": round(consensus.p_home, 4),
                    "consensus_d": round(consensus.p_draw, 4),
                    "consensus_a": round(consensus.p_away, 4),
                    "stale_excluded": consensus.stale_excluded,
                },
                now=now,
            )
        )

        reports = movement.reference_reports(consensus, history, now=now, cfg=move_cfg)
        primary = reports.get(movement.PREVIOUS_POLL)
        if primary is None or not primary.should_reforecast:
            continue

        is_major = primary.materiality == movement.Materiality.MAJOR.value

        # §21: a material move with no nearby team news is the
        # "REVIEW REQUIRED — no confirmed news detected" case. Record it
        # so the knowledge layer can investigate, rather than inventing an
        # explanation here.
        if is_major:
            from .availability import attribution, store as avail_store

            known = avail_store.events_for_fixture(conn, home, away) if conn else []
            if attribution.best_explanation(primary, known) is None:
                unexplained.append((home, away, primary.describe()))

        emitted.append(
            events.make_event(
                events.EventType.MARKET_MAJOR_MOVEMENT
                if is_major
                else events.EventType.MARKET_MOVEMENT,
                events.Severity.IMPORTANT if is_major else events.Severity.INFORMATIONAL,
                primary.describe(),
                round_number=rnd.round_number,
                home_team=home,
                away_team=away,
                detail={
                    "movement_strength_pp": round(primary.movement_strength, 2),
                    "market_agreement": primary.agreement,
                    "dispersion": round(primary.dispersion, 4),
                    "bookmakers": primary.bookmaker_count,
                    "reference": primary.reference,
                    "reference_at": primary.reference_at,
                    **{
                        f"delta_{label}_pp": round(r.movement_strength, 2)
                        for label, r in reports.items()
                        if label != movement.PREVIOUS_POLL
                    },
                },
                now=now,
            )
        )

        # Material movement -> reforecast this fixture only.
        params = router(home, away)
        lam_h, lam_a = params.lambdas(home, away, model_cfg)
        raw = score_matrix(lam_h, lam_a, params.rho, model_cfg)
        div = divergence.assess(
            raw,
            consensus.p_home,
            consensus.p_draw,
            consensus.p_away,
            consensus.dispersion,
            consensus.bookmaker_count,
        )
        rec = recommend_fixture(
            params, home, away, model_cfg, odds=consensus.as_odds()
        )
        reforecast_count += 1

        emitted.append(
            events.make_event(
                events.EventType.FORECAST_RECALCULATED,
                events.Severity.INFORMATIONAL,
                f"{_pick_of(rec)} (EV {rec.ev:.2f}) — {div.describe()}",
                round_number=rnd.round_number,
                home_team=home,
                away_team=away,
                detail={
                    "pick": _pick_of(rec),
                    "p_result": round(rec.p_result, 4),
                    "p_score": round(rec.p_score, 4),
                    "ev": round(rec.ev, 3),
                    "confidence": rec.confidence,
                    "model_vs_market_tvd": round(div.model_vs_market, 4),
                    "book_dispersion": round(div.book_dispersion, 4),
                    "market_divergence": divergence.divergence_factor(div, move_cfg),
                },
                now=now,
            )
        )

        prev = previous_picks.get((home, away))
        if prev is not None:
            prev_pick, prev_conf = prev
            if prev_pick != _pick_of(rec):
                emitted.append(
                    events.make_event(
                        events.EventType.PREDICTION_CHANGED,
                        events.Severity.IMPORTANT,
                        f"{prev_pick} -> {_pick_of(rec)}",
                        round_number=rnd.round_number,
                        home_team=home,
                        away_team=away,
                        detail={
                            "previous_pick": prev_pick,
                            "new_pick": _pick_of(rec),
                            "reason": primary.describe(),
                            "market_agreement": primary.agreement,
                        },
                        now=now,
                    )
                )
            # Confidence is a separate dimension from probability (§20,
            # §68) — a moved market can change P(score) without changing
            # CONF, and that is recorded as its own event, not folded in.
            if prev_conf != rec.confidence:
                emitted.append(
                    events.make_event(
                        events.EventType.CONFIDENCE_CHANGED,
                        events.Severity.INFORMATIONAL,
                        f"{prev_conf} -> {rec.confidence}",
                        round_number=rnd.round_number,
                        home_team=home,
                        away_team=away,
                        detail={
                            "previous_confidence": prev_conf,
                            "new_confidence": rec.confidence,
                        },
                        now=now,
                    )
                )

    next_run = cadence.next_poll_at(
        rnd.deadline_at, now, budget_constrained=budget_constrained()
    )
    return PollOutcome(
        round_number=rnd.round_number,
        fixtures_covered=acquired.covered,
        credits_spent=acquired.credits_spent,
        reforecast=reforecast_count,
        events=emitted,
        next_run_at=next_run,
        unexplained_moves=unexplained,
    )


def _run_availability_job(
    conn: sqlite3.Connection,
    rnd: Round,
    job,
    api_key: str | None,
    season: int,
    now: datetime,
) -> PollOutcome:
    """Structured availability polling (injuries + confirmed XIs)."""
    from .availability import worker as availability_worker

    if not api_key:
        return PollOutcome(
            round_number=rnd.round_number, fixtures_covered=0,
            credits_spent=0, reforecast=0,
            events=[
                events.make_event(
                    events.EventType.POLL_FAILED,
                    events.Severity.INFORMATIONAL,
                    "Availability polling skipped: no API_FOOTBALL_KEY.",
                    round_number=rnd.round_number, now=now,
                )
            ],
            next_run_at=cadence.next_poll_at(rnd.deadline_at, now),
        )

    result = availability_worker.poll_availability(
        conn, rnd, "E0", season, api_key, now=now, kickoff_at=rnd.deadline_at
    )
    return PollOutcome(
        round_number=rnd.round_number,
        fixtures_covered=result.lineups_ingested,
        credits_spent=result.requests_spent,
        reforecast=0,          # availability never triggers a reforecast
        events=result.events,
        next_run_at=cadence.next_poll_at(rnd.deadline_at, now),
    )


def _run_knowledge_job(
    conn: sqlite3.Connection,
    rnd: Round,
    job,
    tavily_key: str | None,
    extractor,
    mode: str,
    now: datetime,
) -> PollOutcome:
    """Routine bounded knowledge research for a round."""
    from .knowledge import queries
    from .knowledge import worker as knowledge_worker

    emitted: list[events.Event] = []
    if not (tavily_key and extractor):
        emitted.append(
            events.make_event(
                events.EventType.POLL_FAILED,
                events.Severity.INFORMATIONAL,
                "Knowledge research skipped: needs TAVILY_API_KEY and a "
                "configured extractor.",
                round_number=rnd.round_number, now=now,
            )
        )
    else:
        for fixture in rnd.fixtures:
            for team in (fixture.home, fixture.away):
                result = knowledge_worker.research(
                    conn, fixture.home, fixture.away,
                    queries.plan_team_queries(team), tavily_key, extractor,
                    now=now, round_number=rnd.round_number, mode=mode,
                )
                emitted.extend(result.events)

    return PollOutcome(
        round_number=rnd.round_number, fixtures_covered=0, credits_spent=0,
        reforecast=0, events=emitted,
        next_run_at=cadence.next_poll_at(rnd.deadline_at, now),
    )


def _investigate_unexplained(
    conn: sqlite3.Connection,
    outcome: PollOutcome,
    tavily_key: str,
    extractor,
    mode: str,
    now: datetime,
) -> list[events.Event]:
    """Targeted investigation of market moves with no known explanation."""
    from .knowledge import worker as knowledge_worker

    emitted: list[events.Event] = []
    for home, away, description in outcome.unexplained_moves:
        result = knowledge_worker.investigate_movement(
            conn, home, away, description, tavily_key, extractor,
            now=now, round_number=outcome.round_number, mode=mode,
        )
        emitted.extend(result.events)
    return emitted


def run_due_jobs(
    conn: sqlite3.Connection,
    api_key: str,
    router_factory,
    now: datetime | None = None,
    event_log: Path = events.DEFAULT_EVENT_LOG,
    availability_key: str | None = None,
    tavily_key: str | None = None,
    extractor=None,
    extractor_mode: str = "shadow",
    season: int = 2026,
    **poll_kwargs,
) -> list[PollOutcome]:
    """Claim and run every due job, dispatching by job type and
    rescheduling each from the cadence.

    A market move that no known team news explains triggers a targeted
    knowledge investigation — the one place the knowledge layer runs
    without being separately scheduled, because that is exactly the
    event-driven case it exists for.
    """
    now = now or datetime.now(timezone.utc)
    scheduler.reclaim_stuck_jobs(conn)
    outcomes: list[PollOutcome] = []

    for job in scheduler.claim_due_jobs(conn, now):
        if cadence.poll_interval(job.deadline, now) is None:
            scheduler.complete_job(conn, job.id, None)
            continue
        try:
            rnd = load_round(Path(job.round_path))

            if job.job_type == scheduler.JobType.POLL_AVAILABILITY.value:
                outcome = _run_availability_job(
                    conn, rnd, job, availability_key, season, now
                )
            elif job.job_type == scheduler.JobType.KNOWLEDGE_RESEARCH.value:
                outcome = _run_knowledge_job(
                    conn, rnd, job, tavily_key, extractor, extractor_mode, now
                )
            else:
                outcome = poll_round(
                    rnd, api_key, router_factory(), conn=conn, now=now,
                    **poll_kwargs,
                )
                # Event-driven follow-up: explain what the market did.
                if outcome.unexplained_moves and tavily_key and extractor:
                    outcome.events.extend(
                        _investigate_unexplained(
                            conn, outcome, tavily_key, extractor,
                            extractor_mode, now,
                        )
                    )

            events.append(outcome.events, event_log)
            scheduler.complete_job(conn, job.id, outcome.next_run_at)
            outcomes.append(outcome)
        except Exception as exc:  # a bad poll must not kill the loop
            events.append(
                [
                    events.make_event(
                        events.EventType.POLL_FAILED,
                        events.Severity.IMPORTANT,
                        f"Poll failed: {exc}",
                        round_number=job.round_number,
                        now=now,
                    )
                ],
                event_log,
            )
            retry_at = cadence.next_poll_at(job.deadline, now)
            scheduler.complete_job(conn, job.id, retry_at, error=str(exc))
    return outcomes
