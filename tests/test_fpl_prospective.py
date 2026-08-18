import pandas as pd
import pytest

from bym.fpl import causality
from bym.fpl import prospective as pr

SEASON = 2026
GW = 1
SQUAD = frozenset(range(1, 16))
STARTERS = frozenset(range(1, 12))
CAPTAIN = 1
PREDICTED = {pid: float(pid) for pid in SQUAD}


def seeded_ledger():
    ledger = pd.DataFrame(columns=pr.COLUMNS)
    return pr.record_squad(
        ledger, SEASON, GW, pr.BASELINE_ARM, SQUAD, STARTERS, CAPTAIN, PREDICTED
    )


def test_record_squad_creates_one_row_per_squad_member_with_flags():
    ledger = seeded_ledger()
    assert len(ledger) == 15
    assert set(ledger["player_id"]) == SQUAD
    starters_recorded = set(ledger[ledger["starter"]]["player_id"])
    assert starters_recorded == STARTERS
    captains_recorded = ledger[ledger["captain"]]["player_id"].tolist()
    assert captains_recorded == [CAPTAIN]


def test_record_squad_refuses_to_overwrite_an_already_recorded_arm_gameweek():
    ledger = seeded_ledger()
    with pytest.raises(ValueError, match="already recorded"):
        pr.record_squad(
            ledger, SEASON, GW, pr.BASELINE_ARM, SQUAD, STARTERS, CAPTAIN, PREDICTED
        )


def test_unregistered_arm_cannot_be_recorded(tmp_path):
    registry = tmp_path / "arms.json"
    with pytest.raises(ValueError, match="not registered"):
        pr.record_squad(
            pd.DataFrame(columns=pr.COLUMNS), SEASON, GW, "SOME_NEW_ARM",
            SQUAD, STARTERS, CAPTAIN, PREDICTED, arm_registry_path=registry,
        )


def test_arms_are_rows_so_a_new_arm_appends_without_disturbing_others(tmp_path):
    registry = tmp_path / "arms.json"
    pr.register_arm("PHASE2_AGENT", "2026-08-17", registry)
    ledger = seeded_ledger()
    ledger = pr.record_squad(
        ledger, SEASON, GW, "PHASE2_AGENT", SQUAD, STARTERS, CAPTAIN, PREDICTED,
        arm_registry_path=registry,
    )
    assert set(ledger["arm"]) == {pr.BASELINE_ARM, "PHASE2_AGENT"}
    baseline_rows = ledger[ledger["arm"] == pr.BASELINE_ARM]
    assert len(baseline_rows) == 15  # untouched by the new arm


def test_score_gameweek_refuses_unless_the_gameweek_is_final():
    ledger = seeded_ledger()
    for status in ("UPCOMING", "LOCKED", "IN_PROGRESS", "PROVISIONAL"):
        with pytest.raises(causality.GameweekNotFinalError):
            pr.score_gameweek(ledger, SEASON, GW, {1: 10.0}, gameweek_status=status)


def test_score_gameweek_fills_actual_points_when_final():
    ledger = seeded_ledger()
    scored = pr.score_gameweek(
        ledger, SEASON, GW, {pid: float(pid) * 2 for pid in SQUAD}, gameweek_status="FINAL"
    )
    row = scored[scored["player_id"] == 3].iloc[0]
    assert row["actual_points"] == 6.0
    assert row["status"] == pr.OK_STATUS


def test_score_gameweek_raises_for_an_unrecorded_gameweek():
    ledger = seeded_ledger()
    with pytest.raises(ValueError, match="No recorded squads"):
        pr.score_gameweek(ledger, SEASON, GW + 1, {1: 5.0}, gameweek_status="FINAL")


def test_standings_is_empty_before_anything_is_scored():
    ledger = seeded_ledger()
    assert pr.standings(ledger).empty


def test_standings_doubles_the_captains_points_and_excludes_bench():
    ledger = seeded_ledger()
    actuals = {pid: 2.0 for pid in SQUAD}  # every player scores 2.0
    scored = pr.score_gameweek(ledger, SEASON, GW, actuals, gameweek_status="FINAL")
    result = pr.standings(scored)
    row = result[result["arm"] == pr.BASELINE_ARM].iloc[0]
    # 11 starters * 2.0, plus the captain's 2.0 doubled = 22 + 2 = 24
    assert row["points"] == pytest.approx(24.0)
    assert row["gameweeks"] == 1


def test_standings_compares_arms_independently():
    ledger = seeded_ledger()
    ledger = pr.record_squad(
        ledger, SEASON, GW, pr.SIMPLE_XP_ARM, SQUAD, STARTERS, captain_id=2,
        predicted_points_by_player=PREDICTED,
    )
    actuals = {pid: 1.0 for pid in SQUAD}
    scored = pr.score_gameweek(ledger, SEASON, GW, actuals, gameweek_status="FINAL")
    result = pr.standings(scored)
    assert set(result["arm"]) == {pr.BASELINE_ARM, pr.SIMPLE_XP_ARM}
    # Both arms have the same starters/actuals here, so identical totals.
    assert result["points"].nunique() == 1
