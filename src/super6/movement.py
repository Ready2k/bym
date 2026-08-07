"""Market movement detection (SPEC.md §6, §9, §21).

Movement is measured in **de-vigged consensus probability space**, never
raw decimal odds, and the primary human-facing number is the absolute
percentage-point change. Relative change is reported alongside but never
drives classification: 2% -> 3% is +50% relative and almost always
meaningless.

Two quantities are kept deliberately separate and are never collapsed
into a single score:

    movement_strength  how far the consensus moved
    market_agreement   how much the bookmakers agree with each other

A large move with tight agreement is a real signal. The same move with
wide dispersion may just mean half the books have updated and half
haven't. Averaging those into one number would destroy exactly the
distinction that makes the second case suspicious.

**This module controls attention, not probabilities.** The frozen model
already consumes the latest consensus through its w=0.5 blend, so a moved
market changes P(score) mechanically on the next forecast. Applying any
further adjustment here would double-count the same information (§32).
What the detector decides is whether the move deserves a reforecast, an
activity record, or an alert.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

import pandas as pd

from .config import DEFAULT_MOVEMENT, MovementConfig
from .sources.consensus import Consensus, parse_timestamp
from .sources.pipeline import CONSENSUS_SNAPSHOT_PATH

# Label for the earliest consensus we hold. Deliberately NOT called
# "opening odds": it is the first value this system observed, not the
# bookmakers' opening price, which we never saw.
FIRST_OBSERVED = "first_observed"
PREVIOUS_POLL = "previous_poll"


class Materiality(str, Enum):
    NOISE = "NOISE"
    MATERIAL = "MATERIAL"
    MAJOR = "MAJOR"


class Agreement(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True)
class OutcomeDelta:
    outcome: str  # "H" | "D" | "A"
    previous: float
    current: float

    @property
    def delta_pp(self) -> float:
        """Absolute movement in percentage points — the primary figure."""
        return (self.current - self.previous) * 100.0

    @property
    def relative_pct(self) -> float | None:
        """Relative movement, reported but never used for classification."""
        if self.previous <= 0:
            return None
        return 100.0 * (self.current - self.previous) / self.previous


@dataclass(frozen=True)
class MovementReport:
    home_team: str
    away_team: str
    detected_at: str
    reference: str  # which reference point `previous` came from
    reference_at: str | None
    deltas: tuple[OutcomeDelta, ...]
    materiality: str
    agreement: str
    dispersion: float
    bookmaker_count: int

    @property
    def movement_strength(self) -> float:
        """Largest absolute pp move across H/D/A."""
        return max((abs(d.delta_pp) for d in self.deltas), default=0.0)

    @property
    def largest(self) -> OutcomeDelta | None:
        if not self.deltas:
            return None
        return max(self.deltas, key=lambda d: abs(d.delta_pp))

    @property
    def should_reforecast(self) -> bool:
        return self.materiality != Materiality.NOISE.value

    def describe(self) -> str:
        d = self.largest
        if d is None:
            return "no movement"
        name = {"H": self.home_team, "D": "Draw", "A": self.away_team}[d.outcome]
        return (
            f"{name}: {d.previous:.1%} -> {d.current:.1%} "
            f"({d.delta_pp:+.1f}pp)"
        )


def classify_materiality(
    strength_pp: float, cfg: MovementConfig = DEFAULT_MOVEMENT
) -> Materiality:
    if strength_pp >= cfg.urgent_movement_threshold_pp:
        return Materiality.MAJOR
    if strength_pp >= cfg.movement_threshold_pp:
        return Materiality.MATERIAL
    return Materiality.NOISE


def classify_agreement(
    dispersion: float, cfg: MovementConfig = DEFAULT_MOVEMENT
) -> Agreement:
    """Bookmaker agreement, derived from dispersion alone — independent of
    how far the consensus moved."""
    if dispersion >= cfg.high_dispersion_threshold:
        return Agreement.LOW
    if dispersion >= cfg.dispersion_threshold:
        return Agreement.MEDIUM
    return Agreement.HIGH


def compare(
    current: Consensus,
    previous_h: float,
    previous_d: float,
    previous_a: float,
    reference: str = PREVIOUS_POLL,
    reference_at: str | None = None,
    cfg: MovementConfig = DEFAULT_MOVEMENT,
) -> MovementReport:
    """Build a movement report between a stored consensus and the current."""
    deltas = (
        OutcomeDelta("H", previous_h, current.p_home),
        OutcomeDelta("D", previous_d, current.p_draw),
        OutcomeDelta("A", previous_a, current.p_away),
    )
    strength = max(abs(d.delta_pp) for d in deltas)
    return MovementReport(
        home_team=current.home_team,
        away_team=current.away_team,
        detected_at=current.calculated_at,
        reference=reference,
        reference_at=reference_at,
        deltas=deltas,
        materiality=classify_materiality(strength, cfg).value,
        agreement=classify_agreement(current.dispersion, cfg).value,
        dispersion=current.dispersion,
        bookmaker_count=current.bookmaker_count,
    )


class ConsensusHistory:
    """Read access to persisted consensus snapshots, for reference points."""

    def __init__(self, frame: pd.DataFrame):
        self.frame = frame

    @classmethod
    def load(cls, path: Path = CONSENSUS_SNAPSHOT_PATH) -> "ConsensusHistory":
        if not path.exists():
            return cls(pd.DataFrame())
        frame = pd.read_csv(path)
        if not frame.empty:
            frame["_ts"] = frame["calculated_at"].map(parse_timestamp)
            frame = frame.dropna(subset=["_ts"]).sort_values("_ts")
        return cls(frame)

    def for_fixture(self, home: str, away: str) -> pd.DataFrame:
        if self.frame.empty:
            return self.frame
        return self.frame[
            (self.frame["home_team"] == home) & (self.frame["away_team"] == away)
        ]

    def latest_before(
        self, home: str, away: str, when: datetime
    ) -> pd.Series | None:
        rows = self.for_fixture(home, away)
        if rows.empty:
            return None
        earlier = rows[rows["_ts"] < when]
        return None if earlier.empty else earlier.iloc[-1]

    def first_observed(self, home: str, away: str) -> pd.Series | None:
        rows = self.for_fixture(home, away)
        return None if rows.empty else rows.iloc[0]

    def nearest_to(
        self,
        home: str,
        away: str,
        target: datetime,
        tolerance: timedelta,
    ) -> pd.Series | None:
        """Snapshot closest to `target`, or None if nothing falls within
        tolerance — an unavailable reference is reported as missing rather
        than substituted with whatever happens to be nearest."""
        rows = self.for_fixture(home, away)
        if rows.empty:
            return None
        gaps = (rows["_ts"] - target).abs()
        best = gaps.idxmin()
        return rows.loc[best] if gaps.loc[best] <= tolerance else None


def reference_reports(
    current: Consensus,
    history: ConsensusHistory,
    now: datetime | None = None,
    cfg: MovementConfig = DEFAULT_MOVEMENT,
) -> dict[str, MovementReport]:
    """Movement against every available reference point (§6 stores
    movement over time, not just the last tick)."""
    now = now or datetime.now(timezone.utc)
    home, away = current.home_team, current.away_team
    reports: dict[str, MovementReport] = {}

    def add(label: str, row: pd.Series | None) -> None:
        if row is None:
            return
        reports[label] = compare(
            current,
            float(row["consensus_h"]),
            float(row["consensus_d"]),
            float(row["consensus_a"]),
            reference=label,
            reference_at=str(row["calculated_at"]),
            cfg=cfg,
        )

    add(PREVIOUS_POLL, history.latest_before(home, away, now))
    add(FIRST_OBSERVED, history.first_observed(home, away))
    tolerance = timedelta(hours=cfg.reference_tolerance_hours)
    for hours in cfg.reference_hours:
        add(
            f"{hours:g}h_ago",
            history.nearest_to(home, away, now - timedelta(hours=hours), tolerance),
        )
    return reports
