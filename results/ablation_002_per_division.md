# ablation_002 — corrected benchmarks + per-division fits (FROZEN)

Date run: 2026-08-07
Test season: 2024/25 (E0 + E1, 932 fixtures) — **validation season**, see
VALIDATION.md; not unbiased evidence for the tuned model.
Training: 2019/20–2023/24 (walk-forward, weekly refits, no leakage)
Model: weighted Dixon-Coles, ξ=0.003 (selected on this same season)

| model                                    | pts/fixture | pts/round(6) | exact% | result% | brier  | logloss |
|------------------------------------------|------------:|-------------:|-------:|--------:|-------:|--------:|
| MARKET-AVAILABLE fav +2-1/1-1/1-2        |      1.3562 |         8.14 |  11.59 |   50.43 | 0.6031 |  1.0064 |
| MARKET-ORACLE (closing) fav +2-1/1-1/1-2 |      1.3573 |         8.14 |  11.27 |   50.97 | 0.6012 |  1.0045 |
| poisson (rho=0)                          |      1.3187 |         7.91 |  11.27 |   49.03 | 0.6128 |  1.0215 |
| dixon-coles (pooled)                     |      1.3187 |         7.91 |  11.27 |   49.03 | 0.6127 |  1.0213 |
| dixon-coles (per-division)               |      1.3391 |         8.03 |  11.59 |   49.57 | 0.6081 |  1.0127 |

Findings at time of freezing:
- Per-division fitting (+0.12 pts/round vs pooled, exact% ties the market)
  is the largest single improvement so far → adopted as default, logged in
  VALIDATION.md. Caveat: one validation season, no paired significance
  test yet.
- Closing vs Friday odds: Brier 0.6012 vs 0.6031 — the oracle advantage is
  real but small at H/D/A granularity. MARKET-AVAILABLE remains the fair
  target (§24); gap now −0.11 pts/round.
- DC vs Poisson (pooled): still indistinguishable. DC's ρ correction alone
  remains unproven on this corpus (§28) — kept for now because it is also
  not worse and the low-score correction is load-bearing for §32's
  within-result reweighting later; revisit at the Elo ablation.
