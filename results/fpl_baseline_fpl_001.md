# FPL BASELINE_FPL_001 — Phase 1B Fixture Evaluation

Run: 2026-08-31, using the completed 2022/23–2024/25 API-Football Premier League player-stat corpus and the frozen FPL baseline. The source fixture-content fingerprint is `669542301254be8970ba40ae778105c67e669cdcea9c04dffdf8c154974e7b92`.

This is a causal, fixture-level diagnostic of the frozen baseline, not a claim of full official-FPL scoring calibration. Bonus, DefCon and other omitted components remain excluded by the baseline manifest.

- Fixtures evaluated: 1,140
- Player-fixture predictions: 62,700
- Transfer/unknown-team players excluded: 37
- Same-calendar-day match results excluded from every training slice.

## Overall

| Metric | Value |
|---|---:|
| MAE | 1.190 |
| Mean predicted points | 1.474 |
| Mean actual component points | 1.496 |
| Calibration gap | -0.022 |
| Minutes-component MAE | 0.508 |
| Goal/assist/save-component MAE | 0.622 |
| Clean-sheet-component MAE | 0.332 |

## By position

| Position | n | MAE | Calibration gap |
|---|---:|---:|---:|
| DEF | 20,444 | 1.309 | -0.001 |
| FWD | 13,148 | 1.206 | +0.012 |
| GK | 4,674 | 1.347 | -0.024 |
| MID | 24,434 | 1.051 | -0.058 |

## Minutes calibration

| Measure | Predicted | Actual | Gap |
|---|---:|---:|---:|
| Appearance | 0.519 | 0.533 | -0.014 |
| 60+ minutes | 0.361 | 0.369 | -0.009 |

## Interpretation

This run is descriptive evidence for the frozen baseline, not a promotion gate. No model parameter, component, or optimiser setting was selected from these results. The next Phase 1B increment is the pre-declared optimiser-baseline/stability evaluation, followed by prospective immutable recommendation snapshots.
