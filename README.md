# Beat Your Mates — Always-On Prediction Agent

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
bym fetch --seasons 2019 2025 --data data
```

Completed seasons are downloaded once and never re-fetched — they cannot
change. The season currently being played is re-downloaded when its copy
is more than a day old, so results from the campaign in progress reach the
model without anyone remembering a flag. If that refresh fails the cached
copy is used and a warning says how old it is. `--force` re-downloads
regardless.

Enter a round manually (§9.1 — Sky's fixture selection and deadline are
config, not derived data). Copy `rounds/example_round.json` to
`rounds/<season>_round_<NN>.json`, edit the six fixtures + deadline, then:

```bash
bym predict --round rounds/2026_27_round_01.json --seasons 2021 2025 --data data
```

Output is the §20 pre-round report: six picks with RESULT / SCORE / EV /
CONF columns, Golden Goal minute (with the Sky one-off interval convention,
§15), and expected round score.

Club names may be written either way — Sky's "Manchester City" or the
corpus's "Man City". They are resolved to the corpus form on load and the
substitution is reported. A name that resolves to nothing is kept as typed
and warned about, because it will be forecast from a league-average
stand-in rather than from that club.

Your own round files are gitignored; only the example is tracked. This
manual copy is one way in, not the only one — the local UI below creates
and switches rounds directly from the browser, including a first-run
setup screen when no round file exists yet.

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

Round setup, source approval and API key entry are all in the browser
too, not just read-only views: Round control's Setup/Rounds tabs create
and switch between rounds, Sources has an Approve button for anything
`REVIEW_REQUIRED`, and Settings has an API keys section that writes
straight into `secrets/` — none of it needs a terminal. A deployment
with no round file yet (a fresh checkout, or a fresh Docker volume — see
below) shows a one-time "set up your first round" screen instead of the
dashboard, so the manual copy step above is optional, not required.

```bash
bym serve --round rounds/2026_27_round_01.json --seasons 2019 2025 --data data
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000). The server is
local-only by default. It never submits predictions to Sky.

To keep the local UI running outside a terminal, use:

```bash
./scripts/bym-service start
./scripts/bym-service status
./scripts/bym-service stop
```

`start` also launches a worker — a looped `bym tick` — alongside the UI,
so scheduling a job from the browser (Round → Setup →
"Confirm & start monitoring", or approving a source) actually gets
polled without a second terminal command. Set `BYM_WORKER=0` to run the
UI only. `restart` and `logs` are also available (`logs` shows both
processes); the script uses `.venv` when it is present, and `BYM_PORT`,
`BYM_DB`, or `BYM_SUBMISSIONS` override its local defaults.

Walk-forward backtest against the §16/§28 baselines — MARKET-AVAILABLE
(early odds — the fair target), MARKET-ORACLE (closing odds — theoretical
gold standard), pure Poisson, and Dixon-Coles pooled vs per-division. See
[VALIDATION.md](VALIDATION.md) for the binding season-role protocol
(2019-23 development / 2024-25 validation / **2025-26 untouched final
test**) and [results/](results) for frozen historical results:

```bash
bym backtest --seasons 2019 2024 --test-season 2024 --data data
```

Select the recency-decay hyperparameter ξ by out-of-sample validation
(§29.2 — never by MLE):

```bash
bym tune-xi --seasons 2019 2024 --validation-season 2024 --data data
```

## Docker deployment

For a NAS or any host where running the CLI by hand isn't practical:

```bash
docker compose up -d --build
```

Two containers share one image: `web` (`bym serve`, port 8000) and
`worker` (an always-on `bym tick` loop — not `bym watch --loop`, which is
scoped to one round and exits once its jobs finish). They share a
database on purpose, so jobs the browser schedules are the ones the
worker actually polls. `./data`, `./rounds`, `./config`, `./secrets` and
`./results` are bind-mounted from the host next to `docker-compose.yml`,
not hidden inside Docker's own storage — visible and backupable through a
NAS's own file manager. Nothing here needs a shell inside the container:
open `http://<host>:8000`, create the first round in the onboarding
screen, then add API keys under Settings to turn monitoring on — the
worker picks a saved key up on its next cycle, no restart needed. See
[docker/README.md](docker/README.md) for the full walkthrough.

