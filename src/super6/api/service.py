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
from ..recommend import RoundRecommendation
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
    )


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
    known_teams: set[str] | None = None,
    corpus: str = "E0+E1",
) -> RoundSummary:
    markets = markets or {}
    availabilities = availabilities or {}
    fixtures = [
        build_fixture_forecast(
            fixture,
            markets.get((fixture.home, fixture.away)),
            availabilities.get((fixture.home, fixture.away)),
            modellability=(
                build_modellability(fixture.home, fixture.away, known_teams, corpus)
                if known_teams is not None
                else None
            ),
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
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT COUNT(*) c FROM jobs WHERE status = 'PENDING'"
            ).fetchone()
            pending = int(row["c"])
        except sqlite3.Error:
            pending = 0

    warnings = []
    for source in sources:
        if source.usage_status == "REVIEW_REQUIRED":
            warnings.append(
                f"{source.source_id} is REVIEW_REQUIRED — not usable until approved"
            )

    return SystemHealth(
        sources=sources,
        scheduler_jobs_pending=pending,
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
