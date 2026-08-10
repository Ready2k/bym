"""Application service: builds read-model contracts from domain objects.

This is the seam the UI will eventually sit behind. It maps domain
objects to contracts field by field — deliberately verbose, so a database
migration cannot reshape the API by accident and no column name leaks into
the frontend.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from .. import events as ev
from ..availability import completeness as completeness_mod
from ..availability import store as avail_store
from ..availability.model import LineupKind
from ..availability.reconcile import resolve_state
from ..db import to_iso
from ..model import golden_goal
from ..recommend import MARKET_ONLY_BASIS, RoundRecommendation
from ..round import Round
from ..sources.consensus import Consensus
from .contracts import (
    ArmPerformance,
    Modellability,
    AvailabilityItem,
    AvailabilityState,
    FixtureForecast,
    GoldenGoalRecommendation,
    KnowledgeEvent,
    MarketState,
    ProspectivePerformance,
    RoundSummary,
    ScoreAlternative,
    SourceHealth,
    SystemHealth,
)

MODEL_VERSION = "PHASE1_MODEL_V1"


def build_market_state(
    consensus: Consensus | None,
    movement=None,
    divergence_report=None,
    divergence_factor: str = "UNKNOWN",
    reason_unavailable: str | None = None,
) -> MarketState:
    if consensus is None:
        return MarketState(
            available=False,
            reason_unavailable=reason_unavailable or "no market view available",
            market_divergence=divergence_factor,
        )
    return MarketState(
        available=True,
        home_probability=round(consensus.p_home, 4),
        draw_probability=round(consensus.p_draw, 4),
        away_probability=round(consensus.p_away, 4),
        bookmaker_count=consensus.bookmaker_count,
        dispersion=round(consensus.dispersion, 4),
        market_agreement=getattr(movement, "agreement", None),
        last_movement=movement.describe() if movement is not None else None,
        movement_strength_pp=(
            round(movement.movement_strength, 2) if movement is not None else None
        ),
        materiality=getattr(movement, "materiality", None),
        model_vs_market=(
            round(divergence_report.model_vs_market, 4)
            if divergence_report is not None
            else None
        ),
        market_divergence=divergence_factor,
        stale=bool(consensus.stale_excluded) and consensus.bookmaker_count == 0,
    )


def build_availability_state(
    conn: sqlite3.Connection, home: str, away: str, has_market: bool
) -> AvailabilityState:
    events = avail_store.events_for_fixture(conn, home, away)
    lineups = avail_store.lineups_for_fixture(conn, home, away)
    report = completeness_mod.assess(home, away, lineups, has_market)

    items = [
        AvailabilityItem(
            team=state.team,
            player_name=state.player_name,
            status=state.event_type,
            certainty=state.claim_status,
            source=state.current.source_id,
            source_reliability=state.current.source_reliability,
            resolved=state.current.resolution == "RESOLVED",
        )
        for state in resolve_state(events)
    ]
    return AvailabilityState(
        confirmed_lineups=report.confirmed_lineups,
        expected_lineups=report.expected_lineups,
        data_quality=report.state,
        feature_completeness=completeness_mod.completeness_factor(report),
        items=items,
        lineup_surprise=_lineup_surprise(lineups),
    )


def _lineup_surprise(lineups) -> str | None:
    """Describe a confirmed XI that departs materially from the expected one.

    `availability/ingest` already raises LINEUP_SURPRISE as an *event* when
    this happens, but events are a feed — a fixture opened after the fact
    would show nothing. The read model needs the standing fact too, so it
    is recomputed here from the same stored lineups with the same rule
    rather than mirrored from the feed.

    Still `NONE DIRECTLY` on the forecast: this describes evidence, and no
    amount of it moves a frozen model.
    """
    from ..availability.lineups import compare_lineups

    latest: dict[tuple[str, str], object] = {}
    for lineup in lineups:  # store returns them ordered by observed_at
        latest[(lineup.team, lineup.kind)] = lineup

    descriptions = []
    for team, kind in list(latest):
        if kind != LineupKind.CONFIRMED.value:
            continue
        expected = latest.get((team, LineupKind.EXPECTED.value))
        if expected is None:
            continue
        try:
            diff = compare_lineups(expected, latest[(team, kind)])
        except ValueError:
            continue
        if diff.material:
            descriptions.append(f"{team}: {diff.describe()}")
    return "; ".join(descriptions) or None


def build_modellability(
    home: str, away: str, known_teams: set[str], corpus: str = "E0+E1"
) -> Modellability:
    """Flag fixtures the model cannot meaningfully forecast."""
    unknown = [t for t in (home, away) if t not in known_teams]
    if not unknown:
        return Modellability(modellable=True, corpus=corpus)
    return Modellability(
        modellable=False,
        unknown_teams=unknown,
        corpus=corpus,
        reason=(
            f"{', '.join(unknown)} not in the {corpus} training corpus; "
            "an unknown team receives the league-average prior, which can "
            "make it favourite over a genuinely below-average known team"
        ),
    )


def _basis_of(recommendation, modellability: Modellability | None) -> str:
    """Resolve what actually produced this forecast.

    A fixture the model cannot judge and which has no market to fall back
    on yields NONE: numbers still exist in the recommendation, but nothing
    in them is worth calling a forecast, and the UI needs to be told that
    rather than inferring it.
    """
    basis = getattr(recommendation, "basis", "PHASE1_MODEL_V1")
    if modellability is not None and not modellability.modellable:
        return basis if basis == MARKET_ONLY_BASIS else "NONE"
    return basis


def build_fixture_forecast(
    recommendation,
    market: MarketState | None = None,
    availability: AvailabilityState | None = None,
    alternatives_count: int = 5,
    modellability: Modellability | None = None,
) -> FixtureForecast:
    alternatives = [
        ScoreAlternative(
            score=score,
            score_probability=round(probability, 4),
            expected_points=round(ev_value, 3),
            is_recommendation=score == recommendation.pick,
        )
        for score, probability, ev_value in recommendation.alternatives(
            alternatives_count
        )
    ]
    return FixtureForecast(
        home_team=recommendation.home,
        away_team=recommendation.away,
        pick=recommendation.pick,
        result_probability=round(recommendation.p_result, 4),
        score_probability=round(recommendation.p_score, 4),
        expected_points=round(recommendation.ev, 3),
        confidence=recommendation.confidence,
        model_pick=recommendation.pick,
        modellability=modellability or Modellability(),
        forecast_basis=_basis_of(recommendation, modellability),
        alternatives=alternatives,
        market=market,
        availability=availability,
    )


def build_round_summary(
    rnd: Round,
    recommendation: RoundRecommendation,
    markets: dict[tuple[str, str], MarketState] | None = None,
    availabilities: dict[tuple[str, str], AvailabilityState] | None = None,
    submission_state: str = "NOT_READY",
    modellabilities: dict[tuple[str, str], Modellability] | None = None,
) -> RoundSummary:
    """`modellabilities` are decided by the caller, not re-derived here.

    The verdict changes how a fixture is *forecast*, so it must already
    have been made before the recommendation existed. Recomputing it at
    read-model time would create a second copy of the same decision, free
    to drift from the one the forecast actually used.
    """
    markets = markets or {}
    availabilities = availabilities or {}
    modellabilities = modellabilities or {}
    fixtures = [
        build_fixture_forecast(
            fixture,
            markets.get((fixture.home, fixture.away)),
            availabilities.get((fixture.home, fixture.away)),
            modellability=modellabilities.get((fixture.home, fixture.away)),
        )
        for fixture in recommendation.fixtures
    ]
    g = recommendation.golden
    return RoundSummary(
        round_number=rnd.round_number,
        deadline_at=to_iso(rnd.deadline_at),
        fixtures=fixtures,
        golden_goal=GoldenGoalRecommendation(
            minute=g.recommended_minute,
            sky_interval=golden_goal.sky_interval(g.recommended_minute),
            intervals={k: round(v, 4) for k, v in g.intervals().items()},
        ),
        expected_round_score=round(recommendation.expected_round_score, 3),
        model_version=MODEL_VERSION,
        submission_state=submission_state,
    )


def build_knowledge_events(
    events: list[ev.Event], mode: str = "shadow", limit: int = 50
) -> list[KnowledgeEvent]:
    knowledge_types = {
        ev.EventType.CLAIM_DISCOVERED.value,
        ev.EventType.CLAIM_REJECTED.value,
        ev.EventType.CLAIM_CORROBORATED.value,
        ev.EventType.CLAIM_CONTRADICTED.value,
        ev.EventType.CLAIM_SUPERSEDED.value,
        ev.EventType.KNOWLEDGE_EXTRACTION_FAILED.value,
        ev.EventType.INTELLIGENCE_EXPLAINS_MARKET_MOVE.value,
    }
    out = []
    for event in events:
        if event.event_type not in knowledge_types:
            continue
        detail = event.detail or {}
        out.append(
            KnowledgeEvent(
                occurred_at=event.occurred_at,
                event_type=event.event_type,
                severity=event.severity,
                summary=event.summary,
                acquisition_source=detail.get("acquisition_source"),
                origin_domain=detail.get("origin_domain"),
                origin_authority=detail.get("origin_authority"),
                published_at=detail.get("published_at"),
                event_at=detail.get("event_at"),
                evidence=detail.get("evidence"),
                story_cluster_id=detail.get("story_cluster_id"),
                promoted=mode == "active",
                extractor_mode=mode,
            )
        )
    return out[-limit:]


def build_system_health(
    conn: sqlite3.Connection | None = None,
    registry: dict | None = None,
    ollama_health=None,
    extractor_config=None,
    schema_version: int = 0,
    last_forecast_at: str | None = None,
) -> SystemHealth:
    from ..sources import api_football, quota as quota_mod, tavily
    from ..sources.odds_api import QUOTA as ODDS_QUOTA

    sources = []
    specs = {
        "the_odds_api": ODDS_QUOTA,
        "api_football": api_football.QUOTA,
        "tavily": tavily.QUOTA,
    }
    for source_id, record in sorted((registry or {}).items()):
        spec = specs.get(source_id)
        sources.append(
            SourceHealth(
                source_id=source_id,
                usage_status=record.usage_status,
                quota_used=quota_mod.used(spec) if spec else 0,
                quota_limit=spec.limit if spec else 0,
                quota_period=spec.period if spec else "",
            )
        )

    pending = 0
    next_check = None
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT COUNT(*) c, MIN(next_run_at) next_at "
                "FROM jobs WHERE status = 'PENDING'"
            ).fetchone()
            pending = int(row["c"])
            # Absent when nothing is scheduled, so the UI can say "not
            # monitoring" instead of implying a watch that does not exist.
            next_check = row["next_at"] if pending else None
        except sqlite3.Error:
            pending, next_check = 0, None

    # Recent extraction reliability. Read from what really ran rather than
    # probed here: a latency probe costs ~20s on a cold model, and putting
    # that on the page-load path is the exact mistake this replaced.
    recent_total = recent_failed = 0
    last_error = ""
    try:
        rows = conn.execute(
            "SELECT status, error FROM extraction_runs "
            "ORDER BY id DESC LIMIT 20"
        ).fetchall()
        recent_total = len(rows)
        for row in rows:
            if row["status"] != "OK":
                recent_failed += 1
                if not last_error and row["error"]:
                    last_error = str(row["error"])[:120]
    except sqlite3.Error:
        pass

    warnings = []
    if recent_total >= 5 and recent_failed / recent_total >= 0.25:
        share = recent_failed / recent_total
        warnings.append(
            f"knowledge extraction failing: {recent_failed} of the last "
            f"{recent_total} runs ({share:.0%}). Credits are spent on the "
            f"search before the model reads it. Last error: {last_error}"
        )
    for source in sources:
        # Only REVIEW_REQUIRED is a task. EVALUATED means assessed and not
        # adopted — warning about it demands action nobody intends to take,
        # and a warning nobody should act on trains you to ignore the panel.
        if source.usage_status == "REVIEW_REQUIRED":
            warnings.append(
                f"{source.source_id} is REVIEW_REQUIRED — review its terms, "
                f"then run `super6 sources approve {source.source_id}`"
            )
        # §25: a quota running out is the failure you notice last, because
        # the symptom is things quietly not happening. Say it while there
        # is still time to decide what to spend the rest on.
        if source.quota_limit:
            left = source.quota_limit - (source.quota_used or 0)
            if left <= 0:
                warnings.append(
                    f"{source.source_id} quota EXHAUSTED for this "
                    f"{source.quota_period} ({source.quota_used}/"
                    f"{source.quota_limit}) — it will stop returning data"
                )
            elif left <= source.quota_limit * 0.2:
                consequence = (
                    " Knowledge research degrades to a wider cadence rather "
                    "than going silent."
                    if source.source_id == "tavily"
                    else ""
                )
                warnings.append(
                    f"{source.source_id} quota low: {left} of "
                    f"{source.quota_limit} left this "
                    f"{source.quota_period}.{consequence}"
                )

    return SystemHealth(
        sources=sources,
        scheduler_jobs_pending=pending,
        last_forecast_at=last_forecast_at,
        next_scheduled_check=next_check,
        extractor=(
            "OllamaClaimExtractor"
            if extractor_config and extractor_config.kind == "ollama"
            else "NullExtractor"
        ),
        extractor_mode=extractor_config.mode if extractor_config else "shadow",
        ollama_reachable=(
            "YES" if ollama_health and ollama_health.reachable else
            "NO" if ollama_health else "UNKNOWN"
        ),
        ollama_model_available=(
            "YES" if ollama_health and ollama_health.model_available else
            "NO" if ollama_health else "UNKNOWN"
        ),
        database_schema_version=schema_version,
        extraction_recent_total=recent_total,
        extraction_recent_failed=recent_failed,
        extraction_last_error=last_error,
        warnings=warnings,
    )


def build_prospective_performance(
    standings_frame, season: int = 2026
) -> ProspectivePerformance:
    arms = [
        ArmPerformance(
            arm=row["arm"],
            fixtures=int(row["fixtures"]),
            rounds=int(row["rounds"]),
            points=int(row["points"]),
            points_per_round=float(row["pts/round"]),
            exact_scores=int(row["exact"]),
            exact_rate=float(row["exact%"]),
        )
        for _, row in standings_frame.iterrows()
    ]
    rounds = max((a.rounds for a in arms), default=0)
    return ProspectivePerformance(season=season, arms=arms, rounds_recorded=rounds)
