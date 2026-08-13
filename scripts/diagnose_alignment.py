"""Check why the paired analysis reports a slightly different model mean
than the frozen final-test run (7.448 vs 7.468 pts/round). Diagnostic
only — no model configuration is varied."""

from pathlib import Path

import pandas as pd

from bym.backtest.walk_forward import run_favourite_baseline, run_frozen_model
from bym.data.football_data import load_matches

TEST_SEASON = 2025
matches = load_matches(Path("data"), list(range(2019, TEST_SEASON + 1)))
test_mask = matches["season"] == TEST_SEASON

model = run_frozen_model(matches, TEST_SEASON).predictions
market = run_favourite_baseline(matches, test_mask, closing=False).predictions

for name, df in [("model", model), ("market", market)]:
    key = df["date"].astype(str) + "|" + df["home_team"] + "|" + df["away_team"]
    print(f"{name}: {len(df)} rows, {key.nunique()} unique keys")
    dupes = key[key.duplicated(keep=False)]
    if len(dupes):
        print(f"  duplicated keys ({len(dupes)}):")
        print(df.loc[dupes.index, ["date", "home_team", "away_team"]].to_string())
