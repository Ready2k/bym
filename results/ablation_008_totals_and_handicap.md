# ablation_008 — §6/§32 totals and Asian handicap (NOT PROMOTED)

Date run: 2026-08-10
Test season: 2024/25 (E0 + E1, 932 fixtures) — validation season
Champion under test: PHASE1_MODEL_V1's configuration — per-division
weighted DC, ξ=0.003, §29.4 promoted priors, §32.3 Option B 1X2 blending
at w=0.5 vs MARKET-AVAILABLE odds.

Run on 2024/25 and **not** 2025/26: that holdout was spent on 2026-08-07
(VALIDATION.md).

## Why this looked like the best candidate left

`docs/next_steps.md` item 3 said no historical odds existed for these
markets. That was wrong, and had been for months: Asian handicap and
over/under 2.5 are in the Football-Data corpus at ~100% coverage for
every season and both divisions, pre-match *and* closing. The parser
simply never read the columns.

The prior reason to expect a gain was mechanical rather than hopeful. A
1X2 price says nothing about how a result's probability splits across
scorelines — which is exactly why §32.3 Option B has to fall back on the
model's own within-result shape. A total prices **goal count** and a
handicap prices **supremacy**, both of which are statements about the
scoreline, and Super 6 is scored on scorelines.

That reasoning still looks right. It just does not pay here.

## What was implemented

`market.constrain_matrix`, the same Option B idea applied to a different
partition: split each scoreline's mass into the fraction a bet wins, the
fraction it loses and the remainder it pushes; scale the won and lost
components to hit the blended target; leave pushed mass alone, because a
market that returns your stake on 1-0 has said nothing about how likely
1-0 was. Relative shape within each side is untouched.

Every line type reduces to one statement. Fair decimal odds satisfy
`(o-1)·E[f_win] = E[f_lose]`, so the devigged price estimates
`E[f_win] / (E[f_win] + E[f_lose])`. Half lines make those fractions
zero-or-one, whole lines push a whole scoreline, quarter lines split the
stake across two adjacent lines and push half of it.

One subtlety was worth getting right rather than approximating. The
obvious implementation — scale each side so its mass becomes the target
share — is correct only when nothing pushes. On a quarter line a
scoreline is half won and half pushed, and its *pushed* half still
carries win weight when the ratio is measured again, so the constraint
lands short (0.637 against a 0.650 target in the first cut). **53% of the
corpus's lines are quarters**, so that is the common case. Solving the
two-equation linear system for the scale pair instead hits the target
exactly on every line type, which the tests assert.

## Point estimates

| arm | pts/round | exact% | result% | logloss | brier |
|---|---:|---:|---:|---:|---:|
| champion | **8.266** | **12.23** | 50.54 | 1.0044 | 0.6021 |
| totals w=0.25 | 8.150 | 11.59 | 50.54 | 1.0045 | 0.6021 |
| totals w=0.50 | 8.202 | 11.80 | 50.64 | 1.0047 | 0.6022 |
| totals w=0.75 | 8.124 | 11.37 | 50.64 | 1.0049 | 0.6023 |
| handicap w=0.25 | 8.279 | 12.23 | 50.64 | **1.0041** | **0.6019** |
| handicap w=0.50 | 8.221 | 12.12 | 50.32 | 1.0042 | 0.6019 |
| handicap w=0.75 | 8.176 | 11.80 | 50.43 | 1.0045 | 0.6021 |
| both w=0.50 | 8.144 | 11.70 | 50.32 | 1.0046 | 0.6021 |

**Exact-score accuracy falls in almost every arm** — 12.23% down to
11.37–11.80%. That is the metric these markets were most expected to
help, since goal count and supremacy are precisely what an exact score
is made of. Only handicap at w=0.25 holds it level.

## Paired significance (§13.3/§36 methodology, 20k bootstrap)

