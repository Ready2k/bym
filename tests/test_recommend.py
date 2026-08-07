from datetime import datetime

import pandas as pd
import pytest

from super6.config import DEFAULT_MODEL
from super6.model import dixon_coles
from super6.recommend import confidence_v1, format_report, recommend_round
from super6.round import Fixture, Round

from test_dixon_coles import synthetic_matches


def test_confidence_categories_only():
    for p_r, p_s in [(0.7, 0.15), (0.5, 0.11), (0.3, 0.05)]:
        assert confidence_v1(p_r, p_s, DEFAULT_MODEL) in {"HIGH", "MEDIUM", "LOW"}


def test_confidence_ordering():
    assert confidence_v1(0.70, 0.15, DEFAULT_MODEL) == "HIGH"
    assert confidence_v1(0.30, 0.05, DEFAULT_MODEL) == "LOW"


def make_round():
    return Round(
        round_number=1,
        deadline_at=datetime(2026, 10, 18, 14, 0),
        fixtures=(
            Fixture("Strong", "Weak"),
            Fixture("Weak", "Strong"),
            Fixture("Middling A", "Middling B"),
            Fixture("Middling B", "Middling A"),
            Fixture("Strong", "Middling A"),
            Fixture("Weak", "Middling B"),
        ),
    )


@pytest.fixture(scope="module")
def rec():
    params = dixon_coles.fit(synthetic_matches(), xi=0.0)
    return recommend_round(params, make_round())


def test_six_recommendations(rec):
    assert len(rec.fixtures) == 6
    assert rec.expected_round_score == pytest.approx(sum(f.ev for f in rec.fixtures))


def test_strong_team_picked_to_beat_weak(rec):
    strong_home = rec.fixtures[0]
    assert strong_home.pick_home > strong_home.pick_away


def test_report_contains_required_columns(rec):
    report = format_report(rec)
    for token in ["PICK", "RESULT", "SCORE", "EV", "CONF", "Golden Goal",
                  "Sky interval", "Expected round score"]:
        assert token in report


def test_odds_blend_shifts_toward_market():
    """A fixture with strong market odds against the model's view must move
    result probabilities toward the market (§32.3, w=0.5)."""
    from super6.recommend import recommend_fixture

    params = dixon_coles.fit(synthetic_matches(), xi=0.0)
    plain = recommend_fixture(params, "Strong", "Weak")
    # market prices Strong far shorter than the model does
    blended = recommend_fixture(
        params, "Strong", "Weak", odds=(1.05, 12.0, 30.0)
    )
    assert blended.matrix.p_home_win > plain.matrix.p_home_win


def test_alternatives_ranked_by_ev(rec):
    alts = rec.fixtures[0].alternatives()
    evs = [ev for _, _, ev in alts]
    assert evs == sorted(evs, reverse=True)
    assert alts[0][0] == rec.fixtures[0].pick
