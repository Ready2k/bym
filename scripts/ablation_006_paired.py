"""Paired significance test for the final pre-freeze candidates.

Differences of ~0.1 pts/round on 932 fixtures are inside unpaired noise,
so compare the way §13.3/§36 requires for strategic decisions: the same
fixtures, per-fixture paired differences, bootstrap confidence interval on
the mean difference. A candidate is only promoted if its lower confidence
bound clears zero.

    .venv/bin/python scripts/ablation_006_paired.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

from bym.backtest.walk_forward import run_model
from bym.data.football_data import load_matches
from bym.model import priors
from bym.scoring import super6_points

TEST_SEASON = 2024
W = 0.5
N_BOOT = 20000
RNG = np.random.default_rng(20260807)

matches = load_matches(Path("data"), list(range(2019, TEST_SEASON + 1)))
test_mask = matches["season"] == TEST_SEASON
promoted = priors.estimate(matches[matches["season"] < TEST_SEASON])

ARMS = {
    "champion": {},
    "champion+priors": {"promoted_priors": promoted},
    "champion+priors+decay0.8": {"promoted_priors": promoted, "season_decay": 0.8},
}

points = {}
for label, kwargs in ARMS.items():
    preds = run_model(
        matches, test_mask, per_division=True, market_blend=W, **kwargs
    ).predictions
    preds = preds.sort_values(["date", "home_team", "away_team"]).reset_index(drop=True)
    key = preds["date"].astype(str) + "|" + preds["home_team"] + "|" + preds["away_team"]
    pts = preds.apply(
        lambda r: super6_points(
            r["pred_home"], r["pred_away"], r["actual_home"], r["actual_away"]
        ),
        axis=1,
    )
    points[label] = pd.Series(pts.to_numpy(), index=key)
    print(f"{label}: {pts.mean() * 6:.3f} pts/round ({len(pts)} fixtures)")

base = points["champion"]
print("\nPaired differences vs champion (per round of 6, 95% bootstrap CI):")
for label in ["champion+priors", "champion+priors+decay0.8"]:
    aligned = points[label].reindex(base.index)
    d = (aligned - base).to_numpy(dtype=float)
    n_changed = int((d != 0).sum())
    boot = RNG.choice(d, size=(N_BOOT, len(d)), replace=True).mean(axis=1) * 6
    lo, hi = np.percentile(boot, [2.5, 97.5])
    print(
        f"  {label:28s} mean {d.mean() * 6:+.3f}  CI [{lo:+.3f}, {hi:+.3f}]  "
        f"fixtures changed: {n_changed}  "
        f"{'SIGNIFICANT' if lo > 0 else 'not significant'}"
    )
