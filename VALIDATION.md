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
| 2026-08-08 | **PROTOCOL BREACH — spent holdout re-run and grid-searched. NO modelling change made.** | Logged below. Model untouched: `FROZEN_PHASE1_V1` unchanged, no ξ/blend/ablation decision taken |
| 2026-08-08 | Market blend weight RE-EXAMINED on legitimate seasons, w=0.5 RETAINED | 2023/24 favours w=1.0 (8.35 vs 8.28), 2024/25 favours w=0.5 (8.24 vs 8.14). Seasons disagree; mean pts/round over w∈[0.5,1.0] spans 0.04 — noise. Pure model (w=0) is clearly worst (7.68 mean). No basis to move a frozen parameter |
| 2026-08-08 | Market-only fallback (w=1.0) ADOPTED for fixtures outside the corpus; unpriced ones get no forecast | Not a model change — in-corpus fixtures byte-identical, `FROZEN_PHASE1_V1` unmodified. Unvalidatable here: the corpus contains zero cross-division matches. Rests on the three-season w-flatness plus a structural argument; must be judged prospectively on 2026/27. Detail below |
| 2026-08-08 | CONF v1 bands CHECKED and found sound — no change | Pooled 2,796 fixtures: HIGH 1.892 pts (stated 67.9%, realised 72.6%), MEDIUM 1.375 (54.8/53.4), LOW 1.212 (44.3/44.5). Monotone in points, calibrated within 1.4pp on MEDIUM and 0.3pp on LOW. An earlier claim that the bands were inverted was an analysis error — see below |

## Market-only fallback for out-of-corpus fixtures, 2026-08-08

Super 6 rounds include FA Cup ties a few times a season. Those can pair a
corpus team with one the model has never seen, and PHASE1_MODEL_V1 then
issues the league-average prior — which does not merely add uncertainty,
it can make a lower-division side favourite. Refusing to show a pick was
honest but left the user with a blank on a weekend they must still enter
six scores.

Such a fixture is now forecast at **w=1.0** when odds exist: result
probabilities entirely from the market, within-result scoreline shape from
the matrix. The split is deliberate — for an unknown team it is the
relative strength that is broken, not the shape of a 1-0 versus a 2-0
given a home win. Labelled `MARKET_ONLY_FALLBACK`, never PHASE1_MODEL_V1.
With no odds the basis is `NONE` and no pick is shown.

**This is not a model change.** Fixtures inside the corpus are untouched
and byte-identical (pinned by test). `FROZEN_PHASE1_V1` is unmodified. The
only path that changes is one that previously produced either a knowingly
wrong number or nothing.

**It cannot be validated on this corpus.** Football-Data.co.uk ships one
file per division-season, so every one of the 17,630 matches is
within-division: there is not a single cross-division fixture to hold out.
The w=1.0 choice rests on the three-season evidence that w∈[0.5,1.0] is
flat on Super 6 points while log loss mildly favours more market weight,
plus the structural argument above — not on a measurement of this case.

It must therefore be judged **prospectively on 2026/27**, like any other
decision touching w. Note the constraint compounds: 2025/26 is already
spent and grid-searched, so it cannot be used to revisit this either.

Known limitations, recorded rather than discovered later:

* The trigger is an *unknown team*, not *a cup match*. An FA Cup tie
  between two corpus teams passes the check and is forecast exactly as if
  it were a league game, with squad rotation unmodelled. `Fixture` has no
  competition field, so the system cannot tell those two cases apart.
* The fallback needs odds, and The Odds API publishes **no National
  League feed**. An EC club drawn against a corpus team therefore cannot
  be priced from the approved source at all, and falls through to
  `NONE` — no forecast — however the cup pass is configured. FA Cup and
  EFL Cup feeds exist and are fetched; League One and League Two are
  mapped too.

## Protocol breach, 2026-08-08

The 2025/26 holdout was re-run and a market-blend weight grid
(w ∈ {0, .25, .5, .75, 1}) was evaluated **on it**, during an
investigation into cup-fixture accuracy. The protocol above forbids this:
a spent holdout may not be re-tested, and no modelling choice may react to
its results.

What was looked at: frozen-model metrics for 2025/26; the full §28
ablation table on 2025/26; the w-grid on 2025/26; per-band CONF outcomes
and p_result calibration on 2025/26.

**No modelling change was made as a result, and none may now be justified
by it.** `FROZEN_PHASE1_V1` is byte-identical, no ablation decision was
taken, and the two decisions logged above were made on 2023/24 and 2024/25
evidence only. The sole code change from this work is additive backtest
instrumentation (`p_result`, `p_score`, `confidence` columns), which does
not touch prediction semantics.

Reruns reported 7.45 pts/round against the 7.47 on record. That gap is a
reproducibility observation belonging to the v1.0.1 determinism entry
above, not a modelling signal: 0.02 pts/round is immaterial beside the
±0.5-ish uncertainty already established for this measurement.

Descriptively, 2025/26 was materially less predictable for both the frozen
model and the market benchmarks than 2023/24 or 2024/25. Recorded as an
observation only — the evidence does not establish why.

The lasting cost is that 2025/26 is now not merely spent but
*grid-searched*, so it can never serve as evidence for any blend-weight or
confidence-threshold choice, even indirectly. Treat any future proposal on
those two parameters as requiring 2026/27 prospective evidence.

### Analysis error that prompted it, recorded so it is not repeated

An interim finding claimed the CONF bands were inverted (MEDIUM scoring
below LOW) and that probabilities were systematically overdispersed. Both
were artefacts. `confidence_v1` gates on `p_result` **and** `p_score`, but
the backtest frame stored only H/D/A marginals, so the analysis
reimplemented the rule from `p_result` alone — placing 160 fixtures in
HIGH for 2025/26 where the real rule places 69. Pooling three seasons with
the real rule shows the bands are monotone and well calibrated.

The instrumentation added to `walk_forward.run_model` exists so this class
of error is not available again: §20's CONF is now calibration-testable
offline instead of only reproducible by hand, with
`test_predictions_carry_the_columns_conf_calibration_needs` pinning it.

## Holdout status

```text
2025/26   SPENT (2026-08-07), GRID-SEARCHED (2026-08-08) — unusable
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
