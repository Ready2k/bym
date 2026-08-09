# ablation_007 — §32.1 Shin's method and MOV-Elo (BOTH REJECTED)

Date run: 2026-08-09
Test season: 2024/25 (E0 + E1, 932 fixtures) — validation season
Champion under test: PHASE1_MODEL_V1's configuration — per-division
weighted DC, ξ=0.003, §29.4 promoted priors, §32.3 Option B market
blending at w=0.5 vs MARKET-AVAILABLE odds, proportional devig, no Elo.

Run on 2024/25 and **not** on 2025/26: that holdout was spent on
2026-08-07 and no ablation decision may react to its results
(VALIDATION.md). Both candidates were listed in `docs/next_steps.md` §5 as
having training data already and needing nothing but someone to run them.

## What was implemented to run this

- **Shin's method** (`model.market.shin_z` / `implied_result_probs_shin`) —
  §32.1 margin removal that treats the bookmaker's margin as protection
  against insider trading rather than a flat proportional loading. Solves
  `p_i = [√(z² + 4(1−z)·π_i²/Σπ) − z] / (2(1−z))` for the insider share `z`
  making `Σp_i = 1`. On this corpus `z` lands at 2–4%, consistent with the
  published range for football books, and it moves weight toward the
  favourite — the favourite-longshot correction it exists to make. `z=0`
  recovers the incumbent proportional method exactly.
- **MOV-Elo** (`model.elo.compute(margin_of_victory=True)`) — scales each
  update by `ln(|margin|+1) × 2.2/(0.001·winner_edge + 2.2)`. Draws take a
  margin floor of 1, since a literal zero would multiply by `ln(1)=0` and
  freeze ratings on roughly a quarter of all matches. Ratings look
  sensible (Liverpool/Man City/Arsenal top, spread widens 482 → 509 vs
  plain Elo).

Both are off by default. Nothing in the frozen model path changed.

## Point estimates

| arm                       | pts/round(6) | exact% | result% | logloss | brier  |
|---------------------------|-------------:|-------:|--------:|--------:|-------:|
| champion                  |        8.266 |  12.23 |   50.54 |  1.0044 | 0.6021 |
| champion + shin           |        8.234 |  12.12 |   50.43 |  1.0043 | 0.6022 |
| champion + elo (plain)    |        8.234 |  12.12 |   50.43 |  1.0050 | 0.6024 |
| champion + elo (mov)      |        8.215 |  12.02 |   50.43 |  1.0049 | 0.6023 |
| champion + shin + elo(mov)|        8.215 |  12.02 |   50.43 |  1.0047 | 0.6023 |

No candidate beats the champion on points. The differences should not be
read as evidence *against* the candidates either: only 1–4 picks changed
in total (below), so these gaps are a handful of fixtures' noise and the
fact that they happen to point the same way carries no weight.

## Paired significance (§13.3/§36 methodology, 20k bootstrap)

Same fixtures, per-fixture paired differences vs champion, per round of six:

```
champion+shin            mean -0.032  CI [-0.097, +0.000]     1/932 fixtures changed  not significant
champion+elo(plain)      mean -0.032  CI [-0.084, +0.000]     2/932 fixtures changed  not significant
champion+elo(mov)        mean -0.052  CI [-0.142, +0.013]     4/932 fixtures changed  not significant
champion+shin+elo(mov)   mean -0.052  CI [-0.142, +0.013]     4/932 fixtures changed  not significant
```

**The points test is close to powerless here, and saying so matters more
than the numbers.** Only 1–4 of 932 picks changed at all. Shin's method
shifts a favourite's probability by about 1pp, which almost never flips an
argmax pick, so a paired points test is being asked to detect a change
that barely reaches the quantity being measured. A "not significant" here
is mostly a statement about the test, not about the candidate.

So the same paired bootstrap was run on the metric these candidates
actually act on — per-fixture `-ln P(actual result)`, which moves on all
932 fixtures:

```
champion+shin            mean -0.00015  CI [-0.00075, +0.00047]  indistinguishable
champion+elo(plain)      mean +0.00055  CI [-0.00042, +0.00149]  indistinguishable
champion+elo(mov)        mean +0.00044  CI [-0.00050, +0.00137]  indistinguishable
champion+shin+elo(mov)   mean +0.00022  CI [-0.00065, +0.00111]  indistinguishable
```

With full power on every fixture, all four straddle zero. Shin's is the
only one whose point estimate is on the right side, at 1.5×10⁻⁴ — two
orders of magnitude smaller than the log-loss gaps that decided earlier
rungs (market blending moved it 1.0213 → 1.0043).

## The retirement case

No single line above retires these. The case is the conjunction:

1. No points improvement.
2. No meaningful log-loss improvement, on the metric with full power.
3. Tiny effect size — the mechanism itself limits how much either could
   move, so there is little room for a larger sample to reveal something.
4. Elo's redundancy was already demonstrated once (ablation_003); this
   rung tested the obvious objection to that finding and it held.
5. No unspent holdout exists to promote *from*, so even a validation-season
   win could not have been confirmed.

Explicitly not part of the case: the fact that all four point estimates on
points happen to be negative. With 1–4 changed picks that is noise, and
treating it as a signal would be reading a direction out of four fixtures.

## Verdicts

**Shin's method: NOT PROMOTED.** Correctly implemented and behaving as the
literature describes, but indistinguishable from proportional
normalisation on this corpus by every metric. The reason is visible in the
mechanism rather than only the result: the blend weight is w=0.5, so half
of any devig difference is diluted by the model side before it reaches a
probability, and the residual is ~1pp on the favourite. §28 removes what
makes things worse and promotes what demonstrably helps; this does
neither. Kept behind `devig="shin"`, re-auditionable if the blend weight
rises or a market with a fatter margin (correct-score, Asian handicap —
`next_steps.md` §4) is ever priced, where the favourite-longshot
correction has more room to matter.

**MOV-Elo: NOT PROMOTED, and it does not overturn ablation_003.**
ablation_003 rejected plain Elo as redundant once per-division α/β and
ξ-decay exist. The question here was whether a *better* rating changes
that verdict. It does not: MOV-Elo is marginally worse than plain Elo on
points (8.215 vs 8.234) and indistinguishable on log loss, so the
redundancy is a property of what the Dixon-Coles fit already captures, not
of the rating's quality. Improving the covariate does not help when the
covariate is the thing that was surplus. Kept behind
`use_elo=True, elo_mov=True`.

**Combined arm: no interaction.** `shin+elo(mov)` is identical to
`elo(mov)` on points and on fixtures changed, so the two do not compensate
for each other.

## What this does not say

The model stays frozen as PHASE1_MODEL_V1. No adoption was on the table
regardless of outcome — the 2025/26 holdout is spent, so a validation-season
win could not have been confirmed on unseen data, and VALIDATION.md is
explicit that validation-season scores are biased upward by every choice
made while looking at them. Had either candidate won here, the honest next
step would have been prospective measurement (`results/prospective_*`), not
promotion. Both losing simply closes the question earlier.

Two rungs of §32.1's standing candidate list are now retired rather than
outstanding, which is the actual value delivered: `next_steps.md` §5 no
longer says "nobody has run these".
