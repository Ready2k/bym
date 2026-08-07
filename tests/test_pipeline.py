"""Acceptance test for Phase 2 Increment 1.

For a manually configured round, the system obtains current 1X2 market
probabilities, persists provenance/timestamps, produces the consensus, and
feeds the existing Phase 1 model without manual odds entry.

The API itself is mocked — live verification needs the user's key.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from super6.round import Fixture, Round
from super6.sources import pipeline
from super6.sources.odds_api import BookmakerOdds, EventOdds

NOW = datetime(2026, 8, 15, 12, 30, tzinfo=timezone.utc)

ROUND = Round(
    round_number=1,
    deadline_at=datetime(2026, 8, 15, 14, 0),
    fixtures=(
        Fixture("Arsenal", "Everton"),
        Fixture("Man City", "Bournemouth"),
        Fixture("Chelsea", "Newcastle"),
        Fixture("Fulham", "Brentford"),
        Fixture("Leeds", "Tottenham"),
        Fixture("West Ham", "Aston Villa"),
    ),
)

# The feed uses full club names; the round uses Football-Data short names.
FEED = [
    ("Arsenal", "Everton", 1.50, 4.20, 7.00),
    ("Manchester City", "AFC Bournemouth", 1.30, 5.50, 9.00),
    ("Chelsea", "Newcastle United", 2.10, 3.50, 3.40),
    ("Fulham", "Brentford", 2.40, 3.30, 3.00),
    ("Leeds United", "Tottenham Hotspur", 2.60, 3.40, 2.70),
    ("West Ham United", "Aston Villa", 3.10, 3.40, 2.30),
]


def make_events(fresh=True):
    age = timedelta(minutes=10) if fresh else timedelta(days=3)
    ts = (NOW - age).isoformat().replace("+00:00", "Z")
    events = []
    for i, (home, away, h, d, a) in enumerate(FEED):
        events.append(
            EventOdds(
                event_id=f"evt{i}",
                sport_key="soccer_epl",
                commence_time="2026-08-15T14:00:00Z",
                home_team=home,
                away_team=away,
                bookmakers=[
                    BookmakerOdds("williamhill", ts, h, d, a),
                    BookmakerOdds("betfair", ts, h * 1.02, d * 0.99, a * 1.01),
                    BookmakerOdds("skybet", ts, h * 0.99, d * 1.01, a * 0.99),
                ],
            )
        )
    return events


@pytest.fixture
def snapshots(tmp_path):
    return tmp_path / "odds.csv", tmp_path / "consensus.csv"


def acquire(monkeypatch, snapshots, events, divisions=("E0",)):
    odds_path, cons_path = snapshots
    monkeypatch.setattr(
        pipeline.odds_api, "fetch_odds", lambda division, key, **kw: events
    )
    return pipeline.acquire_round_odds(
        ROUND, "fake-key", divisions=divisions, now=NOW,
        odds_snapshot_path=odds_path, consensus_snapshot_path=cons_path,
    )


def test_all_six_fixtures_covered_via_name_mapping(monkeypatch, snapshots):
    result = acquire(monkeypatch, snapshots, make_events())
    assert result.covered == 6
    assert all(f.consensus is not None for f in result.fixtures)


def test_consensus_probabilities_are_sane(monkeypatch, snapshots):
    result = acquire(monkeypatch, snapshots, make_events())
    arsenal = next(f for f in result.fixtures if f.home == "Arsenal").consensus
    assert arsenal.p_home + arsenal.p_draw + arsenal.p_away == pytest.approx(1.0)
    assert 0.6 < arsenal.p_home < 0.7  # ~1.50 de-vigged
    assert arsenal.bookmaker_count == 3


def test_both_provenance_layers_persisted(monkeypatch, snapshots):
    odds_path, cons_path = snapshots
    acquire(monkeypatch, snapshots, make_events())

    odds = pd.read_csv(odds_path)
    assert len(odds) == 18  # 6 fixtures x 3 bookmakers
    for col in ["bookmaker", "bookmaker_last_update", "fetched_at",
                "odds_h", "devig_h", "overround"]:
        assert col in odds.columns

    cons = pd.read_csv(cons_path)
    assert len(cons) == 6
    for col in ["calculated_at", "bookmaker_count", "consensus_h",
                "dispersion", "stale_excluded"]:
        assert col in cons.columns


def test_snapshots_append_across_polls(monkeypatch, snapshots):
    odds_path, _ = snapshots
    acquire(monkeypatch, snapshots, make_events())
    acquire(monkeypatch, snapshots, make_events())
    assert len(pd.read_csv(odds_path)) == 36  # history retained, not overwritten


def test_credits_counted_per_division_not_per_fixture(monkeypatch, snapshots):
    result = acquire(monkeypatch, snapshots, make_events(), divisions=("E0", "E1"))
    assert result.credits_spent == 2


def test_apply_to_round_fills_odds(monkeypatch, snapshots):
    result = acquire(monkeypatch, snapshots, make_events())
    updated = pipeline.apply_to_round(ROUND, result)
    assert all(f.odds is not None for f in updated.fixtures)
    h, d, a = updated.fixtures[0].odds
    assert 1 / h + 1 / d + 1 / a == pytest.approx(1.0)  # fair odds


def test_stale_feed_degrades_to_model_not_stale_prices(monkeypatch, snapshots):
    result = acquire(monkeypatch, snapshots, make_events(fresh=False))
    assert result.covered == 0
    assert all("stale" in f.reason for f in result.fixtures)
    updated = pipeline.apply_to_round(ROUND, result)
    assert all(f.odds is None for f in updated.fixtures)  # no imputation


def test_unmatched_fixture_reported_not_guessed(monkeypatch, snapshots):
    events = make_events()
    events[0].home_team = "Some Unknown FC"
    result = acquire(monkeypatch, snapshots, events)
    missing = [f for f in result.fixtures if f.consensus is None]
    assert len(missing) == 1
    assert missing[0].home == "Arsenal"
    assert "no matching event" in missing[0].reason


def test_partial_coverage_leaves_other_fixtures_intact(monkeypatch, snapshots):
    events = make_events()
    events[0].bookmakers = []
    result = acquire(monkeypatch, snapshots, events)
    assert result.covered == 5
    updated = pipeline.apply_to_round(ROUND, result)
    assert updated.fixtures[0].odds is None
    assert updated.fixtures[1].odds is not None
