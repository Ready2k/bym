import numpy as np
import pytest

from bym.model.market import blend_matrix, implied_result_probs
from bym.model.matrix import score_matrix


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
    from bym.model import market

    for odds in [(1.45, 4.5, 7.5), (2.1, 3.5, 3.4), (3.1, 3.4, 2.3), (1.2, 7.0, 15.0)]:
        assert sum(market.implied_result_probs_shin(*odds)) == pytest.approx(1.0, abs=1e-9)


def test_shin_moves_weight_toward_the_favourite():
    """The favourite-longshot correction is the whole point: proportional
    devigging removes margin evenly, which overstates the longshot."""
    from bym.model import market

    odds = (1.35, 5.25, 8.5)
    proportional = market.implied_result_probs(*odds)
    shin = market.implied_result_probs_shin(*odds)
    assert shin[0] > proportional[0]  # favourite up
    assert shin[2] < proportional[2]  # longshot down


def test_shin_z_is_zero_for_a_book_with_no_margin():
    """z=0 must recover the proportional method exactly, so the candidate is
    never worse-defined than the incumbent it would replace."""
    from bym.model import market

    fair = (3.0, 3.0, 3.0)
    assert market.shin_z(*fair) == 0.0
    assert market.implied_result_probs_shin(*fair) == market.implied_result_probs(*fair)


def test_shin_z_lands_in_the_plausible_insider_range():
    from bym.model import market

    z = market.shin_z(1.45, 4.5, 7.5)
    assert 0.0 < z < 0.10


def test_devig_lookup_rejects_an_unknown_method():
    from bym.model import market

    assert market.devig("proportional") is market.implied_result_probs
    assert market.devig("shin") is market.implied_result_probs_shin
    with pytest.raises(ValueError, match="unknown devig method"):
        market.devig("vigfree")


# ---------------------------------------------------------------------------
# §32.3 Option B generalised: totals and Asian handicap (ablation_008)


def base_matrix():
    from bym.config import FROZEN_PHASE1_V1
    from bym.model.matrix import score_matrix

    return score_matrix(1.6, 1.1, -0.05, FROZEN_PHASE1_V1)


def implied(matrix, win, lose):
    won = float((matrix.probs * win).sum())
    lost = float((matrix.probs * lose).sum())
    return won / (won + lost)


def test_a_half_handicap_line_never_pushes():
    from bym.model import market

    win, lose = market.handicap_weights(-0.5, 6)
    assert float((1 - win - lose).sum()) == 0.0


def test_a_whole_handicap_line_pushes_exactly_the_scorelines_it_ties():
    """Line -1.0 returns the stake when the home team wins by exactly one."""
    from bym.model import market

    win, lose = market.handicap_weights(-1.0, 6)
    push = 1 - win - lose
    pushed = {(h, a) for h in range(7) for a in range(7) if push[h, a] > 0}
    assert pushed == {(h, h - 1) for h in range(1, 7)}


def test_a_quarter_line_is_half_a_bet_on_each_neighbour():
    """53% of the corpus's lines are quarters — rounding them to the nearest
    half would be a silent quarter-goal lie on the most common case."""
    from bym.model import market

    win, lose = market.handicap_weights(-0.75, 6)
    # Home winning by exactly one: the -1.0 half pushes, the -0.5 half wins.
    assert win[2, 1] == pytest.approx(0.5)
    assert lose[2, 1] == pytest.approx(0.0)
    assert (1 - win - lose)[2, 1] == pytest.approx(0.5)
    # Winning by two settles both halves.
    assert win[3, 1] == pytest.approx(1.0)


def test_a_totals_line_splits_on_goal_count_not_result():
    from bym.model import market

    win, lose = market.totals_weights(2.5, 6)
    assert win[3, 0] == 1.0 and win[0, 3] == 1.0  # 3 goals either way is over
    assert lose[1, 1] == 1.0 and lose[2, 0] == 1.0  # 2 goals is under
    assert float((1 - win - lose).sum()) == 0.0  # 2.5 can never push


@pytest.mark.parametrize("line", [-1.5, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 1.0])
def test_the_constraint_hits_its_target_exactly_on_every_line_type(line):
    """The naive "scale each side to the target share" is correct only when
    nothing pushes: a quarter line's pushed half still carries win weight
    when the ratio is measured again, and the constraint lands short."""
    from bym.model import market

    matrix = base_matrix()
    win, lose = market.handicap_weights(line, matrix.max_goals)
    out = market.constrain_matrix(matrix, win, lose, 0.65, 1.0)
    assert implied(out, win, lose) == pytest.approx(0.65, abs=1e-9)


def test_the_constraint_conserves_probability_mass():
    from bym.model import market

    matrix = base_matrix()
    for line in (-0.75, -1.0, 0.25):
        win, lose = market.handicap_weights(line, matrix.max_goals)
        out = market.constrain_matrix(matrix, win, lose, 0.7, 1.0)
        assert out.probs.sum() == pytest.approx(1.0, abs=1e-12)
        assert not (out.probs < 0).any()


def test_a_zero_weight_leaves_the_forecast_untouched():
    import numpy as np

    from bym.model import market

    matrix = base_matrix()
    win, lose = market.totals_weights(2.5, matrix.max_goals)
    out = market.constrain_matrix(matrix, win, lose, 0.9, 0.0)
    assert np.allclose(out.probs, matrix.probs)


def test_a_partial_weight_lands_between_model_and_market():
    from bym.model import market

    matrix = base_matrix()
    win, lose = market.totals_weights(2.5, matrix.max_goals)
    model = implied(matrix, win, lose)
    out = market.constrain_matrix(matrix, win, lose, 0.75, 0.5)
    assert implied(out, win, lose) == pytest.approx(0.5 * 0.75 + 0.5 * model, abs=1e-9)


def test_a_market_with_nothing_to_redistribute_is_declined():
    """A line so extreme every reachable scoreline sits on one side carries
    no information, and inventing some would be worse than ignoring it."""
    import numpy as np

    from bym.model import market

    matrix = base_matrix()
    win, lose = market.handicap_weights(-50.0, matrix.max_goals)
    out = market.constrain_matrix(matrix, win, lose, 0.6, 1.0)
    assert np.allclose(out.probs, matrix.probs)


def test_two_way_devig_removes_the_margin():
    from bym.model import market

    home, away = market.implied_two_way(1.91, 1.99)
    assert home + away == pytest.approx(1.0)
    assert home > away  # the shorter price is the likelier side
