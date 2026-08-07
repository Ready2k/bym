"""Team availability domain model (Increment 3A).

**Scope boundary, load-bearing:** this increment understands and explains
team news. It does *not* convert team news into λ adjustments. We have a
validated `team strength + market -> P(score)` model; we have no validated
function saying "striker absent -> λ_home -0.27", and letting an LLM
choose that number would violate §22's rule that probabilities are never
altered without an explainable, reproducible trigger. See
`impact.QUANTITATIVE_IMPACT_MODEL_ENABLED`.

Two dimensions that sound alike and are kept rigorously apart:

    source_reliability   how much we trust the *source*
    claim_status         how certain the *claim* is

An excellent journalist saying "expected to miss out" is
`source_reliability=HIGH, claim_status=LIKELY` — not CONFIRMED. Collapsing
these would let source prestige launder a hedged claim into a fact.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class EventType(str, Enum):
    INJURY = "INJURY"
    SUSPENSION = "SUSPENSION"
    DOUBT = "DOUBT"
    RETURN = "RETURN"
    EXPECTED_STARTER = "EXPECTED_STARTER"
    EXPECTED_BENCH = "EXPECTED_BENCH"
    CONFIRMED_STARTER = "CONFIRMED_STARTER"
    CONFIRMED_BENCH = "CONFIRMED_BENCH"
    CONFIRMED_ABSENT = "CONFIRMED_ABSENT"


class ClaimStatus(str, Enum):
    """How certain the claim is — independent of who made it."""

    RUMOUR = "RUMOUR"
    REPORTED = "REPORTED"
    LIKELY = "LIKELY"
    CONFIRMED = "CONFIRMED"


class Reliability(str, Enum):
    """How much the source is trusted — independent of the claim."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Role(str, Enum):
    GK = "GK"
    DEF = "DEF"
    MID = "MID"
    FWD = "FWD"
    UNKNOWN = "UNKNOWN"


class Importance(str, Enum):
    KEY = "KEY"
    REGULAR = "REGULAR"
    ROTATION = "ROTATION"
    UNKNOWN = "UNKNOWN"


class Resolution(str, Enum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "PLAYER_UNRESOLVED"


class LineupKind(str, Enum):
    EXPECTED = "EXPECTED"
    CONFIRMED = "CONFIRMED"


# §18 source hierarchy: official club/competition > trusted structured
# provider > established journalist > other media > social.
SOURCE_TIER_RELIABILITY: dict[str, Reliability] = {
    "OFFICIAL_CLUB": Reliability.HIGH,
    "OFFICIAL_COMPETITION": Reliability.HIGH,
    "STRUCTURED_PROVIDER": Reliability.HIGH,
    "ESTABLISHED_JOURNALIST": Reliability.MEDIUM,
    "OTHER_MEDIA": Reliability.LOW,
    "SOCIAL": Reliability.LOW,
}

# Ordering used by reconciliation (§3.5). Confirmation level dominates,
# then source authority, then recency — never "latest article wins".
CLAIM_RANK = {
    ClaimStatus.RUMOUR: 0,
    ClaimStatus.REPORTED: 1,
    ClaimStatus.LIKELY: 2,
    ClaimStatus.CONFIRMED: 3,
}
RELIABILITY_RANK = {
    Reliability.LOW: 0,
    Reliability.MEDIUM: 1,
    Reliability.HIGH: 2,
}

# Event types that are inherently confirmations of a fact.
CONFIRMED_TYPES = {
    EventType.CONFIRMED_STARTER,
    EventType.CONFIRMED_BENCH,
    EventType.CONFIRMED_ABSENT,
}

# Event types meaning the player is not available to play.
ABSENT_TYPES = {
    EventType.INJURY,
    EventType.SUSPENSION,
    EventType.CONFIRMED_ABSENT,
}


@dataclass
class AvailabilityEvent:
    """One observation about one player. Events are versioned, never
    edited: a later observation supersedes an earlier one and both remain
    in the record (§19, §22)."""

    home_team: str
    away_team: str
    team: str
    player_name: str
    event_type: str
    claim_status: str
    source_id: str
    source_reliability: str
    source_timestamp: str
    observed_at: str
    round_number: int | None = None
    player_id: str | None = None
    resolution: str = Resolution.RESOLVED.value
    role: str = Role.UNKNOWN.value
    importance: str = Importance.UNKNOWN.value
    expires_at: str | None = None
    supersedes_event_id: int | None = None
    retracted_by_id: int | None = None
    note: str | None = None
    id: int | None = None

    @property
    def fixture(self) -> tuple[str, str]:
        return (self.home_team, self.away_team)

    @property
    def is_confirmation(self) -> bool:
        return EventType(self.event_type) in CONFIRMED_TYPES

    @property
    def implies_absent(self) -> bool:
        return EventType(self.event_type) in ABSENT_TYPES

    @property
    def sort_key(self) -> tuple[int, int, str]:
        """§3.5 priority: confirmation level, then source authority, then
        recency."""
        return (
            CLAIM_RANK[ClaimStatus(self.claim_status)],
            RELIABILITY_RANK[Reliability(self.source_reliability)],
            self.source_timestamp,
        )

    def dedupe_key(self) -> str:
        """Identity of the underlying claim, so re-ingesting the same
        source report is idempotent rather than duplicating history."""
        raw = "|".join(
            [
                self.home_team,
                self.away_team,
                self.team,
                self.player_name.lower(),
                self.event_type,
                self.claim_status,
                self.source_id,
                self.source_timestamp,
            ]
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:32]


@dataclass
class Lineup:
    """Expected and confirmed lineups are separate objects, not one table
    with a status flag someone will eventually misread (§3.2)."""

    home_team: str
    away_team: str
    team: str
    kind: str
    source_id: str
    generated_at: str
    observed_at: str
    players: list[str] = field(default_factory=list)
    confidence: str | None = None  # EXPECTED only
    round_number: int | None = None
    id: int | None = None

    def __post_init__(self):
        if self.kind == LineupKind.CONFIRMED.value and self.confidence is not None:
            raise ValueError(
                "A confirmed lineup has no confidence — it is the fact "
                "against which expectations are judged."
            )
