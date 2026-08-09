import numpy as np
import pytest

from super6.model.market import blend_matrix, implied_result_probs
from super6.model.matrix import score_matrix


def test_implied_probs_remove_margin():
    # a typical overround book (~105%)
    q_h, q_d, q_a = implied_result_probs(1.8, 3.6, 4.8)
    assert q_h + q_d + q_a == pytest.approx(1.0)
    assert q_h > q_d > q_a  # ordering preserved


def test_weight_zero_is_identity():
    m = score_matrix(1.7, 1.1, rho=-0.06)
    blended = blend_matrix(m, 0.9, 0.05, 0.05, weight=0.0)
    assert np.allclose(blended.probs, m.probs)


def test_weight_one_hits_market_result_probs():
    """§32.3: after full blending, result-level probabilities equal the
    market's; the within-result score shape is the model's."""
    m = score_matrix(1.7, 1.1, rho=-0.06)
    q = (0.5, 0.3, 0.2)
    blended = blend_matrix(m, *q, weight=1.0)
    assert blended.p_home_win == pytest.approx(q[0], abs=1e-9)
    assert blended.p_draw == pytest.approx(q[1], abs=1e-9)
    assert blended.p_away_win == pytest.approx(q[2], abs=1e-9)


def test_within_result_shape_preserved():
    """The ratio of any two scorelines in the same result must not change —
    H/D/A prices carry no exact-score information (§32.3)."""
    m = score_matrix(1.7, 1.1, rho=-0.06)
    blended = blend_matrix(m, 0.6, 0.25, 0.15, weight=0.7)
    ratio_before = m.p_score(2, 1) / m.p_score(1, 0)
    ratio_after = blended.p_score(2, 1) / blended.p_score(1, 0)
    assert ratio_after == pytest.approx(ratio_before, rel=1e-9)


def test_blended_matrix_sums_to_one():
    m = score_matrix(2.2, 0.9, rho=-0.04)
    blended = blend_matrix(m, 0.4, 0.35, 0.25, weight=0.5)
    assert blended.probs.sum() == pytest.approx(1.0, abs=1e-12)


def test_invalid_weight_rejected():
    m = score_matrix(1.5, 1.2)
    with pytest.raises(ValueError):
        blend_matrix(m, 0.5, 0.3, 0.2, weight=1.5)


# ---------------------------------------------------------------------------
# §32.1 Shin's method (ablation_007 candidate — implemented, not promoted)


def test_shin_probabilities_sum_to_one():
    from super6.model import market

    for odds in [(1.45, 4.5, 7.5), (2.1, 3.5, 3.4), (3.1, 3.4, 2.3), (1.2, 7.0, 15.0)]:
        assert sum(market.implied_result_probs_shin(*odds)) == pytest.approx(1.0, abs=1e-9)


def test_shin_moves_weight_toward_the_favourite():
    """The favourite-longshot correction is the whole point: proportional
    devigging removes margin evenly, which overstates the longshot."""
    from super6.model import market

    odds = (1.35, 5.25, 8.5)
    proportional = market.implied_result_probs(*odds)
    shin = market.implied_result_probs_shin(*odds)
    assert shin[0] > proportional[0]  # favourite up
    assert shin[2] < proportional[2]  # longshot down


def test_shin_z_is_zero_for_a_book_with_no_margin():
    """z=0 must recover the proportional method exactly, so the candidate is
    never worse-defined than the incumbent it would replace."""
    from super6.model import market

    fair = (3.0, 3.0, 3.0)
    assert market.shin_z(*fair) == 0.0
    assert market.implied_result_probs_shin(*fair) == market.implied_result_probs(*fair)


def test_shin_z_lands_in_the_plausible_insider_range():
    from super6.model import market

    z = market.shin_z(1.45, 4.5, 7.5)
    assert 0.0 < z < 0.10


def test_devig_lookup_rejects_an_unknown_method():
    from super6.model import market

    assert market.devig("proportional") is market.implied_result_probs
    assert market.devig("shin") is market.implied_result_probs_shin
    with pytest.raises(ValueError, match="unknown devig method"):
        market.devig("vigfree")
