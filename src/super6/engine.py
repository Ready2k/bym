"""End-to-end engine run for a manually configured round.

    six fixtures + deadline (manual, §9.1)
        -> odds -> snapshots -> consensus
        -> PHASE1_MODEL_V1 -> six recommendations
        -> structured availability -> confirmed lineup if in window
        -> bounded knowledge research (shadow) -> validated claims
        -> activity records -> engine report

The report separates four layers, and states plainly that team/news
intelligence has no quantitative impact at this phase — because it does
not, and a report that blurred that would be the first step towards
someone believing otherwise.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import events as ev
from .api import service
from .api.contracts import AvailabilityState, MarketState, RoundSummary
from .config import FROZEN_PHASE1_V1
from .db import utcnow
from .knowledge.config import ExtractorConfig, ExtractorMode
from .recommend import recommend_round
from .round import Round
from .sources import pipeline

def to_iso_safe(value: datetime) -> str:
    from .db import to_iso

    return to_iso(value)


QUANTITATIVE_NOTE = (
    "Team and news intelligence at this phase has quantitative impact: "
    "NONE DIRECTLY. Only market consensus reaches the forecast, through "
    "PHASE1_MODEL_V1's frozen w=0.5 blend."
)


@dataclass
class EngineRunResult:
    round_summary: RoundSummary
    market_layer: dict = field(default_factory=dict)
    availability_layer: dict = field(default_factory=dict)
    knowledge_layer: dict = field(default_factory=dict)
    events: list[ev.Event] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def run_engine(
    conn: sqlite3.Connection,
    rnd: Round,
    router,
    odds_api_key: str | None = None,
    tavily_api_key: str | None = None,
    extractor=None,
    extractor_config: ExtractorConfig | None = None,
    now: datetime | None = None,
    run_knowledge: bool = False,
    season: int = 2026,
    **kwargs,
) -> EngineRunResult:
    """One full engine pass. Every external step degrades to 'absent'
    rather than failing the run."""
    now = now or utcnow()
    notes: list[str] = []
    all_events: list[ev.Event] = []
    markets: dict[tuple[str, str], MarketState] = {}
    consensus_by_fixture = {}

    # ---- market layer ---------------------------------------------------
    if odds_api_key:
        try:
            acquired = pipeline.acquire_round_odds(rnd, odds_api_key, now=now)
            rnd = pipeline.apply_to_round(rnd, acquired)
            for fixture_odds in acquired.fixtures:
                key = (fixture_odds.home, fixture_odds.away)
                consensus_by_fixture[key] = fixture_odds.consensus
                markets[key] = service.build_market_state(
                    fixture_odds.consensus,
                    reason_unavailable=fixture_odds.reason or None,
                )
            notes.append(
                f"market: {acquired.covered}/6 fixtures covered, "
                f"{acquired.credits_spent} credit(s)"
            )
        except Exception as exc:
            notes.append(f"market acquisition unavailable: {exc}")
    else:
        # No live source, but manually entered odds in the round file DO
        # reach the model — so the report must say so rather than claiming
        # no market view while the forecast quietly uses one.
        from .sources.consensus import Consensus

        from .model.market import implied_result_probs

        manual = 0
        for fixture in rnd.fixtures:
            key = (fixture.home, fixture.away)
            if fixture.odds is None:
                markets[key] = service.build_market_state(
                    None, reason_unavailable="no odds for this fixture"
                )
                continue
            p_h, p_d, p_a = implied_result_probs(*fixture.odds)
            consensus = Consensus(
                home_team=fixture.home,
                away_team=fixture.away,
                calculated_at=to_iso_safe(now),
                bookmaker_count=1,
                p_home=p_h,
                p_draw=p_d,
                p_away=p_a,
                dispersion=0.0,
                stale_excluded=0,
                oldest_quote=None,
            )
            consensus_by_fixture[key] = consensus
            markets[key] = service.build_market_state(consensus)
            manual += 1
        notes.append(
            f"market: no ODDS_API_KEY — {manual}/6 fixture(s) using "
            "manually entered odds from the round file (single source, "
            "no dispersion available)"
        )

    # ---- forecast layer -------------------------------------------------
    recommendation = recommend_round(router, rnd, FROZEN_PHASE1_V1)

    # ---- structured availability layer ----------------------------------
    availabilities: dict[tuple[str, str], AvailabilityState] = {}
    for fixture in rnd.fixtures:
        key = (fixture.home, fixture.away)
        availabilities[key] = service.build_availability_state(
            conn, fixture.home, fixture.away, has_market=key in consensus_by_fixture
        )

    # ---- knowledge layer (shadow by default) ----------------------------
    knowledge_summary = {
        "enabled": False,
        "mode": (extractor_config.mode if extractor_config else ExtractorMode.SHADOW.value),
        "claims_accepted": 0,
        "claims_rejected": 0,
        "claims_promoted": 0,
        "extraction_failures": 0,
        "quantitative_impact": "NONE DIRECTLY",
    }
    if run_knowledge and tavily_api_key and extractor is not None:
        from .knowledge import queries, worker as knowledge_worker

        knowledge_summary["enabled"] = True
        mode = knowledge_summary["mode"]
        for fixture in rnd.fixtures:
            for team in (fixture.home, fixture.away):
                plans = queries.plan_team_queries(team)
                result = knowledge_worker.research(
                    conn, fixture.home, fixture.away, plans, tavily_api_key,
                    extractor, now=now, round_number=rnd.round_number,
                    mode=mode, **kwargs,
                )
                knowledge_summary["claims_accepted"] += result.claims_accepted
                knowledge_summary["claims_rejected"] += result.claims_rejected
                knowledge_summary["claims_promoted"] += result.claims_promoted
                knowledge_summary["extraction_failures"] += result.extraction_failures
                all_events.extend(result.events)
                notes.extend(result.notes)
    elif run_knowledge:
        notes.append(
            "knowledge: skipped (needs TAVILY_API_KEY and a configured extractor)"
        )

    # ---- submission state (§46) ----------------------------------------
    from . import submission as submission_mod

    picks = [f.pick for f in recommendation.fixtures]
    submission_record = submission_mod.reconcile(
        season, rnd.round_number, picks, rnd.deadline_at, now
    )
    if submission_record.drift:
        notes.append(submission_mod.describe(submission_record))

    summary = service.build_round_summary(
        rnd, recommendation, markets, availabilities,
        submission_state=submission_record.state,
    )
    return EngineRunResult(
        round_summary=summary,
        market_layer={
            "fixtures_with_market": sum(
                1 for m in markets.values() if m.available
            ),
            "states": {f"{k[0]} v {k[1]}": v.to_dict() for k, v in markets.items()},
        },
        availability_layer={
            "quantitative_impact": "NONE DIRECTLY",
            "states": {
                f"{k[0]} v {k[1]}": v.to_dict() for k, v in availabilities.items()
            },
        },
        knowledge_layer=knowledge_summary,
        events=all_events,
        notes=notes,
    )


def format_engine_report(result: EngineRunResult) -> str:
    """Human-readable report with the four layers kept visibly separate."""
    summary = result.round_summary
    lines = [
        f"ENGINE RUN — ROUND {summary.round_number}",
        f"Deadline {summary.deadline_at}   Model {summary.model_version}",
        "",
        "=== FORECAST INPUT ===",
        f"{'':24s} {'PICK':>5s} {'RESULT':>8s} {'SCORE':>7s} {'EV':>6s}  CONF",
    ]
    for fixture in summary.fixtures:
        name = f"{fixture.home_team[:10].upper()} v {fixture.away_team[:10].upper()}"
        lines.append(
            f"{name:24s} {fixture.pick:>5s} {fixture.result_probability:>7.1%} "
            f"{fixture.score_probability:>6.1%} {fixture.expected_points:>6.2f}  "
            f"{fixture.confidence}"
        )
    if summary.golden_goal:
        lines.append(
            f"Golden Goal: minute {summary.golden_goal.minute} "
            f"(Sky interval {summary.golden_goal.sky_interval})"
        )
    lines.append(f"Expected round score: {summary.expected_round_score:.2f}")
    lines.append(f"Submission state: {summary.submission_state}")

    lines += ["", "=== MARKET INTELLIGENCE ==="]
    lines.append(
        f"Fixtures with a market view: "
        f"{result.market_layer.get('fixtures_with_market', 0)}/6"
    )
    for fixture in summary.fixtures:
        market = fixture.market
        if market is None or not market.available:
            reason = market.reason_unavailable if market else "not acquired"
            lines.append(
                f"  {fixture.home_team} v {fixture.away_team}: none — {reason}"
            )
            continue
        lines.append(
            f"  {fixture.home_team} v {fixture.away_team}: "
            f"{market.home_probability:.1%}/{market.draw_probability:.1%}/"
            f"{market.away_probability:.1%} "
            f"({market.bookmaker_count} books, dispersion {market.dispersion:.3f})"
        )

    lines += ["", "=== STRUCTURED TEAM INTELLIGENCE ==="]
    for fixture in summary.fixtures:
        availability = fixture.availability
        if availability is None:
            continue
        lines.append(
            f"  {fixture.home_team} v {fixture.away_team}: "
            f"{availability.confirmed_lineups}/2 confirmed XI, "
            f"data quality {availability.data_quality}, "
            f"completeness {availability.feature_completeness}, "
            f"{len(availability.items)} availability record(s)"
        )
        for item in availability.items[:5]:
            lines.append(
                f"      {item.team} {item.player_name}: {item.status} "
                f"({item.certainty}, {item.source})"
            )

    knowledge = result.knowledge_layer
    lines += ["", "=== KNOWLEDGE INTELLIGENCE ==="]
    if not knowledge.get("enabled"):
        lines.append("  not run this pass")
    else:
        lines.append(
            f"  mode: {knowledge['mode']}   accepted "
            f"{knowledge['claims_accepted']}, rejected "
            f"{knowledge['claims_rejected']}, promoted "
            f"{knowledge['claims_promoted']}, extraction failures "
            f"{knowledge['extraction_failures']}"
        )

    lines += ["", "=" * 62, QUANTITATIVE_NOTE, "=" * 62]
    if result.notes:
        lines += ["", "Notes:"] + [f"  - {n}" for n in result.notes]
    return "\n".join(lines)
