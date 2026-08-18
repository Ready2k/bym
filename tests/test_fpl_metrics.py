import math

import pytest

from bym.backtest import fpl_metrics as metrics
from bym.backtest.fpl_walk_forward import WalkForwardPrediction
from bym.fpl.players import Position
from bym.fpl.projection import FixtureProjection
from bym.fpl.scoring import ExpectedPointsBreakdown

SEASON = 2022
GW = 1


def _proj(p_appearance=1.0, p_60_plus=1.0):
    return FixtureProjection(
        player_id=1, fixture_id="fx", season=SEASON, gameweek=GW,
        p_appearance=p_appearance, p_start=p_appearance, p_60_plus=p_60_plus,
        expected_minutes=90.0, goal_lambda=0.2, assist_lambda=0.2,
        p_clean_sheet=0.3, expected_saves=None,
    )


def _pred(
    player_id=1, fixture_id="fx", position=Position.MID.value,
    predicted=None, actual=None, p_appearance=1.0, p_60_plus=1.0,
):
    return WalkForwardPrediction(
        player_id=player_id, fixture_id=fixture_id, season=SEASON, gameweek=GW,
        position=position, projection=_proj(p_appearance, p_60_plus),
        predicted=predicted, actual=actual,
    )


def bd(appearance=0.0, goal=0.0, assist=0.0, clean_sheet=0.0, saves=0.0):
    return ExpectedPointsBreakdown(appearance, goal, assist, clean_sheet, saves)


def test_evaluate_computes_mae_and_calibration_gap():
    predictions = [
        _pred(predicted=bd(2.0, 3.0), actual=bd(2.0, 1.0)),  # predicted 5, actual 3 -> |err|=2
        _pred(predicted=bd(2.0, 0.0), actual=bd(2.0, 4.0)),  # predicted 2, actual 6 -> |err|=4
    ]
    result = metrics.evaluate(predictions)
    assert result.n == 2
    assert result.mae == pytest.approx((2 + 4) / 2)
    assert result.mean_predicted == pytest.approx((5 + 2) / 2)
    assert result.mean_actual == pytest.approx((3 + 6) / 2)
    assert result.calibration_gap == pytest.approx(3.5 - 4.5)


def test_evaluate_on_empty_predictions_returns_nan_not_a_crash():
    result = metrics.evaluate([])
    assert result.n == 0
    assert math.isnan(result.mae)


def test_evaluate_by_position_splits_metrics_per_position():
    predictions = [
        _pred(position=Position.DEF.value, predicted=bd(goal=6.0), actual=bd(goal=0.0)),
        _pred(position=Position.FWD.value, predicted=bd(goal=4.0), actual=bd(goal=4.0)),
    ]
    by_position = metrics.evaluate_by_position(predictions)
    assert by_position[Position.DEF.value].mae == pytest.approx(6.0)
    assert by_position[Position.FWD.value].mae == pytest.approx(0.0)
    assert set(by_position) == {Position.DEF.value, Position.FWD.value}


def test_error_decomposition_attributes_error_to_the_right_component():
    """A projection that's wrong ONLY on goals must not show up as
    'minutes error' or 'fixture model error' — the whole point of
    decomposing is not chasing the wrong subsystem (§18)."""
    predictions = [
        _pred(
            predicted=bd(appearance=2.0, goal=5.0, assist=0.0, clean_sheet=4.0, saves=0.0),
            actual=bd(appearance=2.0, goal=0.0, assist=0.0, clean_sheet=4.0, saves=0.0),
        )
    ]
    decomposition = metrics.error_decomposition(predictions)
    assert decomposition.minutes_error == pytest.approx(0.0)
    assert decomposition.scoring_rate_error == pytest.approx(5.0)
    assert decomposition.fixture_model_error == pytest.approx(0.0)


def test_error_decomposition_attributes_clean_sheet_error_to_fixture_model():
    predictions = [
        _pred(
            predicted=bd(appearance=2.0, clean_sheet=4.0),
            actual=bd(appearance=2.0, clean_sheet=0.0),
        )
    ]
    decomposition = metrics.error_decomposition(predictions)
    assert decomposition.fixture_model_error == pytest.approx(4.0)
    assert decomposition.minutes_error == pytest.approx(0.0)
    assert decomposition.scoring_rate_error == pytest.approx(0.0)


def test_minutes_calibration_compares_predicted_probability_to_realised_rate():
    predictions = [
        _pred(p_appearance=0.8, p_60_plus=0.6, predicted=bd(), actual=bd(appearance=2.0)),  # appeared, 60+
        _pred(p_appearance=0.8, p_60_plus=0.6, predicted=bd(), actual=bd(appearance=0.0)),  # absent
    ]
    result = metrics.minutes_calibration(predictions)
    assert result.n == 2
    assert result.mean_predicted_p_appearance == pytest.approx(0.8)
    assert result.appearance_rate == pytest.approx(0.5)  # 1 of 2 appeared
    assert result.appearance_calibration_gap == pytest.approx(0.8 - 0.5)
    assert result.mean_predicted_p_60_plus == pytest.approx(0.6)
    assert result.rate_60_plus == pytest.approx(0.5)  # 1 of 2 played 60+


def test_calibration_by_predicted_band_orders_bands_by_predicted_total():
    predictions = [
        _pred(predicted=bd(goal=1.0), actual=bd(goal=1.0)),
        _pred(predicted=bd(goal=5.0), actual=bd(goal=0.0)),
        _pred(predicted=bd(goal=10.0), actual=bd(goal=10.0)),
    ]
    bands = metrics.calibration_by_predicted_band(predictions, n_bands=3)
    assert len(bands) == 3
    assert [b.mean_predicted for b in bands] == sorted(b.mean_predicted for b in bands)
    assert sum(b.n for b in bands) == 3


def test_calibration_by_predicted_band_is_empty_for_no_predictions():
    assert metrics.calibration_by_predicted_band([]) == []


def test_distribution_score_is_explicitly_not_available_for_this_baseline():
    predictions = [_pred(predicted=bd(goal=1.0), actual=bd(goal=1.0))]
    assert metrics.distribution_score(predictions) is None
