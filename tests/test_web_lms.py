"""Last Man Standing on the dashboard: the plan reaches the UI, every
entrant is served off one pass of the model, and one page load makes one
request to a ten-per-minute feed."""

import json
from dataclasses import dataclass

import pytest

from bym import lms
from bym.web import WebConfig, lms_payload, lms_replay

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


def test_lineage_payload_keeps_fixture_context_for_graph_focus(tmp_path, router):
    """A plotted point must be able to name the fixture it represents.

    Reconstructing an opponent in the browser from a club name is ambiguous
    once the model horizon has several gameweeks, so it belongs in the
    server-produced lineage with the probability it explains.
    """
    payload = lms_payload(
        config_with(
            tmp_path, GAMEWEEK,
            {"James": {"entrant": "James", "alive": True,
                       "used_clubs": [], "history": []}},
        ),
        router=router,
        known_teams=set(TEAMS),
    )
    step = payload["lineages"]["James"]["steps"][0]
    assert step["team"] in TEAMS
    assert step["opponent"] in TEAMS
    assert isinstance(step["is_home"], bool)


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


def test_replay_payload_is_transient_and_passes_the_used_clubs(tmp_path, monkeypatch):
    (tmp_path / "2023_E0.csv").write_text("placeholder")
    (tmp_path / "2024_E0.csv").write_text("placeholder")
    config = WebConfig(data_path=tmp_path, seasons=(2023, 2024))
    seen = {}

    monkeypatch.setattr("bym.web.load_matches", lambda *args, **kwargs: "matches")

    class Replay:
        def to_dict(self):
            return {"slate": 2, "candidates": []}

    def fake_replay(matches, season, slate, **kwargs):
        seen.update(matches=matches, season=season, slate=slate, **kwargs)
        return Replay()

    monkeypatch.setattr("bym.backtest.lms.replay_slate", fake_replay)

    result = lms_replay(
        {"season": 2024, "slate": 2, "used_clubs": ["Arsenal"]}, config
    )

    assert result == {"slate": 2, "candidates": []}
    assert seen["matches"] == "matches"
    assert seen["used_clubs"] == ["Arsenal"]


def test_replay_rejects_unavailable_season(tmp_path):
    with pytest.raises(ValueError, match="not available"):
        lms_replay({"season": 2024, "slate": 1}, WebConfig(data_path=tmp_path))


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


def test_an_entrant_who_booked_this_gameweek_is_planned_from_the_next(
    tmp_path, router
):
    """The offline path serves a single gameweek, so an entrant who has
    already booked it has nothing left to plan — and must be told that
    rather than handed a pick for a deadline they have passed."""
    config = config_with(
        tmp_path, GAMEWEEK,
        {"James": {"entrant": "James", "alive": True, "used_clubs": ["Strong"],
                   "history": [{"gameweek": 3, "team": "Strong",
                                "opponent": "Weak", "is_home": True,
                                "result": "PENDING", "source": None}]},
         "Kate": {"entrant": "Kate", "alive": True, "used_clubs": [],
                  "history": []}},
    )
    payload = lms_payload(config, router=router, known_teams=set(TEAMS))

    # James booked GW3, the only gameweek on offer: no picks, no candidates.
    assert payload["plans"]["James"]["picks"] == []
    assert payload["recommendations"]["James"] == []

    # Kate has not, so she is still planned normally off the same matrices.
    assert payload["plans"]["Kate"]["picks"]
    assert payload["recommendations"]["Kate"]


def test_switching_a_pick_through_the_endpoint_frees_the_old_club(tmp_path, router):
    from bym.web import save_lms_pick

    config = config_with(
        tmp_path, GAMEWEEK,
        {"James": {"entrant": "James", "alive": True, "used_clubs": ["Strong"],
                   "history": [{"gameweek": 3, "team": "Strong", "opponent": "Weak",
                                "is_home": True, "result": "PENDING", "source": None}]}},
    )
    save_lms_pick(
        {"entrant": "James", "gameweek": 3, "team": "Middling A",
         "opponent": "Middling B", "is_home": True},
        config,
    )
    state = lms.load_states(config.lms_state_path)["James"]
    assert state.used_clubs == ["Middling A"], "the old club must be released"
    assert [h["team"] for h in state.history] == ["Middling A"]