## Other commands

`bym <command> --help` gives the full flag list for any of these; the
common `--data`/`--seasons`/`--divisions` flags mean the same thing they do
above.

**Always-on operation** — `worker.py`'s §9 loop, driven by the scheduler
rather than a one-off report:

- `bym watch --round rounds/2026_27_round_01.json --seasons 2019 2025 --data data`
  — schedules odds/availability polling for a round per the §9 cadence.
  `--loop` runs it now instead of waiting for cron/systemd; `--with-knowledge`
  also schedules routine knowledge research (costs Tavily credits).
- `bym tick --seasons 2019 2025 --data data` — runs one scheduler pass;
  what cron/systemd actually calls.
- `bym jobs` — lists scheduled polling jobs and their next-run times.
- `bym engine-run --round rounds/2026_27_round_01.json --seasons 2019 2025 --data data`
  — one full four-layer engine pass (forecast/market/team/knowledge) for a
  round, independent of the scheduler. `--json out.json` writes the
  `RoundSummary` API contract to a file.
- `bym health` — engine + Ollama health check (schema version, pending
  jobs, extractor mode, per-source status).
- `bym activity` — the §53 chronological activity feed. `--type` filters
  by event type; `--all` includes routine snapshots, not just material
  changes.

**Sources & credits** — §18.1 registry and metering:

- `bym sources list` / `bym sources approve <source_id>` — inspect or
  promote entries in `config/source_registry.json`. The Sources page in
  the local UI has an Approve button for the same action.
- `bym quota` — API credit usage across all metered sources.
- `bym verify-live` — live-checks each external source (not a cached
  read), for confirming credentials and connectivity actually work.
- `bym coverage --season 2025` — probes API-Football's fixture coverage
  per league/season before relying on it.
- `bym research status|sample|harvest|prevalence|freeze` — the standalone
  `AVAILABILITY_ORACLE_RESEARCH_V1` harness (`research/`) that measures
  whether availability data would be worth wiring into λ; separate from the
  live model, gated by its own request cap and pacing flags.

**Submission & scoring** — §46 state machine and §19 prospective ledger:

- `bym submission status|mark-reviewed|mark-entered --round <file>` — the
  local record of whether you reviewed/entered picks in Sky. The agent
  itself never submits anything (see above).
- `bym record --round <file> --seasons 2019 2025 --data data` — records
  prospective picks for a round before its deadline, for the §19 arm
  comparison. `--submitted` logs the six scores you actually entered.
- `bym score --round <file>` — scores a recorded round once results are
  known; fetches results from football-data.org via `--round-file` if
  `--results` isn't given directly.
- `bym ledger` — the running prospective comparison across arms behind
  [results/prospective_2026_27.md](results/prospective_2026_27.md).

**Model diagnostics**:

- `bym forecast --home <team> --away <team> --seasons 2019 2025 --data data`
  — forecasts one ad-hoc fixture as a sanity check. `--odds H D A` blends in
  a market price the way the real engine would; `--extended` widens the
  corpus to League One/Two and the National League for cup ties, which
  means it is no longer `PHASE1_MODEL_V1`.
- `bym tune-market --seasons 2019 2024 --validation-season 2024 --data data`
  — selects the §32 market-blend weight w by validation, the same way
  `tune-xi` selects ξ.
- `bym final-test --seasons 2019 2025 --test-season 2025 --data data --i-understand-this-is-single-use`
  — the single-shot frozen-model run against the untouched holdout season.
  Refuses unconditionally if `results/final_test_<season>.md` already
  exists (`assert_holdout_unspent`, [VALIDATION.md](VALIDATION.md)) — the
  acknowledgement flag is extra friction on top of that, not the only
  guard. Model Lab's "Final holdout test" control in the local UI is
  behind the same gate, plus a typed confirmation phrase.
- `bym readiness` — how close the data-blocked backlog items
  ([docs/next_steps.md](docs/next_steps.md)) are to having enough evidence
  for an ablation: claim-outcome sample size and round-characteristic
  coverage.
- `bym shadow-report` — metrics on the knowledge agent's `SHADOW`-mode
  extractions (§8), since nothing yet consumes them into the model.

