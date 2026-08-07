from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from super6 import movement
from super6.config import DEFAULT_MOVEMENT
from super6.sources.consensus import Consensus

NOW = datetime(2026, 8, 15, 13, 0, tzinfo=timezone.utc)


def consensus(h, d, a, dispersion=0.01, books=9, at=NOW):
    return Consensus(
        home_team="Chelsea",
        away_team="Newcastle",
        calculated_at=at.isoformat(timespec="seconds"),
        bookmaker_count=books,
        p_home=h,
        p_draw=d,
        p_away=a,
        dispersion=dispersion,
        stale_excluded=0,
        oldest_quote=None,
    )


def test_absolute_pp_is_primary_number():
    report = movement.compare(consensus(0.451, 0.262, 0.287), 0.517, 0.262, 0.221)
    away = next(d for d in report.deltas if d.outcome == "A")
    assert away.delta_pp == pytest.approx(6.6, abs=0.05)
    assert report.movement_strength == pytest.approx(6.6, abs=0.05)


def test_relative_change_reported_but_not_used_for_classification():
    """2% -> 3% is +50% relative and must still be NOISE."""
    report = movement.compare(consensus(0.03, 0.47, 0.50), 0.02, 0.48, 0.50)
    home = next(d for d in report.deltas if d.outcome == "H")
    assert home.relative_pct == pytest.approx(50.0)
    assert home.delta_pp == pytest.approx(1.0)
    assert report.materiality == "NOISE"


def test_materiality_bands():
    assert movement.classify_materiality(1.0).value == "NOISE"
    assert movement.classify_materiality(3.5).value == "MATERIAL"
    assert movement.classify_materiality(7.0).value == "MAJOR"


def test_movement_strength_and_agreement_are_independent():
    """The same move with different dispersion yields the same strength
    but different agreement — they must never be collapsed."""
    tight = movement.compare(consensus(0.29, 0.26, 0.45, dispersion=0.01), 0.22, 0.26, 0.52)
    loose = movement.compare(consensus(0.29, 0.26, 0.45, dispersion=0.15), 0.22, 0.26, 0.52)
    assert tight.movement_strength == pytest.approx(loose.movement_strength)
    assert tight.agreement == "HIGH"
    assert loose.agreement == "LOW"


def test_agreement_classification():
    assert movement.classify_agreement(0.01).value == "HIGH"
    assert movement.classify_agreement(0.07).value == "MEDIUM"
    assert movement.classify_agreement(0.20).value == "LOW"


def test_noise_does_not_trigger_reforecast():
    assert not movement.compare(consensus(0.51, 0.26, 0.23), 0.52, 0.26, 0.22).should_reforecast


def test_material_triggers_reforecast():
    assert movement.compare(consensus(0.45, 0.26, 0.29), 0.52, 0.26, 0.22).should_reforecast


# ---- history / reference points ----------------------------------------


def history_frame(rows):
    frame = pd.DataFrame(rows)
    frame["_ts"] = frame["calculated_at"].map(
        lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    )
    return movement.ConsensusHistory(frame.sort_values("_ts"))


def row(hours_ago, h, d, a):
    return {
        "calculated_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "home_team": "Chelsea",
        "away_team": "Newcastle",
        "consensus_h": h,
        "consensus_d": d,
        "consensus_a": a,
    }


def test_reference_points_collected():
    hist = history_frame(
        [row(48, 0.60, 0.24, 0.16), row(24, 0.55, 0.25, 0.20),
         row(6, 0.52, 0.26, 0.22), row(1.5, 0.50, 0.26, 0.24), row(0.2, 0.48, 0.26, 0.26)]
    )
    reports = movement.reference_reports(consensus(0.45, 0.26, 0.29), hist, now=NOW)
    assert movement.PREVIOUS_POLL in reports
    assert movement.FIRST_OBSERVED in reports
    assert "24h_ago" in reports and "6h_ago" in reports and "1.5h_ago" in reports


def test_first_observed_is_not_called_opening():
    """We never saw the bookmakers' opening price — only our first poll."""
    assert movement.FIRST_OBSERVED == "first_observed"
    hist = history_frame([row(48, 0.60, 0.24, 0.16), row(1, 0.50, 0.26, 0.24)])
    reports = movement.reference_reports(consensus(0.45, 0.26, 0.29), hist, now=NOW)
    first = reports[movement.FIRST_OBSERVED]
    assert first.deltas[0].previous == pytest.approx(0.60)


def test_missing_reference_reported_absent_not_substituted():
    """Only a recent snapshot exists — the 24h reference must be missing,
    not silently filled with the nearest available value."""
    hist = history_frame([row(0.5, 0.50, 0.26, 0.24)])
    reports = movement.reference_reports(consensus(0.45, 0.26, 0.29), hist, now=NOW)
    assert "24h_ago" not in reports
    assert "6h_ago" not in reports
    assert movement.PREVIOUS_POLL in reports


def test_empty_history_yields_no_previous_poll():
    hist = movement.ConsensusHistory(pd.DataFrame())
    assert movement.reference_reports(consensus(0.45, 0.26, 0.29), hist, now=NOW) == {}
