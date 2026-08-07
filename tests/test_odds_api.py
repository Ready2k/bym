"""Offline tests for the The Odds API client.

No live calls are made: the payload shape is exercised against recorded
sample responses. Live verification needs the user's API key and an
APPROVED registry entry.
"""

import json
from datetime import datetime, timezone

import pytest

from super6.sources import odds_api
from super6.sources import registry as reg

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
    events = odds_api._parse_h2h(SAMPLE)
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
    events = odds_api._parse_h2h(payload)
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
    path = tmp_path / "registry.json"
    reg.save_registry(reg.load_registry(path), path)
    reg.approve("the_odds_api", path=path)
    with pytest.raises(ValueError, match="No sport key"):
        odds_api.fetch_odds("E2", "fake-key", registry_path=path)
