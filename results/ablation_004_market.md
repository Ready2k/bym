# ablation_004 — §32 market calibration (FROZEN)

Date run: 2026-08-07
Test season: 2024/25 (E0 + E1, 932 fixtures) — validation season
Model: per-division weighted DC (ξ=0.003) + §32.3 Option B
result-constrained reweighting against MARKET-AVAILABLE odds,
proportional margin removal (§32.1).

| model         | pts/fixture | pts/round(6) | exact% | result% | brier  | logloss |
|---------------|------------:|-------------:|-------:|--------:|-------:|--------:|
| market_w=0.0  |      1.3391 |         8.03 |  11.59 |   49.57 | 0.6081 |  1.0127 |
| market_w=0.25 |      1.3552 |         8.13 |  11.91 |   49.89 | 0.6045 |  1.0065 |
| market_w=0.5  |      1.3734 |         8.24 |  12.02 |   50.64 | 0.6024 |  1.0043 |
| market_w=0.75 |      1.3509 |         8.11 |  11.70 |   50.00 | 0.6020 |  1.0044 |
| market_w=1.0  |      1.3562 |         8.14 |  11.80 |   50.11 | 0.6031 |  1.0064 |

Verdict: **PROMOTED, w=0.5**. Genuine interior peak:
- beats pure model (w=0) by +0.21 pts/round and pure market result
  probabilities (w=1) by +0.10 — §28's "market blend vs market alone"
  answered: neither input is redundant;
- first configuration to beat the MARKET-AVAILABLE benchmark on points
  (8.24 vs 8.14) and exact% (12.02 vs 11.59);
- log loss 1.0043 marginally better than even MARKET-ORACLE closing odds
  (1.0045).

Reference (same season): MARKET-AVAILABLE fav 8.14 pts/round / 11.59
exact% / logloss 1.0064; MARKET-ORACLE 8.14 / 11.27 / 1.0045.

Caveats at freezing: single validation season; w selected on the same
season it is reported on (biased upward by selection); no paired
significance test yet. The honest number comes from the untouched 2025/26
final test after the model design freezes (VALIDATION.md).
