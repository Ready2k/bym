import numpy as np
import pytest

from bym.availability.model import AvailabilityEvent
from bym.phase2 import decide_fixture, picks_for_round
from bym.recommend import FixtureRecommendation, RoundRecommendation


class Matrix:
    def __init__(self, ev):
        self._ev = np.asarray(ev, dtype=float)

    def ev_matrix(self):
        return self._ev


def recommendation(pick=(2, 1)):
    return FixtureRecommendation(
        "Home", "Away", *pick, 0.6, 0.2, 1.8, "MEDIUM", Matrix([
            [0.2, 0.4, 0.1],
            [0.3, 1.8, 0.7],
            [0.1, 0.2, 0.1],
        ]),
    )


def absence(*, team="Home", importance="KEY", status="CONFIRMED", event="CONFIRMED_ABSENT"):
    return AvailabilityEvent(
        home_team="Home", away_team="Away", team=team,
        player_name="Key Player", player_id="p1", event_type=event,
        claim_status=status, source_id="club", source_reliability="HIGH",
        source_timestamp="2026-10-15T10:00:00+00:00",
        observed_at="2026-10-15T10:00:00+00:00", importance=importance,
    )


def test_confirmed_key_absence_vetoes_home_win_using_frozen_matrix():
    decision = decide_fixture(recommendation(), [absence()])
    assert decision.pick == (1, 1)  # highest-EV draw, not an invented score
    assert decision.changed
    assert "confirmed key absence" in decision.reason


def test_unconfirmed_or_non_key_findings_do_not_change_pick():
    for finding in (absence(status="LIKELY"), absence(importance="REGULAR")):
        decision = decide_fixture(recommendation(), [finding])
        assert decision.pick == (2, 1)
        assert not decision.changed


def test_away_absence_only_vetoes_away_baseline():
    decision = decide_fixture(recommendation((1, 2)), [absence(team="Away")])
    assert decision.pick == (1, 1)


def test_raw_llm_like_claim_is_rejected():
    with pytest.raises(TypeError, match="AvailabilityEvent"):
        decide_fixture(recommendation(), [{"team": "Home", "availability": "CONFIRMED_ABSENT"}])


def test_round_returns_separate_recordable_pick_set():
    round_recommendation = RoundRecommendation(
        1, (recommendation(), recommendation((1, 1))), object()
    )
    picks = picks_for_round(
        round_recommendation,
        {("Home", "Away"): [absence()]},
    )
    assert picks == [(1, 1), (1, 1)]