def test_live_pick_keeps_its_probability_and_suggestion_snapshot(
    tmp_path, router, monkeypatch
):
    """The live route tree must show the decision-time plan, not a refit."""
    config = config_with(tmp_path, GAMEWEEK)
    week = lms.gameweek_from_dict(GAMEWEEK)
    monkeypatch.setattr(
        "bym.web._model_context", lambda _config: (None, router, set(TEAMS))
    )
    monkeypatch.setattr(
        "bym.web._lms_current_gameweek",
        lambda _config, _known: ([week], {"mode": "manual", "note": None}, None),
    )

    from bym.web import save_lms_pick

    save_lms_pick(
        {"entrant": "James", "gameweek": 3, "team": "Strong",
         "opponent": "Weak", "is_home": True},
        config,
    )

    entry = lms.load_states(config.lms_state_path)["James"].history[0]
    assert entry["picked_p_win"] > 0.5
    assert entry["suggested_team"] == "Strong"
    assert entry["suggested_p_win"] == pytest.approx(entry["picked_p_win"])


def test_previews_cover_every_eligible_club_and_price_the_whole_horizon(
    tmp_path, router
):
    """"What do Hull cost me" is a question about the horizon, so the
    preview has to carry a survival figure for the path, not just this
    week's win probability."""
    config = config_with(
        tmp_path, GAMEWEEK,
        {"James": {"entrant": "James", "alive": True, "used_clubs": [], "history": []}},
    )
    payload = lms_payload(config, router=router, known_teams=set(TEAMS))
    previews = payload["previews"]["James"]
    offered = {c["team"] for c in payload["recommendations"]["James"]}
    assert set(previews) == offered
    for team, preview in previews.items():
        assert preview["survival_yours"] is not None, team
        assert preview["steps"][0]["team"] == team


def test_a_preview_never_writes_anything(tmp_path, router):
    """A preview that left a club in used_clubs would be data loss wearing
    a feature's clothes."""
    config = config_with(
        tmp_path, GAMEWEEK,
        {"James": {"entrant": "James", "alive": True, "used_clubs": [], "history": []}},
    )
    before = config.lms_state_path.read_text()
    lms_payload(config, router=router, known_teams=set(TEAMS))
    assert config.lms_state_path.read_text() == before


def test_the_gameweek_carries_the_kickoff_that_closes_it(tmp_path, router):
    payload = lms_payload(
        config_with(tmp_path, {**GAMEWEEK, "deadline_at": "2026-08-15T14:00:00+00:00"}),
        router=router, known_teams=set(TEAMS),
    )
    assert payload["gameweek"]["deadline_at"].startswith("2026-08-15T14:00")


def test_a_changeable_week_offers_that_week_s_clubs_not_next_week_s(
    tmp_path, router
):
    """The bug this exists to stop: with GW3 booked but not kicked off,
    the entrant is still deciding GW3 — offering GW4's clubs beside a
    Switch button that posts GW3 records a club against a gameweek it is
    not playing in."""
    config = config_with(
        tmp_path,
        {**GAMEWEEK, "deadline_at": "2099-01-01T00:00:00+00:00"},
        {"James": {"entrant": "James", "alive": True, "used_clubs": ["Strong"],
                   "history": [{"gameweek": 3, "team": "Strong", "opponent": "Weak",
                                "is_home": True, "result": "PENDING", "source": None}]}},
    )
    payload = lms_payload(config, router=router, known_teams=set(TEAMS))

    assert payload["decision_weeks"]["James"] == 3, "GW3 is still open to change"
    offered = {c["team"] for c in payload["switch_candidates"]["James"]}
    playing = {t for f in GAMEWEEK["fixtures"] for t in (f["home"], f["away"])}
    assert offered <= playing, "every club offered must be playing that gameweek"
    assert "Strong" in offered, "the club currently booked cannot block its own week"


def test_a_kicked_off_week_moves_the_decision_on(tmp_path, router):
    config = config_with(
        tmp_path,
        {**GAMEWEEK, "deadline_at": "2000-01-01T00:00:00+00:00"},
        {"James": {"entrant": "James", "alive": True, "used_clubs": ["Strong"],
                   "history": [{"gameweek": 3, "team": "Strong", "opponent": "Weak",
                                "is_home": True, "result": "PENDING", "source": None}]}},
    )
    payload = lms_payload(config, router=router, known_teams=set(TEAMS))
    assert payload["decision_weeks"].get("James") is None, (
        "the round has started and it is the only gameweek on offer — "
        "there is nothing left to decide"
    )
