import pytest

from super6.config import DEFAULT_MODEL
from super6.model import dixon_coles
from super6.lms import (
    Gameweek,
    GameweekFixture,
    LMSState,
    auto_settle,
    format_recommendations,
    format_status,
    gameweek_from_dict,
    record_pick,
    record_result,
    recommend_gameweek,
    result_for_pick,
)

from test_dixon_coles import synthetic_matches


def make_gameweek():
    return Gameweek(
        number=3,
        fixtures=(
            GameweekFixture("Strong", "Weak"),
            GameweekFixture("Middling A", "Middling B"),
        ),
    )


@pytest.fixture(scope="module")
def params():
    return dixon_coles.fit(synthetic_matches(), xi=0.0)


def test_ranked_by_win_probability_descending(params):
    candidates = recommend_gameweek(params, make_gameweek())
    probs = [c.p_win for c in candidates]
    assert probs == sorted(probs, reverse=True)


def test_all_four_clubs_eligible_when_none_used(params):
    candidates = recommend_gameweek(params, make_gameweek())
    assert {c.team for c in candidates} == {"Strong", "Weak", "Middling A", "Middling B"}


def test_strong_favourite_is_top_pick(params):
    candidates = recommend_gameweek(params, make_gameweek())
    assert candidates[0].team == "Strong"


def test_used_clubs_are_excluded(params):
    candidates = recommend_gameweek(params, make_gameweek(), used_clubs={"Strong"})
    assert "Strong" not in {c.team for c in candidates}
    assert len(candidates) == 3


def test_used_club_exclusion_is_case_insensitive(params):
    candidates = recommend_gameweek(params, make_gameweek(), used_clubs={"strong"})
    assert "Strong" not in {c.team for c in candidates}


def test_no_candidates_when_every_club_used(params):
    candidates = recommend_gameweek(
        params, make_gameweek(),
        used_clubs={"Strong", "Weak", "Middling A", "Middling B"},
    )
    assert candidates == []


def test_win_draw_loss_sum_to_one(params):
    for c in recommend_gameweek(params, make_gameweek()):
        assert c.p_win + c.p_draw + c.p_loss == pytest.approx(1.0)


def test_report_handles_empty_candidates(params):
    report = format_recommendations(
        make_gameweek(), [],
        used_clubs={"Strong", "Weak", "Middling A", "Middling B"},
    )
    assert "No eligible clubs" in report


def test_report_contains_recommended_pick(params):
    candidates = recommend_gameweek(params, make_gameweek())
    report = format_recommendations(make_gameweek(), candidates)
    assert "Recommended pick: Strong" in report


def test_gameweek_from_dict_round_trip():
    raw = {
        "gameweek": 5,
        "fixtures": [
            {"home": "Arsenal", "away": "Everton"},
            {"home": "Fulham", "away": "Brighton", "odds": [2.1, 3.4, 3.2]},
        ],
    }
    gw = gameweek_from_dict(raw)
    assert gw.number == 5
    assert gw.fixtures[1].odds == (2.1, 3.4, 3.2)


def test_gameweek_rejects_a_team_playing_twice():
    raw = {
        "gameweek": 5,
        "fixtures": [
            {"home": "Arsenal", "away": "Everton"},
            {"home": "Everton", "away": "Fulham"},
        ],
    }
    with pytest.raises(ValueError):
        gameweek_from_dict(raw)


def test_gameweek_rejects_bad_odds():
    raw = {
        "gameweek": 5,
        "fixtures": [{"home": "Arsenal", "away": "Everton", "odds": [1.0, 3.4, 3.2]}],
    }
    with pytest.raises(ValueError):
        gameweek_from_dict(raw)


# ---------------------------------------------------------------------------
# Survival state
# ---------------------------------------------------------------------------


def test_record_pick_appends_to_used_clubs():
    state = LMSState(entrant="James")
    record_pick(state, 1, "Man City", "Bournemouth", True)
    assert state.used_clubs == ["Man City"]
    assert state.history[0]["result"] == "PENDING"


def test_record_pick_rejects_reused_club():
    state = LMSState(entrant="James")
    record_pick(state, 1, "Man City", "Bournemouth", True)
    with pytest.raises(ValueError):
        record_pick(state, 5, "Man City", "Fulham", True)


def test_record_pick_rejects_duplicate_gameweek():
    state = LMSState(entrant="James")
    record_pick(state, 1, "Man City", "Bournemouth", True)
    with pytest.raises(ValueError):
        record_pick(state, 1, "Liverpool", "Newcastle", False)


