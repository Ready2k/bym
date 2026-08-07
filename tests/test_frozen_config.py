"""The frozen Phase 1 arm must not drift when Phase 2+ edits defaults."""

from super6.config import DEFAULT_MODEL, FROZEN_PHASE1_V1, ModelConfig


def test_frozen_config_matches_manifest():
    """Values pinned in MANIFEST.md. If this test fails, either the freeze
    was violated or the manifest needs a documented amendment — do not
    'fix' it by editing the expected values."""
    assert FROZEN_PHASE1_V1.xi == 0.003
    assert FROZEN_PHASE1_V1.market_blend_weight == 0.5
    assert FROZEN_PHASE1_V1.use_promoted_priors is True
    assert FROZEN_PHASE1_V1.season_decay == 1.0
    assert FROZEN_PHASE1_V1.tail_tolerance == 1e-5
    assert FROZEN_PHASE1_V1.lambda_floor == 0.05
    assert FROZEN_PHASE1_V1.lambda_ceiling == 6.0
    assert FROZEN_PHASE1_V1.refit_every_days == 7


def test_frozen_config_is_independent_of_defaults():
    """Mutating DEFAULT_MODEL (as Phase 2 will) must not touch the frozen
    arm — they are separate instances, not aliases."""
    assert FROZEN_PHASE1_V1 is not DEFAULT_MODEL
    phase2 = ModelConfig(xi=0.01, market_blend_weight=0.9)
    assert FROZEN_PHASE1_V1.xi == 0.003
    assert phase2.xi == 0.01
