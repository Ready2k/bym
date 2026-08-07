# baseline_001 — first ever backtest (FROZEN, never overwrite)

Date run: 2026-08-07
Test season: 2024/25 (E0 + E1, 932 fixtures)
Training: 2019/20–2023/24 (walk-forward, weekly refits, no leakage)
Model: weighted Dixon-Coles, default ξ=0.002 (pre-tuning), pooled divisions
Code: Phase 1A initial implementation (32 tests passing)

| model               | pts/fixture | pts/round(6) | exact% | result% | brier  | logloss |
|---------------------|------------:|-------------:|-------:|--------:|-------:|--------:|
| bookmaker-favourite |      1.3562 |         8.14 |  11.59 |   50.43 | 0.6031 |  1.0064 |
| poisson (rho=0)     |      1.3176 |         7.91 |  11.16 |   49.14 | 0.6141 |  1.0240 |
| dixon-coles         |      1.3208 |         7.92 |  11.27 |   49.14 | 0.6140 |  1.0239 |

Notes at time of freezing:
- "bookmaker-favourite" here used the earlier (non-closing) price set —
  what later became the MARKET-AVAILABLE benchmark — with the fixed-score
  convention H→2-1, D→1-1, A→1-2.
- Gap to market: −0.22 pts/round before Elo, market blending, xG, lineups,
  news, or opponent strategy exist.
- DC vs Poisson: +0.003 pts/fixture ≈ 2.8 points over the whole corpus.
  Classified: DC advantage unproven (§28).
- ξ tuning on 2024/25 (after this run) selected ξ=0.003; from that point
  2024/25 is validation data and this table must not be quoted as unbiased
  performance of the tuned model. 2025/26 is the untouched final test.