def test_record_pick_rejects_after_elimination():
    state = LMSState(entrant="James")
    record_pick(state, 1, "Man City", "Bournemouth", True)
    record_result(state, 1, "loss")
    with pytest.raises(ValueError):
        record_pick(state, 2, "Liverpool", "Newcastle", False)


def test_win_keeps_entrant_alive():
    state = LMSState(entrant="James")
    record_pick(state, 1, "Man City", "Bournemouth", True)
    record_result(state, 1, "win")
    assert state.alive is True
    assert state.history[0]["result"] == "WIN"


@pytest.mark.parametrize("result", ["draw", "loss"])
def test_draw_and_loss_both_eliminate(result):
    state = LMSState(entrant="James")
    record_pick(state, 1, "Man City", "Bournemouth", True)
    record_result(state, 1, result)
    assert state.alive is False


def test_record_result_rejects_unknown_gameweek():
    state = LMSState(entrant="James")
    with pytest.raises(ValueError):
        record_result(state, 1, "WIN")


def test_record_result_rejects_invalid_result():
    state = LMSState(entrant="James")
    record_pick(state, 1, "Man City", "Bournemouth", True)
    with pytest.raises(ValueError):
        record_result(state, 1, "VOID")


def test_format_status_shows_eliminated():
    state = LMSState(entrant="James")
    record_pick(state, 1, "Man City", "Bournemouth", True)
    record_result(state, 1, "DRAW")
    report = format_status(state)
    assert "ELIMINATED" in report
    assert "Man City" in report


def test_record_result_defaults_to_manual_source():
    state = LMSState(entrant="James")
    record_pick(state, 1, "Man City", "Bournemouth", True)
    record_result(state, 1, "WIN")
    assert state.history[0]["source"] == "MANUAL"


# ---------------------------------------------------------------------------
# Automatic settlement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "is_home,home_goals,away_goals,expected",
    [
        (True, 2, 0, "WIN"),
        (True, 0, 2, "LOSS"),
        (True, 1, 1, "DRAW"),
        (False, 2, 0, "LOSS"),
        (False, 0, 2, "WIN"),
        (False, 1, 1, "DRAW"),
    ],
)
def test_result_for_pick(is_home, home_goals, away_goals, expected):
    assert result_for_pick(is_home, home_goals, away_goals) == expected


def test_auto_settle_win_keeps_entrant_alive():
    gw = make_gameweek()
    state = LMSState(entrant="James")
    record_pick(state, gw.number, "Strong", "Weak", True)
    settled = auto_settle({"James": state}, gw, {0: (2, 0)})
    assert settled == [("James", gw.number, "WIN")]
    assert state.alive is True
    assert state.history[0]["result"] == "WIN"
    assert state.history[0]["source"] == "AUTO"


def test_auto_settle_loss_eliminates():
    gw = make_gameweek()
    state = LMSState(entrant="James")
    record_pick(state, gw.number, "Strong", "Weak", True)
    settled = auto_settle({"James": state}, gw, {0: (0, 2)})
    assert settled == [("James", gw.number, "LOSS")]
    assert state.alive is False


def test_auto_settle_leaves_unresulted_fixture_pending():
    gw = make_gameweek()
    state = LMSState(entrant="James")
    record_pick(state, gw.number, "Strong", "Weak", True)
    settled = auto_settle({"James": state}, gw, {})
    assert settled == []
    assert state.history[0]["result"] == "PENDING"


def test_auto_settle_leaves_void_fixture_pending():
    gw = make_gameweek()
    state = LMSState(entrant="James")
    record_pick(state, gw.number, "Strong", "Weak", True)
    settled = auto_settle({"James": state}, gw, {0: None})
    assert settled == []
    assert state.history[0]["result"] == "PENDING"


def test_auto_settle_skips_already_settled_picks():
    gw = make_gameweek()
    state = LMSState(entrant="James")
    record_pick(state, gw.number, "Strong", "Weak", True)
    record_result(state, gw.number, "WIN", source="MANUAL")
    settled = auto_settle({"James": state}, gw, {0: (0, 2)})
    assert settled == []
    assert state.history[0]["result"] == "WIN"
    assert state.history[0]["source"] == "MANUAL"


def test_auto_settle_matches_pick_by_fixture_not_history_order():
    gw = make_gameweek()
    state = LMSState(entrant="James")
    record_pick(state, gw.number, "Middling B", "Middling A", False)
    settled = auto_settle({"James": state}, gw, {1: (1, 3)})
    assert settled == [("James", gw.number, "WIN")]
