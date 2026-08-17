from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from bym.backtest import lms as lms_backtest


def _matches(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp(date),
                "home_team": home,
                "away_team": away,
                "home_goals": 1,
                "away_goals": 0,
                "season": 2025,
                "division": "E0",
                "odds_h": 2.0,
                "odds_d": 3.0,
                "odds_a": 4.0,
            }
            for date, home, away in rows
        ]
    )


def test_infer_fixture_slates_uses_club_repeats_and_date_gaps():
    matches = _matches(
        [
            ("2025-08-01", "A", "B"),
            ("2025-08-01", "C", "D"),
            # No repeat, still part of the same short fixture window.
            ("2025-08-03", "E", "F"),
            # A repeats, so this starts a new slate even before the gap rule.
            ("2025-08-04", "A", "C"),
            # A later date starts another slate even without a club repeat.
            ("2025-08-10", "G", "H"),
        ]
    )

    slates = lms_backtest.infer_fixture_slates(matches, max_gap_days=3)

    assert [len(slate) for slate in slates] == [3, 1, 1]
    assert list(slates[1]["home_team"]) == ["A"]


def test_metrics_reports_binary_calibration_and_loss():
    rows = pd.DataFrame({"p_win": [0.8, 0.6, 0.4], "won": [True, False, False]})

    result = lms_backtest.metrics(rows)

    assert result.decisions == 3
    assert result.wins == 1
    assert result.win_rate == pytest.approx(1 / 3)
    assert result.mean_predicted_win == pytest.approx(0.6)
    assert result.calibration_gap == pytest.approx(1 / 3 - 0.6)
    assert result.brier == pytest.approx(((0.8 - 1) ** 2 + 0.6**2 + 0.4**2) / 3)
    assert result.log_loss > 0


def test_market_pick_chooses_the_best_winning_probability_not_a_draw():
    slate = _matches(
        [
            ("2025-08-01", "Home", "Away"),
            ("2025-08-01", "Other Home", "Other Away"),
        ]
    )
    slate.loc[0, ["odds_h", "odds_d", "odds_a"]] = [4.0, 2.0, 3.0]
    slate.loc[1, ["odds_h", "odds_d", "odds_a"]] = [1.5, 5.0, 8.0]

    team, p_win = lms_backtest._market_pick(slate)  # noqa: SLF001 - unit boundary

    assert team == "Other Home"
    assert p_win > 0.6


def test_replay_slate_excludes_used_clubs_and_exposes_results(monkeypatch):
    matches = _matches(
        [
            ("2025-08-01", "A", "B"),
            ("2025-08-01", "C", "D"),
            ("2025-08-08", "A", "C"),
        ]
    )
    seen = {}

    monkeypatch.setattr(lms_backtest, "_fit_params", lambda *args: object())

    def fake_recommend(_params, _gameweek, used_clubs, **_kwargs):
        seen["used"] = used_clubs
        return [
            SimpleNamespace(
                team="C", opponent="D", is_home=True, p_win=0.7,
                p_draw=0.2, p_loss=0.1, confidence="HIGH",
            )
        ]

    monkeypatch.setattr(lms_backtest, "recommend_gameweek", fake_recommend)
    planned = SimpleNamespace(
        gameweek=1,
        candidate=SimpleNamespace(
            team="C", opponent="D", is_home=True, p_win=0.7,
        ),
    )
    def fake_plan(*_args, **kwargs):
        seen["planned_used"] = kwargs["used_clubs"]
        return SimpleNamespace(this_week=planned, picks=(planned,))

    monkeypatch.setattr(lms_backtest, "plan_horizon", fake_plan)
    replay = lms_backtest.replay_slate(
        matches, 2025, 1, used_clubs=["A"], min_training_matches=0
    )

    assert seen["used"] == ["A"]
    assert seen["planned_used"] == ["A"]
    assert replay.model_pick == "C"
    assert replay.candidates[0]["won"] is True
    assert replay.fixtures[0]["score"] == "1–0"
    assert replay.suggested_pick == "C"
    assert replay.suggested_path[0]["game"] == 1
