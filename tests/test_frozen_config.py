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


def test_ablation_007_candidates_stay_out_of_the_production_path():
    """ablation_007 rejected Shin devigging and MOV-Elo. Both stay in the
    tree for reproducibility, so the thing worth asserting is that they are
    inert: the frozen arm is unchanged, and every entry point defaults to
    the incumbent. Without this, a rejected candidate is one stray keyword
    argument away from becoming a live modelling change nobody decided on."""
    import inspect

    from super6.backtest.walk_forward import run_model
    from super6.model import elo, market

    # The frozen arm carries no knowledge of either candidate.
    assert not hasattr(FROZEN_PHASE1_V1, "devig")
    assert not hasattr(FROZEN_PHASE1_V1, "elo_mov")
    assert FROZEN_PHASE1_V1.market_blend_weight == 0.5
    assert FROZEN_PHASE1_V1.xi == 0.003

    # Both candidates are opt-in at every entry point.
    defaults = inspect.signature(run_model).parameters
    assert defaults["devig"].default == "proportional"
    assert defaults["elo_mov"].default is False
    assert defaults["use_elo"].default is False
    assert inspect.signature(elo.compute).parameters["margin_of_victory"].default is False

    # The incumbent devig is what an unconfigured caller gets.
    assert market.devig("proportional") is market.implied_result_probs
