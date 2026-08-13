"""Last Man Standing on the dashboard: the plan reaches the UI, every
entrant is served off one pass of the model, and one page load makes one
request to a ten-per-minute feed."""

import json
from dataclasses import dataclass

import pytest

from bym import lms
from bym.web import WebConfig, lms_payload

TEAMS = ("Strong", "Middling A", "Middling B", "Weak")

# Expected goals by club, strongest first. A stub rather than a fitted
# model: these tests are about what the payload contains and how many
# times the model and the feed are called, none of which should depend on
# a fit, and a shared fit would couple them to whatever else in the suite
# has already drawn from the synthetic corpus's RNG.
STRENGTH = {"Strong": 2.4, "Middling A": 1.3, "Middling B": 1.2, "Weak": 0.6}


@dataclass(frozen=True)
class StubParams:
    rho: float = 0.0

    def lambdas(self, home, away, cfg=None):
        return STRENGTH.get(home, 1.0) * 1.2, STRENGTH.get(away, 1.0)


@pytest.fixture
def router():
    return lambda home, away: StubParams()


def config_with(tmp_path, gameweek: dict, states: dict | None = None) -> WebConfig:
    (tmp_path / "lms_gameweek.json").write_text(json.dumps(gameweek))
    if states is not None:
        (tmp_path / "lms_state.json").write_text(json.dumps(states))
    return WebConfig(
        lms_gameweek_path=tmp_path / "lms_gameweek.json",
        lms_state_path=tmp_path / "lms_state.json",
    )


GAMEWEEK = {
    "gameweek": 3,
    "fixtures": [
        {"home": "Strong", "away": "Weak"},
        {"home": "Middling A", "away": "Middling B"},
    ],
}


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    """Default to the offline path — these tests are about the payload, not
    about football-data.org. The one test that needs a feed patches it."""
    monkeypatch.setattr("bym.secrets.get_key", lambda name: None)


def test_payload_carries_a_plan_per_entrant(tmp_path, router):
    config = config_with(
        tmp_path, GAMEWEEK,
        {"James": {"entrant": "James", "alive": True,
                   "used_clubs": ["Strong"], "history": []}},
    )
    payload = lms_payload(config, router=router, known_teams=set(TEAMS))
    assert payload["gameweek"]["number"] == 3
    assert "Strong" not in {p["team"] for p in payload["plans"]["James"]["picks"]}
    assert "Strong" in {p["team"] for p in payload["plans"]["__all__"]["picks"]}


def test_plan_reports_what_the_weekly_favourite_would_have_given(tmp_path, router):
    payload = lms_payload(
        config_with(tmp_path, GAMEWEEK), router=router, known_teams=set(TEAMS)
    )
    plan = payload["plans"]["__all__"]
    assert plan["greedy_team"] == "Strong"
    assert plan["survival_p"] >= plan["greedy_survival_p"]
    assert plan["survival_gain"] == pytest.approx(
        plan["survival_p"] - plan["greedy_survival_p"]
    )


def test_eliminated_entrants_get_no_recommendation(tmp_path, router):
    config = config_with(
        tmp_path, GAMEWEEK,
        {"Out": {"entrant": "Out", "alive": False, "used_clubs": ["Weak"],
                 "history": [{"gameweek": 1, "team": "Weak", "opponent": "Strong",
                              "is_home": False, "result": "LOSS", "source": "AUTO"}]}},
    )
    payload = lms_payload(config, router=router, known_teams=set(TEAMS))
    assert "Out" not in payload["plans"]
    assert "Out" not in payload["recommendations"]
    assert payload["entrants"]["Out"]["alive"] is False


def test_the_model_runs_once_per_gameweek_not_once_per_entrant(
    tmp_path, router, monkeypatch
):
    """Entrants differ only in which clubs they have used, so every extra
    entrant recomputing the same score matrices is pure waste — and a
    dashboard that refits per entrant gets slower as the competition
    grows."""
    calls = []
    real = lms.fixture_outlooks
    monkeypatch.setattr(
        lms, "fixture_outlooks",
        lambda *a, **kw: (calls.append(1), real(*a, **kw))[1],
    )
    entrants = {
        name: {"entrant": name, "alive": True, "used_clubs": [], "history": []}
        for name in ("A", "B", "C", "D")
    }
    payload = lms_payload(
        config_with(tmp_path, GAMEWEEK, entrants),
        router=router, known_teams=set(TEAMS),
    )
    assert len(payload["plans"]) == 5  # four entrants plus __all__
    assert len(calls) == 1  # one gameweek in the cached fallback


