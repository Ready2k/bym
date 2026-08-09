# Engine Status

For what's deferred and *why*, plus what would actually unblock each
item, see [next_steps.md](next_steps.md) — this file is a snapshot of
what's built, that one is the backlog.

Status levels, used strictly:

```text
BUILT           code exists
TESTED OFFLINE  exercised against mocks/recorded payloads
LIVE VERIFIED   a real network call has succeeded
ACTIVE          running and influencing output
SHADOW          running, recorded, NOT influencing output
```

An adapter existing is **not** "live".

## Component status

| Component | Status | Notes |
|---|---|---|
| Phase 1 forecast core | BUILT, TESTED OFFLINE, **ACTIVE** | `PHASE1_MODEL_V1`, frozen, holdout spent |
| Prospective ledger | BUILT, TESTED OFFLINE, ACTIVE | append-only, arms pre-registered |
| The Odds API adapter | **LIVE VERIFIED** | 20 UK books, consensus confirmed |
| API-Football adapter | **BLOCKED — free plan** | adapter correct vs 2024; current season paywalled |
| Tavily adapter | **LIVE VERIFIED** | `topic=news` required for dates |
| Market movement / divergence | BUILT, TESTED OFFLINE | needs live odds to exercise |
| Scheduler | BUILT, TESTED OFFLINE | restart-safe, SQLite, three job types |
| Always-on orchestration | BUILT, TESTED OFFLINE | odds + availability + event-driven knowledge |
| Submission state (§46) | BUILT, TESTED OFFLINE, ACTIVE | drift detection after entry |
| Availability intelligence | BUILT, TESTED OFFLINE | **no usable live feed** — see API-Football |
| football-data.org adapter | **LIVE VERIFIED** | fixtures/results/status; §2.2 VOID source |
| Result ingestion + VOID | **LIVE VERIFIED** | `super6 score --round-file` |
| Knowledge agent | BUILT, TESTED OFFLINE | **SHADOW** by default |
| Ollama extractor | BUILT, TESTED OFFLINE, **LIVE VERIFIED** | `llama3.1:8b` on Desktop-HomePC.local; extraction confirmed |
| API read-model contracts | BUILT, TESTED OFFLINE | frozen for the future UI |
| **UI (§37+)** | BUILT, TESTED OFFLINE, **ACTIVE** | see "Local decision UI" below — no longer deferred |
| Opponent history + profiles (§11) | BUILT, TESTED OFFLINE, ACTIVE | descriptive + model-relative — see caveat below |
| Opponent-aware strategy, single round (§12-14) | BUILT, TESTED OFFLINE, ACTIVE | current round only, kept alongside the season simulator below |
| Season Monte Carlo simulator (§13, `season_simulation.py`) | BUILT, TESTED OFFLINE, ACTIVE | Round Opportunity Model, paired significance test, ATTACK/DEFEND/NORMAL labels — see caveat below |
| Private-league tiebreak (§27 decision #7) | BUILT, TESTED OFFLINE, ACTIVE | points → exact scores → Golden Goal closeness, `league.league_standings` |
| **100 Club annotation (§12.1)** | **DEFERRED — not started** | no national-entrant data exists to estimate rarity from |
| **Increment 5 player-impact model (evidence → λ)** | **DEFERRED — not started** | deliberate, gated on `QUANTITATIVE_IMPACT_MODEL_ENABLED` |

## Local decision UI

```bash
super6 serve --round rounds/2026_27_round_01.json --seasons 2019 2025 --data data
```

A stdlib-`http.server` single-page app (`web.py`, `web_assets/`), not
FastAPI/Next.js (§26's optional stack item was never taken up) — the
contracts in `api/contracts.py` are the frozen boundary either could sit
behind. Pages: Home, Round, Cup Match, League, Opponents, Performance,
Activity, and an Expert group (Model Lab, Sources, System Health,
Settings) — matching most of §39's navigation. Covers UI Phase 1
(prediction cards, alternatives, Golden Goal, submission state, results)
and most of UI Phase 2 (intelligence feed, changes, alerts, source
provenance, system health) from §70. Exercised end-to-end against sample
data entered through the UI itself; covered by tests run from pytest
(`tests/js/`, `tests/test_web.py`).

Not built: a conversational "Ask the agent" entry point (§67, itself
Priority-low in the spec), and WCAG conformance has not been audited
(basic aria-live/aria-label/skip-link markup is present, nothing more).

## Opponent modelling & strategy — scope

What's live is narrower than §11-14 describe in full, though the gap
narrowed with `opponent_intelligence.py`:

* **Opponent profiles** (`league.opponent_profiles`) are descriptive —
  home/draw/away rate, average goals, common-score distribution
  (`score_frequency`, every scoreline seen, not just the top-3), and a
  `conservatism` share (picks with total goals ≤ 2).
* **Model-relative behaviour** (`opponent_intelligence.py`, new) joins
  those picks against our own stored forecasts
  (`prediction_history.round_snapshots`) to add what a pure pick tally
  cannot see:
  * `favourite_bias_profiles` — how often a rival's pick shares our
    model's favoured result (§11 "favourite bias" / "upset frequency").
  * `opponent_influence_weight` — a continuous 0→1 shrinkage weight from
    sample size alone (`n / (n + 20)`), the same empirical-Bayes shape as
    §29.4's promoted-team priors. §11 also names posterior_uncertainty and
    predictive_validation as inputs; both need an opponent-prediction
    track record to compute honestly and are a documented follow-on, not
    implemented yet.
  * `predict_pick_distribution` — §11/§49.3's "P(Dave predicts Arsenal
    2-0)" style forecast: shrinks a rival's own scoreline frequencies
    toward the league-wide pooled average by influence weight, then
    reweights toward/away from the fixture's favoured result using the
    rival's (also shrunk) favourite rate — the same
    `q(h,a) = p(h,a) × [q_R / p_R]` shape as §32.3's market reweighting
    (`model/market.py`), applied to two pick buckets instead of three
    results. Surfaced in the Opponents page as "Likely picks this round".
  * `chase_defend_tendencies` — §11 "chase when behind / defend when
    ahead": divergence rate from our model's favoured result while
    trailing vs while leading, using only standings from rounds strictly
    before the one measured (no leakage of the round's own outcome).
  * `team_market_reputations` / `team_bias_profiles` — §11's
    team-specific bias: does a rival back a club because of *which club
    it is*, rather than because of the fixture? Reputation comes from the
    bookmaker odds persisted with each forecast (`market_snapshot`),
    centred per venue so a team isn't rated "big" for having been drawn
    at home often, and each rival's rate of backing that team is compared
    against the price the market put on it — so a bias figure means "more
    often than the odds warranted", not "often". Promoted-team status
    comes from division membership via `priors.arrivals` (which division
    a team played in last season versus this one — no results, no model
    output). Surfaced per rival on the Opponents page.
  All of this is a documented heuristic baseline, not fitted or
  backtested — there is no opponent-prediction corpus yet to validate
  against, so §28's ablation discipline applies here exactly as it does
  to the forecasting core.
  Deliberate design constraint throughout: the fitted model's own
  attack/defence strength is *not* used as the "big club" proxy, and no
  hard-coded club list exists. `league.py` and `opponent_intelligence.py`
  stay independent of the forecasting core, which is why reputation is
  taken from market odds — externally sourced — instead.
* **Single-round strategy** (`strategy.py`, `league.recommend_strategy`)
  evaluates candidate pick sets for the current round only, using
  `strategy.noise_floor`/`preferred`'s ad hoc symmetric-margin check.
  Still there, unchanged, as the fast path the season simulator's UI panel
  sits alongside — see `_strategy_payload` vs `_season_strategy_payload`
  in `web.py`.
* **Season Monte Carlo simulator** (`season_simulation.py`, new) adds the
  season-length dimension §13 actually specifies:
  * `season_state` — §13.1's `points_gap`/`exact_score_gap`/
    `rounds_remaining`, computed from standings plus a user-supplied
    season length (Settings → Season; Sky never publishes this, so it
    stays a manual estimate, `season_rounds_total`, default 38).
  * `sample_synthetic_round` — §13.2's Round Opportunity Model: six
    fixtures drawn from the historical corpus and forecast with the
    *current* fitted parameters, standing in for a round Sky hasn't
    selected yet. Deliberately simpler than §13.2's full description —
    drawn from competitions the model already knows, not yet matched to
    observed Super 6 characteristics (favourite strength, home/away
    balance, competitiveness, goal totals). That richer matching is a
    documented follow-on, gated by §28 like everything else here.
  * `simulate_remaining_season` — combines the current round (real
    fixtures, candidate-specific) with a representative subset of future
    rounds (shared across every candidate — §13.3's common random
    numbers), simulating each known opponent's pick per fixture per
    simulated world from `opponent_intelligence.predict_pick_distribution`
    (falling back to our own model's matrix with no evidence), with the
    season's actual points-then-exact-score tiebreak (§2.1), not points
    alone.
  * `paired_comparison`/`PairedComparison.accepts` — §13.3's actual
    mechanism: `D_i = Win_i(candidate) - Win_i(baseline)`, `ΔP = mean(D)`,
    accepted only when the paired confidence interval's lower bound
    exceeds zero **and** clears `configurable_minimum_gain`. Replaces the
    single-round path's `noise_floor` symmetric-margin approximation for
    every candidate the season simulator itself ranks.
  * `recommend_season_strategy` — `NORMAL`/`ATTACK`/`DEFEND` as labels on
    whichever candidate the paired test actually accepts, never a
    hard-coded gap/rounds threshold. `ATTACK` names an accepted
    differentiation win; `DEFEND` names an accepted
    `strategy.build_correlate_candidate` win — a new candidate type that
    picks the field's single most-likely scoreline per fixture (pooled
    opponent predictions), the variance-reducing move §12 describes for a
    leader, which no candidate generator produced before this.
  Performance cap, stated rather than hidden: a web page load samples at
  most `MAX_SYNTHETIC_ROUNDS = 12` future rounds and 1,000 simulated
  worlds, not the full ~38-round season — only the current round's own
  contribution differs between candidates, so a representative subset is
  enough to compare them; `season_simulation.py`'s own functions accept
  any horizon and are uncapped when called directly (CLI/tests).
* **100 Club** (§12.1) still has no code path — there is no source of
  national-entrant pick data to estimate scoreline rarity from, and
  nothing here has invented one.

The single-round path is not wrong where it still runs — it is now
genuinely superseded by the season simulator wherever there is a
`recommendation` and at least one other participant in the standings; the
League page shows both panels, labelled by scope, so the difference is
visible rather than silently swapped.

## Private-league tiebreak (§27 decision #7)

SPEC.md's one remaining open question is now closed: the private league's
own tiebreak, confirmed 2026-08-09, is **points → most correct exact
scores → closest cumulative Golden Goal accuracy**, and only a tie
surviving all three levels actually splits (stricter than the national
game, which splits after exact scores).

```text
league.OpponentRound.golden_goal          per-round guess, optional, 1-90
web.py: DEFAULT_UI_STATE["golden_goal_actual"]   per-round actual, optional
league.league_standings(history, results, golden_goal_actual)
    -> LeagueStanding.golden_goal_average_distance
       mean |guess - actual| over rounds where both are known;
       None (never 0) ranks behind any participant with real evidence
```

Deliberately **not** modelled in the forward-looking `season_simulation.py`
Monte Carlo — nothing there simulates a Golden Goal outcome per synthetic
round, so ties surviving both points and exact scores across thousands of
simulated worlds fall back to a plain split. Documented as a stated
simplification (`simulate_remaining_season`'s tiebreak comment): such ties
are already rare enough with continuous simulated point totals that this
has negligible effect on P(Finish 1st), and `league_standings` — the real,
recorded table — is where the third level is fully implemented and
actually matters.

Surfaced in the UI: a Golden Goal field on the opponent-import form
(`#opponents`), an actual-minute field on the results form (`#round` →
Results), and a "Golden Goal avg." column on the League standings table.

## §19 reproducibility

`prediction_history.normalise_fixture` now retains, per stored forecast
version, the inputs that actually drive a pick — not only the output:

```text
basis                model vs MARKET_ONLY_FALLBACK (per fixture, not
                     just round-level model_version)
market_snapshot      home/draw/away probability, bookmaker count,
                     agreement, staleness — as consumed, not live
availability_snapshot  confirmed/expected lineup counts, data quality,
                       item count, quantitative_impact ("NONE DIRECTLY")
```

A `basis` change is now a tracked diff (`_TRACKED_FIELDS`), surfaced the
same way a score or confidence change is (§21). Fixing this correctly
required a second fix: adding a field to `_TRACKED_FIELDS` on an
already-running round previously produced one spurious "changed" event
per fixture the next time it was recomputed, since the old snapshot
simply never had that key. `_changes()` now requires the field to have
**existed** in the previous snapshot (`field in previous`, not `.get()`)
before treating its value as having moved — a schema addition is not a
forecast change. Caught live against real stored data
(`data/prediction_history.json`, `data/events/activity.jsonl`), both
cleaned up; see `test_a_newly_tracked_field_does_not_spuriously_diff_an_existing_round`.

Not covered: `opponent_snapshot`/`strategy_prediction` linkage (no
strategy recommendation is persisted anywhere to link to yet), and a
per-request `feature_snapshot` hash (the frozen model's training data is
already hash-pinned once, permanently, in MANIFEST.md — a new hash per
forecast run would only add value once the model refits on a rolling
basis).

## §6 market breadth

`sources/odds_api.py` fetches `h2h` (1X2) only by default —
`DEFAULT_MARKETS = "h2h"`, unchanged, so the live budget (§25) and blend
weight are unaffected. Added: `TotalsOdds` (over/under goals), parsed
whenever the response contains a `totals` market — which only happens if
a caller explicitly passes `markets="h2h,totals"` to `fetch_odds`, at the
extra credit cost `request_cost` already accounts for (`markets x
regions`). Nothing calls it that way yet; the capability exists to
collect without being switched on.

**Not wired into the forecast, deliberately.** Football-Data.co.uk (the
walk-forward backtesting corpus, §16) carries H/D/A odds only — there is
no historical totals data to test a "does this improve calibration"
claim against, so nothing here feeds `TotalsOdds` into `P(score)` or
§32.3's rescaling. Consistent with §28: captured for §6's "capture where
available" and a future prospective ablation once real totals history
accumulates through 2026/27, not assumed to help.

Still unfetched: Asian handicap, BTTS, correct-score markets.

## Ollama

```text
Host:  Desktop-HomePC.local  (configurable via OLLAMA_BASE_URL)
Role:  structured claim extraction ONLY
Mode:  shadow by default
```

Ollama has **no route** to `λ_home`, `λ_away`, `P(score)`, the market
blend weight `w`, Dixon-Coles parameters, forecast probabilities or
strategy scores. It receives evidence text and emits candidate claims
against the existing `CandidateClaim` schema; those claims then pass the
same deterministic validators as any other extractor.

Forbidden output fields (`impact_score`, `lambda_adjustment`,
`confidence`, `weight`) are named explicitly and fail validation loudly
rather than being silently dropped.

Configuration, all environment-driven and safe by default:

```bash
export CLAIM_EXTRACTOR=ollama
export CLAIM_EXTRACTOR_MODE=shadow
export OLLAMA_BASE_URL=http://Desktop-HomePC.local:11434
export OLLAMA_MODEL=llama3.1:8b
```

Health check:

```bash
super6 health
```

Diagnoses DNS failure, connection refused, timeout, missing model and
unexpected API responses separately, and never prints credentials — a URL
containing embedded auth is reduced to its hostname before it reaches any
log or provenance record.

## Shadow mode

Turning the LLM on is deliberate **twice**: an extractor must be
configured, and then promoted out of shadow. In shadow mode claims are
retrieved, extracted, validated, stored with full provenance, and marked
`promoted = 0` — they never enter reconciled availability state.

```bash
super6 shadow-report
```

reports counts only: emitted, accepted, rejected, promoted, failed runs,
and rejection reasons by category. **No AI-generated quality score is
produced**, because manual review of real football reporting is the point.

## Provenance

Every extraction run records provider, model, model digest where the
server supplies one, host, prompt version, extractor version, mode,
requested/completed timestamps, evidence hash and outcome counts. No
secrets, no article content — retention remains metadata plus a
400-character audit snippet.

## Live verification

```bash
super6 verify-live
```

Checks each source independently. **Approval is never bypassed**: a source
that is not `APPROVED` reports `BLOCKED` rather than being queried, and a
successful verification approves nothing. Absence is distinguished from
failure — a lineup not yet published and an empty injury list are both
`absent`, not faults.

Current live verification status:

```text
the_odds_api      LIVE VERIFIED  (20 bookmakers, de-vig + consensus confirmed)
api_football      FAILED         (free plan covers 2022-2024 ONLY; 2026 paywalled)
tavily            LIVE VERIFIED  (topic=news required for published_date)
football_data_org LIVE VERIFIED  (380 PL fixtures 2026/27; no lineups on plan)
ollama            LIVE VERIFIED  (llama3.1:8b, 2 claims accepted end-to-end)
```

Approval remains a manual act:

```bash
super6 sources approve the_odds_api
```

`sky_super6` is permanently `PROHIBITED` and `approve()` refuses it.

## Always-on orchestration

The scheduler runs three job types, deliberately separate because they
have different cadences and different budgets:

```text
POLL_ODDS           §9 deadline ladder
POLL_AVAILABILITY   injuries routinely; confirmed XIs only inside the
                    75-minute pre-kickoff window
KNOWLEDGE_RESEARCH  opt-in (--with-knowledge); costs search credits
```

```bash
super6 watch --round rounds/round_01.json --seasons 2021 2026 --data data --loop
```

**Event-driven investigation.** A MAJOR market move with no availability
event nearby is the §21 "REVIEW REQUIRED — no confirmed news detected"
case. The odds worker flags it, and the knowledge layer investigates that
specific fixture — regardless of whether routine research is scheduled.
This is the one place knowledge runs unscheduled, because it is exactly
the event-driven case it exists for. A move that *is* explained by known
team news triggers nothing.

Each layer reschedules independently, and one failing layer neither stops
the others nor abandons its own job — it records the error and retries.

## Submission state (§46)

The system cannot know what you entered into Sky, so you tell it, and its
job is then to notice when its own recommendation drifts away from what
you entered.

```text
NOT_READY → READY → CHANGED_SINCE_REVIEW
                  → MARKED_AS_SUBMITTED → CHANGED_AFTER_SUBMISSION
                  → ROUND_CLOSED
```

```bash
super6 submission mark-entered --round 1 --picks 2-0 1-0 2-1 1-2 3-0 1-2 --golden-goal 9
```

```bash
super6 submission status --round 1
```

`CHANGED_AFTER_SUBMISSION` is the state the mechanism exists for, and its
wording always ends with "Remember to update these predictions in Super 6
yourself" — nothing in this system submits anything, and a test asserts
no phrasing implies otherwise.

## Engine run

```bash
super6 engine-run --round rounds/round_01.json --seasons 2021 2026 --data data
```

Produces a four-layer report — FORECAST INPUT, MARKET INTELLIGENCE,
STRUCTURED TEAM INTELLIGENCE, KNOWLEDGE INTELLIGENCE — and states
explicitly that team/news intelligence has `quantitative impact: NONE
DIRECTLY`. Every external step degrades to "absent" rather than failing
the run, so a forecast is produced with no credentials at all.

`--json` writes the `RoundSummary` contract for contract inspection.

## Tests

```text
536 passing, 6 skipped (live)
```

Network access is blocked at the socket layer for the entire ordinary
suite, so "no test makes a network call" is enforced rather than trusted.
Live tests opt back in with `SUPER6_LIVE_TESTS=1`.

## Pre-UI blockers — resolved

The four items formerly gating §37+ UI work are now cleared, which is why
the UI row above reads ACTIVE rather than deferred:

1. **DONE** — all four sources (`the_odds_api`, `api_football`,
   `football_data_org`, `tavily`) are registered `APPROVED` in
   `config/source_registry.json`, with keys present under `secrets/`.
2. **DONE** — Ollama live-verified 2026-08-08 on `llama3.1:8b`.
3. **DONE** — see "Live verification status" above; 4 of 5 checks passed,
   `api_football` failed on data coverage (not on approval), as documented.
   Quota files (`config/*_quota.json`) additionally show real calls
   landing against `api_football` and `the_odds_api` as recently as
   2026-08-09, so the gate stays exercised, not just checked once.
4. **DONE** — the engine has run repeatedly against live sources
   (`data/prediction_history.json`, `data/submissions.json` both updated
   2026-08-09) against `rounds/2026_27_round_01.json`, which holds the
   real 2026/27 Round 1 fixtures/odds. Renamed 2026-08-09 from
   `example_round.json`: every file in `rounds/` is now a real round named
   `<season>_round_<NN>.json`, so nothing there reads as a template.

§37+ UI work is under way against the frozen contracts in
`super6/api/contracts.py` — see "Local decision UI" above for what's
built and what's still missing.
