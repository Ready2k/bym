"""Uncertainty on the 2025/26 holdout headline comparison.

This does NOT try alternative model configurations on the holdout — that
would be tuning against it. It re-runs exactly the two arms already
reported (frozen model, MARKET-AVAILABLE baseline) and quantifies whether
the observed gap is distinguishable from noise, which is part of reading
the result honestly rather than part of choosing a model.

    .venv/bin/python scripts/final_test_paired_analysis.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

from super6.backtest.walk_forward import run_favourite_baseline, run_frozen_model
from super6.data.football_data import load_matches
from super6.scoring import super6_points

TEST_SEASON = 2025
N_BOOT = 20000
RNG = np.random.default_rng(20260807)

matches = load_matches(Path("data"), list(range(2019, TEST_SEASON + 1)))
test_mask = matches["season"] == TEST_SEASON


def keyed_points(preds: pd.DataFrame) -> pd.Series:
    preds = preds.sort_values(["date", "home_team", "away_team"]).reset_index(drop=True)
    key = preds["date"].astype(str) + "|" + preds["home_team"] + "|" + preds["away_team"]
    pts = preds.apply(
        lambda r: super6_points(
            r["pred_home"], r["pred_away"], r["actual_home"], r["actual_away"]
        ),
        axis=1,
    )
    return pd.Series(pts.to_numpy(), index=key)


model = keyed_points(run_frozen_model(matches, TEST_SEASON).predictions)
market = keyed_points(
    run_favourite_baseline(matches, test_mask, closing=False).predictions
)

common = model.index.intersection(market.index)
d = (model.reindex(common) - market.reindex(common)).to_numpy(dtype=float)
boot = RNG.choice(d, size=(N_BOOT, len(d)), replace=True).mean(axis=1) * 6
lo, hi = np.percentile(boot, [2.5, 97.5])

print(f"Paired fixtures: {len(common)}")
print(f"Frozen model:      {model.reindex(common).mean() * 6:.3f} pts/round")
print(f"MARKET-AVAILABLE:  {market.reindex(common).mean() * 6:.3f} pts/round")
print(f"\nPaired difference: {d.mean() * 6:+.3f} pts/round")
print(f"95% bootstrap CI:  [{lo:+.3f}, {hi:+.3f}]")
print(f"Fixtures where picks differ: {int((d != 0).sum())}")
print(f"P(model better on a random resample): {(boot > 0).mean():.3f}")
