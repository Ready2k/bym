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
config, not derived data). Each file in `rounds/` is one real round, named
`<season>_round_<NN>.json`. Copy the most recent one, edit the six
fixtures + deadline, then:

```bash
super6 predict --round rounds/2026_27_round_01.json --seasons 2021 2025 --data data
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
super6 serve --round rounds/2026_27_round_01.json --seasons 2019 2025 --data data
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
├── prediction_history.py  append-only forecast/change log + input snapshots (§10, §19, §43)
├── strategy_history.py   append-only strategy decisions, linked to the forecast they came from (§19)
├── prospective.py       2026/27 round-by-round arm comparison
├── submission.py        submission-state machine (§46)
├── reminders.py         deadline alerts
├── notifications.py     in-app + opt-in SMTP email delivery (§54.1)
├── events.py             activity-feed / alert severity model (§21, §53-54)
├── transparency.py      read-only explainability payloads for the UI (§56)
├── verify.py            `super6 verify-live` — per-source live checks
├── league.py             opponent history, league table, single-round strategy sim (§11-13)
├── opponent_intelligence.py  favourite bias, team bias, influence weight, predicted picks, chase/defend (§11)
├── strategy.py           single-round candidate generation + CORRELATE (DEFEND) candidate
├── season_simulation.py  season Monte Carlo: season state, Round Opportunity Model, paired test, mode labels (§12-13)
├── round_characteristics.py  what real Super 6 rounds look like — the §13.2 matching target
├── claim_outcomes.py     recorded team-news claims paired with realised results (§8 step 1)
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

**Phase 3 (beat the mates) is substantially built.** Opponent-pick
import, league standings and opponent profiles (§11) are built, including
model-relative behaviour (`opponent_intelligence.py`): favourite
bias/upset rate, team-specific bias (does a rival over-back big clubs or
under-back promoted ones, measured against the price the market put on
them rather than as a raw rate), a continuous Bayesian
opponent-influence weight, a predicted-pick distribution for the current
round's fixtures (§49.3), and chase/defend tendencies relative to
private-league standing.
`season_simulation.py` adds §13's actual season-length Monte Carlo on top:
§13.1 season state, a §13.2 Round Opportunity Model for the rounds Sky
hasn't published yet (six fixtures sampled from the historical corpus,
forecast with current parameters), §13.3's paired significance test
(`D_i = Win_i(candidate) - Win_i(baseline)`, accepted only when the lower
confidence bound clears zero and a configurable minimum gain), and
`NORMAL`/`ATTACK`/`DEFEND` labels attached to whichever candidate the test
actually accepts — including a new `strategy.build_correlate_candidate`
("match the field's likely picks") giving DEFEND a genuine candidate to
choose, not just a label with nothing behind it. `league.recommend_strategy`
(single-round only) is kept alongside it as the fast path; both are shown
on the League page. Still missing: the full §13.2 characteristic-matching
for synthetic rounds (favourite strength, home/away balance, ...) and the
§12.1 100 Club annotation — there is no source of national-entrant pick
data to estimate scoreline rarity from.

**§27's tiebreak question is resolved.** The private league's own rule
(confirmed 2026-08-09): points, then most correct exact scores, then
closest cumulative Golden Goal accuracy — stricter than the national
game, which simply splits after exact scores. `league.league_standings`
takes an optional `golden_goal_actual` map and produces
`LeagueStanding.golden_goal_average_distance`; `OpponentRound.golden_goal`
carries each participant's per-round guess. Not modelled in the forward
season simulator (no per-synthetic-round Golden Goal outcome exists to
draw from) — documented there as a deliberate simplification, since a tie
surviving both points and exact scores across thousands of simulated
worlds is already rare enough not to matter much.

**§19 reproducibility is stronger.** Each stored forecast version
(`prediction_history.normalise_fixture`) now keeps `forecast_basis`
(model vs `MARKET_ONLY_FALLBACK`) and a compact snapshot of the market
and availability state actually consumed — not just the output pick — so
"why did we predict Wolves 3-0 on 17 January" is answerable from what was
recorded, not only from the current live state. A `basis` change is
tracked as a material diff (§21) like a score or confidence change.
§19's `opponent_snapshot`/`strategy_prediction` linkage is now covered by
`strategy_history.py`: every strategy and season-strategy recommendation
is persisted append-only with the raw picks it deviated from, the
opponent data that existed at the time, and the `forecast_version` it was
taken against. The reference points strategy → forecast rather than the
spec diagram's forecast → strategy, because an append-only forecast
record cannot be reopened to add a field derived from it. Still open:
`feature_snapshot` beyond MANIFEST.md's static hash pin for the frozen
model (a genuine per-request corpus hash would only matter once the
model refits on a rolling basis, which it does not yet).

**§6 market breadth: totals odds can now be captured, opt-in.**
`sources/odds_api.py` fetches `h2h` only by default — `DEFAULT_MARKETS`
and the live budget are unchanged. Over/under goals parsing
(`TotalsOdds`) exists and activates by passing `markets="h2h,totals"`
explicitly, at the extra credit cost that implies (§25). Not consumed by
the forecast: Football-Data.co.uk, the backtesting corpus, has no
historical totals odds to validate a use against per §28's ablation
rule — captured for §6's "capture where available" and a future
prospective ablation, not wired into `P(score)`. Asian handicap, BTTS and
correct-score markets remain unfetched.

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

Per the phase gates in §23 and the ablation rule in §28. For what would
actually unblock each of these — not just that they're missing — see
[docs/next_steps.md](docs/next_steps.md).

- **xG** (Phase 1B) — no confirmed free source; fields are nullable by design.
- **Shin's method** for margin removal, **MOV-Elo** — both implemented and
  run through the ablation ladder on 2026-08-09, both **rejected**: Shin's
  is indistinguishable from proportional devigging on this corpus, and
  MOV-Elo is marginally worse than plain Elo, which ablation_003 had
  already found redundant. Available behind `devig="shin"` and
  `use_elo=True, elo_mov=True`; neither is in the frozen model path. See
  [results/ablation_007_shin_and_mov_elo.md](results/ablation_007_shin_and_mov_elo.md).
  (Promoted-team priors, §29.4, were adopted — see VALIDATION.md.)
- **Evidence → λ adjustments** (Increment 5 player-impact model) — the
  knowledge agent extracts and explains news/availability events (§8) but
  nothing yet converts one into a model parameter change; gated behind
  `QUANTITATIVE_IMPACT_MODEL_ENABLED = False` until a fitted, versioned
  impact model earns its place under §28. The training data for one is
  now accruing — `claim_outcomes.py` pairs each recorded claim with the
  result of the fixture it was about — but no model is fitted, and none
  should be until there is a defensible sample (`super6 readiness`).
- **Full §13.2 Round Opportunity Model characteristic-matching**
  (favourite strength, home/away balance, competitiveness, goal totals) —
  `season_simulation.sample_synthetic_round` draws fixtures from the
  historical corpus but doesn't yet match them to observed Super 6
  characteristics beyond "a competition the model knows"; a documented
  follow-on gated by §28, not assumed to matter until shown to. What the
  matching would aim at is now measured and accumulating
  (`round_characteristics.py`, `super6 readiness`); the sampler itself
  waits on enough rounds for an ablation to have any power.
- **100 Club annotation** (§12.1) — no source of national-entrant pick
  data exists to estimate scoreline rarity from.
- **FastAPI/Postgres** (§26) — the local UI runs on stdlib `http.server`
  and SQLite; the modelling core is plain Python modules precisely so a
  framework/database swap doesn't require a rewrite.
