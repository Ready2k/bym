import pandas as pd
import pytest

from super6.backtest.metrics import evaluate


def test_evaluate_known_values():
    predictions = pd.DataFrame(
        {
            "pred_home": [2, 1, 0],
            "pred_away": [0, 1, 2],
            "actual_home": [2, 3, 1],
            "actual_away": [0, 1, 0],
            "p_h": [0.6, 0.5, 0.2],
            "p_d": [0.25, 0.3, 0.3],
            "p_a": [0.15, 0.2, 0.5],
        }
    )
    m = evaluate(predictions)
    # fixture 1: exact (5), fixture 2: wrong (draw vs home win, 0),
    # fixture 3: wrong (away vs home win, 0)
    assert m.points_per_fixture == pytest.approx(5 / 3)
    assert m.exact_score_rate == pytest.approx(1 / 3)
    assert m.result_accuracy == pytest.approx(1 / 3)
    # actual results: H, H, H
    brier = (
        ((0.6 - 1) ** 2 + 0.25**2 + 0.15**2)
        + ((0.5 - 1) ** 2 + 0.3**2 + 0.2**2)
        + ((0.2 - 1) ** 2 + 0.3**2 + 0.5**2)
    ) / 3
    assert m.brier == pytest.approx(brier)
