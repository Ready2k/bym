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
from .api.contracts import (
    AvailabilityState,
    MarketState,
    Modellability,
    RoundSummary,
)
from .config import FROZEN_PHASE1_V1
from .db import utcnow
from .knowledge.config import ExtractorConfig, ExtractorMode
from .recommend import RoundRecommendation, recommend_round
from .round import Round, resolve_round_teams
from .sources import pipeline


def to_iso_safe(value: datetime) -> str:
    from .db import to_iso

    return to_iso(value)


_MODELLABLE = Modellability()

QUANTITATIVE_NOTE = (
    "Team and news intelligence at this phase has quantitative impact: "
    "NONE DIRECTLY. Only market consensus reaches the forecast, through "
    "PHASE1_MODEL_V1's frozen w=0.5 blend."
)


def _enrich_markets(
    markets: dict,
    consensus_by_fixture: dict,
    recommendation,
    now: datetime,
    notes: list[str],
    consensus_snapshot_path: Path | None = None,
) -> dict:
    """Attach movement and model-vs-market divergence to each market state.

    Both are read-model concerns only. `movement` decides whether a move
    deserves attention; `divergence` compares the model's own opinion with
    the market. Neither may touch the forecast — the frozen model already
    consumed this consensus through its w=0.5 blend, and adjusting again
    here would count the same information twice (§32).

    Degrades to the plain market state on any failure, in keeping with the
    rest of the run: a missing snapshot history is a missing reference
    point, not a failed engine pass.
    """
    if not consensus_by_fixture:
        return markets

    from . import divergence as divergence_mod
    from . import movement as movement_mod

    try:
        history = (
            movement_mod.ConsensusHistory.load(consensus_snapshot_path)
            if consensus_snapshot_path is not None
            else movement_mod.ConsensusHistory.load()
        )
    except (OSError, ValueError, KeyError) as exc:
        notes.append(f"market movement unavailable: {exc}")
        return markets

    by_fixture = {(f.home, f.away): f for f in recommendation.fixtures}
    moved = 0
    for key, consensus in consensus_by_fixture.items():
        fixture = by_fixture.get(key)
        if fixture is None or consensus is None:
            continue
        try:
            reports = movement_mod.reference_reports(consensus, history, now=now)
            primary = reports.get(movement_mod.PREVIOUS_POLL)
            report = divergence_mod.assess(
                fixture.model_only_matrix,
                consensus.p_home,
                consensus.p_draw,
                consensus.p_away,
                consensus.dispersion,
                consensus.bookmaker_count,
            )
            markets[key] = service.build_market_state(
                consensus,
                movement=primary,
                divergence_report=report,
                divergence_factor=divergence_mod.divergence_factor(report),
            )
            if primary is not None and primary.should_reforecast:
                moved += 1
        except (ValueError, KeyError, ZeroDivisionError) as exc:
            notes.append(f"market attention skipped for {key[0]} v {key[1]}: {exc}")
    if moved:
        notes.append(
            f"market: {moved} fixture(s) moved beyond the noise threshold "
            "since the previous poll"
        )
    return markets


