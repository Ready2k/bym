"""§6/§32: do the totals and Asian-handicap markets improve the score matrix?

docs/next_steps.md item 3 claimed no historical odds existed for these
markets. They were in the Football-Data corpus the whole time at ~100%
coverage, pre-match and closing — the parser simply never read the
columns.

Why they might matter more than the 1X2 blend already adopted: a result
price says nothing about how a result's probability splits across
scorelines, which is why §32.3 Option B has to preserve the model's own
within-result shape. A total prices goal count and a handicap prices
supremacy — both directly about the scoreline, which is what Super 6 is
scored on.

Run on the 2024/25 HYPERPARAMETER VALIDATION season per VALIDATION.md.
Not on 2025/26: that holdout was spent on 2026-08-07.

    .venv/bin/python scripts/ablation_008_totals_and_handicap.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

from bym.backtest.walk_forward import run_model
from bym.data.football_data import load_matches
from bym.model import priors
from bym.scoring import super6_points

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
    # Each market on its own first, at three weights, so any change can be
    # attributed to one market rather than to "more market".
    "totals w=0.25": {"totals_blend": 0.25},
    "totals w=0.50": {"totals_blend": 0.50},
    "totals w=0.75": {"totals_blend": 0.75},
    "handicap w=0.25": {"handicap_blend": 0.25},
    "handicap w=0.50": {"handicap_blend": 0.50},
    "handicap w=0.75": {"handicap_blend": 0.75},
    # Then together, in case they constrain complementary marginals.
    "both w=0.50": {"totals_blend": 0.50, "handicap_blend": 0.50},
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
