# ablation_003 — Elo covariate audition (FROZEN)

Date run: 2026-08-07
Test season: 2024/25 (E0 + E1, 932 fixtures) — validation season
Model: weighted DC per-division, ξ=0.003, vs same + §30 Elo covariate
(pooled sequential Elo, K=20, home adv 60, no MOV multiplier)

| model                       | pts/fixture | pts/round(6) | exact% | result% | brier  | logloss |
|-----------------------------|------------:|-------------:|-------:|--------:|-------:|--------:|
| dixon-coles (per-division)  |      1.3391 |         8.03 |  11.59 |   49.57 | 0.6081 |  1.0127 |
| dixon-coles (per-div + elo) |      1.3391 |         8.03 |  11.59 |   49.57 | 0.6078 |  1.0127 |

Verdict: **NOT PROMOTED** (§28). Identical picks and points; Brier −0.0003.
With per-division α/β + ξ-decay already encoding team strength, θ carries
no incremental information — precisely the redundancy §30 predicted the
ablation would catch. Code kept behind `use_elo=False` default; may be
re-auditioned if the model family changes (e.g. after market calibration).