**Last Man Standing** (`src/bym/lms.py`) — a separate, smaller game
reusing the same fitted Dixon-Coles router: pick one club per gameweek,
survive only if it wins, never reuse a club. Not part of the §1-53 spec —
its own module, its own state file (`data/lms_state.json`), no exact
scores or EV involved.

The format's trap is that a club is a one-use resource for the whole
season, so the club most likely to win *this* gameweek is regularly one a
later gameweek needs more. Picking week by week spends the best clubs
first and leaves coin-flips for the weeks that have no alternative, which
is why `lms-plan` — not `lms-recommend` — is the command to start from.

- `bym lms-plan --seasons 2019 2025 --data data --entrant <name>`
  — assigns one club to each of the next `--horizon` gameweeks (4 by
  default) so that the probability of surviving **all** of them is as high
  as it can be, and prints what taking the weekly favourite would have
  cost instead. Solved exactly, not approximated: maximising ∏ P(win) is
  minimising ∑ −log P(win), a rectangular assignment problem where "one
  club once" is the constraint that no club is used twice
  (`scipy.optimize.linear_sum_assignment`). Only the first pick is
  committed — the rest are re-planned every week as fixtures and form
  move. With `--entrant`, planning starts at the first gameweek that
  entrant has not already booked: a booked week has spent the week as well
  as the club, so planning from it would name a pick for a passed deadline
  and solve the weeks after it as though the club it suggested were still
  to be spent. Future gameweeks are shrunk toward their own mean before planning
  (`LMSConfig.future_shrink_per_week`), because a fixture a month out is
  forecast from today's squads; the survival probabilities reported are
  the unshrunk ones, so the plan is chosen cautiously and scored honestly.
  One request covers the whole horizon, so planning ahead costs no extra
  API quota.
- `bym lms-backtest --seasons 2019 2025 --test-seasons 2025 --data data`
  — leak-free replay of historical Premier League fixture slates. Reports
  weekly win accuracy and calibration, plus actual four-slate survival for
  the one-club-once planner versus its legal greedy alternative. Football-
  Data has no official matchweek field, so the command labels its date and
  club-repeat reconstruction as fixture *slates*; its market comparison is
  the corpus's earlier-price proxy, not a claim about an exact LMS deadline.
- `bym lms-recommend --seasons 2019 2025 --data data --entrant <name>`
  — every club still eligible this gameweek, ranked by P(win) (from the
  same score matrix Super 6 uses, just read as home/draw/away instead of
  an exact score). This is the menu, not the recommendation — see
  `lms-plan`. Fetches the current Premier League gameweek from
  football-data.org automatically (needs `FOOTBALL_DATA_ORG_KEY`) — no
  fixture list to type in. `--entrant` pulls the used-club list from that
  entrant's saved state so used clubs are excluded automatically; `--used`
  excludes extra clubs ad hoc without a state file.

  Confidence bands here are LMS's own (`LMSConfig`, HIGH ≥ 70%), not Super
  6's §20 result bands (HIGH ≥ 58%): the same probability means something
  different when being wrong ends the competition rather than costing
  points off one fixture of six. Both are provisional judgement rather
  than backtested — a survivor competition gives one observation per
  entrant per season, so there is nothing yet to calibrate against.

  Where the competition publishes what everyone else picked, adding a
  `popularity` map to the gameweek file also prints a **pot equity**
  table: surviving alongside the whole field wins nothing, so a
  heavily-backed favourite is worth less than its win probability
  suggests, and beating a club the field has piled onto is worth more.
  ```json
  {"gameweek": 1, "fixtures": [...], "popularity": {"Man City": 55, "Liverpool": 20}}
  ```
  Shares may be percentages or fractions. It is a one-gameweek measure of
  pot share, deliberately kept separate from the horizon plan rather than
  fused into a single score — the exchange rate between "survive longer"
  and "survive alone" depends on pot size and rollover rules the model
  knows nothing about.
