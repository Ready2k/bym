"""Stable read-model contracts for the eventual UI.

**These are domain contracts, not database rows.** Every field is named
and mapped explicitly, so a schema migration cannot silently reshape the
API, and the UI never depends on a column name. A test asserts no
contract carries database-internal fields.

No UI exists yet and none should be built against these until they are
exercised by a live engine run — that is the point of freezing them here
first.

Vocabulary rules carried through from the engine, because the UI must not
be able to imply things the engine refuses to say:

* probability and confidence are separate fields, never merged (§20);
* CONF is a category, never a number;
* team/news intelligence always carries `quantitative_impact` =
  "NONE DIRECTLY" at this phase;
* market-move explanations are `PLAUSIBLE`, never causal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0.0"


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


@dataclass(frozen=True)
class Contract:
    """Base: every contract serialises to plain JSON-safe data."""

    def to_dict(self) -> dict:
        return _clean(asdict(self))


@dataclass(frozen=True)
class ScoreAlternative(Contract):
    score: str
    score_probability: float
    expected_points: float
    is_recommendation: bool = False


@dataclass(frozen=True)
class MarketState(Contract):
    """What the market currently says, and how much it agrees with itself."""

    available: bool
    home_probability: float | None = None
    draw_probability: float | None = None
    away_probability: float | None = None
    bookmaker_count: int = 0
    dispersion: float | None = None
    market_agreement: str | None = None      # HIGH | MEDIUM | LOW
    last_movement: str | None = None
    movement_strength_pp: float | None = None
    materiality: str | None = None           # NOISE | MATERIAL | MAJOR
    model_vs_market: float | None = None
    market_divergence: str = "UNKNOWN"
    stale: bool = False
    reason_unavailable: str | None = None


@dataclass(frozen=True)
class AvailabilityItem(Contract):
    team: str
    player_name: str
    status: str                 # e.g. CONFIRMED_ABSENT, DOUBT
    certainty: str              # RUMOUR | REPORTED | LIKELY | CONFIRMED
    source: str
    source_reliability: str
    resolved: bool
    quantitative_impact: str = "NONE DIRECTLY"


@dataclass(frozen=True)
class AvailabilityState(Contract):
    """Structured team intelligence. Explicitly cannot move a forecast."""

    confirmed_lineups: int
    expected_lineups: int
    data_quality: str            # GOOD | DEGRADED | LIMITED | STALE
    feature_completeness: str    # HIGH | MEDIUM | LOW
    items: list[AvailabilityItem] = field(default_factory=list)
    lineup_surprise: str | None = None
    quantitative_impact: str = "NONE DIRECTLY"


@dataclass(frozen=True)
class KnowledgeEvent(Contract):
    """One piece of web-derived intelligence, with both source identities
    preserved and its promotion state visible."""

    occurred_at: str
    event_type: str
    severity: str
    summary: str
    acquisition_source: str | None = None
    origin_domain: str | None = None
    origin_authority: str | None = None
    published_at: str | None = None
    event_at: str | None = None
    evidence: str | None = None
    story_cluster_id: str | None = None
    promoted: bool = False
    extractor_mode: str = "shadow"
    quantitative_impact: str = "NONE DIRECTLY"


@dataclass(frozen=True)
class GoldenGoalRecommendation(Contract):
    minute: int
    sky_interval: str
    intervals: dict[str, float] = field(default_factory=dict)
    rationale: str = (
        "Chosen to minimise expected tiebreak distance, not because this "
        "minute is individually the most likely."
    )


@dataclass(frozen=True)
class PredictionVersion(Contract):
    recorded_at: str
    pick: str
    confidence: str
    reason: str = ""
    model_version: str = ""
    superseded: bool = False


@dataclass(frozen=True)
class Modellability(Contract):
    """Whether the model can meaningfully forecast this fixture at all.

    A team outside the training corpus receives the league-average prior,
    which does not merely add uncertainty — it can make a lower-division
    side favourite over a genuinely below-average team from the division
    above. That is a different kind of statement from "low confidence",
    so the UI needs to be able to refuse to show a pick rather than
    display a confident-looking wrong one.

    `modellable` is the whole verdict: a false value means withhold the
    pick. It is a plain field rather than a derived property because only
    declared fields survive `to_dict`, and a consumer that cannot see the
    verdict cannot act on it.
    """

    modellable: bool = True
    unknown_teams: list[str] = field(default_factory=list)
    corpus: str = "E0+E1"
    reason: str = ""


@dataclass(frozen=True)
class FixtureForecast(Contract):
    """§20's columns, with probability and confidence kept apart."""

    home_team: str
    away_team: str
    pick: str
    result_probability: float     # P(selected outcome)
    score_probability: float      # P(exact selected scoreline)
    expected_points: float
    confidence: str               # HIGH | MEDIUM | LOW — never a number
    model_pick: str
    modellability: Modellability = field(default_factory=Modellability)
    # What produced this forecast. Round-level `model_version` cannot say
    # this, because one fixture in a round may be outside the model's reach
    # while the other five are not.
    #   PHASE1_MODEL_V1      the frozen model
    #   MARKET_ONLY_FALLBACK unmodellable, but priced — result from the
    #                        market, scoreline shape from a neutral prior
    #   NONE                 unmodellable and unpriced; no usable forecast
    forecast_basis: str = "PHASE1_MODEL_V1"
    strategy_pick: str | None = None
    user_pick: str | None = None
    status: str = "SCHEDULED"
    alternatives: list[ScoreAlternative] = field(default_factory=list)
    market: MarketState | None = None
    availability: AvailabilityState | None = None
    history: list[PredictionVersion] = field(default_factory=list)


@dataclass(frozen=True)
class RoundSummary(Contract):
    round_number: int
    deadline_at: str
    fixtures: list[FixtureForecast] = field(default_factory=list)
    golden_goal: GoldenGoalRecommendation | None = None
    expected_round_score: float = 0.0
    model_version: str = ""
    submission_state: str = "NOT READY"
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class SourceHealth(Contract):
    source_id: str
    usage_status: str
    reachable: str = "UNKNOWN"     # UNKNOWN | YES | NO
    quota_used: int = 0
    quota_limit: int = 0
    quota_period: str = ""
    detail: str = ""


@dataclass(frozen=True)
class SystemHealth(Contract):
    sources: list[SourceHealth] = field(default_factory=list)
    scheduler_jobs_pending: int = 0
    last_forecast_at: str | None = None
    next_scheduled_check: str | None = None
    extractor: str = "NullExtractor"
    extractor_mode: str = "shadow"
    ollama_reachable: str = "UNKNOWN"
    ollama_model_available: str = "UNKNOWN"
    database_schema_version: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ArmPerformance(Contract):
    arm: str
    fixtures: int
    rounds: int
    points: int
    points_per_round: float
    exact_scores: int
    exact_rate: float


@dataclass(frozen=True)
class ProspectivePerformance(Contract):
    """2026/27 round-by-round comparison. Carries its own health warning
    because six fixtures a week is a thin sample and the table will look
    like a scoreboard long before it means anything."""

    season: int
    arms: list[ArmPerformance] = field(default_factory=list)
    rounds_recorded: int = 0
    sample_warning: str = (
        "Six fixtures per round is a thin sample. Expect most of a season "
        "before any arm difference is distinguishable from noise."
    )
    schema_version: str = SCHEMA_VERSION
