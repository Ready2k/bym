# FPL BASELINE_FPL_001 — Phase 1B Optimiser Stability

This is an algorithmic stress test, not a historical transfer backtest. The project has no permitted historical FPL price/squad snapshots, so it cannot infer realised market-transfer performance from player stats alone.

## Legitimate baseline comparison

| Strategy | Evaluated expected points | Third forward | Reliable DEF cover |
|---|---:|---:|---:|
| Single nominal solve (`top_k=1`) | 32.097 | yes | no |
| Contingency-aware search | 32.598 | no | yes |

The wider search changes 2 XI/captain decision(s) from the nominal-only baseline and selects the reliable-cover squad in this constructed adverse case.

## Search-width stability

| K | Evaluated xP | Same squad as widest search? | XI/captain differences vs widest |
|---:|---:|---|---:|
| 1 | 32.097 | no | 2 |
| 5 | 32.377 | no | 0 |
| 10 | 32.598 | yes | 0 |
| 25 | 32.598 | yes | 0 |
| 50 | 32.598 | yes | 0 |
| 100 | 32.598 | yes | 0 |

On this canonical case, the choice first matches the K=100 search at **K=10**.

## Boundary of this evidence

This supports the need for contingency-aware top-K enumeration and records its deterministic behaviour. It does **not** justify a generalised live transfer-performance claim or a new default K on its own; that would require permitted, historical FPL price and squad snapshots.
