"""Market divergence — the §20 CONF ingredient, finally non-placeholder.

Two observations that sound similar and mean entirely different things,
stored separately and never conflated:

    model_vs_market   our statistical football model disagrees with the
                      bookmakers. Interesting: one of them is wrong, and
                      it is usually us.

    book_dispersion   the bookmakers disagree with each other. Often
                      mundane: some books have updated and some haven't.

Collapsing these into one "divergence" figure would make a stale-feed
artefact indistinguishable from a genuine model/market disagreement.

Distance is total variation distance over H/D/A — bounded [0, 1] and
directly readable as "the largest probability mass that would have to
move to reconcile the two views".

The model probabilities used here must be the **pre-calibration** ones
(raw Dixon-Coles), not the post-blend figures. Comparing the blended
output to the market would measure how hard the blend pulled, which is a
function of w, not of disagreement.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import DEFAULT_MOVEMENT, MovementConfig
from .model.matrix import ScoreMatrix


@dataclass(frozen=True)
class DivergenceReport:
    model_h: float
    model_d: float
    model_a: float
    market_h: float
    market_d: float
    market_a: float
    model_vs_market: float  # total variation distance, [0, 1]
    book_dispersion: float
    bookmaker_count: int

    @property
    def largest_disagreement(self) -> tuple[str, float]:
        """Outcome where model and market differ most, in pp."""
        pairs = {
            "H": (self.model_h - self.market_h) * 100.0,
            "D": (self.model_d - self.market_d) * 100.0,
            "A": (self.model_a - self.market_a) * 100.0,
        }
        outcome = max(pairs, key=lambda k: abs(pairs[k]))
        return outcome, pairs[outcome]

    def describe(self) -> str:
        outcome, gap = self.largest_disagreement
        name = {"H": "home", "D": "draw", "A": "away"}[outcome]
        direction = "above" if gap > 0 else "below"
        return (
            f"model {name} probability {abs(gap):.1f}pp {direction} market "
            f"(TVD {self.model_vs_market:.3f}, {self.bookmaker_count} books, "
            f"dispersion {self.book_dispersion:.3f})"
        )


def total_variation(
    p: tuple[float, float, float], q: tuple[float, float, float]
) -> float:
    return 0.5 * sum(abs(a - b) for a, b in zip(p, q))


def assess(
    raw_matrix: ScoreMatrix,
    market_h: float,
    market_d: float,
    market_a: float,
    book_dispersion: float,
    bookmaker_count: int,
) -> DivergenceReport:
    """Compare the *pre-calibration* model result probabilities against
    the market consensus."""
    model = (raw_matrix.p_home_win, raw_matrix.p_draw, raw_matrix.p_away_win)
    market = (market_h, market_d, market_a)
    return DivergenceReport(
        model_h=model[0],
        model_d=model[1],
        model_a=model[2],
        market_h=market_h,
        market_d=market_d,
        market_a=market_a,
        model_vs_market=total_variation(model, market),
        book_dispersion=book_dispersion,
        bookmaker_count=bookmaker_count,
    )


def divergence_factor(
    report: DivergenceReport | None, cfg: MovementConfig = DEFAULT_MOVEMENT
) -> str:
    """The §20 `market_divergence` CONF ingredient, as a category.

    Deliberately a category rather than a number: §20 requires CONF itself
    to be HIGH/MEDIUM/LOW, and inventing a precise-looking scalar from two
    loosely-calibrated inputs would imply accuracy this does not have.
    The full five-factor CONF still awaits calibration testing (§16).

    Returns LOW divergence (i.e. reassuring) when model and market agree
    and the books agree with each other; HIGH when the model contradicts a
    market that is itself confident.
    """
    if report is None:
        return "UNKNOWN"
    books_agree = report.book_dispersion < cfg.dispersion_threshold
    if report.model_vs_market >= 0.10:
        return "HIGH" if books_agree else "MEDIUM"
    if report.model_vs_market >= 0.05:
        return "MEDIUM"
    return "LOW"
