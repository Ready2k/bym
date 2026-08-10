# ablation_009 — §7 zero-cost shots-based xG stand-in (NOT PROMOTED)

Date run: 2026-08-10
Test season: 2024/25 (E0 + E1, 932 fixtures) — validation season
Champion under test: PHASE1_MODEL_V1's configuration — per-division
weighted DC, ξ=0.003, §29.4 promoted priors, §32.3 Option B 1X2 blending
at w=0.5 vs MARKET-AVAILABLE odds.

Run on 2024/25 and **not** 2025/26 (VALIDATION.md: holdout spent
2026-08-07).

## Why a proxy at all — the free-source survey

`docs/next_steps.md` item 4 asserted "no confirmed free source exists"
and had never checked. Checked on 2026-08-10:

| source | Championship? | free? | usable for live 2026/27? |
|---|---|---|---|
| [Understat](https://understat.com/) | **No** — EPL, La Liga, Bundesliga, Serie A, Ligue 1, RFPL only | yes (scrape, no API) | PL only |
| [FBref](https://fbref.com/en/expected-goals-model-explained/) | had it | was free | **No** — Opta terminated FBref's advanced-data feed on **2026-01-20**; xG removed |
| [FootyStats API](https://footystats.org/api) | yes | **£29.99/mo** | yes, but 3× §25's £10 target |
| API-Football | yes | free tier paywalls current seasons | no |

The conclusion the item asserted turns out to be right, but not for the
reason given, and it became *more* right in January 2026. The binding
constraint is the **Championship**, which is 59% of this corpus (552 of
932 fixtures in the test season). A Premier-League-only xG feed cannot
serve a model fitted across both divisions.

Shots and shots on target, however, are in the free Football-Data corpus
at 100% coverage for every season and both divisions. §28 does not
require a candidate to be the best possible version of itself before
being tested, so the crude version was tested.

## What was implemented

`model/shots.py`: a Poisson fit of goals on shots on target and shots off
target, giving each past match a shot-derived expected-goal figure. On
2019–2024 that recovers 0.32 goals per shot on target, and the proxy's
variance is 0.77 against actual goals' 1.59 — smoother, which is the
thesis.

The arm is a **second Dixon-Coles fit on the proxy target**, blended at
the λ level: `λ = (1-w)·λ_goals + w·λ_shots`. Same model family, different
target, so the comparison isolates the target rather than confounding it
with a change of model. Blending at λ leaves the champion's own ρ shaping
the final matrix.

The proxy fit sets `fix_rho_zero=True`, deliberately and visibly: proxy
goals are continuous and `_tau_log` identifies its {0,1}² cells by exact
equality, so ρ would be unidentifiable and drift silently. Better to
disable it and say so than to let the Dixon-Coles correction switch
itself off unnoticed.

## Point estimates

| arm | pts/round | exact% | result% | logloss | brier |
|---|---:|---:|---:|---:|---:|
| champion | **8.266** | **12.23** | 50.54 | **1.0044** | 0.6021 |
| shots w=0.15 | 8.195 | 11.91 | 50.43 | 1.0045 | 0.6020 |
| shots w=0.25 | 8.189 | 11.80 | 50.54 | 1.0047 | 0.6020 |
| shots w=0.50 | 8.195 | 11.91 | 50.43 | 1.0056 | 0.6023 |
| shots w=0.75 | 8.189 | 11.80 | 50.54 | 1.0073 | 0.6031 |
| shots w=1.00 | 8.144 | 11.48 | 50.64 | 1.0098 | 0.6046 |

Log loss degrades **monotonically** with weight, 1.0044 → 1.0098. Points
and exact% are worse at every weight.

## Paired significance (§13.3/§36, 20k bootstrap)

```
points, per round of six
  shots w=0.15   mean -0.071  CI [-0.200, +0.045]  changed  9/932  not significant
  shots w=0.25   mean -0.077  CI [-0.245, +0.077]  changed 16/932  not significant
  shots w=0.50   mean -0.071  CI [-0.315, +0.167]  changed 41/932  not significant
  shots w=0.75   mean -0.077  CI [-0.373, +0.219]  changed 63/932  not significant
  shots w=1.00   mean -0.122  CI [-0.470, +0.225]  changed 82/932  not significant

log loss (negative = better)
  shots w=0.15   mean +0.00006  CI [-0.00085, +0.00095]  indistinguishable
  shots w=0.25   mean +0.00024  CI [-0.00125, +0.00172]  indistinguishable
  shots w=0.50   mean +0.00119  CI [-0.00186, +0.00415]  indistinguishable
  shots w=0.75   mean +0.00290  CI [-0.00172, +0.00743]  indistinguishable
  shots w=1.00   mean +0.00541  CI [-0.00068, +0.01156]  indistinguishable
```

Well powered at the higher weights — 82 of 932 picks change at w=1.00,
the most of any candidate tested. The monotone log-loss trend is the
clearer signal than any single interval.

## Why it fails — and this is the useful part

The smoothing works. It is the *bias* it introduces that costs more than
the variance it removes, and that bias is measurable rather than
hypothesised.

Per team-season, take actual goals minus what the shot profile predicted.
If shot quality were noise, that residual would not persist. It does:

```
team-seasons 308, consecutive-season pairs 253
year-over-year correlation of (goals - proxy):  +0.193
```

And it is structured, not scattered:

```
persistently OVER-performing their shot profile   goals/match
  Man City      +0.390
  Arsenal       +0.336
  Liverpool     +0.217
  Tottenham     +0.159
  Brentford     +0.143

persistently UNDER-performing
  Crystal Palace -0.094
  Sheffield Weds -0.113
  Wolves         -0.121
  Southampton    -0.126
  Everton        -0.152
```

**The proxy systematically underrates the strongest teams and overrates
the weakest.** Man City is nearly four tenths of a goal per match better
than its shot count implies. Counting shots equally treats a tap-in and a
hopeful 30-yarder as the same event, so it erases precisely the
chance-quality difference that separates these teams — and blending it in
drags λ toward the league mean in a way that is persistently wrong.

That is bias, not noise, which is why more weight is monotonically worse
rather than noisily flat.

## Verdicts

**Shots-based proxy: NOT PROMOTED.** Available behind `shot_blend`, off
by default, not in the frozen model path.

**What this does NOT say — and the distinction matters for the budget
decision.** This is not evidence that real xG would fail. The proxy fails
for a specific, identified reason: it has no shot-quality information,
which is exactly what a real xG model adds. A feed with shot locations
would capture the very gap the residual table above exposes. Reading this
ablation as "xG doesn't help here" would be reading it backwards.

What it does establish is narrower and still worth having: **the free
route is exhausted.** Shot counts are the only xG-adjacent signal
available across both divisions at zero cost, and they cannot substitute
for chance quality. Any further progress on §7 needs either a
Championship-covering paid feed (a §25 budget decision, currently
~£30/month against a £10 target) or a free source that does not yet
exist.

The parser change is kept regardless: shots and shots on target are now
in the canonical frame, so the next attempt starts from data rather than
from re-parsing.
