import pandas as pd
import pytest

from super6 import prospective as pr

FIXTURES = [
    ("Arsenal", "Everton"),
    ("Chelsea", "Newcastle"),
    ("Fulham", "Brentford"),
]


def seeded_ledger():
    ledger = pd.DataFrame(columns=pr.COLUMNS)
    ledger = pr.record_picks(
        ledger, 2026, 1, FIXTURES, pr.FROZEN_ARM, [(2, 0), (1, 1), (2, 1)]
    )
    ledger = pr.record_picks(
        ledger, 2026, 1, FIXTURES, pr.MARKET_ARM, [(2, 1), (1, 1), (2, 1)]
    )
    return ledger


def test_market_pick_convention():
    assert pr.market_pick((1.5, 4.0, 6.0)) == (2, 1)  # home favourite
    assert pr.market_pick((6.0, 4.0, 1.5)) == (1, 2)  # away favourite
    assert pr.market_pick((4.0, 1.5, 6.0)) == (1, 1)  # draw favourite


def test_arms_are_rows_so_new_arms_append():
    ledger = seeded_ledger()
    ledger = pr.record_picks(
        ledger, 2026, 1, FIXTURES, "PHASE2_AGENT", [(3, 0), (1, 0), (1, 1)]
    )
    assert set(ledger["arm"]) == {pr.FROZEN_ARM, pr.MARKET_ARM, "PHASE2_AGENT"}
    # the frozen arm's rows are untouched by the new arm
    frozen = ledger[ledger["arm"] == pr.FROZEN_ARM]
    assert list(frozen["pred_home"]) == [2, 1, 2]


def test_refuses_to_overwrite_recorded_arm():
    ledger = seeded_ledger()
    with pytest.raises(ValueError, match="already recorded"):
        pr.record_picks(
            ledger, 2026, 1, FIXTURES, pr.FROZEN_ARM, [(0, 0), (0, 0), (0, 0)]
        )


def test_scoring_and_standings():
    ledger = seeded_ledger()
    # Arsenal 2-0 (frozen exact, market result-only),
    # Chelsea 3-1 (both wrong), Fulham 2-1 (both exact)
    ledger = pr.score_round(
        ledger, 2026, 1, {0: (2, 0), 1: (3, 1), 2: (2, 1)}
    )
    table = pr.standings(ledger).set_index("arm")
    assert table.loc[pr.FROZEN_ARM, "points"] == 10  # 5 + 0 + 5
    assert table.loc[pr.MARKET_ARM, "points"] == 7  # 2 + 0 + 5
    assert table.loc[pr.FROZEN_ARM, "exact"] == 2
    assert table.loc[pr.FROZEN_ARM, "rounds"] == 1


def test_void_fixture_excluded_not_zeroed():
    """§2.2: a VOID fixture must not be counted as a zero-point miss."""
    ledger = seeded_ledger()
    ledger = pr.score_round(ledger, 2026, 1, {0: (2, 0), 1: None, 2: (2, 1)})
    table = pr.standings(ledger).set_index("arm")
    assert table.loc[pr.FROZEN_ARM, "fixtures"] == 2  # void one excluded
    assert table.loc[pr.FROZEN_ARM, "points"] == 10
    assert table.loc[pr.FROZEN_ARM, "exact%"] == 100.0  # 2 of 2, not 2 of 3
    voided = ledger[ledger["status"] == pr.VOID_STATUS]
    assert len(voided) == 2  # still present for both arms, not deleted


def test_roundtrip_csv(tmp_path):
    ledger = pr.score_round(seeded_ledger(), 2026, 1, {0: (2, 0), 1: (3, 1), 2: (2, 1)})
    path = tmp_path / "ledger.csv"
    pr.save_ledger(ledger, path)
    reloaded = pr.load_ledger(path)
    assert pr.standings(reloaded).equals(pr.standings(ledger))


def test_score_after_csv_roundtrip(tmp_path):
    """The real workflow: record and save before the deadline, then score
    in a separate process after results arrive. An all-empty status column
    round-trips as float64, which used to reject the string assignment."""
    path = tmp_path / "ledger.csv"
    pr.save_ledger(seeded_ledger(), path)

    reloaded = pr.load_ledger(path)
    scored = pr.score_round(reloaded, 2026, 1, {0: (2, 0), 1: None, 2: (2, 1)})
    pr.save_ledger(scored, path)

    table = pr.standings(pr.load_ledger(path)).set_index("arm")
    assert table.loc[pr.FROZEN_ARM, "points"] == 10
    assert table.loc[pr.FROZEN_ARM, "fixtures"] == 2  # void excluded


def test_record_after_csv_roundtrip(tmp_path):
    """Appending a later arm to a reloaded ledger must also work."""
    path = tmp_path / "ledger.csv"
    pr.save_ledger(seeded_ledger(), path)
    reloaded = pr.load_ledger(path)
    appended = pr.record_picks(
        reloaded, 2026, 1, FIXTURES, pr.SUBMITTED_ARM, [(2, 0), (1, 1), (0, 0)]
    )
    assert pr.already_recorded(appended, 2026, 1, pr.SUBMITTED_ARM)


def test_empty_ledger_standings():
    assert pr.standings(pd.DataFrame(columns=pr.COLUMNS)).empty
