import numpy as np
import pytest

from bym.model import golden_goal


def test_distribution_sums_to_one_with_no_goal_mass():
    bands, p_no_goal = golden_goal.first_goal_distribution([2.5, 3.1, 2.2, 2.8, 2.0, 2.9])
    assert bands.sum() + p_no_goal == pytest.approx(1.0, abs=1e-12)


def test_recommended_minute_is_weighted_median():
    """§15: minimise expected |band distance|, i.e. the conditional median —
    no neighbouring band should have lower expected absolute distance."""
    fc = golden_goal.forecast([2.5, 3.1, 2.2, 2.8, 2.0, 2.9])
    bands = fc.band_probs / fc.band_probs.sum()
    minutes = np.arange(1, len(bands) + 1)

    def expected_distance(guess: int) -> float:
        return float((bands * np.abs(minutes - guess)).sum())

    best = fc.recommended_minute
    for other in [best - 1, best + 1]:
        if 1 <= other <= 90:
            assert expected_distance(best) <= expected_distance(other) + 1e-12


def test_high_rates_mean_early_goal():
    high = golden_goal.forecast([4.0] * 6)
    low = golden_goal.forecast([1.5] * 6)
    assert high.recommended_minute < low.recommended_minute


def test_sky_interval_convention():
    # §15: minute 6 wins if the first goal falls between 5:00 and 5:59
    assert golden_goal.sky_interval(6) == "5:00-5:59"
    assert golden_goal.sky_interval(1) == "0:00-0:59"


def test_intervals_cover_distribution():
    fc = golden_goal.forecast([2.5] * 6)
    total = sum(fc.intervals().values())
    assert total == pytest.approx(fc.band_probs.sum(), abs=1e-9)