@dataclass
class EngineRunResult:
    round_summary: RoundSummary
    recommendation: RoundRecommendation | None = None
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
    submission_path: Path | None = None,
    known_teams: set[str] | None = None,
    corpus: str = "E0+E1",
    consensus_snapshot_path: Path | None = None,
    **kwargs,
) -> EngineRunResult:
    """One full engine pass. Every external step degrades to 'absent'
    rather than failing the run.

    `known_teams` is the set of teams the router was actually fitted on.
    Supplying it lets each fixture carry a modellability verdict, so the
    UI can refuse to show a pick for a team the model has never seen
    rather than presenting the league-average prior as a forecast. Omit
    it and every fixture is reported as modellable, which is only honest
    when the caller already knows the round is inside the corpus.
    """
    now = now or utcnow()
    notes: list[str] = []
    all_events: list[ev.Event] = []
    markets: dict[tuple[str, str], MarketState] = {}
    consensus_by_fixture = {}

    # ---- team-name resolution ---------------------------------------------
    # The round file holds whatever name the user typed — often Sky's full
    # club name ("Manchester City") — while the training corpus uses
    # football-data.co.uk's short forms ("Man City"). Resolving here, before
    # anything else touches `rnd`, means every later layer (modellability,
    # the model itself, market/availability matching) sees one consistent
    # name per club instead of flagging a perfectly known team as unmodellable.
    if known_teams is not None:
        rnd, renamed = resolve_round_teams(rnd, known_teams)
        if renamed:
            notes.append(
                f"teams: resolved {len(renamed)} name(s) to the corpus's "
                "canonical club name"
            )

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
            notes.extend(f"market: {note}" for note in acquired.notes)
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

    # ---- modellability, decided BEFORE the forecast ----------------------
    # It has to come first: a fixture the model cannot judge is forecast
    # differently, not merely annotated afterwards.
    modellabilities = {}
    if known_teams is not None:
        modellabilities = {
            (f.home, f.away): service.build_modellability(
                f.home, f.away, known_teams, corpus
            )
            for f in rnd.fixtures
        }
    market_only = {
        (f.home, f.away)
        for f in rnd.fixtures
        if not modellabilities.get((f.home, f.away), _MODELLABLE).modellable
        and f.odds is not None
    }
    unpriced = {
        (f.home, f.away)
        for f in rnd.fixtures
        if not modellabilities.get((f.home, f.away), _MODELLABLE).modellable
        and f.odds is None
    }
    if market_only:
        notes.append(
            f"forecast: {len(market_only)} fixture(s) outside the {corpus} "
            "corpus fell back to market-only (w=1.0); not PHASE1_MODEL_V1"
        )
    if unpriced:
        notes.append(
            f"forecast: {len(unpriced)} fixture(s) outside the {corpus} "
            "corpus have no odds either — no usable forecast for them"
        )

    # ---- forecast layer -------------------------------------------------
    recommendation = recommend_round(
        router, rnd, FROZEN_PHASE1_V1, market_only=market_only
    )

    # ---- market attention layer (§6, §21, §33) --------------------------
    # Movement and divergence are computed here and nowhere near the
    # forecast: the frozen model has already consumed the latest consensus
    # through its w=0.5 blend, so adjusting anything on the strength of a
    # move would double-count it. These populate the read model only.
    markets = _enrich_markets(
        markets, consensus_by_fixture, recommendation, now, notes,
        consensus_snapshot_path=consensus_snapshot_path,
    )

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
    elif extractor_config is not None and extractor_config.kind != "null":
        # Configured and running — just not from here. Without this note the
        # UI would report the knowledge layer as disabled moments after the
        # user switched it on, which is worse than saying nothing.
        knowledge_summary["enabled"] = True
        notes.append(
            "knowledge: extractor configured; research runs on the scheduled "
            "cadence (`bym tick` / the always-on worker), not on page load"
        )

    # ---- submission state (§46) ----------------------------------------
    from . import submission as submission_mod

    picks = [f.pick for f in recommendation.fixtures]
    reconcile_kwargs = {"path": submission_path} if submission_path is not None else {}
    submission_record = submission_mod.reconcile(
        season, rnd.round_number, picks, rnd.deadline_at, now, **reconcile_kwargs
    )
    if submission_record.drift:
        notes.append(submission_mod.describe(submission_record))

    summary = service.build_round_summary(
        rnd, recommendation, markets, availabilities,
        submission_state=submission_record.state,
        modellabilities=modellabilities,
    )
    return EngineRunResult(
        round_summary=summary,
        recommendation=recommendation,
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

    unmodellable = [f for f in summary.fixtures if not f.modellability.modellable]
    if unmodellable:
        lines += ["", "!! NOT A TRUSTWORTHY FORECAST — outside the training corpus"]
        for fixture in unmodellable:
            lines.append(
                f"  {fixture.home_team} v {fixture.away_team}: "
                f"{fixture.modellability.reason}"
            )
        lines.append(
            "  The pick above is what the model returns, not what it knows. "
            "Do not enter it."
        )

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
        if market.last_movement:
            lines.append(
                f"      moved: {market.last_movement} "
                f"[{market.materiality}, books {market.market_agreement}]"
            )
        if market.model_vs_market is not None:
            lines.append(
                f"      model vs market: TVD {market.model_vs_market:.3f} "
                f"(divergence {market.market_divergence})"
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
