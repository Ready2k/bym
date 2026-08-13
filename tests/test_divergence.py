import pytest

from bym import divergence
from bym.model.matrix import score_matrix


def test_total_variation_bounds():
    assert divergence.total_variation((0.5, 0.3, 0.2), (0.5, 0.3, 0.2)) == 0.0
    assert divergence.total_variation((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)) == pytest.approx(1.0)


def test_model_vs_market_and_dispersion_are_separate():
    """Identical model/market agreement, different bookmaker dispersion —
    the two must not move together."""
    m = score_matrix(1.6, 1.2, rho=-0.05)
    agree_tight = divergence.assess(m, m.p_home_win, m.p_draw, m.p_away_win, 0.01, 9)
    agree_loose = divergence.assess(m, m.p_home_win, m.p_draw, m.p_away_win, 0.20, 9)
    assert agree_tight.model_vs_market == pytest.approx(agree_loose.model_vs_market)
    assert agree_tight.book_dispersion != agree_loose.book_dispersion


def test_uses_pre_calibration_model_probabilities():
    """Passing the raw matrix must measure genuine disagreement; a blended
    matrix would measure how hard w pulled instead."""
    from bym.model.market import blend_matrix

    raw = score_matrix(1.6, 1.2, rho=-0.05)
    market = (0.30, 0.25, 0.45)
    blended = blend_matrix(raw, *market, weight=0.5)

    raw_report = divergence.assess(raw, *market, 0.01, 9)
    blended_report = divergence.assess(blended, *market, 0.01, 9)
    assert raw_report.model_vs_market > blended_report.model_vs_market


def test_largest_disagreement_identifies_outcome():
    m = score_matrix(2.0, 0.9, rho=-0.05)  # model strongly favours home
    report = divergence.assess(m, 0.25, 0.25, 0.50, 0.01, 9)
    outcome, gap = report.largest_disagreement
    assert outcome in {"H", "A"}
    assert abs(gap) > 10


def test_divergence_factor_categories():
    m = score_matrix(1.6, 1.2, rho=-0.05)
    agreeing = divergence.assess(m, m.p_home_win, m.p_draw, m.p_away_win, 0.01, 9)
    assert divergence.divergence_factor(agreeing) == "LOW"

    disagreeing_tight = divergence.assess(m, 0.20, 0.20, 0.60, 0.01, 9)
    assert divergence.divergence_factor(disagreeing_tight) == "HIGH"

    # same disagreement but the books disagree with each other too —
    # weaker evidence that the model is the one that's wrong
    disagreeing_loose = divergence.assess(m, 0.20, 0.20, 0.60, 0.20, 9)
    assert divergence.divergence_factor(disagreeing_loose) == "MEDIUM"


def test_factor_unknown_without_market():
    assert divergence.divergence_factor(None) == "UNKNOWN"
