"""Offline tests for the The Odds API client.

No live calls are made: the payload shape is exercised against recorded
sample responses. Live verification needs the user's API key and an
APPROVED registry entry.
"""

import json
from datetime import datetime, timezone

import pytest

from bym.sources import odds_api
from bym.sources import registry as reg

SAMPLE = [
    {
        "id": "abc123",
        "sport_key": "soccer_epl",
        "commence_time": "2026-08-15T14:00:00Z",
        "home_team": "Arsenal",
        "away_team": "Everton",
        "bookmakers": [
            {
                "key": "williamhill",
                "title": "William Hill",
                "last_update": "2026-08-15T12:00:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-08-15T12:00:00Z",
                        "outcomes": [
                            {"name": "Everton", "price": 7.0},
                            {"name": "Arsenal", "price": 1.5},
                            {"name": "Draw", "price": 4.2},
                        ],
                    }
                ],
            },
            {
                "key": "betfair",
                "title": "Betfair",
                "last_update": "2026-08-15T12:05:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-08-15T12:05:00Z",
                        "outcomes": [
                            {"name": "Arsenal", "price": 1.55},
                            {"name": "Draw", "price": 4.0},
                            {"name": "Everton", "price": 6.5},
                        ],
                    }
                ],
            },
        ],
    }
]


def test_request_cost_is_markets_times_regions():
    assert odds_api.request_cost("h2h", "uk") == 1
    assert odds_api.request_cost("h2h,totals", "uk") == 2
    assert odds_api.request_cost("h2h", "uk,eu") == 2


def test_parse_matches_outcomes_by_name_not_position():
    """Outcome order is not guaranteed — William Hill lists the away team
    first in this payload."""
    events = odds_api._parse_events(SAMPLE)
    assert len(events) == 1
    wh = next(b for b in events[0].bookmakers if b.bookmaker == "williamhill")
    assert wh.home == 1.5
    assert wh.away == 7.0
    assert wh.draw == 4.2


def test_parse_skips_incomplete_market():
    payload = json.loads(json.dumps(SAMPLE))
    payload[0]["bookmakers"][0]["markets"][0]["outcomes"] = [
        {"name": "Arsenal", "price": 1.5}
    ]
    events = odds_api._parse_events(payload)
    assert [b.bookmaker for b in events[0].bookmakers] == ["betfair"]


def test_fetch_refused_when_not_approved(tmp_path):
    """The registry gate must fire before any network call."""
    path = tmp_path / "registry.json"
    reg.save_registry(reg.load_registry(path), path)
    with pytest.raises(reg.SourceNotApprovedError):
        odds_api.fetch_odds("E0", "fake-key", registry_path=path)


def test_quota_accounting(tmp_path):
    qpath = tmp_path / "quota.json"
    now = datetime(2026, 8, 7, tzinfo=timezone.utc)
    assert odds_api.credits_used_this_month(qpath, now) == 0
    odds_api.record_usage(2, "498", qpath, now)
    odds_api.record_usage(2, "496", qpath, now)
    assert odds_api.credits_used_this_month(qpath, now) == 4
    entry = odds_api.load_quota(qpath)["2026-08"]
    assert entry["api_reported_remaining"] == 496


def test_quota_blocks_when_budget_exhausted(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    reg.save_registry(reg.load_registry(path), path)
    reg.approve("the_odds_api", path=path)
    qpath = tmp_path / "quota.json"
    odds_api.record_usage(500, None, qpath)

    def fail(*a, **k):  # network must never be reached
        raise AssertionError("request attempted despite exhausted quota")

    monkeypatch.setattr(odds_api.requests, "get", fail)
    with pytest.raises(odds_api.QuotaExceededError):
        odds_api.fetch_odds(
            "E0", "fake-key", quota_path=qpath, registry_path=path
        )


def test_unknown_division_rejected(tmp_path):
    """EC is in the match corpus but the provider publishes no National
    League feed, so it must be refused outright rather than requested."""
    path = tmp_path / "registry.json"
    reg.save_registry(reg.load_registry(path), path)
    reg.approve("the_odds_api", path=path)
    with pytest.raises(ValueError, match="No sport key"):
        odds_api.fetch_odds("EC", "fake-key", registry_path=path)
    assert "EC" in odds_api.UNPRICEABLE_DIVISIONS
    assert not odds_api.UNPRICEABLE_DIVISIONS & set(odds_api.SPORT_KEYS)


def test_cup_competitions_are_mapped_and_kept_out_of_the_default_pass():
    """A cup tie appears in the cup feed, not either club's league feed."""
    assert odds_api.SPORT_KEYS["FA_CUP"] == "soccer_fa_cup"
    assert odds_api.SPORT_KEYS["EFL_CUP"] == "soccer_england_efl_cup"
    # Cups cost an extra billed request each, so they are a second pass.
    assert set(odds_api.CUP_DIVISIONS) == {"FA_CUP", "EFL_CUP"}
    assert all(division in odds_api.SPORT_KEYS for division in odds_api.CUP_DIVISIONS)


# ---------------------------------------------------------------------------
# §6 market breadth: totals (over/under) — opt-in, never fetched by default


def test_default_markets_is_h2h_only_no_cost_change():
    """Nothing about §6 market breadth changes the default budget."""
    assert odds_api.DEFAULT_MARKETS == "h2h"
    assert odds_api.request_cost() == 1


def test_totals_market_is_parsed_when_present():
    payload = json.loads(json.dumps(SAMPLE))
    payload[0]["bookmakers"][0]["markets"].append({
        "key": "totals",
        "last_update": "2026-08-15T12:00:00Z",
        "outcomes": [
            {"name": "Over", "price": 1.9, "point": 2.5},
            {"name": "Under", "price": 1.95, "point": 2.5},
        ],
    })
    events = odds_api._parse_events(payload)
    (totals,) = events[0].totals
    assert totals.bookmaker == "williamhill"
    assert totals.line == 2.5
    assert totals.over == 1.9
    assert totals.under == 1.95


def test_totals_market_absent_by_default():
    """The sample payload only ever requested h2h — no totals market
    exists in it, and none should be invented."""
    events = odds_api._parse_events(SAMPLE)
    assert events[0].totals == []


def test_totals_with_multiple_lines_keeps_each_complete_one_only():
    payload = json.loads(json.dumps(SAMPLE))
    payload[0]["bookmakers"][0]["markets"].append({
        "key": "totals",
        "last_update": "2026-08-15T12:00:00Z",
        "outcomes": [
            {"name": "Over", "price": 1.9, "point": 2.5},
            {"name": "Under", "price": 1.95, "point": 2.5},
            {"name": "Over", "price": 2.6, "point": 3.5},
            # Under 3.5 missing — this line must be skipped, not guessed at.
        ],
    })
    events = odds_api._parse_events(payload)
    lines = [t.line for t in events[0].totals]
    assert lines == [2.5]
