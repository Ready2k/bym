import json

import pytest

from bym.round import load_round

VALID = {
    "round_number": 14,
    "deadline_at": "2026-10-18T14:00:00",
    "fixtures": [
        {"home": "Arsenal", "away": "Everton"},
        {"home": "Chelsea", "away": "Newcastle"},
        {"home": "Fulham", "away": "Brentford"},
        {"home": "Leeds", "away": "Tottenham"},
        {"home": "Man City", "away": "Bournemouth"},
        {"home": "West Ham", "away": "Aston Villa"},
    ],
}


def write(tmp_path, payload):
    p = tmp_path / "round.json"
    p.write_text(json.dumps(payload))
    return p


def test_loads_valid_round(tmp_path):
    rnd = load_round(write(tmp_path, VALID))
    assert rnd.round_number == 14
    assert len(rnd.fixtures) == 6
    assert rnd.source == "MANUAL"
    assert str(rnd.deadline_at.tzinfo) == "Europe/London"


def test_rejects_wrong_fixture_count(tmp_path):
    bad = dict(VALID, fixtures=VALID["fixtures"][:5])
    with pytest.raises(ValueError, match="exactly 6"):
        load_round(write(tmp_path, bad))


def test_rejects_duplicate_fixture(tmp_path):
    bad = dict(VALID, fixtures=VALID["fixtures"][:5] + [VALID["fixtures"][0]])
    with pytest.raises(ValueError, match="Duplicate"):
        load_round(write(tmp_path, bad))


def test_optional_odds_parsed(tmp_path):
    payload = dict(VALID)
    payload["fixtures"] = [dict(f) for f in VALID["fixtures"]]
    payload["fixtures"][0]["odds"] = [1.5, 4.2, 7.0]
    rnd = load_round(write(tmp_path, payload))
    assert rnd.fixtures[0].odds == (1.5, 4.2, 7.0)
    assert rnd.fixtures[1].odds is None


def test_invalid_odds_rejected(tmp_path):
    payload = dict(VALID)
    payload["fixtures"] = [dict(f) for f in VALID["fixtures"]]
    payload["fixtures"][0]["odds"] = [1.5, 4.2]  # missing away odds
    with pytest.raises(ValueError, match="odds"):
        load_round(write(tmp_path, payload))
    payload["fixtures"][0]["odds"] = [0.9, 4.2, 7.0]  # impossible price
    with pytest.raises(ValueError, match="odds"):
        load_round(write(tmp_path, payload))