- `bym lms-fixtures --seasons 2019 2025 --data data [--gameweek <file>]`
  — fetches and saves the current gameweek on its own, e.g. for `lms-settle`
  to read later. Without a key (or as a manual override — a late
  postponement, a team name the resolver missed), a gameweek file can still
  be hand-written:
  ```json
  {
    "gameweek": 1,
    "fixtures": [
      {"home": "Arsenal", "away": "Everton"},
      {"home": "Fulham", "away": "Brighton", "odds": [2.1, 3.4, 3.2]}
    ]
  }
  ```
  — however many fixtures the Premier League actually plays that week, not
  Super 6's fixed six; `odds` is optional per fixture, same [H, D, A]
  convention as a Super 6 round. Pass it to `lms-recommend` with
  `--gameweek <file>` to use it instead of the live fetch. `lms-plan`
  accepts it too, but only as a fallback when the fetch fails: one
  gameweek is not a horizon, so the "plan" degenerates to the weekly
  favourite and says so.
- `bym lms-pick --entrant <name> --gameweek <n> --team <club> --opponent <club> [--home]`
  — records a pick, or changes one already recorded for that gameweek. A
  club is only spent once the round it was picked for kicks off, so
  changing before then is a legal move rather than a correction: the
  replaced club is released back to the entrant automatically, which is
  the step editing `lms_state.json` by hand tends to miss. After the first
  match of the gameweek starts, or once the pick has been settled, it is
  final. A club already used in a *different* gameweek is still refused.
  Gameweeks written by hand carry no kickoff time and so stay changeable —
  locking what cannot be timed would be worse than trusting the rule the
  competition already trusts you with.
- `bym lms-settle [--fd-season 2026]` — fetches full-time results from
  football-data.org (needs `FOOTBALL_DATA_ORG_KEY`, same source `bym
  score` uses) and settles every PENDING pick that's finished. This is the
  trusted path: in a multi-entrant competition, an entrant reporting their
  own elimination is a conflict of interest an automatic feed doesn't
  have. It works off the saved picks rather than a gameweek file, so a
  pick still settles after the competition has moved on to a later
  gameweek — which is the normal case for anything played on a Monday.
- `bym lms-result --entrant <name> --gameweek <n> --result WIN|DRAW|LOSS`
  — settles a pick **by hand**. Fallback only, for a fixture `lms-settle`
  can't resolve (no key configured, or outside football-data.org's
  coverage) — the saved record is tagged `MANUAL` either way, visible in
  `lms-status` and the UI, so a self-reported result is never
  indistinguishable from a verified one.
- `bym lms-status [--entrant <name>]` — survival status and pick history,
  each entry tagged `(auto)` or `(manual)`; omit `--entrant` to list
  everyone recorded.

It also has its own tab in the local dashboard (`bym serve`, see below) —
the next few gameweeks' fixtures are fetched and planned automatically on
every page load: type an entrant name, see the recommended pick with the
plan behind it and what the weekly favourite would have cost, then every
eligible club with a **Pick** (or **Switch**) button.

Two things there answer questions the numbers alone don't. **Pick lineage**
draws the path you have actually taken against the path the plan would
have given, as cumulative survival across the horizon — survival is a
product, so a week's deficit is not paid that week, it is carried by every
week after it, and the curves show the gap opening and never closing.
**Selecting any club** re-solves the remaining gameweeks with that club
spent and redraws the same curves, so "what would picking Hull cost me"
is answered over the whole horizon rather than by one week's win
probability. Nothing is saved until a button is pressed.

Every load also tries `lms-settle`'s result fetch; a
pending pick only ever shows manual WIN/DRAW/LOSS buttons when no
`FOOTBALL_DATA_ORG_KEY` is configured, and using them is logged as
self-reported. Without a key the fixtures editor becomes the primary way
in, same as the CLI's hand-written gameweek file. Same state files as the
CLI (`data/lms_gameweek.json`, `data/lms_state.json` by default), so either
interface can be used interchangeably.

## Layout

