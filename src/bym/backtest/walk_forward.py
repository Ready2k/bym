"""Walk-forward backtesting (SPEC.md §16) and the §28 ablation baselines.

Strictly walk-forward: for each test date, parameters are fit only on
matches strictly before that date — future information never leaks into
historical predictions (§16, §22).

Baselines implemented (§16):
    MARKET-AVAILABLE — favourite result from Football-Data's earlier price
        set (≈ Friday-afternoon prices — the closest free proxy to odds
        knowable at a Super 6 deadline), scoreline via the explicit fixed
        mapping below.
    MARKET-ORACLE — same, from closing odds ('C' columns). Closing prices
        contain information up to kickoff — after the Super 6 deadline for
        late kickoffs — so this is a theoretical gold standard, not a fair
        deployable benchmark. Not beating it is no disgrace; MARKET-
        AVAILABLE is the meaningful target.
    pure Poisson — same pipeline, ρ fixed to 0.
    Dixon-Coles — pooled and per-division fits (ablation arms).

The market baselines have no exact-score opinion — a 1X2 market prices
H/D/A only. Their scoreline is the explicit fixed convention in
FAVOURITE_SCORELINES, and the baseline label says so; their Brier/log loss
are the meaningful market metrics, their points/exact% are "market result
+ naive score heuristic", never a real market score model (§32 will add
that properly).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import DEFAULT_MODEL, FROZEN_PHASE1_V1, ModelConfig
from ..model import dixon_coles, elo, market, priors, shots
from ..model.matrix import score_matrix
from ..recommend import confidence_v1
from .metrics import BacktestMetrics, evaluate

FAVOURITE_SCORELINES = {"H": (2, 1), "D": (1, 1), "A": (1, 2)}


@dataclass(frozen=True)
class WalkForwardResult:
    predictions: pd.DataFrame
    metrics: BacktestMetrics
    n_refits: int


def run_model(
    matches: pd.DataFrame,
    test_mask: pd.Series,
    xi: float | None = None,
    fix_rho_zero: bool = False,
    per_division: bool = False,
    use_elo: bool = False,
    elo_mov: bool = False,
    market_blend: float | None = None,
    devig: str = "proportional",
    totals_blend: float | None = None,
    handicap_blend: float | None = None,
    shot_blend: float | None = None,
    promoted_priors: priors.PromotedPriors | None = None,
    season_decay: float = 1.0,
    cfg: ModelConfig = DEFAULT_MODEL,
) -> WalkForwardResult:
    """Walk-forward the Dixon-Coles (or ρ=0 Poisson) model over the fixtures
    selected by test_mask, refitting every cfg.refit_every_days.

    per_division=True fits separate μ/γ/α/β/ρ per competition (own base
    rate and home advantage), instead of one pooled cross-division model.
    A team newly arrived in a division (promoted/relegated) then has no
    fitted parameters there and receives the league-average prior — the
    §29.4 zero-evidence limit. Pooled vs per-division is a §28 ablation,
    not an assumption.

    use_elo=True appends pre-match Elo ratings (pooled across divisions —
    sequential, so strictly pre-match and leak-free) and fits §30's θ
    covariate. Elo columns ride along into per-division train slices, so
    cross-division information survives division splitting.

    elo_mov=True scales each Elo update by margin of victory (§32.1
    candidate). Only meaningful alongside use_elo=True, since nothing
    consumes the ratings otherwise.

    market_blend, if set, applies §32.3 Option B result-constrained
    reweighting per fixture using MARKET-AVAILABLE odds (the earlier price
    set — never closing odds, which contain post-deadline information).
    Fixtures without odds fall back to the raw model matrix — graceful
    degradation, not imputation.

    shot_blend (§7) mixes in a second Dixon-Coles fit whose target is
    shot-derived expected goals (model/shots.py) instead of actual goals,
    blending at the λ level: λ = (1-w)·λ_goals + w·λ_shots. Testing the
    xG thesis with the only free signal that covers both divisions —
    goals are a noisy realisation, so fitting on a smoother target may
    estimate strength better.

    The proxy arm is fitted with fix_rho_zero=True, deliberately and
    visibly: proxy goals are continuous, and Dixon-Coles' τ identifies
    its {0,1}² cells by exact equality, so ρ would be unidentifiable and
    drift silently. Blending at λ means the champion's own ρ still shapes
    the final matrix, so the comparison isolates the fitting target
    rather than confounding it with the loss of the correction.

    devig selects §32.1's margin-removal method for those odds:
    "proportional" (the incumbent) or "shin". Only meaningful alongside
    market_blend.

    totals_blend / handicap_blend apply the same Option B idea to the
    over/under 2.5 and Asian-handicap markets, which constrain goal count
    and supremacy rather than the result. Both are independent of
    market_blend and of each other, so an ablation can attribute any
    change to one market. Applied after the 1X2 blend, so they refine a
    matrix the result market has already shaped; a fixture missing that
    market's price is left alone, the same graceful degradation h2h odds
    already get.

    promoted_priors (§29.4): per-division fits give each team newly
    arrived in its division *this test season* a Gaussian prior on (α, β)
    keyed by arrival direction. Estimate the priors on training seasons
    only (priors.estimate) — passing priors estimated on the test season
    would be leakage.

    season_decay (§29.5): extra per-season-boundary weight decay; 1.0
    disables.
    """
    if use_elo:
        matches = elo.compute(matches, margin_of_victory=elo_mov).matches
        test_mask = test_mask.reset_index(drop=True)
    elif elo_mov:
        raise ValueError("elo_mov requires use_elo=True — nothing reads the ratings otherwise")
    implied = market.devig(devig)
    test = matches[test_mask].sort_values("date")
    rows = []
    params_by_div: dict[str, dixon_coles.DixonColesParams] = {}
    shot_params_by_div: dict[str, dixon_coles.DixonColesParams] = {}
    last_fit: pd.Timestamp | None = None
    n_refits = 0

    # §29.4: arrival maps per division for the season(s) under test.
    # Division membership is public pre-season information, not leakage.
    arrival_priors: dict[str, dict[str, priors.PriorSpec]] = {}
    if promoted_priors is not None:
        if not per_division:
            raise ValueError("promoted_priors requires per_division=True")
        for div in sorted(test["division"].unique()):
            div_priors: dict[str, priors.PriorSpec] = {}
            for season in sorted(test[test["division"] == div]["season"].unique()):
                for team, direction in priors.arrivals(matches, div, int(season)).items():
                    spec = promoted_priors.lookup(div, direction)
                    if spec is not None:
                        div_priors[team] = spec
            arrival_priors[div] = div_priors

    def train_slice(date: pd.Timestamp, div: str) -> pd.DataFrame:
        train = matches[matches["date"] < date]
        if per_division:
            train = train[train["division"] == div]
        return train

    for date, group in test.groupby("date"):
        if last_fit is None or (date - last_fit).days >= cfg.refit_every_days:
            divisions = (
                sorted(test["division"].unique()) if per_division else ["ALL"]
            )
            refreshed = {}
            refreshed_shots = {}
            for div in divisions:
                train = train_slice(date, div)
                if len(train) < 200:
                    continue  # not enough history yet to fit sensibly
                if shot_blend is not None:
                    conversion = shots.fit_conversion(train)
                    if conversion is not None:
                        refreshed_shots[div] = dixon_coles.fit(
                            shots.with_proxy_goals(train, conversion),
                            xi=xi, reference_date=date,
                            fix_rho_zero=True,  # τ is undefined on a continuous target
                            season_decay=season_decay,
                            reference_season=int(test["season"].max()),
                            cfg=cfg,
                        )
                refreshed[div] = dixon_coles.fit(
                    train, xi=xi, reference_date=date,
                    fix_rho_zero=fix_rho_zero, use_elo=use_elo,
                    param_priors=arrival_priors.get(div) or None,
                    season_decay=season_decay,
                    reference_season=int(test["season"].max()),
                    cfg=cfg,
                )
            if refreshed:
                params_by_div = refreshed
                shot_params_by_div = refreshed_shots
                last_fit = date
                n_refits += 1
        if not params_by_div:
            continue

        for _, r in group.iterrows():
            key = r["division"] if per_division else "ALL"
            params = params_by_div.get(key)
            if params is None:
                continue
            d_elo = (
                elo.elo_diff(r["elo_home"], r["elo_away"]) if use_elo else None
            )
            lam_h, lam_a = params.lambdas(
                r["home_team"], r["away_team"], cfg, elo_diff=d_elo
            )
            shot_params = shot_params_by_div.get(key) if shot_blend else None
            if shot_params is not None:
                shot_h, shot_a = shot_params.lambdas(
                    r["home_team"], r["away_team"], cfg
                )
                lam_h = (1.0 - shot_blend) * lam_h + shot_blend * shot_h
                lam_a = (1.0 - shot_blend) * lam_a + shot_blend * shot_a
            m = score_matrix(lam_h, lam_a, params.rho, cfg)
            if market_blend is not None and pd.notna(r["odds_h"]):
                mh, md, ma = implied(r["odds_h"], r["odds_d"], r["odds_a"])
                m = market.blend_matrix(m, mh, md, ma, market_blend)
            if totals_blend is not None and pd.notna(r.get("odds_over25")):
                over, _under = market.implied_two_way(
                    r["odds_over25"], r["odds_under25"]
                )
                win, lose = market.totals_weights(2.5, m.max_goals)
                m = market.constrain_matrix(m, win, lose, over, totals_blend)
            if handicap_blend is not None and pd.notna(r.get("odds_ah_h")) and pd.notna(r.get("ah_line")):
                home_cover, _away = market.implied_two_way(
                    r["odds_ah_h"], r["odds_ah_a"]
                )
                win, lose = market.handicap_weights(float(r["ah_line"]), m.max_goals)
                m = market.constrain_matrix(m, win, lose, home_cover, handicap_blend)
            ph, pa = m.best_pick()
            p_result, p_score = m.p_result(ph, pa), m.p_score(ph, pa)
            rows.append(
                {
                    "date": date,
                    "home_team": r["home_team"],
                    "away_team": r["away_team"],
                    "pred_home": ph,
                    "pred_away": pa,
                    "actual_home": r["home_goals"],
                    "actual_away": r["away_goals"],
                    "p_h": m.p_home_win,
                    "p_d": m.p_draw,
                    "p_a": m.p_away_win,
                    # §20's own columns, so CONF can be calibration-tested
                    # here rather than only inspected live. Without p_score
                    # the confidence rule cannot be reproduced offline at
                    # all — it reads both probabilities, not just the result.
                    "p_result": p_result,
                    "p_score": p_score,
                    "confidence": confidence_v1(p_result, p_score, cfg),
                }
            )

    predictions = pd.DataFrame(rows)
    return WalkForwardResult(predictions, evaluate(predictions), n_refits)


def run_favourite_baseline(
    matches: pd.DataFrame, test_mask: pd.Series, closing: bool = False
) -> WalkForwardResult | None:
    """Market favourite baseline: margin-normalised bookmaker probabilities
    pick the favourite result; scoreline via the explicit fixed mapping in
    FAVOURITE_SCORELINES (a 1X2 market has no exact-score opinion).

    closing=False → MARKET-AVAILABLE (earlier price set),
    closing=True  → MARKET-ORACLE (closing odds).
    Returns None when the test slice has no such odds."""
    cols = ["odds_ch", "odds_cd", "odds_ca"] if closing else ["odds_h", "odds_d", "odds_a"]
    test = matches[test_mask].dropna(subset=cols)
    if test.empty:
        return None
    inv = 1.0 / test[cols].to_numpy(dtype=float)
    probs = inv / inv.sum(axis=1, keepdims=True)  # simple margin removal (§32.1)
    fav = np.array(["H", "D", "A"])[probs.argmax(axis=1)]
    picks = [FAVOURITE_SCORELINES[f] for f in fav]

    predictions = pd.DataFrame(
        {
            "date": test["date"].to_numpy(),
            "home_team": test["home_team"].to_numpy(),
            "away_team": test["away_team"].to_numpy(),
            "pred_home": [p[0] for p in picks],
            "pred_away": [p[1] for p in picks],
            "actual_home": test["home_goals"].to_numpy(),
            "actual_away": test["away_goals"].to_numpy(),
            "p_h": probs[:, 0],
            "p_d": probs[:, 1],
            "p_a": probs[:, 2],
        }
    )
    return WalkForwardResult(predictions, evaluate(predictions), 0)


def compare(
    matches: pd.DataFrame,
    test_season: int,
    xi: float | None = None,
    cfg: ModelConfig = DEFAULT_MODEL,
) -> pd.DataFrame:
    """Run the §28 ablation table for one held-out season."""
    test_mask = matches["season"] == test_season
    rows = []

    score_note = "+2-1/1-1/1-2"  # explicit fixed-score convention
    available = run_favourite_baseline(matches, test_mask, closing=False)
    if available is not None:
        rows.append(available.metrics.as_row(f"MARKET-AVAILABLE fav {score_note}"))
    oracle = run_favourite_baseline(matches, test_mask, closing=True)
    if oracle is not None:
        rows.append(oracle.metrics.as_row(f"MARKET-ORACLE (closing) fav {score_note}"))

    poisson = run_model(matches, test_mask, xi=xi, fix_rho_zero=True, cfg=cfg)
    rows.append(poisson.metrics.as_row("poisson (rho=0)"))

    dc = run_model(matches, test_mask, xi=xi, fix_rho_zero=False, cfg=cfg)
    rows.append(dc.metrics.as_row("dixon-coles (pooled)"))

    dc_div = run_model(
        matches, test_mask, xi=xi, fix_rho_zero=False, per_division=True, cfg=cfg
    )
    rows.append(dc_div.metrics.as_row("dixon-coles (per-division)"))

    # §28 next rung: per-division DC + §30 Elo covariate (θ fitted vs θ=0).
    dc_div_elo = run_model(
        matches, test_mask, xi=xi, per_division=True, use_elo=True, cfg=cfg
    )
    rows.append(dc_div_elo.metrics.as_row("dixon-coles (per-div + elo)"))

    # The champion: current adopted configuration (VALIDATION.md).
    if cfg.market_blend_weight is not None:
        promoted = None
        label = f"CHAMPION per-div DC + market w={cfg.market_blend_weight}"
        if cfg.use_promoted_priors:
            train_seasons = matches["season"] < test_season
            promoted = priors.estimate(matches[train_seasons])
            label += " + §29.4 priors"
        if cfg.season_decay != 1.0:
            label += f" + season_decay={cfg.season_decay}"
        champion = run_model(
            matches,
            test_mask,
            xi=xi,
            per_division=True,
            market_blend=cfg.market_blend_weight,
            promoted_priors=promoted,
            season_decay=cfg.season_decay,
            cfg=cfg,
        )
        rows.append(champion.metrics.as_row(label))

    return pd.DataFrame(rows)


def tune_xi(
    matches: pd.DataFrame,
    validation_season: int,
    cfg: ModelConfig = DEFAULT_MODEL,
) -> pd.DataFrame:
    """§29.2 outer loop: score each candidate ξ on a held-out season and
    report the out-of-sample metrics; ξ is selected here, never by MLE."""
    test_mask = matches["season"] == validation_season
    rows = []
    for xi in cfg.xi_grid:
        result = run_model(matches, test_mask, xi=xi, cfg=cfg)
        row = result.metrics.as_row(f"xi={xi}")
        row["xi"] = xi
        # half-life = ln2/ξ: "matches lose half their influence after N
        # days" is the model statement; ξ is just its encoding.
        row["half-life(d)"] = round(np.log(2) / xi) if xi > 0 else np.inf
        rows.append(row)
    return pd.DataFrame(rows)


def run_frozen_model(
    matches: pd.DataFrame,
    test_season: int,
    cfg: ModelConfig = FROZEN_PHASE1_V1,
) -> WalkForwardResult:
    """PHASE1_MODEL_V1 exactly as frozen — the configuration defined in
    MANIFEST.md, pinned to FROZEN_PHASE1_V1 so later Phase 2+ edits to
    DEFAULT_MODEL cannot make this arm drift. No tuning knobs are exposed
    here on purpose."""
    test_mask = matches["season"] == test_season
    promoted = None
    if cfg.use_promoted_priors:
        promoted = priors.estimate(matches[matches["season"] < test_season])
    return run_model(
        matches,
        test_mask,
        xi=cfg.xi,
        per_division=True,
        use_elo=False,
        market_blend=cfg.market_blend_weight,
        promoted_priors=promoted,
        season_decay=cfg.season_decay,
        cfg=cfg,
    )


def run_final_test_rows(matches: pd.DataFrame, test_season: int) -> list[dict]:
    """The frozen-model-vs-market comparison table for `bym final-test`.

    Shared by the CLI and the web UI so the two can never compute this
    differently — the single-use discipline this protects (VALIDATION.md)
    would be worthless if a UI shortcut quietly used a different pipeline
    from the one that produced the recorded numbers.
    """
    test_mask = matches["season"] == test_season
    rows = []
    for label, closing in [("MARKET-AVAILABLE", False), ("MARKET-ORACLE", True)]:
        baseline = run_favourite_baseline(matches, test_mask, closing=closing)
        if baseline is not None:
            rows.append(baseline.metrics.as_row(f"{label} fav +2-1/1-1/1-2"))
    frozen = run_frozen_model(matches, test_season)
    rows.append(frozen.metrics.as_row("PHASE1_MODEL_V1 (frozen)"))
    return rows


def tune_market(
    matches: pd.DataFrame,
    validation_season: int,
    cfg: ModelConfig = DEFAULT_MODEL,
) -> pd.DataFrame:
    """§32 blend-weight selection on a held-out season: per-division DC,
    Option B reweighting against MARKET-AVAILABLE odds, weight grid
    including the pure-model (0) and pure-market-result (1) endpoints."""
    test_mask = matches["season"] == validation_season
    rows = []
    for w in cfg.market_blend_grid:
        result = run_model(
            matches, test_mask, per_division=True, market_blend=w, cfg=cfg
        )
        row = result.metrics.as_row(f"market_w={w}")
        row["weight"] = w
        rows.append(row)
    return pd.DataFrame(rows)
