# Super 6 Always-On Prediction Agent

Implementation of [SPEC.md](SPEC.md) — currently **Phase 1A** (§23): the
deliberately boring core the spec mandates before anything fancier is
allowed to exist:

```
historical results → weighted Dixon-Coles → score matrix → Super 6 EV
→ six recommendations + Golden Goal → walk-forward backtest vs baselines
```

## Setup

```bash
uv venv && uv pip install -e ".[dev]"
```

## Usage

Download the free Football-Data.co.uk corpus (§18 — Premier League `E0` and
Championship `E1`):

```bash
super6 fetch --seasons 2019 2025 --data data
```

Enter a round manually (§9.1 — Sky's fixture selection and deadline are
config, not derived data). Copy `rounds/example_round.json`, edit the six
fixtures + deadline, then:

```bash
super6 predict --round rounds/example_round.json --seasons 2021 2025 --data data
```

Output is the §20 pre-round report: six picks with RESULT / SCORE / EV /
CONF columns, Golden Goal minute (with the Sky one-off interval convention,
§15), and expected round score.

Walk-forward backtest against the §16/§28 baselines — MARKET-AVAILABLE
(early odds — the fair target), MARKET-ORACLE (closing odds — theoretical
gold standard), pure Poisson, and Dixon-Coles pooled vs per-division. See
[VALIDATION.md](VALIDATION.md) for the binding season-role protocol
(2019-23 development / 2024-25 validation / **2025-26 untouched final
test**) and [results/](results) for frozen historical results:

```bash
super6 backtest --seasons 2019 2024 --test-season 2024 --data data
```

Select the recency-decay hyperparameter ξ by out-of-sample validation
(§29.2 — never by MLE):

```bash
super6 tune-xi --seasons 2019 2024 --validation-season 2024 --data data
```

## Layout

```
src/super6/
├── config.py            tunable defaults — config, not magic numbers (§30, §31)
├── scoring.py           Super 6 points rules + fixture status (§2, §2.2)
├── round.py             manual round entry (§9.1)
├── recommend.py         §20 report, EV picks, Phase 1A confidence
├── data/football_data.py  Football-Data.co.uk ingestion + cache (§18)
├── model/
│   ├── dixon_coles.py   weighted MLE: μ, γ, α, β, ρ (§29-31)
│   ├── matrix.py        score matrix w/ tail handling + EV (§31, §2/§33)
│   └── golden_goal.py   first-goal hazard, Sky minute bands (§15)
└── backtest/
    ├── walk_forward.py  leak-free walk-forward + ablation baselines (§16, §28)
    └── metrics.py       pts/fixture, exact %, Brier, log loss (§16)
```

## Status

**Phase 1 quantitative core: CLOSED.** Frozen as `PHASE1_MODEL_V1`
(MANIFEST.md, tag `phase1-model-v1.0.1`). Holdout verdict on the untouched
2025/26 season: **market-equivalent** — 7.47 pts/round vs the fair market
benchmark's 7.42, paired CI [−0.470, +0.541], P(ahead) 0.543. §24's target
is neither demonstrated nor refuted. Full write-up:
[results/final_test_2025_26.md](results/final_test_2025_26.md).

Known weakness: calibration is worse than the market out-of-sample (log
loss 1.0397 vs 1.0322), reversing the validation finding. Recorded, not
"fixed" — the holdout is spent and may not be tuned against.

**Next: Phase 2 live intelligence, measured prospectively** through
2026/27 — see [results/prospective_2026_27.md](results/prospective_2026_27.md).
No Phase 2 or Phase 3 component may claim improvement until its arm
demonstrates it there.

## Ablation ladder status (§28)

Every decision is logged in [VALIDATION.md](VALIDATION.md) and frozen in
[results/](results). On the 2024/25 validation season:

| Rung | Verdict | Pts/round |
|---|---|---:|
| Pure Poisson | baseline | 7.91 |
| Dixon-Coles ρ (pooled) | kept, advantage unproven | 7.91 |
| Per-division fits | **ADOPTED** | 8.03 |
| Elo covariate θ (§30) | **BINNED** — redundant vs α/β + decay | 8.03 |
| Market calibration w=0.5 (§32.3 Option B) | **ADOPTED** | **8.24** |
| *MARKET-AVAILABLE benchmark* | *the §24 target* | *8.14* |

The blended model is the first configuration to beat the fair market
benchmark. Honest caveat: hyperparameters (ξ, w) were selected on 2024/25,
so its numbers are biased upward by selection — the real verdict comes
from the untouched 2025/26 final test once the design freezes.

## What's deliberately not here yet

Per the phase gates in §23 and the ablation rule in §28:

- **xG** (Phase 1B) — no confirmed free source; fields are nullable by design.
- **Shin's method** for margin removal, MOV-Elo, promoted-team priors
  (§29.4) — future ablation candidates, each must beat the current rung.
- **News/reforecasting, automated odds ingestion** (Phase 2) — until then,
  odds are entered manually per fixture in the round JSON.
- **Opponent modelling + season Monte Carlo** (Phase 3),
  **FastAPI/Postgres/web UI** (§26) — the modelling core is plain Python
  modules precisely so these wrap around it later without a rewrite.
