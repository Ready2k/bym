import json

import pytest

from bym.config import DEFAULT_LMS, DEFAULT_MODEL, LMSConfig
from bym.model import dixon_coles
from bym.lms import (
    FixtureOutlook,
    Gameweek,
    GameweekFixture,
    LMSState,
    PickCandidate,
    auto_settle,
    field_equity,
    format_field_equity,
    format_plan,
    format_recommendations,
    format_status,
    gameweek_from_dict,
    load_states,
    plan_horizon,
    record_pick,
    record_result,
    recommend_gameweek,
    result_for_pick,
    save_states,
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


def test_report_names_the_strongest_club_and_defers_to_the_plan(params):
    candidates = recommend_gameweek(params, make_gameweek())
    report = format_recommendations(make_gameweek(), candidates)
    assert "Strongest club this gameweek: Strong" in report
    # The single-gameweek ranking must not present itself as the decision —
    # spending the best club now is exactly the mistake the format punishes.
    assert "lms-plan" in report


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


# ---------------------------------------------------------------------------
# Horizon planning — the club is a one-use resource, not a weekly choice
# ---------------------------------------------------------------------------


def outlook(home, away, p_home, p_away):
    return FixtureOutlook(
        GameweekFixture(home, away), p_home, 1 - p_home - p_away, p_away
    )


def horizon():
    """Two gameweeks where the greedy pick is provably wrong.

    Ace is the favourite in both, but GW1 offers a near-equal alternative
    (Deputy, 0.60 against 0.65) while GW2 offers nothing (0.40). Spending
    Ace in GW1 buys 5pp now and gives up 45pp later.

    Written as probabilities rather than fitted from data on purpose: the
    claim under test is about the planning, and a test that first has to
    win an argument about which synthetic club is stronger is testing the
    wrong thing.
    """
    return (
        [Gameweek(1, ()), Gameweek(2, ())],
        [
            [outlook("Ace", "Foil", 0.65, 0.15), outlook("Deputy", "Mug", 0.60, 0.20)],
            [outlook("Ace", "Mug", 0.85, 0.05), outlook("Deputy", "Foil", 0.40, 0.35)],
        ],
    )


def plan(used=(), lms_cfg=DEFAULT_LMS, weeks=None):
    gameweeks, outlooks = horizon()
    if weeks is not None:
        gameweeks, outlooks = gameweeks[:weeks], outlooks[:weeks]
    return plan_horizon(
        None, gameweeks, used, lms_cfg=lms_cfg, outlooks=outlooks
    )


def test_plan_saves_the_best_club_for_the_gameweek_that_needs_it():
    picks = [p.candidate.team for p in plan().picks]
    assert picks == ["Deputy", "Ace"]


def test_plan_beats_greedy_on_joint_survival():
    p = plan()
    assert [g.candidate.team for g in p.greedy_picks] == ["Ace", "Deputy"]
    assert p.survival_p == pytest.approx(0.60 * 0.85)
    assert p.greedy_survival_p == pytest.approx(0.65 * 0.40)
    assert p.survival_gain > 0.24


def test_plan_survival_is_the_product_of_its_picks():
    p = plan()
    expected = 1.0
    for pick in p.picks:
        expected *= pick.candidate.p_win
    assert p.survival_p == pytest.approx(expected)


def test_plan_never_reuses_a_club():
    teams = [p.candidate.team for p in plan().picks]
    assert len(teams) == len(set(teams))


def test_plan_excludes_already_used_clubs():
    assert "Ace" not in {p.candidate.team for p in plan(used={"ace"}).picks}


def test_single_gameweek_plan_is_the_favourite():
    p = plan(weeks=1)
    assert p.this_week.candidate.team == "Ace"
    assert p.survival_gain == pytest.approx(0.0)


def test_plan_reports_gameweeks_with_no_legal_pick():
    p = plan(used={"Ace", "Deputy", "Foil", "Mug"})
    assert p.picks == ()
    assert p.unplannable == (1, 2)
    assert "No legal pick available" in format_plan(p)


def test_plan_handles_an_empty_horizon(params):
    p = plan_horizon(params, [])
    assert p.picks == ()
    assert p.survival_p == 0.0


def test_future_probabilities_are_shrunk_but_reported_unshrunk():
    now, later = plan().picks
    assert now.planning_p_win == pytest.approx(now.candidate.p_win)
    # A favourite a gameweek out plans as slightly less of a lock than the
    # model says, because the model is describing today's squads.
    assert later.planning_p_win < later.candidate.p_win


def test_shrink_can_be_disabled():
    for p in plan(lms_cfg=LMSConfig(future_shrink_per_week=0.0)).picks:
        assert p.planning_p_win == pytest.approx(p.candidate.p_win)


def test_shrink_does_not_change_the_reported_survival_probability():
    """The plan is chosen cautiously and scored honestly — a shrink that
    leaked into the headline number would understate the plan it picked."""
    assert plan().survival_p == pytest.approx(0.60 * 0.85)


def test_plan_report_shows_what_greedy_would_have_cost():
    report = format_plan(plan())
    assert "Pick now (GW1)" in report
    assert "Taking the favourite (Ace" in report
    assert "pp for planning ahead" in report


def test_plan_is_optimal_against_every_legal_alternative(params):
    """Brute force over every legal plan, on the real fitted model — this
    is what distinguishes planning from a cleverer-looking heuristic."""
    import itertools

    weeks = [
        Gameweek(1, (GameweekFixture("Strong", "Middling A"),
                     GameweekFixture("Middling B", "Weak"))),
        Gameweek(2, (GameweekFixture("Strong", "Weak"),
                     GameweekFixture("Middling A", "Middling B"))),
    ]
    p = plan_horizon(params, weeks)
    by_week = [
        {c.team: c.p_win for c in recommend_gameweek(params, gw)} for gw in weeks
    ]
    best = 0.0
    for combo in itertools.product(*[list(w) for w in by_week]):
        if len(set(combo)) != len(combo):  # one club once
            continue
        product = 1.0
        for week, team in zip(by_week, combo):
            product *= week[team]
        best = max(best, product)
    assert p.survival_p == pytest.approx(best)
    assert p.survival_p >= p.greedy_survival_p


# ---------------------------------------------------------------------------
# Confidence banding
# ---------------------------------------------------------------------------


def test_confidence_uses_lms_thresholds_not_super6s(params):
    """A 0.60 win probability is HIGH under Super 6's result banding (0.58)
    and must not be here — that is the survivable-looking pick that ends
    entrants' competitions."""
    assert DEFAULT_MODEL.conf_high_result_p < 0.60 < DEFAULT_LMS.conf_high_p_win
    gw = Gameweek(1, (GameweekFixture("Strong", "Middling A"),))
    (top, _) = recommend_gameweek(params, gw)[:2]
    assert (top.confidence == "HIGH") == (top.p_win >= DEFAULT_LMS.conf_high_p_win)


def test_ranking_ties_break_deterministically():
    from bym.lms import FixtureOutlook, rank_candidates

    outlooks = [
        FixtureOutlook(GameweekFixture("Zebra", "Yak"), 0.5, 0.25, 0.25),
        FixtureOutlook(GameweekFixture("Ant", "Bee"), 0.5, 0.25, 0.25),
    ]
    assert [c.team for c in rank_candidates(outlooks)][:2] == ["Ant", "Zebra"]


# ---------------------------------------------------------------------------
# Pot equity — surviving alongside the whole field wins nothing
# ---------------------------------------------------------------------------


def candidate(team, opponent, p_win):
    return PickCandidate(team, opponent, True, p_win, 0.2, 1 - 0.2 - p_win, "MEDIUM")


def test_a_crowded_favourite_loses_to_a_differentiated_pick():
    cands = [candidate("Crowd", "A", 0.75), candidate("Lonely", "B", 0.70)]
    equities = field_equity(cands, {"Crowd": 0.90, "Lonely": 0.05})
    assert equities[0].candidate.team == "Lonely"
    # ...and the raw ranking, which pot equity is correcting, says otherwise.
    assert max(cands, key=lambda c: c.p_win).team == "Crowd"


def test_an_uncontested_favourite_still_wins_on_equity():
    cands = [candidate("Best", "A", 0.75), candidate("Other", "B", 0.60)]
    equities = field_equity(cands, {"Best": 0.10, "Other": 0.10})
    assert equities[0].candidate.team == "Best"


def test_rivals_on_the_opponent_are_eliminated_not_counted_as_survivors():
    """If your club wins, everyone who picked the club it beat is out —
    the one correlation that makes independence plainly wrong."""
    cands = [candidate("Home", "Away", 0.60), candidate("Elsewhere", "X", 0.60)]
    equities = {e.candidate.team: e for e in field_equity(
        cands, {"Home": 0.10, "Away": 0.50, "Elsewhere": 0.10}
    )}
    # Half the field is on Away, and beating them removes that half outright.
    assert equities["Home"].equity > equities["Elsewhere"].equity


def test_popularity_accepts_percentages():
    cands = [candidate("Crowd", "A", 0.75), candidate("Lonely", "B", 0.70)]
    as_pct = field_equity(cands, {"Crowd": 90, "Lonely": 5})
    as_frac = field_equity(cands, {"Crowd": 0.90, "Lonely": 0.05})
    assert [e.candidate.team for e in as_pct] == [e.candidate.team for e in as_frac]
    assert as_pct[0].equity == pytest.approx(as_frac[0].equity)


def test_field_equity_is_empty_without_candidates():
    assert field_equity([], {"Anything": 0.5}) == []


def test_field_equity_report_names_the_best_pick():
    cands = [candidate("Crowd", "A", 0.75), candidate("Lonely", "B", 0.70)]
    report = format_field_equity(field_equity(cands, {"Crowd": 0.9, "Lonely": 0.05}))
    assert "Highest pot equity: Lonely" in report


# ---------------------------------------------------------------------------
# State durability
# ---------------------------------------------------------------------------


def test_a_corrected_result_revives_a_wrongly_eliminated_entrant():
    """Results get corrected — a mistyped LOSS, or a feed reporting an
    abandoned match. Survival is derived from the history, so fixing the
    record fixes the status."""
    state = LMSState(entrant="James")
    record_pick(state, 1, "Man City", "Bournemouth", True)
    record_result(state, 1, "LOSS")
    assert state.alive is False
    record_result(state, 1, "WIN")
    assert state.alive is True


def test_an_earlier_elimination_still_stands_after_a_later_correction():
    state = LMSState(entrant="James")
    record_pick(state, 1, "Man City", "Bournemouth", True)
    record_result(state, 1, "DRAW")
    state.alive = True  # as if the entrant had been resurrected by hand
    record_pick(state, 2, "Arsenal", "Fulham", True)
    record_result(state, 2, "WIN")
    assert state.alive is False


def test_load_states_survives_unrecognised_keys(tmp_path):
    path = tmp_path / "lms_state.json"
    path.write_text(json.dumps({
        "James": {
            "entrant": "James", "alive": True, "used_clubs": ["Arsenal"],
            "history": [], "notes": "hand-edited", "schema_version": 3,
        }
    }))
    states = load_states(path)
    assert states["James"].used_clubs == ["Arsenal"]


def test_save_states_leaves_no_partial_file_behind(tmp_path):
    path = tmp_path / "nested" / "lms_state.json"
    save_states({"James": LMSState(entrant="James")}, path)
    assert json.loads(path.read_text())["James"]["entrant"] == "James"
    assert list(path.parent.iterdir()) == [path]


def test_save_states_round_trips(tmp_path):
    path = tmp_path / "lms_state.json"
    state = LMSState(entrant="James")
    record_pick(state, 1, "Man City", "Bournemouth", True)
    record_result(state, 1, "WIN")
    save_states({"James": state}, path)
    assert load_states(path)["James"].history == state.history


def test_auto_settle_matches_a_hand_typed_pick_to_a_fetched_fixture():
    """The pick was typed by a human, the fixture list came from a feed.
    An exact-string match here fails silently, leaving the pick PENDING."""
    gw = Gameweek(3, (GameweekFixture("Man City", "Bournemouth"),))
    state = LMSState(entrant="James")
    record_pick(state, 3, " man city ", "BOURNEMOUTH", True)
    settled = auto_settle({"James": state}, gw, {0: (2, 0)})
    assert settled == [("James", 3, "WIN")]


def test_pending_gameweeks_covers_picks_the_competition_has_moved_past():
    """Settlement must not be tied to whichever gameweek is current: once a
    gameweek's last fixture kicks off, the feed's current matchday moves
    on, and a pick keyed to it would stay PENDING forever."""
    from bym.lms import pending_gameweeks

    james, sam = LMSState(entrant="James"), LMSState(entrant="Sam")
    record_pick(james, 3, "Man City", "Bournemouth", True)
    record_pick(sam, 3, "Bournemouth", "Man City", False)   # same fixture
    record_pick(james, 4, "Arsenal", "Everton", True)
    record_result(james, 3, "WIN")                          # already settled

    pending = pending_gameweeks({"James": james, "Sam": sam})
    assert sorted(pending) == [3, 4]
    # One fixture for GW3, not one per entrant on it.
    assert [(f.home, f.away) for f in pending[3].fixtures] == [
        ("Man City", "Bournemouth")
    ]
    assert [(f.home, f.away) for f in pending[4].fixtures] == [("Arsenal", "Everton")]


def test_nothing_pending_when_every_pick_is_settled():
    from bym.lms import pending_gameweeks

    state = LMSState(entrant="James")
    record_pick(state, 1, "Man City", "Bournemouth", True)
    record_result(state, 1, "WIN")
    assert pending_gameweeks({"James": state}) == {}


def test_plan_stops_at_the_first_gameweek_it_cannot_fill():
    """A gameweek with no legal pick eliminates the entrant, so planning
    past one is planning for a competition already lost — and the pick
    offered for 'now' must be this gameweek's, never a later one's."""
    weeks = [Gameweek(1, ()), Gameweek(2, ())]
    outlooks = [
        [outlook("Ace", "Foil", 0.70, 0.10)],   # only Ace is legal
        [outlook("Ace", "Mug", 0.80, 0.10)],    # ...and it is spent by then
    ]
    p = plan_horizon(None, weeks, used_clubs={"Foil", "Mug"}, outlooks=outlooks)
    assert [(x.gameweek, x.candidate.team) for x in p.picks] == [(1, "Ace")]
    assert p.unplannable == (2,)
    assert p.this_week.gameweek == 1


def test_greedy_running_out_of_clubs_scores_zero_not_a_short_horizon():
    """Greedy spending Ace in GW1 leaves GW2 unpickable. Scoring it over
    only the week it managed to fill would make the failure look like a
    better plan — fewer probabilities multiplied together."""
    weeks = [Gameweek(1, ()), Gameweek(2, ())]
    outlooks = [
        [outlook("Ace", "Foil", 0.70, 0.10), outlook("Deputy", "Mug", 0.60, 0.20)],
        [outlook("Ace", "Mug", 0.80, 0.10)],
    ]
    p = plan_horizon(None, weeks, used_clubs={"Foil", "Mug"}, outlooks=outlooks)
    assert [x.candidate.team for x in p.picks] == ["Deputy", "Ace"]
    assert p.greedy_picks[0].candidate.team == "Ace"
    assert p.greedy_survival_p == 0.0
    assert p.survival_p == pytest.approx(0.60 * 0.80)
    assert "no legal club for GW2" in format_plan(p)


# --- A booked gameweek is spent, not just its club ----------------------
#
# used_clubs alone cannot place an entrant in the horizon. Someone who has
# booked GW1 has spent the week as well as the club, and planning still
# from GW1 told them to pick for a deadline that had passed — while
# solving GW2+ as though the club it suggested for GW1 were unavailable
# later, which understated every week after it.

def _weeks(*numbers):
    return [Gameweek(n, ()) for n in numbers]


def test_a_booked_gameweek_drops_out_of_the_horizon():
    from bym.lms import open_gameweek_indices

    state = LMSState(entrant="James", history=[{"gameweek": 1, "team": "Man United"}])
    assert open_gameweek_indices(_weeks(1, 2, 3, 4), state) == [1, 2, 3]


def test_an_entrant_with_no_history_keeps_the_whole_horizon():
    from bym.lms import open_gameweek_indices

    assert open_gameweek_indices(_weeks(1, 2, 3), LMSState(entrant="New")) == [0, 1, 2]
    assert open_gameweek_indices(_weeks(1, 2, 3), None) == [0, 1, 2]


def test_a_gap_in_booked_weeks_is_respected_not_assumed_leading():
    """Bookings are not necessarily a prefix — a hand-edited state file is
    the documented fallback when a result cannot be fetched."""
    from bym.lms import open_gameweek_indices

    state = LMSState(
        entrant="James",
        history=[{"gameweek": 1, "team": "A"}, {"gameweek": 3, "team": "B"}],
    )
    assert open_gameweek_indices(_weeks(1, 2, 3, 4), state) == [1, 3]


def test_every_week_booked_leaves_nothing_to_plan():
    from bym.lms import open_gameweek_indices

    state = LMSState(
        entrant="James",
        history=[{"gameweek": n, "team": str(n)} for n in (1, 2)],
    )
    assert open_gameweek_indices(_weeks(1, 2), state) == []


def test_planning_from_the_open_week_scores_only_the_weeks_left(params):
    """The survival figure must cover the weeks the entrant can still
    pick. Including a booked week both names a pick they cannot make and
    reserves its club against the weeks that follow."""
    weeks = [
        Gameweek(1, (GameweekFixture("Strong", "Weak"),)),
        Gameweek(2, (GameweekFixture("Strong", "Middling A"),
                     GameweekFixture("Middling B", "Weak"))),
    ]
    state = LMSState(
        entrant="James",
        used_clubs=["Middling A"],
        history=[{"gameweek": 1, "team": "Middling A"}],
    )
    from bym.lms import open_gameweek_indices

    open_idx = open_gameweek_indices(weeks, state)
    plan = plan_horizon(params, [weeks[i] for i in open_idx], set(state.used_clubs))

    assert [p.gameweek for p in plan.picks] == [2]
    assert plan.this_week.gameweek == 2
    assert "Middling A" not in [p.candidate.team for p in plan.picks]


# --- Pick lineage -------------------------------------------------------

def test_cumulative_survival_is_a_running_product():
    from bym.lms import cumulative_survival

    out = cumulative_survival([0.5, 0.5, 0.5])
    assert out == pytest.approx([0.5, 0.25, 0.125])


def test_survival_stops_at_the_first_unknown_rather_than_skipping_it():
    """A product with a missing term is unknown, not the product of the
    terms that happened to be present — carrying on would quietly report a
    figure for weeks it cannot actually price."""
    from bym.lms import cumulative_survival

    assert cumulative_survival([0.5, None, 0.5]) == [0.5, None, None]


def test_pick_outlook_reads_the_probability_from_the_club_s_own_side(params):
    from bym.lms import pick_outlook

    home = pick_outlook(params, "Strong", "Weak", is_home=True)
    away = pick_outlook(params, "Strong", "Weak", is_home=False)
    assert home > away, "home advantage should survive the flip"
    assert 0 < away < home < 1


def test_lineage_places_booked_and_planned_weeks_on_one_path(params):
    from bym.lms import lineage, plan_horizon

    weeks = [
        Gameweek(1, (GameweekFixture("Strong", "Weak"),
                     GameweekFixture("Middling A", "Middling B"))),
        Gameweek(2, (GameweekFixture("Strong", "Middling A"),
                     GameweekFixture("Middling B", "Weak"))),
    ]
    state = LMSState(
        entrant="James", used_clubs=["Middling A"],
        history=[{"gameweek": 1, "team": "Middling A", "opponent": "Middling B",
                  "is_home": True, "result": "PENDING", "source": None}],
    )
    reference = plan_horizon(params, weeks)
    entrant_plan = plan_horizon(params, weeks[1:], {"Middling A"})
    steps = lineage(params, state, weeks, entrant_plan, reference)

    assert [s.gameweek for s in steps] == [1, 2]
    assert [s.status for s in steps] == ["BOOKED", "PLANNED"]
    assert steps[0].team == "Middling A"
    # The booked week is compared against what an unused entrant was told,
    # which is the only meaning "the recommendation" has for a spent week.
    assert steps[0].plan_team == reference.picks[0].candidate.team
    assert steps[0].delta == pytest.approx(steps[0].p_win - steps[0].plan_p_win)


def test_a_booked_week_outside_the_fixture_list_keeps_its_club(params):
    """Fixtures roll out of the horizon; the pick still happened. Naming
    the club with an unknown probability beats dropping the week."""
    from bym.lms import lineage, plan_horizon

    weeks = [Gameweek(9, (GameweekFixture("Strong", "Weak"),))]
    state = LMSState(
        entrant="James", used_clubs=["Middling A"],
        history=[{"gameweek": 1, "team": "Middling A", "opponent": None,
                  "is_home": True, "result": "WIN", "source": "AUTO"}],
    )
    steps = lineage(params, state, weeks, plan_horizon(params, weeks, {"Middling A"}))
    booked = [s for s in steps if s.status == "BOOKED"][0]
    assert booked.team == "Middling A"
    assert booked.p_win is None
    assert booked.delta is None
