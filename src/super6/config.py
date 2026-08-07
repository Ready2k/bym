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
    # searched by outer walk-forward validation (`super6 tune-xi`), not fit
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
    # None = no blending; the grid is searched by `super6 tune-market` on
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
class DataConfig:
    # Football-Data.co.uk (§18): free historical results + odds corpus.
    base_url: str = "https://www.football-data.co.uk/mmz4281"
    # E0 = Premier League, E1 = Championship — the leagues Super 6 draws from.
    divisions: tuple[str, ...] = ("E0", "E1")
    # Bookmaker odds column preference order (first available wins).
    odds_prefixes: tuple[str, ...] = ("B365", "PS", "Avg", "BbAv", "WH")


DEFAULT_MODEL = ModelConfig()
DEFAULT_DATA = DataConfig()
