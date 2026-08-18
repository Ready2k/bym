"""Player-forecast evaluation metrics (SPEC_FPL.md §12.4, §18 Phase 1B).

Consumes `backtest.fpl_walk_forward.WalkForwardPrediction` pairs only —
never touches the optimiser or a decision layer (that split belongs to a
separate decision-quality module). Every metric here answers "was the
PROJECTION right," not "was the DECISION good" — §18 pre-registers these
as two different metric groups on purpose.

**Error decomposition.** A wrong total tells you nothing about which
subsystem to fix, so every MAE here is also split by which projection
component produced it: minutes (`appearance_points`), scoring rate
(goal/assist/saves points — all player-level rates), and fixture/team
model (`clean_sheet_points`, driven by `FixtureOutlook`, not the player
at all). "The projection was 3 points wrong" answers nothing on its own;
which of these three actually moved is the real diagnostic, per §18's own
warning against tuning the wrong subsystem.

**Distribution scoring is not a working metric here.** BASELINE_FPL_001
is a point-estimate model (`points_variance` is always `None` — §6.2,
docs/MANIFEST_FPL.md) with no predictive distribution to score against a
realised outcome. `distribution_score` exists so a later distributional
refinement has a stable name to implement against, and returns `None`
(never a fabricated number) for this baseline.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..fpl import scoring
from .fpl_walk_forward import WalkForwardPrediction

NAN = float("nan")


@dataclass(frozen=True)
class ErrorDecomposition:
    minutes_error: float        # MAE of appearance_points
    scoring_rate_error: float   # MAE of goal + assist + saves points, summed
    fixture_model_error: float  # MAE of clean_sheet_points
    n: int


def _component_errors(p: WalkForwardPrediction) -> tuple[float, float, float]:
    minutes = abs(p.predicted.appearance_points - p.actual.appearance_points)
    scoring_rate = (
        abs(p.predicted.goal_points - p.actual.goal_points)
        + abs(p.predicted.assist_points - p.actual.assist_points)
        + abs(p.predicted.saves_points - p.actual.saves_points)
    )
    fixture_model = abs(p.predicted.clean_sheet_points - p.actual.clean_sheet_points)
    return minutes, scoring_rate, fixture_model


def error_decomposition(predictions: list[WalkForwardPrediction]) -> ErrorDecomposition:
    if not predictions:
        return ErrorDecomposition(NAN, NAN, NAN, 0)
    n = len(predictions)
    minutes_total = scoring_total = fixture_total = 0.0
    for p in predictions:
        m, s, f = _component_errors(p)
        minutes_total += m
        scoring_total += s
        fixture_total += f
    return ErrorDecomposition(minutes_total / n, scoring_total / n, fixture_total / n, n)


@dataclass(frozen=True)
class PlayerForecastMetrics:
    n: int
    mae: float
    mean_predicted: float
    mean_actual: float
    calibration_gap: float  # mean_predicted - mean_actual; 0 = calibrated on average
    error_decomposition: ErrorDecomposition


def evaluate(predictions: list[WalkForwardPrediction]) -> PlayerForecastMetrics:
    if not predictions:
        return PlayerForecastMetrics(0, NAN, NAN, NAN, NAN, error_decomposition(predictions))
    n = len(predictions)
    mae = sum(abs(p.predicted.total - p.actual.total) for p in predictions) / n
    mean_predicted = sum(p.predicted.total for p in predictions) / n
    mean_actual = sum(p.actual.total for p in predictions) / n
    return PlayerForecastMetrics(
        n=n, mae=mae, mean_predicted=mean_predicted, mean_actual=mean_actual,
        calibration_gap=mean_predicted - mean_actual,
        error_decomposition=error_decomposition(predictions),
    )


def evaluate_by_position(
    predictions: list[WalkForwardPrediction],
) -> dict[str, PlayerForecastMetrics]:
    """§18: 'Position: GK/DEF/MID/FWD separately' — a pooled MAE can hide
    a position-specific bias (e.g. defenders systematically overrated)
    that only shows up once split out."""
    by_position: dict[str, list[WalkForwardPrediction]] = defaultdict(list)
    for p in predictions:
        by_position[p.position].append(p)
    return {position: evaluate(preds) for position, preds in by_position.items()}


@dataclass(frozen=True)
class MinutesCalibration:
    """§18: 'Minutes: start/appearance/60+ calibration' — compares the
    PREDICTED probability itself (not the points it produced) against the
    realised binary outcome, the same calibration-gap framing this
    codebase already uses for LMS (`backtest/lms.py`'s `LMSMetrics`)."""

    n: int
    mean_predicted_p_appearance: float
    appearance_rate: float
    appearance_calibration_gap: float
    mean_predicted_p_60_plus: float
    rate_60_plus: float
    calibration_gap_60_plus: float


def minutes_calibration(predictions: list[WalkForwardPrediction]) -> MinutesCalibration:
    if not predictions:
        return MinutesCalibration(0, NAN, NAN, NAN, NAN, NAN, NAN)
    n = len(predictions)
    mean_p_app = sum(p.projection.p_appearance for p in predictions) / n
    appearance_rate = sum(1 for p in predictions if p.actual.appearance_points > 0) / n
    mean_p_60 = sum(p.projection.p_60_plus for p in predictions) / n
    rate_60_plus = sum(
        1 for p in predictions
        if p.actual.appearance_points == scoring.APPEARANCE_POINTS_60_PLUS
    ) / n
    return MinutesCalibration(
        n=n,
        mean_predicted_p_appearance=mean_p_app,
        appearance_rate=appearance_rate,
        appearance_calibration_gap=mean_p_app - appearance_rate,
        mean_predicted_p_60_plus=mean_p_60,
        rate_60_plus=rate_60_plus,
        calibration_gap_60_plus=mean_p_60 - rate_60_plus,
    )


@dataclass(frozen=True)
class CalibrationBand:
    band_label: str
    n: int
    mean_predicted: float
    mean_actual: float


def calibration_by_predicted_band(
    predictions: list[WalkForwardPrediction], n_bands: int = 5
) -> list[CalibrationBand]:
    """Reliability check: bucket by PREDICTED total xP (quantile-sized
    bands) and compare each bucket's mean predicted vs mean actual — a
    model that is right on average overall (`evaluate()`'s
    `calibration_gap` ~ 0) can still be badly miscalibrated within
    specific bands, e.g. overconfident on the highest-xP players while
    underconfident on the lowest."""
    if not predictions:
        return []
    ordered = sorted(predictions, key=lambda p: p.predicted.total)
    n = len(ordered)
    band_size = max(1, -(-n // n_bands))  # ceil division so every band gets a member
    bands: list[CalibrationBand] = []
    for i in range(0, n, band_size):
        chunk = ordered[i : i + band_size]
        lo, hi = chunk[0].predicted.total, chunk[-1].predicted.total
        bands.append(
            CalibrationBand(
                band_label=f"{lo:.1f}-{hi:.1f}",
                n=len(chunk),
                mean_predicted=sum(c.predicted.total for c in chunk) / len(chunk),
                mean_actual=sum(c.actual.total for c in chunk) / len(chunk),
            )
        )
    return bands


def distribution_score(predictions: list[WalkForwardPrediction]) -> float | None:
    """§18: 'Distribution: log score or CRPS'. `None` — not a fabricated
    number — for BASELINE_FPL_001, which has no predictive distribution to
    score (see module docstring)."""
    return None