def test_no_gameweek_still_returns_entrant_state(tmp_path, router):
    config = WebConfig(
        lms_gameweek_path=tmp_path / "missing.json",
        lms_state_path=tmp_path / "lms_state.json",
    )
    (tmp_path / "lms_state.json").write_text(json.dumps(
        {"James": {"entrant": "James", "alive": True, "used_clubs": [], "history": []}}
    ))
    payload = lms_payload(config, router=router, known_teams=set(TEAMS))
    assert payload["gameweek"] is None
    assert payload["plans"] == {}
    assert payload["entrants"]["James"]["alive"] is True


def test_one_page_load_makes_one_premier_league_request(tmp_path, router, monkeypatch):
    """The fixture list and the results come from the same endpoint for the
    same season. Fetching it twice per page load spends a free tier that
    allows ten requests a minute across the whole system."""
    from bym.sources import football_data_org as fdo

    payload_json = {"matches": [
        {"id": 1, "status": "SCHEDULED", "matchday": 3,
         "utcDate": "2026-09-12T14:00:00Z",
         "homeTeam": {"name": "Strong"}, "awayTeam": {"name": "Weak"},
         "score": {"fullTime": {"home": None, "away": None}}},
        {"id": 2, "status": "SCHEDULED", "matchday": 3,
         "utcDate": "2026-09-12T16:30:00Z",
         "homeTeam": {"name": "Middling A"}, "awayTeam": {"name": "Middling B"},
         "score": {"fullTime": {"home": None, "away": None}}},
    ]}
    fetched = []

    def fake_fetch(division, season, api_key, **kw):
        fetched.append(division)
        return fdo.parse_matches(payload_json) if division == "E0" else []

    monkeypatch.setattr("bym.secrets.get_key", lambda name: "key")
    monkeypatch.setattr(fdo, "fetch_matches", fake_fetch)

    config = config_with(tmp_path, GAMEWEEK)
    payload = lms_payload(config, router=router, known_teams=set(TEAMS))
    assert payload["fixtures_source"]["mode"] == "auto"
    assert fetched.count("E0") == 1


def test_settlement_still_runs_off_the_shared_fetch(tmp_path, router, monkeypatch):
    """Reusing the fixture fetch must not quietly disable auto-settlement —
    the whole point of it is that no entrant reports their own survival."""
    from bym.sources import football_data_org as fdo

    payload_json = {"matches": [
        {"id": 1, "status": "FINISHED", "matchday": 3,
         "utcDate": "2026-09-12T14:00:00Z",
         "homeTeam": {"name": "Strong"}, "awayTeam": {"name": "Weak"},
         "score": {"fullTime": {"home": 2, "away": 0}}},
        {"id": 2, "status": "SCHEDULED", "matchday": 4,
         "utcDate": "2026-09-19T14:00:00Z",
         "homeTeam": {"name": "Middling A"}, "awayTeam": {"name": "Middling B"},
         "score": {"fullTime": {"home": None, "away": None}}},
    ]}
    monkeypatch.setattr("bym.secrets.get_key", lambda name: "key")
    monkeypatch.setattr(
        fdo, "fetch_matches",
        lambda division, season, api_key, **kw: (
            fdo.parse_matches(payload_json) if division == "E0" else []
        ),
    )
    config = config_with(
        tmp_path, GAMEWEEK,
        {"James": {"entrant": "James", "alive": True, "used_clubs": ["Strong"],
                   "history": [{"gameweek": 3, "team": "Strong", "opponent": "Weak",
                                "is_home": True, "result": "PENDING",
                                "source": None}]}},
    )
    payload = lms_payload(config, router=router, known_teams=set(TEAMS))
    assert payload["auto_settle"]["settled"] == [["James", 3, "WIN"]] or \
        payload["auto_settle"]["settled"] == [("James", 3, "WIN")]
    saved = json.loads((tmp_path / "lms_state.json").read_text())
    assert saved["James"]["history"][0]["result"] == "WIN"
    assert saved["James"]["history"][0]["source"] == "AUTO"
    # The entrant block handed to the UI reflects the settlement that just
    # happened, not the state as it was read at the top of the request.
    assert payload["entrants"]["James"]["history"][0]["result"] == "WIN"
