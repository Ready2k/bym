import numpy as np
import pytest
from scipy.stats import poisson

from super6.model.matrix import score_matrix


def test_matrix_sums_to_one():
    m = score_matrix(1.8, 1.1, rho=-0.08)
    assert m.probs.sum() == pytest.approx(1.0, abs=1e-12)


def test_result_probs_partition():
    m = score_matrix(2.1, 0.8, rho=-0.05)
    assert m.p_home_win + m.p_draw + m.p_away_win == pytest.approx(1.0, abs=1e-12)


def test_rho_zero_matches_independent_poisson():
    lam_h, lam_a = 1.5, 1.2
    m = score_matrix(lam_h, lam_a, rho=0.0)
    for h, a in [(0, 0), (1, 1), (2, 1), (3, 0)]:
        expected = poisson.pmf(h, lam_h) * poisson.pmf(a, lam_a)
        # grid renormalisation only shifts by < tail_tolerance
        assert m.p_score(h, a) == pytest.approx(expected, rel=1e-4)


def test_rho_negative_raises_draws_lowers_low_asymmetric_scores():
    base = score_matrix(1.4, 1.1, rho=0.0)
    dc = score_matrix(1.4, 1.1, rho=-0.1)
    # standard DC with negative rho: 0-0 and 1-1 up, 1-0 and 0-1 down
    assert dc.p_score(0, 0) > base.p_score(0, 0)
    assert dc.p_score(1, 1) > base.p_score(1, 1)
    assert dc.p_score(1, 0) < base.p_score(1, 0)
    assert dc.p_score(0, 1) < base.p_score(0, 1)


def test_ev_formula_spec_worked_example():
    """§2: EV = 2·P(result) + 3·P(score) ≡ 5·P(score) + 2·(P(result)-P(score))."""
    m = score_matrix(1.9, 1.0, rho=-0.06)
    for h, a in [(2, 1), (1, 1), (0, 2)]:
        p_r, p_s = m.p_result(h, a), m.p_score(h, a)
        assert m.ev(h, a) == pytest.approx(2 * p_r + 3 * p_s)
        assert m.ev(h, a) == pytest.approx(5 * p_s + 2 * (p_r - p_s))


def test_best_pick_maximises_ev_not_probability():
    """§2: highest-probability scoreline is not necessarily highest-EV —
    best_pick must return the EV argmax."""
    m = score_matrix(1.6, 1.3, rho=-0.05)
    ph, pa = m.best_pick()
    ev = m.ev_matrix()
    assert ev[ph, pa] == pytest.approx(ev.max())
