"""The quantitative-impact boundary (Increment 3.7).

**An availability event cannot change λ.** Not "should not" — cannot: no
code path exists from an availability event to a model parameter, and this
module is the single place where such a path would ever be added.

The reasoning is §22's guardrail. We have a validated
`team strength + market -> P(score)` model. We have no validated function
mapping "striker absent" to a goal-rate delta. The original spec sketches
examples like `λ 1.82 -> 1.62`, but those numbers must come from a fitted,
versioned, backtested model — not from an LLM with strong opinions about
centre-forwards. Until such a model exists and has earned its place under
§28's ablation framework, team news informs *attention and confidence*,
never probabilities.

Three reforecast routes exist (§3.7):

    1. an existing model input changed (market consensus) -> reforecast
    2. intelligence changed with no quantitative input    -> record, flag
                                                              review, may
                                                              affect CONF,
                                                              never λ
    3. a validated contextual model consumes the feature  -> later

Route 3 is what this flag gates.
"""

from __future__ import annotations

from dataclasses import dataclass

# Route 3 is not available. Flipping this to True is a deliberate,
# reviewable act that must be accompanied by a fitted, versioned impact
# model and its walk-forward evidence (§28). A test asserts it is False.
QUANTITATIVE_IMPACT_MODEL_ENABLED = False
IMPACT_MODEL_VERSION: str | None = None


class ImpactModelNotValidatedError(RuntimeError):
    """Raised if anything attempts a team-news-driven λ adjustment."""


@dataclass(frozen=True)
class ImpactAssessment:
    """What Increment 3 *can* say about an availability change."""

    lambda_delta_home: float
    lambda_delta_away: float
    quantitative: bool
    explanation: str

    @property
    def alters_forecast(self) -> bool:
        return self.quantitative and (
            self.lambda_delta_home != 0.0 or self.lambda_delta_away != 0.0
        )


def assess_impact(*_args, **_kwargs) -> ImpactAssessment:
    """Qualitative assessment only, while route 3 is disabled.

    Returns zero deltas and says so explicitly, rather than returning a
    plausible-looking number nobody validated.
    """
    if not QUANTITATIVE_IMPACT_MODEL_ENABLED:
        return ImpactAssessment(
            lambda_delta_home=0.0,
            lambda_delta_away=0.0,
            quantitative=False,
            explanation=(
                "No validated player-impact model is enabled. Team news is "
                "recorded and may affect confidence and review flags, but "
                "does not alter goal rates (§22, §28)."
            ),
        )
    raise ImpactModelNotValidatedError(  # pragma: no cover — guarded by flag
        "QUANTITATIVE_IMPACT_MODEL_ENABLED is set but no impact model is "
        "wired in. Enable it only alongside a fitted, versioned model with "
        "walk-forward evidence."
    )


def apply_to_lambdas(
    lam_home: float, lam_away: float, assessment: ImpactAssessment
) -> tuple[float, float]:
    """Apply an assessment to goal rates — a no-op while route 3 is off.

    Exists so the eventual quantitative model has one obvious, testable
    place to plug into, rather than being sprinkled through the forecast
    path later.
    """
    if not assessment.quantitative:
        return lam_home, lam_away
    return (
        lam_home + assessment.lambda_delta_home,
        lam_away + assessment.lambda_delta_away,
    )
