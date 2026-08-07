"""Backtest metrics (SPEC.md §16): points/fixture, exact-score and result
accuracy, Brier score and log loss over H/D/A probabilities."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..scoring import result_of, super6_points

RESULTS = ("H", "D", "A")


@dataclass(frozen=True)
class BacktestMetrics:
    n_fixtures: int
    points_per_fixture: float
    exact_score_rate: float
    result_accuracy: float
    brier: float
    log_loss: float

    def as_row(self, label: str) -> dict:
        return {
            "model": label,
            "fixtures": self.n_fixtures,
            "pts/fixture": round(self.points_per_fixture, 4),
            "pts/round(6)": round(self.points_per_fixture * 6, 2),
            "exact%": round(self.exact_score_rate * 100, 2),
            "result%": round(self.result_accuracy * 100, 2),
            "brier": round(self.brier, 4),
            "logloss": round(self.log_loss, 4),
        }


def evaluate(predictions: pd.DataFrame) -> BacktestMetrics:
    """predictions columns: pred_home, pred_away, actual_home, actual_away,
    p_h, p_d, p_a (result probabilities)."""
    if len(predictions) == 0:
        nan = float("nan")
        return BacktestMetrics(0, nan, nan, nan, nan, nan)
    pts = predictions.apply(
        lambda r: super6_points(
            r["pred_home"], r["pred_away"], r["actual_home"], r["actual_away"]
        ),
        axis=1,
    )
    exact = (predictions["pred_home"] == predictions["actual_home"]) & (
        predictions["pred_away"] == predictions["actual_away"]
    )
    pred_res = predictions.apply(
        lambda r: result_of(r["pred_home"], r["pred_away"]), axis=1
    )
    act_res = predictions.apply(
        lambda r: result_of(r["actual_home"], r["actual_away"]), axis=1
    )

    probs = predictions[["p_h", "p_d", "p_a"]].to_numpy(dtype=float)
    onehot = np.stack([(act_res == r).to_numpy(dtype=float) for r in RESULTS], axis=1)
    brier = float(((probs - onehot) ** 2).sum(axis=1).mean())
    p_actual = np.clip((probs * onehot).sum(axis=1), 1e-12, 1.0)
    log_loss = float(-np.log(p_actual).mean())

    return BacktestMetrics(
        n_fixtures=len(predictions),
        points_per_fixture=float(pts.mean()),
        exact_score_rate=float(exact.mean()),
        result_accuracy=float((pred_res == act_res).mean()),
        brier=brier,
        log_loss=log_loss,
    )
