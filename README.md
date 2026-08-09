# Super 6 Always-On Prediction Agent

Implementation of [SPEC.md](SPEC.md). Phase 1A (§23), the deliberately
boring quantitative core the spec mandates before anything fancier is
allowed to exist, is closed and frozen:

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

## Local decision UI

A local, decision-first dashboard built directly on the stable API
contracts (`api/contracts.py`). Pages: Home, Round, Cup Match, League,
Opponents, Performance, Activity, and an Expert group (Model Lab, Sources,
System Health, Settings). It shows the six current recommendations,
separates result probability / exact-score probability / EV / confidence,
offers fixture explanations and alternatives, surfaces market movement and
divergence, an opponent-aware strategy panel for the current round (§12-14
— see caveat below), and records only your manual confirmation that you
entered picks in Super 6. It is a stdlib `http.server` app, not
FastAPI/Next.js — §26's optional web-framework choice was never taken up.

```bash
super6 serve --round rounds/example_round.json --seasons 2019 2025 --data data
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000). The server is
local-only by default. It never submits predictions to Sky.

To keep the local UI running outside a terminal, use:

```bash
./scripts/super6-service start
./scripts/super6-service status
./scripts/super6-service stop
```

`restart` and `logs` are also available. The script uses `.venv` when it is
present; set `SUPER6_PORT`, `SUPER6_DB`, or `SUPER6_SUBMISSIONS` to override
its local defaults.

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
├── engine.py            four-layer report: forecast/market/team/knowledge (`super6 engine-run`)
├── cadence.py           §9 poll/reforecast ladder as a pure function
├── scheduler.py         restart-safe job scheduling (SQLite-persisted, §26)
├── worker.py            always-on orchestration: odds/availability/knowledge polling
├── movement.py          material market-move detection (§6)
├── divergence.py        model-vs-market disagreement
├── prediction_history.py  append-only forecast/change log (§10, §43)
├── prospective.py       2026/27 round-by-round arm comparison
├── submission.py        submission-state machine (§46)
├── reminders.py         deadline alerts
├── notifications.py     in-app + opt-in SMTP email delivery (§54.1)
├── events.py             activity-feed / alert severity model (§21, §53-54)
├── transparency.py      read-only explainability payloads for the UI (§56)
├── verify.py            `super6 verify-live` — per-source live checks
├── league.py             opponent history, league table, single-round strategy sim (§11-13)
├── opponent_intelligence.py  favourite bias, influence weight, predicted picks, chase/defend (§11)
├── strategy.py           candidate generation + significance test for league.py
├── db.py                 SQLite schema (jobs, availability, evidence, claims, …)
├── secrets.py            credential loading, hostname-only in logs/provenance
├── web.py + web_assets/  local decision UI (`super6 serve`, stdlib http.server)
├── data/football_data.py  Football-Data.co.uk ingestion + cache (§18)
├── sources/
│   ├── registry.py      §18.1 source policy — gates every outbound query
│   ├── odds_api.py      The Odds API client + credit metering (§25)
│   ├── api_football.py  confirmed lineups / injuries / suspensions
│   ├── football_data_org.py  fixtures/results, VOID source (§2.2)
│   ├── tavily.py         news/evidence search + extract for the knowledge agent
│   ├── consensus.py     per-book de-vig, median consensus, staleness
│   ├── teams.py         cross-source team-name resolution (never fuzzy)
│   ├── quota.py / capabilities.py  per-source metering and coverage
│   └── pipeline.py      fetch → map → consensus → persist → model
├── availability/         team-news pipeline (ingest, reconcile, completeness, impact — no λ path yet)
├── knowledge/            Tavily retrieval → claim extraction (Ollama) → clustering, SHADOW by default
├── model/
│   ├── dixon_coles.py   weighted MLE: μ, γ, α, β, ρ (§29-31)
│   ├── elo.py            Elo covariate (θ) — fitted but BINNED, see ablation ladder
│   ├── priors.py         promoted/relegated-team empirical-Bayes priors (§29.4, ADOPTED)
│   ├── market.py          margin removal + result-constrained reweighting (§32)
│   ├── matrix.py        score matrix w/ tail handling + EV (§31, §2/§33)
│   └── golden_goal.py   first-goal hazard, Sky minute bands (§15)
├── api/                  frozen read-model contracts (`contracts.py`) + service layer
├── research/              availability-oracle research harness (separate from the live model)
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

**Phase 2 (always-on intelligence) is built and active.** Increments 1-4 —
live odds (The Odds API), market movement/divergence detection, team
availability intelligence (API-Football), and the knowledge agent
(Tavily + Ollama extraction) — are all in place; see
[docs/phase2_increment1.md](docs/phase2_increment1.md) through
[increment4.md](docs/phase2_increment4.md) and
[docs/engine_status.md](docs/engine_status.md) for exact per-source
status. All four sources are registered `APPROVED` in
`config/source_registry.json`. The knowledge agent stays `SHADOW` by
default (§8 evidence is extracted and explained, but there is
deliberately no code path from a news/availability event to a `λ`
adjustment yet — that is the still-deferred Increment 5 player-impact
model).

**Phase 3 (beat the mates) has started.** Opponent-pick import, league
standings and opponent profiles (§11) are built, including model-relative
behaviour (`opponent_intelligence.py`): favourite bias/upset rate, a
continuous Bayesian opponent-influence weight, a predicted-pick
distribution for the current round's fixtures (§49.3), and chase/defend
tendencies relative to private-league standing. `league.recommend_strategy`
picks between candidate score sets by simulated chance of finishing
first — but only for **the current round** against current standings, not
the season-long Monte Carlo simulator §13 specifies (no
known/unknown-future-round split, no Round Opportunity Model, no
`ATTACK`/`DEFEND` labels, no 100 Club annotation). Treat §13's full season
simulator as still to be built; what exists today is a single-round
approximation of it, now with a richer opponent model feeding it.

**Measurement is prospective** through 2026/27 —
[results/prospective_2026_27.md](results/prospective_2026_27.md). No Phase
2 or Phase 3 component may claim improvement until its arm demonstrates it
there.

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
- **Shin's method** for margin removal, MOV-Elo — future ablation
  candidates, each must beat the current rung. (Promoted-team priors,
  §29.4, were adopted — see VALIDATION.md — and are no longer on this list.)
- **Evidence → λ adjustments** (Increment 5 player-impact model) — the
  knowledge agent extracts and explains news/availability events (§8) but
  nothing yet converts one into a model parameter change; gated behind
  `QUANTITATIVE_IMPACT_MODEL_ENABLED = False` until a fitted, versioned
  impact model earns its place under §28.
- **Season Monte Carlo simulator, Round Opportunity Model, ATTACK/DEFEND
  labels** (§12-13), **100 Club annotation** (§12.1) — the strategy engine
  that exists today (`strategy.py`, `league.recommend_strategy`) only
  compares candidates for the current round, not the remaining season.
- **Team-specific opponent bias** (favours large clubs, underrates
  promoted clubs, §11) — needs a club-reputation reference independent of
  any one fixture's forecast; not faked with a hard-coded club list.
  Favourite bias, influence weighting, predicted picks and chase/defend
  tendencies (the rest of §11) are built — see `opponent_intelligence.py`.
- **FastAPI/Postgres** (§26) — the local UI runs on stdlib `http.server`
  and SQLite; the modelling core is plain Python modules precisely so a
  framework/database swap doesn't require a rewrite.
