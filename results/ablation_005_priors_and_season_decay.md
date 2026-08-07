# ablation_005/006 — §29.4 priors and §29.5 season decay (FROZEN)

Date run: 2026-08-07
Test season: 2024/25 (E0 + E1, 932 fixtures) — validation season
Champion under test: per-division weighted DC, ξ=0.003, §32.3 Option B
market blending at w=0.5 vs MARKET-AVAILABLE odds.

## Fitted §29.4 priors (from training seasons 2019/20–2023/24 only)

```
(E0, below)  promoted into the PL:      alpha -0.278 (var 0.079)  beta +0.260 (var 0.031)
(E1, above)  relegated into the Champ:  alpha +0.256 (var 0.043)  beta -0.235 (var 0.033)
(E1, below)  promoted into the Champ:   alpha -0.101 (var 0.055)  beta +0.117 (var 0.020)
```

The estimates recover the expected football reality without being told it:
newly-promoted PL teams attack ~25% below division average and concede
~30% above it; parachuted-down teams are the mirror image one level down.
This is direct evidence that the zero-evidence prior they replace is
*wrong*, not merely uninformative.

## Point estimates

| arm                            | pts/fixture | pts/round(6) | exact% | result% | brier  | logloss |
|--------------------------------|------------:|-------------:|-------:|--------:|-------:|--------:|
| champion (zero prior)          |      1.3734 |         8.24 |  12.02 |   50.64 | 0.6024 |  1.0043 |
| champion + §29.4 priors        |      1.3777 |         8.27 |  12.23 |   50.54 | 0.6021 |  1.0044 |
| champion + season_decay=0.8    |      1.3745 |         8.25 |  12.12 |   50.54 | 0.6025 |  1.0042 |
| champion + season_decay=0.6    |      1.3734 |         8.24 |  12.02 |   50.64 | 0.6027 |  1.0043 |
| champion + priors + decay=0.8  |      1.3927 |         8.36 |  12.66 |   50.64 | 0.6024 |  1.0046 |

## Paired significance (§13.3/§36 methodology, 20k bootstrap)

Same fixtures, per-fixture paired differences vs champion, 95% CI on the
mean difference expressed per round of six:

```
champion+priors            mean +0.026  CI [-0.167, +0.225]  24/932 fixtures changed  NOT SIGNIFICANT
champion+priors+decay0.8   mean +0.116  CI [-0.097, +0.335]  28/932 fixtures changed  NOT SIGNIFICANT
```

## Verdicts

**§29.4 promoted priors: ADOPTED — on correctness grounds, explicitly not
on a significant validation win.** The paired test cannot resolve a
difference this small from 24 affected fixtures, and the honest statement
is "consistent with, but not demonstrating, improvement." It is adopted
because the alternative is worse on first principles: a zero prior is not
neutral, it asserts that a promoted team is exactly league-average, and
the fitted priors above measure that assertion to be false. §28's rule is
that a component is removed if it makes predictions *worse*; this does
not. It is also spec-mandated architecture (§29.4), not opportunistic
feature-shopping.

**§29.5 season decay: BINNED (season_decay = 1.0).** 0.6 reproduced the
baseline exactly; 0.8 moved points by 0.01 and failed the paired test. The
ξ=0.003 daily decay already discharges §29.5's intent — a season-old match
sits at roughly a third weight before any boundary term is applied.
Implementation retained behind the default-off flag.

**The 8.36 arm was deliberately NOT frozen**, despite posting the best
points and exact-score figures on the board. Its gain is not significant,
its extra component is independently redundant, and it has the *worst* log
loss of the five arms — the signature of noise, not signal. Freezing the
highest number would be exactly the grid-search worship this protocol
exists to prevent.
