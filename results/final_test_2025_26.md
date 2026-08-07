# FINAL TEST — 2025/26 holdout (FROZEN, SINGLE-USE, SPENT)

Date run: 2026-08-07
Model: PHASE1_MODEL_V1, git SHA 6f39b7e359737502b6ffaea6aba793bd97814377,
tag `phase1-model-v1`, frozen in MANIFEST.md **before** this run.
Test season: 2025/26 (E0 + E1, 932 fixtures), never previously evaluated.
Training: 2019/20–2024/25, walk-forward, weekly refits, no leakage.

**This holdout is now spent.** No modelling choice may be made in response
to the numbers below (VALIDATION.md). Any future model change requires a
new holdout season.

## Result

| model                             | pts/fixture | pts/round(6) | exact% | result% | brier  | logloss |
|-----------------------------------|------------:|-------------:|-------:|--------:|-------:|--------:|
| MARKET-AVAILABLE fav +2-1/1-1/1-2 |      1.2361 |         7.42 |   9.66 |   47.32 | 0.6216 |  1.0322 |
| MARKET-ORACLE fav +2-1/1-1/1-2    |      1.2436 |         7.46 |   9.76 |   47.53 | 0.6201 |  1.0295 |
| **PHASE1_MODEL_V1 (frozen)**      |      1.2446 |         7.47 |   9.87 |   47.42 | 0.6264 |  1.0397 |

Paired comparison vs MARKET-AVAILABLE (same fixtures, 20k bootstrap):

```
Paired difference: +0.032 pts/round
95% CI:            [-0.470, +0.541]
Picks differ on:   177 of 932 fixtures
P(model ahead on a random resample): 0.543
```

## Reproducibility defect found while analysing this result — disclosed

The paired analysis re-ran the frozen model and got 7.448 pts/round where
the official `final-test` run reported 7.468 — on identical inputs, with
identical fixture coverage (932 unique keys, fully overlapping).

Cause: `priors.arrivals()` iterated a `set` of team names. Python
randomises string hashing per process, so set iteration order varied
between runs, which changed the summation order of the §29.4 penalty
terms in the MLE objective, which perturbed L-BFGS-B at float precision,
which flipped a handful of borderline scorelines. The same frozen model
therefore answered slightly differently on different runs — a direct
violation of the reproducibility requirement (§19, §22).

Fixed post-freeze by sorting team iteration in `priors.arrivals()` and
the penalty-array construction in `dixon_coles.fit()`, with a test that
runs the computation under three `PYTHONHASHSEED` values and requires
identical output. **This is a determinism fix, not a modelling change** —
it alters no parameter, feature or hyperparameter.

**The holdout was deliberately NOT re-run after the fix.** Re-running a
spent holdout to obtain a tidier number is precisely the failure this
protocol exists to prevent. The figures recorded above are the ones the
official single-use run produced. The observed run-to-run variation
(~0.02 pts/round) is roughly 30x smaller than the ±0.5 confidence
interval on the headline comparison, so it changes no conclusion below.

## Verdict against the pre-registered thresholds

Pre-registered in MANIFEST.md before the result was seen:

> Blend ≈ fair market → *Still good. Statistical score-shape model adds
> useful structure but not clear predictive superiority.*

**This is the outcome: PHASE1_MODEL_V1 is statistically indistinguishable
from the fair market benchmark.** It finished nominally ahead on points
(+0.03/round) and exact-score rate (9.87% vs 9.66%), and nominally ahead
of even the closing-odds oracle on points — but the paired confidence
interval spans zero comfortably and P(ahead) is 0.543, which is a coin
flip. The §24 target ("beats bookmaker-only probabilities") is **not
demonstrated** out-of-sample. It is also not refuted.

## The finding that reversed — record it, don't fix it

On the validation season the model's probability metrics *beat* the
market (log loss 1.0044 vs 1.0064). On the holdout they *lost* to it
(1.0397 vs 1.0322), and Brier moved the same way (0.6264 vs 0.6216).

This is the clearest measurable consequence of selection bias in the
validation numbers: the market blend weight w=0.5 was chosen partly
because it produced well-calibrated probabilities on 2024/25, and that
specific advantage did not survive a fresh season. The points advantage
partially survived; the calibration advantage did not.

**No action is being taken on this.** The correct response to a
disappointing holdout number is to record it, not to adjust w until the
number improves — which would convert the one unbiased estimate this
project has into another validation season. If calibration is to be
improved, it must be developed on training/validation data and tested on
a *new* holdout (2026/27, as it is played).

## Season-difficulty context

All three arms scored ~0.7 pts/round lower than on 2024/25 (7.4 vs
8.1–8.2). 2025/26 was simply a harder season to forecast. This is why
absolute scores are not comparable across seasons and why the
same-season market benchmark is the only meaningful reference point.

## What this means for the project

The honest one-line summary: **after Dixon-Coles, per-division fitting,
recency weighting, promoted-team priors and market calibration, the
system performs about as well as betting-market favourites — no better,
no worse, on the evidence available.**

That is a reasonable Phase 1 outcome, not a failure. The market is an
extremely strong benchmark, and matching it with a self-contained
statistical model means the remaining §23 phases (team news, confirmed
lineups, late market movement, opponent modelling) now have a credible
foundation to add to rather than a broken one to repair. It also means
none of those phases may claim credit for beating the market until they
demonstrate it on a fresh holdout.
