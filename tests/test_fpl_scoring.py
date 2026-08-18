import pytest

from bym.fpl import scoring
from bym.fpl.players import Position
from bym.fpl.projection import FixtureProjection


def proj(**overrides) -> FixtureProjection:
    defaults = dict(
        player_id=1, fixture_id="2022:1:Chelsea:Everton", season=2022, gameweek=1,
        p_appearance=1.0, p_start=1.0, p_60_plus=1.0, expected_minutes=90.0,
        goal_lambda=0.0, assist_lambda=0.0, p_clean_sheet=0.0, expected_saves=None,
    )
    defaults.update(overrides)
    return FixtureProjection(**defaults)


def test_goal_points_use_the_rare_goalkeeper_value_not_a_defender_value():
    """A goalkeeper's goal is worth 10, not 6 — the one value in this
    table that is easy to get wrong by analogy with defenders."""
    breakdown = scoring.fixture_expected_points(
        proj(goal_lambda=1.0, p_60_plus=0.0, p_appearance=0.0), Position.GK.value
    )
    assert breakdown.goal_points == 10.0


@pytest.mark.parametrize(
    "position, expected",
    [(Position.GK.value, 10), (Position.DEF.value, 6), (Position.MID.value, 5), (Position.FWD.value, 4)],
)
def test_goal_points_by_position(position, expected):
    breakdown = scoring.fixture_expected_points(
        proj(goal_lambda=1.0, p_60_plus=0.0, p_appearance=0.0), position
    )
    assert breakdown.goal_points == expected


def test_assist_points_are_position_independent():
    for position in (Position.GK.value, Position.DEF.value, Position.MID.value, Position.FWD.value):
        breakdown = scoring.fixture_expected_points(
            proj(assist_lambda=1.0, p_60_plus=0.0, p_appearance=0.0), position
        )
        assert breakdown.assist_points == 3.0


def test_appearance_points_blend_60_plus_and_under_60_rates():
    # p_60_plus=0.7 -> 2pts, remaining 0.2 of appearances under 60 -> 1pt
    breakdown = scoring.fixture_expected_points(
        proj(p_appearance=0.9, p_60_plus=0.7), Position.MID.value
    )
    assert breakdown.appearance_points == pytest.approx(0.7 * 2 + 0.2 * 1)


def test_appearance_points_are_never_negative_when_60_plus_exceeds_appearance():
    """Structurally p_60_plus <= p_appearance, but the formula must not
    produce a negative under-60 contribution if a caller hands in
    inconsistent numbers directly."""
    breakdown = scoring.fixture_expected_points(
        proj(p_appearance=0.5, p_60_plus=0.9), Position.MID.value
    )
    assert breakdown.appearance_points == pytest.approx(0.9 * 2)


@pytest.mark.parametrize(
    "position, expected",
    [(Position.GK.value, 4), (Position.DEF.value, 4), (Position.MID.value, 1), (Position.FWD.value, 0)],
)
def test_clean_sheet_points_by_position(position, expected):
    breakdown = scoring.fixture_expected_points(
        proj(p_clean_sheet=1.0, p_60_plus=1.0, p_appearance=1.0), position
    )
    assert breakdown.clean_sheet_points == expected


def test_clean_sheet_points_require_60_plus_minutes():
    """A player projected to play under 60 minutes contributes no clean
    sheet points even at a certain team clean sheet — §2's "60+ minutes
    required" rule, not an accident of the raw team probability."""
    breakdown = scoring.fixture_expected_points(
        proj(p_clean_sheet=1.0, p_60_plus=0.0, p_appearance=0.3), Position.DEF.value
    )
    assert breakdown.clean_sheet_points == 0.0


def test_saves_points_only_apply_when_expected_saves_is_present():
    with_saves = scoring.fixture_expected_points(
        proj(expected_saves=6.0), Position.GK.value
    )
    assert with_saves.saves_points == pytest.approx(2.0)  # 6 saves / 3

    without_saves = scoring.fixture_expected_points(proj(expected_saves=None), Position.MID.value)
    assert without_saves.saves_points == 0.0


def test_total_sums_only_modelled_components():
    breakdown = scoring.fixture_expected_points(
        proj(p_appearance=1.0, p_60_plus=1.0, goal_lambda=0.5, assist_lambda=0.5,
             p_clean_sheet=0.4), Position.MID.value
    )
    assert breakdown.total == pytest.approx(
        breakdown.appearance_points + breakdown.goal_points
        + breakdown.assist_points + breakdown.clean_sheet_points + breakdown.saves_points
    )


def test_a_goal_scored_by_a_goalkeeper_is_not_confused_with_an_own_goal():
    """§2: a GK scoring is +10 (the rarest scoring event, GOAL_POINTS).
    An own goal is -2 for ANY position and is a wholly different, still
    unmodelled component (OMITTED_COMPONENTS) — these must never collapse
    into one 'goalkeeper scoring event' concept."""
    assert scoring.GOAL_POINTS[Position.GK.value] == 10
    assert "own_goals" in scoring.OMITTED_COMPONENTS
    assert not hasattr(scoring, "own_goal_points")
    breakdown = scoring.fixture_expected_points(
        proj(goal_lambda=1.0, p_60_plus=0.0, p_appearance=0.0), Position.GK.value
    )
    # A GK's goal_points come from GOAL_POINTS only — no own-goal penalty
    # is ever netted into this figure, since it isn't modelled at all.
    assert breakdown.goal_points == 10.0


def test_omitted_components_are_named_not_silently_zeroed():
    breakdown = scoring.fixture_expected_points(proj(), Position.MID.value)
    assert breakdown.omitted_components == {
        "bonus", "defcon", "cards", "own_goals", "goals_conceded_deduction", "penalties",
    }


def test_unknown_position_is_rejected():
    with pytest.raises(ValueError, match="unknown position"):
        scoring.fixture_expected_points(proj(), "STRIKER")
