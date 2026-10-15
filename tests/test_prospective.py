from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from bym import prospective as pr
from bym.round import Fixture, Round

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


def test_market_picks_require_complete_odds_before_any_recording():
    picks, missing = pr.market_picks([
        (1.5, 4.0, 6.0),
        None,
        (6.0, 4.0, 1.5),
    ])
    assert picks is None
    assert missing == [1]


def test_market_picks_build_the_complete_benchmark():
    picks, missing = pr.market_picks([
        (1.5, 4.0, 6.0),
        (4.0, 1.5, 6.0),
        (6.0, 4.0, 1.5),
    ])
    assert picks == [(2, 1), (1, 1), (1, 2)]
    assert missing == []


def test_market_pick_rejects_invalid_prices():
    with pytest.raises(ValueError, match="greater than 1.0"):
        pr.market_pick((1.5, 1.0, 6.0))


def test_record_can_retry_market_after_partial_fetch(monkeypatch, tmp_path):
    """A partial market fetch must not make a later complete fetch impossible."""
    from bym import cli

    fixtures = tuple(
        Fixture(f"Home {i}", f"Away {i}") for i in range(6)
    )
    round_without_odds = Round(
        14, datetime(2026, 10, 18, 14, tzinfo=timezone.utc), fixtures
    )
    round_with_odds = Round(
        14,
        round_without_odds.deadline_at,
        tuple(
            Fixture(f.home, f.away, (1.5, 4.0, 6.0)) for f in fixtures
        ),
    )
    matches = pd.DataFrame({
        "home_team": [f.home for f in fixtures],
        "away_team": [f.away for f in fixtures],
    })
    recommendation = SimpleNamespace(
        fixtures=[
            SimpleNamespace(
                home=f.home, away=f.away, pick_home=2, pick_away=0, pick="2-0"
            )
            for f in fixtures
        ]
    )
    args = SimpleNamespace(
        round="ignored.json",
        fetch_odds=True,
        ledger=str(tmp_path / "ledger.csv"),
        data="ignored",
        seasons=[2020, 2024],
        divisions=["E0"],
        season=2026,
        submitted=None,
    )

    monkeypatch.setattr("bym.round.load_round", lambda _: round_without_odds)
    monkeypatch.setattr("bym.data.football_data.load_matches", lambda *a, **k: matches)
    monkeypatch.setattr(cli, "_fit_router", lambda *a, **k: object())
    monkeypatch.setattr("bym.recommend.recommend_round", lambda *a, **k: recommendation)
    fetched = iter([(round_without_odds, None), (round_with_odds, object())])
    monkeypatch.setattr(cli, "_acquire_odds", lambda *a, **k: next(fetched))

    assert cli.cmd_record(args) == 0
    first = pr.load_ledger(tmp_path / "ledger.csv")
    assert set(first["arm"]) == {
        pr.FROZEN_ARM, pr.PHASE2_ARM, pr.PHASE3_ARM,
    }

    assert cli.cmd_record(args) == 0
    second = pr.load_ledger(tmp_path / "ledger.csv")
    assert set(second["arm"]) == {
        pr.FROZEN_ARM, pr.PHASE2_ARM, pr.PHASE3_ARM, pr.MARKET_ARM,
    }


def test_arms_are_rows_so_new_arms_append(tmp_path):
    registry = tmp_path / "arms.json"
    pr.register_arm("PHASE2_AGENT", "2026-08-07", registry)
    ledger = seeded_ledger()
    ledger = pr.record_picks(
        ledger, 2026, 1, FIXTURES, "PHASE2_AGENT", [(3, 0), (1, 0), (1, 1)],
        arm_registry_path=registry,
    )
    assert set(ledger["arm"]) == {pr.FROZEN_ARM, pr.MARKET_ARM, "PHASE2_AGENT"}
    # the frozen arm's rows are untouched by the new arm
    frozen = ledger[ledger["arm"] == pr.FROZEN_ARM]
    assert list(frozen["pred_home"]) == [2, 1, 2]


def test_unregistered_arm_cannot_be_recorded(tmp_path):
    """An arm added after the fact is indistinguishable from one fitted to
    known results, so backfilling is refused outright."""
    registry = tmp_path / "arms.json"
    with pytest.raises(ValueError, match="not registered"):
        pr.record_picks(
            pd.DataFrame(columns=pr.COLUMNS), 2026, 1, FIXTURES,
            "MYSTERY_ARM", [(1, 0)] * 3, arm_registry_path=registry,
        )


def test_registration_date_cannot_be_moved(tmp_path):
    registry = tmp_path / "arms.json"
    pr.register_arm("PHASE3_STRATEGY", "2026-09-01", registry)
    with pytest.raises(ValueError, match="already registered"):
        pr.register_arm("PHASE3_STRATEGY", "2026-08-01", registry)
    assert pr.load_arm_registry(registry)["PHASE3_STRATEGY"] == "2026-09-01"


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
