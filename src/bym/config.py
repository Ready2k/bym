"""Model and pipeline configuration.

Everything here is config, not spec (SPEC.md §30 caps/floors, §31
tail_tolerance, §29.2 ξ candidates): values are defaults to be validated or
replaced by walk-forward backtesting, never magic numbers baked into model
code.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelConfig:
    # §29.2 exponential time-decay, per day. 0.0 = no decay. The grid is
    # searched by outer walk-forward validation (`bym tune-xi`), not fit
    # by MLE. 0.003 (~231-day half-life) selected on the 2024/25 validation
    # season: log loss plateaus across 0.002-0.005, points peak at
    # 0.002-0.003; this is the stable midpoint, not a grid-search idol.
    # See VALIDATION.md — 2024/25 is validation data from this point on.
    xi: float = 0.003
    xi_grid: tuple[float, ...] = (0.0, 0.0005, 0.001, 0.002, 0.003, 0.005)

    # §31 scoreline truncation: grid grows until omitted tail mass is below
    # tail_tolerance, then the finite grid is renormalised proportionally.
    tail_tolerance: float = 1e-5
    min_goals_grid: int = 10
    max_goals_cap: int = 20

    # §31 ρ is bounded during MLE to keep τ strictly positive.
    rho_bounds: tuple[float, float] = (-0.2, 0.2)

    # §30 λ caps/floors — plausible expected-goals range; bounds are config
    # to be tuned from the historical goal distribution in backtesting.
    lambda_floor: float = 0.05
    lambda_ceiling: float = 6.0

    # §32 market blend weight (Option B result-constrained reweighting).
    # None = no blending; the grid is searched by `bym tune-market` on
    # the validation season, endpoints included so pure-model and
    # pure-market-result are both baselines (§28 "market blend vs market
    # alone"). 0.5 selected on 2024/25: interior peak beating both
    # endpoints on points (8.24 vs 8.03/8.14) and log loss (1.0043) —
    # see VALIDATION.md decisions table.
    market_blend_weight: float | None = 0.5
    market_blend_grid: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)

    # §29.4 promoted/relegated-team empirical-Bayes priors. ADOPTED on
    # correctness grounds, not on a significant validation win: the paired
    # test could not resolve +0.026 pts/round (CI crosses zero, only 24 of
    # 932 fixtures affected). The zero-evidence prior it replaces is not a
    # neutral default — it asserts a newly-promoted team is exactly league
    # average, which the fitted priors show is false (E0-from-below
    # alpha = -0.278). Choosing a measured prior over a known-wrong one.
    use_promoted_priors: bool = True

    # §29.5 season-transition shrinkage, translated for a full-refit
    # architecture: extra weight multiplier season_decay^(seasons_ago) on
    # top of xi. BINNED at 1.0 (disabled): 0.6 reproduced the baseline
    # exactly, 0.8 moved points by 0.01 and failed the paired test. The
    # xi=0.003 daily decay already discharges §29.5's intent — a
    # season-old match is already at roughly a third weight.
    season_decay: float = 1.0
    season_decay_grid: tuple[float, ...] = (1.0, 0.8, 0.6)

    # Backtest refit cadence (days between parameter refits, §16).
    refit_every_days: int = 7

    # Phase 1A confidence thresholds (see recommend.confidence_v1).
    conf_high_result_p: float = 0.58
    conf_high_score_p: float = 0.12
    conf_low_result_p: float = 0.45
    conf_low_score_p: float = 0.10


@dataclass(frozen=True)
class MovementConfig:
    """Market-movement materiality thresholds (Phase 2 Increment 2).

    PROVISIONAL and operational only — these carry no model-training
    claim. They exist to control *attention* (what gets reforecast,
    logged, alerted), never to adjust probabilities. Real values should be
    calibrated from the movement distribution observed prospectively
    during 2026/27, not from football folklore.
    """

    # Absolute percentage-point change in consensus probability. Absolute
    # pp is the primary human-facing number: relative change makes a
    # 2%->3% move look like a crisis (+50%).
    movement_threshold_pp: float = 3.0
    urgent_movement_threshold_pp: float = 6.0

    # Cross-bookmaker dispersion (max-min of de-vigged home probability)
    # above which the market is treated as disagreeing with itself.
    dispersion_threshold: float = 0.05
    high_dispersion_threshold: float = 0.10

    # Reference points for movement comparison, in hours before now.
    reference_hours: tuple[float, ...] = (1.5, 6.0, 24.0)
    # A stored snapshot counts as "N hours ago" if it falls within this
    # tolerance of the target; otherwise that reference is unavailable
    # rather than silently substituted with the nearest thing to hand.
    reference_tolerance_hours: float = 2.0


@dataclass(frozen=True)
class LMSConfig:
    """Last Man Standing tuning (`lms.py`).

    PROVISIONAL and strategy-only. Nothing here touches the fitted model —
    LMS reads exactly the same score matrix Super 6 does. These values
    decide how that probability is *acted on* in a knockout format, which
    is a different question from how a scoreline is forecast, and they
    carry no validation claim yet: unlike ξ or the market blend weight,
    there is no backtest behind them, because the competition has one
    observation per entrant per season. Treat them as stated judgement to
    be revised prospectively, not as calibrated numbers.
    """

    # Confidence banding for a survive-or-die pick. Deliberately NOT
    # ModelConfig's conf_high_result_p (0.58): that threshold bands the
    # probability of a Super 6 *selected outcome*, where being wrong costs
    # points off one fixture out of six. Here being wrong ends the
    # competition, and the strongest Premier League home favourites price
    # around 0.75-0.85, so calling 0.58 "HIGH" would flatter exactly the
    # coin-flip picks that eliminate entrants.
    conf_high_p_win: float = 0.70
    conf_low_p_win: float = 0.55

    # Default lookahead depth for `lms.plan_horizon`, in gameweeks
    # including the one being picked. 4 covers the reason lookahead exists
    # at all (a club with two strong fixtures in the window can only be
    # spent on one of them) while staying inside the range where the
    # fixture list is stable — beyond about a month, postponements and
    # rescheduling churn the plan faster than it can pay off.
    horizon_gameweeks: int = 4

    # Per-week shrink applied to a FUTURE gameweek's win probabilities
    # before planning, toward that gameweek's own mean: a fixture k weeks
    # out is forecast from today's squad strengths, and injuries, form and
    # transfers have k weeks to invalidate that. Without it the planner
    # treats a distant 0.80 as being as bankable as this week's 0.80 and
    # hoards clubs for fixtures that may never look that good on the day.
    # 0.05/week (a 4-week-out edge retains ~86% of its size) is a
    # deliberately mild default — the direction is defensible, the
    # magnitude is a guess.
    future_shrink_per_week: float = 0.05


@dataclass(frozen=True)
class DataConfig:
    # Football-Data.co.uk (§18): free historical results + odds corpus.
    base_url: str = "https://www.football-data.co.uk/mmz4281"
    # E0 = Premier League, E1 = Championship — the leagues Super 6 draws
    # from, and the ONLY corpus PHASE1_MODEL_V1 was fitted and validated
    # against. Do not widen this: the per-division fits are unaffected by
    # extra divisions, but the pooled cross-division fallback is not, and
    # silently changing it would drift a frozen, holdout-tested model.
    divisions: tuple[str, ...] = ("E0", "E1")

    # Lower divisions, available from the same free source. Used ONLY for
    # ad-hoc `bym forecast --extended` sanity checks involving teams
    # outside the Super 6 corpus (cup ties, friendlies). Any forecast
    # using these is explicitly NOT PHASE1_MODEL_V1 and is labelled so.
    extended_divisions: tuple[str, ...] = ("E0", "E1", "E2", "E3", "EC")
    # Bookmaker odds column preference order (first available wins).
    odds_prefixes: tuple[str, ...] = ("B365", "PS", "Avg", "BbAv", "WH")
    # Football-Data names the derivative markets with a different prefix
    # set from 1X2 — Pinnacle is `PSH` for the match result but `PAHH` and
    # `P>2.5` for handicap and totals. A separate list rather than a
    # transformation of the one above, because the mapping is not a rule,
    # it is just how the file is laid out.
    derivative_odds_prefixes: tuple[str, ...] = ("B365", "P", "Avg", "Max", "BFE")

    # §18's refresh-limit discipline is right for a finished season — those
    # files never change, and re-downloading them is pure waste. It is
    # wrong for the season being played: the cache would pin the model to
    # whatever was published the first time it ran, so every result of the
    # current campaign stays invisible and the fit drifts further from the
    # league every week, silently. A season in progress is therefore
    # refetched once its copy is older than this.
    season_max_age_hours: float = 24.0
    # A season starting in year Y is treated as still in progress until
    # this month of Y+1. English football ends in late May; July is a
    # deliberate margin, and being a month late costs one conditional
    # request a day against a file that is not changing.
    season_complete_from_month: int = 7


DEFAULT_MODEL = ModelConfig()
DEFAULT_DATA = DataConfig()
DEFAULT_MOVEMENT = MovementConfig()
DEFAULT_LMS = LMSConfig()

# PHASE1_MODEL_V1 — the frozen quantitative core (MANIFEST.md), pinned
# explicitly rather than tracking DEFAULT_MODEL.
#
# DO NOT EDIT. Phase 2+ work will change DEFAULT_MODEL; if the frozen arm
# followed those edits, the prospective 2026/27 comparison against the
# always-on agent would silently compare Phase 2 against itself. Every
# value below is written out literally so that a diff to this constant is
# visible in review rather than inherited by accident.
FROZEN_PHASE1_V1 = ModelConfig(
    xi=0.003,
    tail_tolerance=1e-5,
    min_goals_grid=10,
    max_goals_cap=20,
    rho_bounds=(-0.2, 0.2),
    lambda_floor=0.05,
    lambda_ceiling=6.0,
    market_blend_weight=0.5,
    use_promoted_priors=True,
    season_decay=1.0,
    refit_every_days=7,
)