```
points, per round of six
  totals w=0.25      mean -0.116  CI [-0.251, +0.019]  changed 12/932  not significant
  totals w=0.50      mean -0.064  CI [-0.238, +0.109]  changed 20/932  not significant
  totals w=0.75      mean -0.142  CI [-0.354, +0.077]  changed 29/932  not significant
  handicap w=0.25    mean +0.013  CI [-0.135, +0.155]  changed 15/932  not significant
  handicap w=0.50    mean -0.045  CI [-0.270, +0.174]  changed 32/932  not significant
  handicap w=0.75    mean -0.090  CI [-0.348, +0.161]  changed 44/932  not significant
  both w=0.50        mean -0.122  CI [-0.380, +0.129]  changed 44/932  not significant

log loss (negative = better), all 932 fixtures move
  totals w=0.25      mean +0.00008  CI [-0.00047, +0.00063]  indistinguishable
  totals w=0.50      mean +0.00024  CI [-0.00086, +0.00133]  indistinguishable
  totals w=0.75      mean +0.00047  CI [-0.00119, +0.00214]  indistinguishable
  handicap w=0.25    mean -0.00029  CI [-0.00144, +0.00085]  indistinguishable
  handicap w=0.50    mean -0.00026  CI [-0.00254, +0.00203]  indistinguishable
  handicap w=0.75    mean +0.00008  CI [-0.00340, +0.00343]  indistinguishable
  both w=0.50        mean +0.00014  CI [-0.00244, +0.00270]  indistinguishable
```

**This ablation is materially better powered than ablation_007.** There,
1–4 of 932 picks changed and "not significant" was mostly a statement
about the test. Here 12–44 change, and the log-loss test moves every
fixture. A null carries more weight.

## Why it does not pay — measured, not guessed

The obvious explanation would be that the champion already knows what
these markets know. It does not:

```
                n     market - model      mean|diff|    p90|diff|
over 2.5       932        -0.0049           0.0506       0.1047
AH cover       932        +0.0048           0.0567       0.1187
```

The model and the market disagree by about **5–6 percentage points on
average**, and 10–12 points in the tail. That is a real disagreement, not
a rounding difference.

But the *mean signed* difference is ~0.005 — essentially zero. The
disagreement is symmetric, not directional: the model is not
systematically over- or under-estimating goal count or supremacy, it is
noisily wrong in both directions. Correcting toward the market therefore
moves each fixture ~5pp in a direction that is about as often wrong as
right. That adds variance without removing bias, which is exactly the
monotone deterioration the log-loss column shows: totals gets steadily
worse with weight (1.0045 → 1.0047 → 1.0049), and handicap likewise past
its smallest weight (1.0041 → 1.0042 → 1.0045).

The 1X2 blend that *was* adopted (ablation_004) worked because the
market's result probabilities are better than the model's in a
systematic, exploitable way. These two markets are better in a way that
does not survive being averaged in.

## Verdicts

**Totals (over/under 2.5): NOT PROMOTED.** Monotonically worse on log
loss as weight rises, and costs exact-score accuracy at every weight.
Available behind `totals_blend`.

**Asian handicap: NOT PROMOTED, but the least dead of the candidates
tested recently.** At w=0.25 it is the only arm to beat the champion on
log loss (1.0041 vs 1.0044) *and* Brier (0.6019 vs 0.6021) *and* have a
positive points mean (+0.013), while holding exact% level. Every one of
those is inside noise, and the trend past w=0.25 is clearly downward, so
there is no promotion case. Worth one more look if the model family
changes — a supremacy constraint has more to correct when the underlying
goal model is worse. Available behind `handicap_blend`.

**Combined: no interaction.** `both w=0.50` is worse than either alone,
consistent with two independent doses of the same variance.

## What this does not say

The model stays frozen as PHASE1_MODEL_V1. As with ablation_007, no
adoption was available regardless of outcome: the 2025/26 holdout is
spent, so a validation-season win could not have been confirmed on unseen
data. Retiring a candidate cheaply is still worth the run, and that is
what happened.

The parser change stands on its own merits and is kept: the corpus's
handicap and totals columns are now read into the canonical frame,
pre-match and closing, so the next person to want them does not have to
rediscover that they were there all along.
