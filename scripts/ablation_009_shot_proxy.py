"""§7: does a zero-cost shots-based xG stand-in improve the forecast?

Real xG is unavailable to this project: every free source either omits
the Championship (59% of the corpus) or lost its feed when Opta cut
FBref off in January 2026, and the paid options breach §25's GBP10
target. See docs/next_steps.md item 4.

Shots and shots on target ARE in the free corpus at 100% coverage for
both divisions. The thesis under test is the one that motivates xG
generally: goals are a noisy realisation of underlying performance, so
fitting team strength on a smoother target should estimate it better.
The proxy's variance is 0.77 against actual goals' 1.59, so it is
smoother; whether that helps is what this measures.

The proxy arm is a second Dixon-Coles fit on shot-derived expected
goals, blended at the lambda level so the champion's own rho still
shapes the matrix. It is fitted with fix_rho_zero=True because tau is
undefined on a continuous target — stated here rather than discovered
later.

Run on the 2024/25 HYPERPARAMETER VALIDATION season per VALIDATION.md.
Not on 2025/26: that holdout was spent on 2026-08-07.

    .venv/bin/python scripts/ablation_009_shot_proxy.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

from super6.backtest.walk_forward import run_model
from super6.data.football_data import load_matches
from super6.model import priors
from super6.scoring import super6_points

TEST_SEASON = 2024
W = 0.5  # the promoted 1X2 blend weight (ablation_004)
N_BOOT = 20000
RNG = np.random.default_rng(20260810)

matches = load_matches(Path("data"), list(range(2019, TEST_SEASON + 1)))
test_mask = matches["season"] == TEST_SEASON
promoted = priors.estimate(matches[matches["season"] < TEST_SEASON])

CHAMPION = {"per_division": True, "market_blend": W, "promoted_priors": promoted}

ARMS = {
    "champion": {},
    "shots w=0.15": {"shot_blend": 0.15},
    "shots w=0.25": {"shot_blend": 0.25},
    "shots w=0.50": {"shot_blend": 0.50},
    "shots w=0.75": {"shot_blend": 0.75},
    "shots w=1.00": {"shot_blend": 1.00},
}


def result_log_loss(preds: pd.DataFrame) -> np.ndarray:
    """Per-fixture -ln P(actual result)."""
    home, away = preds["actual_home"], preds["actual_away"]
    p = np.where(home > away, preds["p_h"], np.where(home < away, preds["p_a"], preds["p_d"]))
    return -np.log(np.clip(p.astype(float), 1e-15, 1.0))


points: dict[str, pd.Series] = {}
losses: dict[str, pd.Series] = {}
for label, extra in ARMS.items():
    result = run_model(matches, test_mask, **CHAMPION, **extra)
    preds = result.predictions.sort_values(
        ["date", "home_team", "away_team"]
    ).reset_index(drop=True)
    key = preds["date"].astype(str) + "|" + preds["home_team"] + "|" + preds["away_team"]
    pts = preds.apply(
        lambda r: super6_points(
            r["pred_home"], r["pred_away"], r["actual_home"], r["actual_away"]
        ),
        axis=1,
    )
    points[label] = pd.Series(pts.to_numpy(), index=key)
    losses[label] = pd.Series(result_log_loss(preds), index=key)
    m = result.metrics
    print(
        f"{label:18s} {pts.mean() * 6:6.3f} pts/round  "
        f"exact {m.exact_score_rate * 100:5.2f}%  result {m.result_accuracy * 100:5.2f}%  "
        f"logloss {m.log_loss:6.4f}  brier {m.brier:6.4f}"
    )

base, base_loss = points["champion"], losses["champion"]
print("\nPaired points difference vs champion (per round of 6, 95% bootstrap CI):")
for label in ARMS:
    if label == "champion":
        continue
    d = (points[label].reindex(base.index) - base).to_numpy(dtype=float)
    boot = RNG.choice(d, size=(N_BOOT, len(d)), replace=True).mean(axis=1) * 6
    lo, hi = np.percentile(boot, [2.5, 97.5])
    print(
        f"  {label:18s} mean {d.mean() * 6:+.3f}  CI [{lo:+.3f}, {hi:+.3f}]  "
        f"changed {int((d != 0).sum()):4d}/{len(d)}  "
        f"{'SIGNIFICANT' if lo > 0 else 'not significant'}"
    )

print("\nPaired log-loss difference vs champion (negative = better, 95% CI):")
for label in ARMS:
    if label == "champion":
        continue
    d = (losses[label].reindex(base_loss.index) - base_loss).to_numpy(dtype=float)
    boot = RNG.choice(d, size=(N_BOOT, len(d)), replace=True).mean(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    verdict = "BETTER" if hi < 0 else ("WORSE" if lo > 0 else "indistinguishable")
    print(f"  {label:18s} mean {d.mean():+.5f}  CI [{lo:+.5f}, {hi:+.5f}]  {verdict}")