```
src/bym/
├── config.py            tunable defaults — config, not magic numbers (§30, §31)
├── scoring.py           Super 6 points rules + fixture status (§2, §2.2)
├── round.py             manual round entry (§9.1)
├── recommend.py         §20 report, EV picks, Phase 1A confidence
├── engine.py            four-layer report: forecast/market/team/knowledge (`bym engine-run`)
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
├── verify.py            `bym verify-live` — per-source live checks
├── league.py             opponent history, league table, single-round strategy sim (§11-13)
├── opponent_intelligence.py  favourite bias, team bias, influence weight, predicted picks, chase/defend (§11)
├── strategy.py           single-round candidate generation + CORRELATE (DEFEND) candidate
├── season_simulation.py  season Monte Carlo: season state, Round Opportunity Model, paired test, mode labels (§12-13)
├── round_characteristics.py  what real Super 6 rounds look like — the §13.2 matching target
├── claim_outcomes.py     recorded team-news claims paired with realised results (§8 step 1)
├── db.py                 SQLite schema (jobs, availability, evidence, claims, …)
├── secrets.py            credential loading, hostname-only in logs/provenance
├── validation.py         single-use-holdout gate shared by the CLI and UI
├── web.py + web_assets/  local decision UI (`bym serve`, stdlib http.server)
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
│   ├── shots.py           zero-cost xG stand-in from shot counts (§7, REJECTED — ablation_009)
│   ├── market.py          margin removal + result-constrained reweighting (§32)
│   ├── matrix.py        score matrix w/ tail handling + EV (§31, §2/§33)
│   └── golden_goal.py   first-goal hazard, Sky minute bands (§15)
├── api/                  frozen read-model contracts (`contracts.py`) + service layer
├── research/              availability-oracle research harness (separate from the live model)
└── backtest/
    ├── walk_forward.py  leak-free walk-forward + ablation baselines (§16, §28)
    └── metrics.py       pts/fixture, exact %, Brier, log loss (§16)
```

`Dockerfile` and `docker-compose.yml` at the repo root package `bym serve`
and a looped `bym tick` from that same image — see
[Docker deployment](#docker-deployment) above.

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
prospective ablation, not wired into `P(score)`. BTTS and correct-score
markets remain unfetched and have no historical corpus to validate
against. Asian handicap and over/under 2.5 *are* in the historical
corpus at ~100% coverage and are now parsed; both were tested as score-
matrix constraints on 2026-08-10 and **neither was promoted** —
[results/ablation_008](results/ablation_008_totals_and_handicap.md).

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

- **xG** (Phase 1B) — fields are nullable by design. Free routes are now
  exhausted and documented: no free source covers the Championship (59%
  of the corpus) since Opta cut FBref's feed in January 2026, and the
  zero-cost shots proxy was built and **rejected** — it lacks shot
  quality, so it persistently underrates strong teams
  ([results/ablation_009](results/ablation_009_shot_proxy.md)). Four
  candidate sources have now been probed and dated in next_steps.md item
  4; none is usable. A paid Championship-covering feed (~£30/month
  against §25's £10 target) was **declined on 2026-08-10** — the item is
  closed, and reopens only if a free, terms-permitting source appears.
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
  should be until there is a defensible sample (`bym readiness`).
- **Full §13.2 Round Opportunity Model characteristic-matching**
  (favourite strength, home/away balance, competitiveness, goal totals) —
  `season_simulation.sample_synthetic_round` draws fixtures from the
  historical corpus but doesn't yet match them to observed Super 6
  characteristics beyond "a competition the model knows"; a documented
  follow-on gated by §28, not assumed to matter until shown to. What the
  matching would aim at is now measured and accumulating
  (`round_characteristics.py`, `bym readiness`); the sampler itself
  waits on enough rounds for an ablation to have any power.
- **100 Club annotation** (§12.1) — no source of national-entrant pick
  data exists to estimate scoreline rarity from.
- **A screen-reader and hand-driven keyboard pass.** WCAG 2.1 A/AA was
  audited with axe-core on 2026-08-09 (one level-A failure found and
  fixed; see [docs/accessibility_audit.md](docs/accessibility_audit.md)),
  but keyboard interaction could not be exercised through the browser
  harness and nothing has been listened to with a screen reader.
- **FastAPI/Postgres** (§26) — the local UI runs on stdlib `http.server`
  and SQLite; the modelling core is plain Python modules precisely so a
  framework/database swap doesn't require a rewrite.

## Licence

The project source code is licensed under the [Apache License 2.0](LICENSE).
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for important exclusions
and third-party terms.

## Attribution

The code in this repository was written with [Claude](https://claude.com)
(Anthropic). The project's direction, specification, and documentation —
including [SPEC.md](SPEC.md) and the design/rules records in `docs/` and
`research/` — are the author's own.
