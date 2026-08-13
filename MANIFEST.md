# PHASE1_MODEL_V1 — FROZEN MODEL MANIFEST

Frozen: 2026-08-07, before the 2025/26 holdout was ever evaluated.

This document defines exactly what was frozen. Any change to the
configuration below produces a *different model*, which may not be
evaluated on the 2025/26 holdout — that season is single-use
(VALIDATION.md).

## Configuration

```text
Competition fitting:
  per-division (E0, E1 fitted separately)
  pooled fallback for cross-division / unknown-team fixtures

Recency:
  exponential time-decay
  xi = 0.003
  half-life ~= 231 days

Dixon-Coles:
  enabled (tau low-score correction, rho fitted jointly)
  Elo covariate = DISABLED (theta binned, ablation_003)

Promoted-team prior (§29.4):
  empirical-Bayes Gaussian prior on (alpha, beta)
  keyed by (division, arrival direction)
  direction: 'above' = relegated in, 'below' = promoted in / absent
  estimated from training seasons only, never the evaluation season
  applied as an MLE penalty, so shrinkage fades continuously with evidence

Season-transition shrinkage (§29.5):
  DISABLED (season_decay = 1.0, binned in ablation_005)

Market:
  MARKET-AVAILABLE odds only (never closing odds)
  proportional margin removal (§32.1)
  result-constrained reweighting, §32.3 Option B
  weight = 0.5

Missing odds:
  raw statistical model, no imputation

Tail handling:
  tolerance-based grid growth, tail_tolerance = 1e-5
  proportional renormalisation, no mass folded onto real scorelines

Lambda caps:
  floor 0.05, ceiling 6.0

Refit cadence in backtest:
  every 7 days, training data strictly before each test date
```

All hyperparameters (ξ, market weight, prior settings) were selected using
data up to and including 2024/25 only.

## Entry point

`bym.backtest.walk_forward.run_frozen_model()` takes every
hyperparameter from `config.DEFAULT_MODEL` and exposes no tuning knobs, so
the frozen configuration cannot drift via arguments. The CLI wrapper
`bym final-test` additionally refuses to run without an explicit
single-use acknowledgement flag.

## Provenance

```text
Code:         git tag phase1-model-v1
Git SHA:      6f39b7e359737502b6ffaea6aba793bd97814377
Test suite:   57 tests passing at freeze
Data source:  Football-Data.co.uk (E0, E1), seasons 2019/20-2025/26
Corpus:       6,524 fixtures (5,592 usable for training up to 2024/25)
Holdout:      2025/26, 932 fixtures, untouched at freeze time
```

Data snapshot (SHA-256, first 16 hex chars):

```text
100037618b94f940  data/2019_E0.csv      f31450c623c0b74b  data/2019_E1.csv
5afe63f69401457b  data/2020_E0.csv      cf93295e310c60e5  data/2020_E1.csv
335afcbabeb2939f  data/2021_E0.csv      a181451be4801b9f  data/2021_E1.csv
8442792d3b614c94  data/2022_E0.csv      cd38ab0586ea8cb4  data/2022_E1.csv
b2e057b0ed959f19  data/2023_E0.csv      5737f6cc95c95092  data/2023_E1.csv
d0c8ce4a96d886cf  data/2024_E0.csv      f34c340446c91637  data/2024_E1.csv
3e3a8352f9ada678  data/2025_E0.csv      98954c319950f191  data/2025_E1.csv
```

## Post-freeze amendment: v1.0.1 (determinism only)

The originally tagged `phase1-model-v1` (SHA 6f39b7e) contained a
reproducibility defect: hash-randomised set iteration in
`priors.arrivals()` made the fitted parameters vary marginally between
runs (~0.02 pts/round). Fixed by sorting the iteration order in
`priors.arrivals()` and `dixon_coles.fit()`, tagged `phase1-model-v1.0.1`.

**No model semantics changed** — no parameter, feature, hyperparameter or
data source differs. The 2025/26 holdout was **not** re-run after the fix;
the recorded result stands as produced by the original single-use run
(results/final_test_2025_26.md).

## Validation-season performance (2024/25, selection-biased)

| model | pts/round | exact% | log loss |
|---|---:|---:|---:|
| MARKET-AVAILABLE (fair benchmark) | 8.14 | 11.59 | 1.0064 |
| MARKET-ORACLE (closing odds) | 8.14 | 11.27 | 1.0045 |
| PHASE1_MODEL_V1 | 8.27 | 12.23 | 1.0044 |

These figures are **not** an unbiased estimate: ξ and the market weight
were chosen on this season. The unbiased number comes from 2025/26, run
once, after this freeze.

## Interpretation thresholds agreed in advance

| 2025/26 outcome | Interpretation |
|---|---|
| Blend still beats fair market | Excellent. Phase 1 model validated. |
| Blend ≈ fair market | Still good. Score-shape model adds structure, not clear superiority. |
| Blend trails market slightly | Useful system, §24 target not demonstrated out-of-sample. |
| Blend materially below DC/market | Validation overfit. Reassess — do not tune against 2025/26. |

Recording these before seeing the result is the point: it prevents the
outcome from retroactively selecting its own interpretation.
