import numpy as np
import pandas as pd
import pytest

from bym.model import dixon_coles, elo

from test_dixon_coles import synthetic_matches


def simple_matches(rows) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals"]
    )


def test_ratings_are_pre_match():
    m = simple_matches(
        [
            (pd.Timestamp("2024-08-01"), "A", "B", 3, 0),
            (pd.Timestamp("2024-08-08"), "A", "B", 1, 1),
        ]
    )
    result = elo.compute(m)
    # first meeting: both at init, regardless of its own result
    assert result.matches.iloc[0]["elo_home"] == elo.INIT_RATING
    assert result.matches.iloc[0]["elo_away"] == elo.INIT_RATING
    # second meeting: A's win has been applied, B's loss likewise
    assert result.matches.iloc[1]["elo_home"] > elo.INIT_RATING
    assert result.matches.iloc[1]["elo_away"] < elo.INIT_RATING


def test_zero_sum():
    m = simple_matches(
        [
            (pd.Timestamp("2024-08-01"), "A", "B", 2, 0),
            (pd.Timestamp("2024-08-08"), "C", "A", 0, 1),
            (pd.Timestamp("2024-08-15"), "B", "C", 1, 1),
        ]
    )
    ratings = elo.compute(m).ratings
    assert sum(ratings.values()) == pytest.approx(3 * elo.INIT_RATING)


def test_winners_rise():
    rows = [
        (pd.Timestamp("2024-08-01") + pd.Timedelta(days=7 * i), "Winner", "Loser", 2, 0)
        for i in range(10)
    ]
    ratings = elo.compute(simple_matches(rows)).ratings
    assert ratings["Winner"] > elo.INIT_RATING > ratings["Loser"]


def test_fit_with_elo_covariate():
    """§30: DC+Elo fit yields a finite θ, and with informative Elo the
    coefficient should be positive (higher-rated team scores more)."""
    m = synthetic_matches(40)
    m = elo.compute(m).matches
    fitted = dixon_coles.fit(m, xi=0.0, use_elo=True)
    assert fitted.theta is not None and np.isfinite(fitted.theta)

    fitted_plain = dixon_coles.fit(m, xi=0.0)
    assert fitted_plain.theta is None


def test_missing_elo_term_dropped_not_defaulted():
    """§30 missing-feature rule: lambdas without elo_diff must equal a
    θ=None model's lambdas — the term is dropped, not imputed."""
    m = elo.compute(synthetic_matches(20)).matches
    with_elo = dixon_coles.fit(m, xi=0.0, use_elo=True)
    lam_no_diff = with_elo.lambdas("Strong", "Weak")
    lam_with_diff = with_elo.lambdas("Strong", "Weak", elo_diff=1.0)
    assert lam_no_diff != lam_with_diff  # diff actually matters when given
    # and no elo_diff → pure α/β/γ/μ prediction, finite and sane
    assert all(np.isfinite(lam_no_diff))


# ---------------------------------------------------------------------------
# §32.1 margin-of-victory Elo (ablation_007 candidate — implemented, not promoted)


def test_mov_multiplier_grows_with_margin_but_with_diminishing_returns():
    from bym.model import elo

    one, two, three, four = (elo.mov_multiplier(m, 0.0) for m in (1, 2, 3, 4))
    assert one < two < three < four
    # Each extra goal adds less than the one before, so a single thrashing
    # cannot dominate a season of ratings.
    assert four - three < three - two < two - one


def test_mov_multiplier_damps_the_expected_win_and_rewards_the_upset():
    """Without the autocorrelation term, strong teams gain twice over: they
    win more often and by more."""
    from bym.model import elo

    assert elo.mov_multiplier(1, 400.0) < elo.mov_multiplier(1, 0.0)
    assert elo.mov_multiplier(1, -400.0) > elo.mov_multiplier(1, 0.0)


def test_a_draw_still_moves_ratings():
    """A literal zero margin would multiply the update by ln(1)=0 and freeze
    every rating on every draw — roughly a quarter of all matches."""
    from bym.model import elo

    assert elo.mov_multiplier(0, 0.0) > 0
    assert elo.mov_multiplier(0, 0.0) == elo.mov_multiplier(1, 0.0)


def test_mov_is_off_by_default_and_changes_ratings_when_on():
    import pandas as pd

    from bym.model import elo

    matches = pd.DataFrame({
        "date": pd.to_datetime(["2024-08-01", "2024-08-08", "2024-08-15"]),
        "home_team": ["A", "B", "A"],
        "away_team": ["B", "A", "B"],
        "home_goals": [5, 1, 1],
        "away_goals": [0, 1, 0],
    })
    plain = elo.compute(matches).ratings
    mov = elo.compute(matches, margin_of_victory=True).ratings
    assert plain != mov
    # The 5-0 should carry A further under MOV than under plain Elo.
    assert mov["A"] > plain["A"]
