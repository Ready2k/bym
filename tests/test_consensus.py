from datetime import datetime, timedelta, timezone

import pytest

from bym.sources import consensus as con
from bym.sources.odds_api import BookmakerOdds, EventOdds

NOW = datetime(2026, 8, 15, 13, 0, tzinfo=timezone.utc)


def book(key, h, d, a, minutes_old=10):
    ts = (NOW - timedelta(minutes=minutes_old)).isoformat().replace("+00:00", "Z")
    return BookmakerOdds(bookmaker=key, last_update=ts, home=h, draw=d, away=a)


def event(bookmakers):
    return EventOdds(
        event_id="e1",
        sport_key="soccer_epl",
        commence_time="2026-08-15T14:00:00Z",
        home_team="Arsenal",
        away_team="Everton",
        bookmakers=bookmakers,
    )


def test_devig_removes_margin():
    d = con.devig(book("wh", 1.5, 4.2, 7.0))
    assert d.p_home + d.p_draw + d.p_away == pytest.approx(1.0)
    assert d.overround > 1.0  # the book had a margin to remove


def test_consensus_sums_to_one():
    c = con.build_consensus(
        event([book("a", 1.5, 4.2, 7.0), book("b", 1.55, 4.0, 6.5), book("c", 1.48, 4.3, 7.2)]),
        now=NOW,
    )
    assert c.p_home + c.p_draw + c.p_away == pytest.approx(1.0)
    assert c.bookmaker_count == 3


def test_median_resists_one_bizarre_quote():
    """A single eccentric book must not drag the consensus around — the
    reason for a median rather than a mean."""
    sane = [book("a", 1.5, 4.2, 7.0), book("b", 1.52, 4.1, 6.8), book("c", 1.48, 4.3, 7.2)]
    with_outlier = sane + [book("rogue", 5.0, 4.0, 1.6)]
    base = con.build_consensus(event(sane), now=NOW)
    shifted = con.build_consensus(event(with_outlier), now=NOW)
    assert abs(shifted.p_home - base.p_home) < 0.05
    assert shifted.dispersion > base.dispersion  # but it shows up as disagreement


def test_stale_quotes_excluded():
    c = con.build_consensus(
        event([book("fresh", 1.5, 4.2, 7.0), book("ancient", 1.9, 3.6, 4.5, minutes_old=60 * 30)]),
        now=NOW,
    )
    assert c.bookmaker_count == 1
    assert c.stale_excluded == 1


def test_all_stale_returns_none():
    """No usable quote must degrade to the model, not to a stale price."""
    c = con.build_consensus(
        event([book("ancient", 1.9, 3.6, 4.5, minutes_old=60 * 30)]), now=NOW
    )
    assert c is None


def test_no_bookmakers_returns_none():
    assert con.build_consensus(event([]), now=NOW) is None


def test_dispersion_zero_for_single_book():
    c = con.build_consensus(event([book("a", 1.5, 4.2, 7.0)]), now=NOW)
    assert c.dispersion == 0.0


def test_as_odds_roundtrips():
    c = con.build_consensus(event([book("a", 1.5, 4.2, 7.0), book("b", 1.55, 4.0, 6.5)]), now=NOW)
    h, d, a = c.as_odds()
    assert 1 / h + 1 / d + 1 / a == pytest.approx(1.0)  # fair odds, no margin


def test_snapshot_rows_carry_provenance():
    rows = con.snapshot_rows(event([book("wh", 1.5, 4.2, 7.0)]), "2026-08-15T13:00:00")
    assert rows[0]["bookmaker"] == "wh"
    assert rows[0]["bookmaker_last_update"]
    assert rows[0]["fetched_at"] == "2026-08-15T13:00:00"
    assert rows[0]["devig_h"] > 0.6
