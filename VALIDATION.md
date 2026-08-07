# Validation Protocol

Binding rules for how seasons are used. This exists so that no modelling
decision quietly contaminates the data it is later judged on (§16, §22).

## Season roles

```text
2019/20 ── 2023/24   TRAINING / DEVELOPMENT
                     fit parameters, explore, debug freely

2024/25              HYPERPARAMETER VALIDATION
                     ξ selection, ablation decisions (Elo, market,
                     per-division, …), any modelling choice.
                     Its scores are NOT unbiased evidence — every choice
                     made by looking at them biases them upward.

                     ── FREEZE MODEL ──

2025/26              FINAL UNTOUCHED TEST
                     run once, after the model design is frozen.
                     Weekly refitting inside the season is fine (each
                     prediction uses only data before that fixture), but
                     NO ξ change, ablation decision, or modelling choice
                     may react to its results.
```

The claim this protects: *"this model design was chosen without seeing the
season on which we report its performance."*

## Benchmarks

- **MARKET-AVAILABLE** — Football-Data's earlier price set (weekend prices
  generally collected Friday afternoon). The fair, deployable benchmark:
  approximately what was knowable at a Super 6 deadline. **This is the
  target to beat (§24).**
- **MARKET-ORACLE** — closing odds (`C` columns, 2019/20 onward). Contains
  information up to each kickoff — for late kickoffs, hours *after* the
  Super 6 deadline locked. Theoretical gold standard; consistently losing
  to it is expected and not a failure. Once live, our own timestamped odds
  snapshots (§6, §19 `odds_snapshots`) replace this approximation properly.

Both market baselines price H/D/A only. Their exact-score pick is the
explicit fixed convention `H→2-1, D→1-1, A→1-2` (labelled as such in
results); their Brier/log loss are the real market metrics, their points
and exact% are "market result + naive score heuristic".

## Decisions taken on the validation season (2024/25)

| Date | Decision | Evidence |
|---|---|---|
| 2026-08-07 | ξ = 0.003 (~231-day half-life) | log loss plateau 0.002–0.005 (1.0239/1.0213/1.0213); pts peak 0.002–0.003; stable midpoint chosen, per-curve not per-winner |
| 2026-08-07 | Per-division fits adopted (separate μ/γ/α/β/ρ for E0 and E1; pooled fallback for cross-division fixtures) | ablation_002: 8.03 vs 7.91 pts/round, exact% 11.59 vs 11.27, log loss 1.0127 vs 1.0213. Provisional caveat: single validation season, no paired significance test yet — revisit if the Elo-round ablation contradicts it |
| 2026-08-07 | Elo covariate NOT promoted (θ redundant once per-division α/β + ξ-decay exist) | ablation_003: identical picks/points, Brier −0.0003. Kept behind `use_elo=False`; re-audition if model family changes |
| 2026-08-07 | Market calibration PROMOTED: §32.3 Option B result-constrained reweighting vs MARKET-AVAILABLE odds, blend weight w=0.5 | ablation_004: interior peak — 8.24 pts/round vs 8.03 (pure model) and 8.14 (pure market result); exact% 12.02; log loss 1.0043. First config to beat the fair market benchmark |
| 2026-08-07 | §29.4 promoted-team priors ADOPTED (on correctness grounds, not a significant win) | ablation_005: +0.026 pts/round, paired CI [-0.167,+0.225] — NOT significant. Adopted because the zero prior it replaces is measurably wrong (E0-from-below α=−0.278), and §28 removes components that make things worse, which this does not |
| 2026-08-07 | §29.5 season decay BINNED (season_decay=1.0) | ablation_005: 0.6 reproduces baseline exactly, 0.8 fails paired test. ξ=0.003 already discharges §29.5's intent |
| 2026-08-07 | **MODEL FROZEN — PHASE1_MODEL_V1** (see MANIFEST.md) | No further modelling changes before the 2025/26 holdout runs |
| 2026-08-07 | **HOLDOUT SPENT — 2025/26 evaluated once** | 7.47 pts/round vs fair market 7.42; paired CI [-0.470, +0.541], P(ahead) 0.543 → statistically indistinguishable from market. §24 target not demonstrated, not refuted. results/final_test_2025_26.md |
| 2026-08-07 | Determinism fix (v1.0.1), holdout NOT re-run | Hash-randomised set iteration made runs vary ~0.02 pts/round; no model semantics changed |

## Holdout status

```text
2025/26   SPENT (2026-08-07)
```

There is no unspent holdout season remaining. Any future modelling change
— including any response to the calibration result above — must be
developed on data up to 2024/25 and tested on a genuinely new season
(2026/27 as it is played), never re-tested on 2025/26.

2026/27 is therefore **prospective evaluation only**: picks recorded
before each deadline, scored afterwards, never reconstructed. Protocol and
arms in results/prospective_2026_27.md.

Every future ablation decision (Elo θ, market blending, per-division,
promoted-team priors, …) gets appended to this table.

## Frozen historical results

Never overwritten — see `results/`. `results/baseline_001.md` is the first
ever backtest of the deliberately boring model, kept for comparison with
whatever the production system becomes.
