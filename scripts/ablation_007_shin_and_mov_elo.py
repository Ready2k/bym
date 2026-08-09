"""§32.1's two standing ablation candidates: Shin's method and MOV-Elo.

Both were listed in docs/next_steps.md as "simply haven't been run through
the ablation ladder yet" — the training data for each already exists, so
neither was blocked on anything but someone running it.

Run on the 2024/25 HYPERPARAMETER VALIDATION season, per VALIDATION.md.
Not on 2025/26: that holdout was spent on 2026-08-07 and no ablation
decision may react to its results.

Same shape as ablation_006 — paired per-fixture differences against the
champion on identical fixtures, bootstrap CI on the mean. A candidate is
promoted only if its lower bound clears zero (§28, §13.3/§36).

    .venv/bin/python scripts/ablation_007_shin_and_mov_elo.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

from super6.backtest.walk_forward import run_model
from super6.data.football_data import load_matches
from super6.model import priors
from super6.scoring import super6_points

TEST_SEASON = 2024
W = 0.5  # the promoted market blend weight (ablation_004)
N_BOOT = 20000
RNG = np.random.default_rng(20260809)

matches = load_matches(Path("data"), list(range(2019, TEST_SEASON + 1)))
test_mask = matches["season"] == TEST_SEASON
promoted = priors.estimate(matches[matches["season"] < TEST_SEASON])

# The champion is PHASE1_MODEL_V1's configuration: per-division fits,
# promoted priors, market blend at w=0.5, proportional devig, no Elo.
CHAMPION = {"per_division": True, "market_blend": W, "promoted_priors": promoted}

ARMS = {
    "champion": {},
    # §32.1 candidate 1 — Shin's insider-trading margin removal in place of
    # proportional normalisation. Touches only how odds become probabilities.
    "champion+shin": {"devig": "shin"},
    # §32.1 candidate 2 — margin-of-victory Elo. ablation_003 rejected plain
    # Elo as redundant once per-division α/β and ξ-decay exist; this asks
    # whether a *better* rating changes that verdict, so both the plain and
    # MOV variants are run to keep the comparison honest.
    "champion+elo(plain)": {"use_elo": True},
    "champion+elo(mov)": {"use_elo": True, "elo_mov": True},
    # Both candidates together, in case they are individually flat but
    # complementary.
    "champion+shin+elo(mov)": {"devig": "shin", "use_elo": True, "elo_mov": True},
}

def result_log_loss(preds: pd.DataFrame) -> np.ndarray:
    """Per-fixture -ln P(actual result).

    Points are the objective, but for a candidate that only re-weights
    probabilities they are a blunt instrument: a 1pp shift almost never
    flips the argmax pick, so a paired points test on a few changed
    fixtures has close to no power. Log loss moves on every fixture and is
    the metric these candidates actually act on, so it is tested the same
    paired way rather than being read off an aggregate.
    """
    home, away = preds["actual_home"], preds["actual_away"]
    p = np.where(home > away, preds["p_h"], np.where(home < away, preds["p_a"], preds["p_d"]))
    return -np.log(np.clip(p.astype(float), 1e-15, 1.0))


points: dict[str, pd.Series] = {}
losses: dict[str, pd.Series] = {}
metrics: dict[str, object] = {}
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
    metrics[label] = result.metrics
    m = result.metrics
    print(
        f"{label:24s} {pts.mean() * 6:6.3f} pts/round  "
        f"exact {m.exact_score_rate * 100:5.2f}%  "
        f"result {m.result_accuracy * 100:5.2f}%  "
        f"logloss {m.log_loss:6.4f}  brier {m.brier:6.4f}  "
        f"({len(pts)} fixtures)"
    )

base = points["champion"]
print("\nPaired differences vs champion (per round of 6, 95% bootstrap CI):")
for label in ARMS:
    if label == "champion":
        continue
    aligned = points[label].reindex(base.index)
    d = (aligned - base).to_numpy(dtype=float)
    n_changed = int((d != 0).sum())
    boot = RNG.choice(d, size=(N_BOOT, len(d)), replace=True).mean(axis=1) * 6
    lo, hi = np.percentile(boot, [2.5, 97.5])
    print(
        f"  {label:24s} mean {d.mean() * 6:+.3f}  CI [{lo:+.3f}, {hi:+.3f}]  "
        f"fixtures changed: {n_changed:4d}  "
        f"{'SIGNIFICANT' if lo > 0 else 'not significant'}"
    )

base_loss = losses["champion"]
print("\nPaired log-loss differences vs champion (negative = better, 95% CI):")
for label in ARMS:
    if label == "champion":
        continue
    d = (losses[label].reindex(base_loss.index) - base_loss).to_numpy(dtype=float)
    boot = RNG.choice(d, size=(N_BOOT, len(d)), replace=True).mean(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    verdict = "BETTER" if hi < 0 else ("WORSE" if lo > 0 else "indistinguishable")
    print(
        f"  {label:24s} mean {d.mean():+.5f}  CI [{lo:+.5f}, {hi:+.5f}]  "
        f"fixtures moved: {int((d != 0).sum()):4d}  {verdict}"
    )
