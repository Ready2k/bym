"""Final pre-freeze ablation (VALIDATION.md): §29.4 promoted priors and
§29.5 season-decay against the current champion, on the 2024/25 validation
season. Run from the repo root:

    .venv/bin/python scripts/ablation_005_prefreeze.py
"""

from pathlib import Path

import pandas as pd

from super6.backtest.walk_forward import run_model
from super6.config import DEFAULT_MODEL
from super6.data.football_data import load_matches
from super6.model import priors

TEST_SEASON = 2024
W = 0.5

matches = load_matches(Path("data"), list(range(2019, TEST_SEASON + 1)))
test_mask = matches["season"] == TEST_SEASON

# §29.4: estimate priors on training seasons only.
train = matches[matches["season"] < TEST_SEASON]
promoted = priors.estimate(train)
print("Estimated §29.4 priors (division, direction):")
for key, spec in sorted(promoted.by_key.items()):
    print(
        f"  {key}: alpha {spec.mean_alpha:+.3f} (var {spec.var_alpha:.3f}), "
        f"beta {spec.mean_beta:+.3f} (var {spec.var_beta:.3f})"
    )
print()

rows = []

def arm(label, **kwargs):
    result = run_model(
        matches, test_mask, per_division=True, market_blend=W, **kwargs
    )
    rows.append(result.metrics.as_row(label))
    print(pd.DataFrame([rows[-1]]).to_string(index=False, header=(len(rows) == 1)))

arm("champion (zero prior)")
arm("champion + §29.4 priors", promoted_priors=promoted)
for s in DEFAULT_MODEL.season_decay_grid[1:]:
    arm(f"champion + season_decay={s}", season_decay=s)
arm(
    "champion + priors + best-decay-if-any placeholder",
    promoted_priors=promoted,
    season_decay=DEFAULT_MODEL.season_decay_grid[1],
)

print("\nFULL TABLE")
print(pd.DataFrame(rows).to_string(index=False))
