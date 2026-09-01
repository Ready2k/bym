# Beat Your Mates — Fantasy Premier League Mode
## Specification v0.5 — addendum to SPEC.md

**Status: approved for Phase 0/1A start**, with three rule corrections and one
lifecycle addition folded in from the 2026-08-17 v0.4 review (§20). No further
architecture revision is expected unless Phase 0/1A implementation surfaces a
real contradiction — this document has done its job.

**Reviewed: 2026-08-17 — consolidated canonical revision, supersedes v0.1–v0.4**

This document specifies a **third `bym` game mode**: Fantasy Premier League
(FPL) — player projections, squad selection under a budget, captaincy,
transfers, chips, and eventually private-mini-league strategy.

This is now the **single evolving document** for the FPL mode, matching
`SPEC.md`'s own convention (one file, one version number, a cumulative
Decisions Log) rather than a new file per revision. The three drafts that
preceded it (v0.1 full draft, v0.2 data-source-policy correction, v0.3
data-strategy revision) are retired in favour of this document; their
history is preserved in §20 rather than kept as separate files.

It follows
[research/FPL_VIABILITY_RESEARCH_V1.md](../research/FPL_VIABILITY_RESEARCH_V1.md).
The architectural conclusion is unchanged: FPL cannot be a thin decision
layer over the existing Dixon-Coles primitive because it needs a new
player-level data axis, but it **must reuse the existing match-level model
as an input rather than duplicate or retune it**.

Player-performance data is **not a Phase 1 blocker** as of the 2026-08-17
data-source review (§5): existing approved sources plus carefully scoped
research datasets are sufficient to build and audition the Phase 1 player
model. A calibrated production freeze still requires permitted,
reproducible FPL outcome labels. The remaining load-bearing live-data
question is FPL-specific game state: current FPL prices/positions and the
user's exact squad/selling values/bank/chips (§13) — the undocumented
`fantasy.premierleague.com/api/*` endpoints remain `REVIEW_REQUIRED` until
a human source-policy review is complete. Phase 1's design supports either
an approved read adapter or a user/permitted-source import path without
changing projection or optimiser interfaces.

Cross-references to `§N` without a document name mean this document.
`SPEC.md §N` means the parent spec. Identifiers use `fpl_*`, matching the
`super6_*` convention in CLAUDE.md's Naming section — this project stays
`bym`, the game it targets is `fpl`, just as the other mode targets
`sky_super6`.

---

# 1. Objective & Relationship to Super 6 / LMS

`bym` already runs Super 6 and Last Man Standing against the same frozen
match-level core. FPL adds a genuinely new decision problem: estimate player
point distributions, then choose a legal squad/line-up/captain/transfer plan.
The match core remains upstream evidence only.

`bym` recommends; the human makes every FPL change. The agent never submits a
transfer, activates a chip, changes a captain, or manages an authenticated FPL
session. See §14.

## 1.1 Objective Priority Ordering

1. If a **primary Classic private mini-league** is configured, maximise the
   probability of finishing first in that league over the remaining season,
   subject to the user's current squad/chip state. This is the FPL analogue of
   SPEC.md §1.1's private-league-first objective.
2. If no primary private league is configured, maximise expected FPL points.
   Expected overall rank may be reported separately, but Phase 1 does not
   pretend that raw xP and expected rank are mathematically identical.
3. Always expose downside before action: transfer hits, bench/captain risk,
   budget sensitivity, chip opportunity cost, and horizon uncertainty.

If multiple private leagues are configured, one must be explicitly selected as
`primary_league_id` for optimisation. Others are reported as secondary impacts;
the optimiser must not silently average incompatible league objectives.

## 1.2 League Types

Phase 3 first supports **Classic** private leagues, where total FPL points drive
rank. Head-to-Head leagues use a different weekly win/draw/loss objective and
are a separate future strategy mode, not a flag on the Classic objective.
League cups are reporting-only until a dedicated knockout objective exists.

---

# 2. FPL Rules Recap (2026/27 season)

These rules are model inputs and must be versioned by season. They are not a
substitute for the official rules and must be re-verified before every season
freeze.

**Squad.** 15 players: 2 GK / 5 DEF / 5 MID / 3 FWD, £100.0m initial
budget, maximum 3 players per Premier League club. A starting XI has exactly
1 goalkeeper, at least 3 defenders, at least 2 midfielders, and at least
1 forward. Captain points are doubled; vice-captain takes over only if the
captain records no appearance.

**Gameweek deadline.** All transfers, line-up, captain, vice-captain, bench
order and chips for a Gameweek lock **90 minutes before the first fixture of
that Gameweek**. Information first published after that instant is future
information for that Gameweek and is prohibited from its recommendation path.

**Scoring.** Appearance points, goals by position, assists, clean sheets,
goals conceded for GK/DEF, goalkeeper saves, penalty saves/misses, cards, own
goals, bonus, and defensive-contribution points. For 2026/27, defenders earn
+2 for 10+ CBIT actions; midfielders/forwards earn +2 for 12+ CBIRT actions.
DefCon remains capped at +2 per match.

The 2026/27 Bonus Points System is also season-versioned. Among the published
changes: being tackled no longer loses BPS; CBI earns 1 BPS per three actions
rather than per two; goalkeeper save/BPS treatment has been retuned. The BPS
**weights are published**; the modelling limitation is forecasting all of the
underlying Opta event inputs and the player's relative BPS rank within a
fixture, not discovering a secret weighting formula.

**Transfers.** One new free transfer is earned per Gameweek and unused free
transfers can be banked up to **five**. Each additional transfer beyond the
available free transfers costs −4 points. Transfer accounting must model the
number of actual transfers, not the size of a set-difference between squads.

**Selling value.** Existing players cannot be treated as if they must be
repurchased at today's market price. A manager receives only £0.1m of profit
for each £0.2m rise while owned; price falls are borne in full. Optimiser budget
constraints therefore operate on the cash effect of transfers (§7), using
purchase/current/selling prices separately.

**Chips.** Two of each in 2026/27: Wildcard, Free Hit, Bench Boost and Triple
Captain — one set for the first half through the GW19 deadline and a refreshed
set from GW20. Only one chip may be active in a Gameweek. Wildcard and Free
Hit are both unavailable in GW1: Free Hit is explicitly blocked by rule and
cannot be played in consecutive Gameweeks; the first Wildcard has no effective
use in GW1 either, since squad-building transfers are already unlimited and
free until the opening deadline, and it becomes available from GW2. Playing a
Wildcard or Free Hit **preserves** any banked free transfers — it does not
wipe them — but does not itself earn an extra free transfer for the
Gameweek it is played in; a manager who banks 2 free transfers, then plays
Wildcard in GW6, still has those 2 banked (plus GW7's normal allotment) in
GW7. §8/§9 must model this explicitly rather than assume the "obvious" wipe
behaviour.

**Blank/Double Gameweeks.** A player can score across multiple fixtures in one
Gameweek; a captain's multiplier applies to the points from each fixture they
play. A player with no fixture scores zero. This is why §4 separates
per-fixture projection from per-Gameweek aggregation.

**Prices.** Prices can move by £0.1m per day based on aggregate transfer
activity. The exact mechanism remains undisclosed. The official 2026/27 game
also exposes a price-change predictor, which may be used only if its source is
approved; it is evidence, not a guarantee.

**Finalisation.**

```text
GAMEWEEK FINALISATION

Results remain provisional until the official FPL Gameweek
lockdown at 09:00 UK time on the day after the final fixture
of that Gameweek — extended for 2026/27 specifically to allow
Opta review of BPS and defensive-contribution data.

Prospective scoring/evaluation may ingest provisional results
for display, but permanent evaluation ledgers must not mark a
Gameweek FINAL until the official finalisation point has passed.

Subsequent records must distinguish:
PROVISIONAL
FINAL
```

§4's `fpl_gameweek_state` and §11's lifecycle diagram make this a first-class
state rather than an implicit "wait until FPL looks finished."

Official rule references for this revision:
- Premier League 2026/27 FPL changes
- Premier League 2026/27 chip rules
- Premier League 2026/27 defensive-contribution rules
- Premier League 2026/27 Bonus Points System changes
- FPL Help / managing team / transfer rules

## 2.1 Private League vs Overall Objectives

Overall-points optimisation and private-league optimisation are different
objectives. Phase 1 optimises xP. Phase 3 optimises league outcome using
opponent-state uncertainty and simulated point distributions (§10).

**Do not use "template" ownership as a substitute for an overall-rank
objective.** Ownership can inform risk and rank variance, but an owned player
still earns points. Likewise, a differential is not valuable merely because it
is different; it is valuable only when its distribution improves the selected
rank objective.

---

# 3. Overall Architecture

```text
                    ┌───────────────────────────┐
                    │ Existing Dixon-Coles      │
                    │ match-level core          │
                    │ FROZEN_PHASE1_V1          │
                    └──────────────┬────────────┘
                                   │ team λ, score matrix,
                                   │ clean-sheet P
                                   ▼
      ┌────────────────┐   ┌──────────────────────┐
      │ Player Stats   │──▶│ Player Projection    │
      │ + Availability │   │ per fixture (§6)     │
      └────────────────┘   └──────────┬───────────┘
                                      │
                                      ▼
                           ┌──────────────────────┐
                           │ GW Projection        │
                           │ aggregate + samples  │
                           └──────────┬───────────┘
                                      │
      ┌────────────────┐              ▼
      │ Game State     │──────▶┌──────────────────────┐
      │ Provider (§13) │       │ Squad / XI / Captain │
      │ API or import  │       │ Optimiser (§7)       │
      └────────────────┘       └──────────┬───────────┘
                                          │
                                          ▼
                               ┌──────────────────────┐
                               │ Transfer + Chip      │
                               │ Planner (§8, §9)     │
                               └──────────┬───────────┘
                                          │
                                          ▼
                               ┌──────────────────────┐
                               │ Mates-Relative       │
                               │ Simulation (§10)     │
                               └──────────┬───────────┘
                                          │
                                          ▼
                                  Recommendation
```

### 3.1 Hard Boundaries

- FPL never changes `FROZEN_PHASE1_V1`; it consumes match outputs.
- Player/news/availability information uses only information knowable before
  the FPL Gameweek deadline for that recommendation.
- Confirmed lineups are **post-deadline evidence for FPL**, not a current-GW
  decision input (§11).
- The game-state acquisition mechanism sits behind an interface. The optimiser
  cannot know or care whether state came from an approved endpoint, a permitted
  provider, or user import.
- No FPL write automation exists (§14).

### 3.2 New vs Reused

Reused, with the existing implementation each concept extends:

| Concept | Existing implementation |
| --- | --- |
| Team-level match forecast, computed once per gameweek | `FixtureOutlook` — [`lms.py:172`](../src/bym/lms.py) |
| Provisional, unfrozen decision-layer config alongside a frozen model | `LMSConfig` — [`config.py:108`](../src/bym/config.py), explicitly "PROVISIONAL and strategy-only... no backtest behind them" |
| Source registry gate and human-triggered promotion | `assert_approved`/`approve` — [`registry.py:274`](../src/bym/sources/registry.py), [`registry.py:304`](../src/bym/sources/registry.py) |
| Multi-gameweek horizon planning under uncertainty | `season_simulation.py`'s Monte Carlo season simulator (SPEC.md §12-14) — the precedent for §8/§9's transfer/chip horizon, not a parameter tweak to it |
| Recommend-never-submit boundary | SPEC.md §3/§22's Sky boundary — §14 restates it for FPL |

Also reused without a direct code precedent to extend: score matrices,
availability evidence model, provenance/audit patterns, scheduler patterns.

New: player identity and season registry, player-match statistics, per-fixture
and Gameweek player projections, autosub/captain modelling, FPL game-state
provider, squad optimisation, transfer planning, chip planning, and
private-league simulation. None of these exist anywhere in the codebase today.

---

# 4. Data Model

Player identity must be separated from season-specific FPL identity. FPL
positions, clubs, prices and element IDs can change between seasons.

```text
fpl_player
    player_id                     # stable internal identity
    canonical_name

fpl_player_season
    player_id
    season
    fpl_element_id               # season-scoped external id
    team_id
    position                     # GK | DEF | MID | FWD, season-scoped
    start_price
    external_ids{}               # provider -> provider id

fpl_player_match_stats
    player_id
    fixture_id
    season
    source_id
    source_record_version
    dataset_manifest_id            nullable for live API observations
    observed_at
    minutes
    goals
    assists
    shots
    xg                            nullable
    xa                            nullable
    saves                         nullable
    yellow_cards
    red_cards
    own_goals
    penalties_saved              nullable
    penalties_missed             nullable
    tackles                      nullable
    interceptions               nullable
    blocks                       nullable
    clearances                   nullable
    recoveries                   nullable
    cbit_actions                 nullable, derived when components exist
    cbirt_actions                nullable, derived when components exist
    bps                          nullable

fpl_fixture_projection           # player x fixture, not player x GW
    player_id
    fixture_id
    season
    gameweek
    p_appearance
    p_start
    p_60_plus
    expected_minutes
    goal_lambda                  # expected count, not merely P(any goal)
    assist_lambda                # expected count / model equivalent
    p_clean_sheet
    expected_saves
    expected_defcon_points
    expected_bonus
    expected_points
    points_variance
    model_version
    input_snapshot_ids[]
    computed_at

fpl_gameweek_projection          # aggregates 0/1/2+ fixtures
    player_id
    season
    gameweek
    expected_points
    points_variance
    quantiles{}                  # e.g. p10/p50/p90
    simulation_key               nullable
    model_version
    computed_at

fpl_squad_snapshot
    season
    gameweek
    as_of
    deadline
    bank
    free_transfers
    active_chip                  nullable
    chips_available[]
    players[15]                  # player_id, purchase_price, current_price,
                                 # selling_price
    starter_ids[11]
    captain_id
    vice_captain_id
    bench_order[4]
    source_kind                  # APPROVED_API | IMPORT | MANUAL

fpl_transfer_plan
    season
    gameweek
    as_of
    transfers_out[]
    transfers_in[]
    transfer_count
    free_transfers_used
    hit_cost
    projected_gain
    horizon_gameweeks
    downside_summary
    reasoning

fpl_chip_plan
    season
    chip
    recommended_gameweek
    horizon_considered
    confidence                   # HIGH | MEDIUM | LOW
    opportunity_cost
    reasoning

fpl_mini_league
    league_id
    league_name
    league_type                  # CLASSIC | H2H
    is_primary
    entrant_ids[]

fpl_mini_league_entrant_state
    league_id
    entrant_id
    gameweek
    snapshot_type                # OBSERVED_PREVIOUS | PREDICTED_CURRENT |
                                 # ACTUAL_POST_DEADLINE
    observed_at
    player_ids[15]
    starter_ids[11]              nullable until known
    captain_id                   nullable until known
    vice_captain_id              nullable until known
    uncertainty                  nullable for observed/actual

fpl_gameweek_state               # the other end of the lifecycle from
                                 # fpl_recommendation_snapshot's pre-deadline
                                 # freeze (§11.2) — this is the post-deadline
                                 # settlement side
    season
    gameweek
    deadline
    final_fixture_ended_at        nullable until the last fixture finishes
    official_finalisation_at      nullable until known; deadline + rule
                                  # (currently 09:00 UK the day after
                                  # final_fixture_ended_at's date)
    status                        # UPCOMING | LOCKED | IN_PROGRESS |
                                  # PROVISIONAL | FINAL
```

`fpl_gameweek_state.status` transitions one way, never backward:
```text
UPCOMING -> LOCKED (at deadline) -> IN_PROGRESS (kickoff) ->
PROVISIONAL (final fixture ends) -> FINAL (official finalisation point)
```
Any query for "actual points," "captain outcome," "transfer gain," "DefCon,"
"bonus," or "mini-league position" that feeds a permanent evaluation ledger
(§12) must read this field and refuse to treat a `PROVISIONAL` Gameweek as
settled — the numbers genuinely can still move after the final whistle.

Every recommendation must be reconstructable from exact model version,
fixture outlook, player-stat snapshot, availability state, game-state snapshot,
and optimisation configuration. A single `model_version` and `computed_at`
field is not sufficient provenance on its own.

---

# 5. Player-Level Data & Sources

Player-performance data is a substantial new dependency, but after the
2026-08-17 source review it is **not a Phase 1A build/research blocker**. A
production-quality calibration claim still depends on permitted FPL scoring
labels. The architecture now
distinguishes four source roles rather than searching for one mythical feed
that does everything:

1. **production/live player evidence** — current form, minutes, xG and lineups;
2. **historical development data** — match-level player labels/features for
   walk-forward modelling;
3. **research-only feature evidence** — useful for deciding whether a feature
   such as DefCon is worth pursuing, but not automatically permitted in
   production;
4. **benchmark implementations** — independent models used to challenge our
   projections, not ingested as authoritative truth.

Every source remains subject to SPEC.md §18.1. Dataset convenience never
promotes a source to `APPROVED`.

## 5.1 Required Data Shape

At minimum the Phase 1 projection engine needs:

- stable player identity and season-scoped club/FPL position mapping;
- appearances, starts and minutes;
- goals and assists;
- attacking opportunity data sufficient to estimate player share of team
  scoring, preferably xG/xA/shots/key passes;
- goalkeeper saves where available;
- actual FPL points/BPS for calibration where permitted;
- defensive actions for DefCon **only if provenance and use rights are good
  enough**; otherwise DefCon remains an explicit omitted component.

The source strategy may combine providers. A single provider is not required to
supply both training labels and live state.

## 5.2 Existing Registry Sources and Their FPL Role

Status confirmed directly against `config/source_registry.json` (the
persisted overrides that win over `registry.py`'s seed defaults) on
2026-08-17:

| Source | Registry status | FPL role / decision |
| --- | --- | --- |
| `api_football` | `APPROVED` | Historical player-match development data; current 2026/27 coverage on the held free plan is not available. |
| `big_balls_sports_data` | `EVALUATED` | Candidate live player/form/xG/lineup source; must remain gated until approved — see the caveat below before treating it as a strong candidate. |
| `football_data_co_uk` | `APPROVED` | Existing historical match/market corpus feeding the frozen team model. |
| `football_data_org` | `APPROVED` | Current fixtures/results/status; can derive the FPL deadline from the first fixture minus the season rule when no authoritative FPL deadline field is available. |
| `tavily` | `APPROVED` | Pre-deadline injury/press/news discovery through the existing knowledge pipeline. |
| `the_odds_api` | `APPROVED` | Market evidence for the frozen team model; not an FPL player-stat source. |
| `sky_super6` | `PROHIBITED` | No FPL role. |

### API-Football

The free account has been live-probed. Historical 2022–2024 single-fixture
requests provide lineups and `/fixtures/players`-style player performance with
stable provider player IDs, including minutes, goals, assists, shots, passes,
tackles and interceptions. Current-season access is paywalled on that account,
and the batch `ids` path is also a paid capability.

Therefore Phase 1 may use `api_football` for **historical development/research
collection only** under the approved source scope. It does not solve current
2026/27 live availability at zero cost and does not by itself expose the full
clearance/recovery shape required for FPL DefCon.

### Big Balls Sports Data — caveat before calling it "preferred"

The provider currently documents free football endpoints for fixtures,
confirmed lineups, match statistics including xG, per-season player goals /
assists / xG and club-form fields such as appearances and minutes. Its
published free allowance is **1,000 requests/day** (2,000 with the provider's
GitHub-linked tier), so `Not metered` must not be treated as a permanent quota
assumption in code or documentation.

Its own registry entry already records a live-probe finding this section must
not be written around as if it were an untested candidate: evaluated
2026-08-10 as a candidate xG source and **rejected for that purpose** — a live
key probe found `/v1/leagues` returns England = EPL only (no Championship,
59% of the Super 6/LMS corpus), and its advertised match-level xG did not
appear in any payload (0 of 30 finished EPL matches had populated
`team_stats`, and the one match in 40 that did carried 28 other fields with no
xG). Its fixture horizon also ended 2026-05-24 with no 2026/27 season data as
of that probe. This is a documented "advertised vs. actually delivered" gap
for a field the FPL live-evidence use case also depends on (per-player
goals/assists/appearances), not merely a missing-Championship limitation —
treat it as reason for the live-verification step below, not as background
noise to route around.

`big_balls_sports_data` remains `EVALUATED` until its intended FPL use,
retention and terms have been reviewed and approved in the registry. Before
relying on it for 2026/27, live verification must prove Premier League player
coverage for the fields actually required, given the prior probe already found
one advertised field silently absent in practice. Published documentation does
not yet establish complete CBIT/CBIRT-grade defensive actions, so it is not
the DefCon authority by default.

## 5.3 Research / Benchmark Sources to Register Deliberately

These are **not production approvals**. They are candidates to seed in the
registry with an explicit purpose/scope.

### `vaastav_fpl_historical` — `REVIEW_REQUIRED`, research/development scope

The community historical FPL dataset ([`vaastav/Fantasy-Premier-League`](https://github.com/vaastav/Fantasy-Premier-League))
exposes Gameweek-level FPL labels and features across seasons, including
minutes, starts, goals, assists, FPL points, bonus/BPS, prices, ownership and
xG/xA; newer datasets also include tackles, recoveries and combined
clearances/blocks/interceptions where present.

It is highly useful for research, but much of it is derived from the FPL
website/API and Understat, so downstream availability does **not** settle the
upstream source-policy question. Use requires its own review and a frozen
`fpl_research_dataset_manifest`.

Hard leakage rule: the dataset's historical `xP`/`ep_this` field is scraped
after the Gameweek and may contain post-match information. It is forbidden as
a same-Gameweek model feature unless an observation timestamp proves it was
available before that historical FPL deadline. Otherwise drop it or use only a
pre-registered lag such as the previous Gameweek.

### `understat` — `REVIEW_REQUIRED`

Structurally useful for attacking player features: match-level xG, xA, shots,
key passes, non-penalty xG and involvement/build-up measures. It does not solve
DefCon. Automation, retention and current terms must be reviewed before direct
ingestion is enabled.

### `fpl_core_insights` — `EVALUATED`, `usage_scope=RESEARCH_ONLY`

Technically valuable because its per-match tables ([`olbauday/FPL-Core-Insights`](https://github.com/olbauday/FPL-Core-Insights))
include tackles, interceptions, recoveries, blocks and clearances aligned to
FPL identities — close to the exact data required to study CBIT/CBIRT and
2026/27 DefCon. However, its detailed match-data provenance is aggregated from
multiple free sources rather than one clearly licensed authoritative feed. Use
it to test **whether DefCon features have predictive value and whether our
calculations match observed outcomes**, not as a production source until
provenance and use rights are independently cleared.

### `openfpl` — `EVALUATED`, `usage_scope=BENCHMARK_ONLY`

[OpenFPL](https://github.com/daniegr/OpenFPL) is a benchmark implementation,
not an authoritative data source. Its accompanying paper (Groos,
["OpenFPL: An open-source forecasting method rivaling state-of-the-art Fantasy
Premier League services"](https://arxiv.org/abs/2508.09992)) documents
position-specific ensemble models trained on FPL + Understat data from
2020/21–2023/24 and reports prospective evaluation on 2024/25. Phase 1 should
use it as an independent sanity benchmark where the comparison can be
reproduced, without importing its outputs as training labels or treating its
published performance as our validation result.

Source evidence for this revision should be captured in SourceRecords / the
research manifest rather than treated as eternal facts. Initial references:

- Big Balls Sports Data soccer API documentation, and its own registry probe
  note in `config/source_registry.json`;
- API-Football `/fixtures/players` documentation plus the project's live free-tier probes;
- `vaastav/Fantasy-Premier-League` data dictionary / README;
- `olbauday/FPL-Core-Insights` schema/documentation;
- `daniegr/OpenFPL` and its accompanying paper (arXiv:2508.09992).

## 5.4 DefCon Data Decision

DefCon is **not allowed to block Phase 1A**.

Preferred order:

```text
permitted production CBIT/CBIRT source exists
    -> model DefCon quantitatively

otherwise research-only defensive data demonstrates material value
    -> pursue/qualify a production source

otherwise
    -> omit quantitative DefCon from FPL_MODEL_V1
       and expose the omission in model coverage
```

Do not fabricate DefCon from tackles alone. For defenders the rule needs CBIT;
for midfielders/forwards it needs CBIRT, which adds recoveries.

## 5.5 Game State Is Separate — and Is Now the Main Live Source Gap

Current FPL prices/positions, the user's purchase/selling prices, bank, free
transfers and chip state belong to `FPLGameStateProvider` (§13). Neither
historical API-Football nor generic football feeds replace those FPL-specific
values.

`football_data_org` can supply current fixtures/results/status and therefore a
**derived deadline** (`min(kickoff) - 90 minutes`) under the frozen 2026/27
rule. `Tavily` can supply pre-deadline knowledge evidence. The remaining
source-policy decision is therefore narrower than earlier drafts implied: how
to obtain and reconstruct the FPL-specific candidate universe/current prices
plus the user's exact mutable squad economics.

## 5.6 Phase 1A Initial Data Plan

Unless source review changes it, Phase 1A should begin with this explicit stack:

```text
TEAM MATCH ENVIRONMENT
    existing FROZEN_PHASE1_V1
    + football_data_org current fixtures/status

HISTORICAL PLAYER DEVELOPMENT
    api_football (approved, historical seasons available on held plan)
    + vaastav_fpl_historical only after research-scope review

LIVE PLAYER EVIDENCE
    big_balls_sports_data only after APPROVED + 2026/27 live verification
    (already carries a documented reason to distrust its advertised fields
    until re-verified — §5.2)
    tavily for pre-deadline availability/news evidence

FPL-SPECIFIC GAME STATE
    explicit user/permitted import by default
    direct FPL reads only if §13 review separately approves them

DEFCON
    OFF quantitatively in FPL_MODEL_V1 unless a permitted source is available
    research-only datasets may audition its value

BENCHMARK
    OpenFPL, independently reproduced where compatible
```

This stack is enough to **build and test the engine**, but it does not waive the
need for permitted FPL scoring labels before claiming player-xP calibration.
If `vaastav_fpl_historical` or an equivalent FPL-label source fails review,
Phase 1A can still develop component models on API-Football, but promotion to a
calibrated `FPL_MODEL_V1` must wait for lawful/reproducible FPL outcome labels.

---

# 6. Player Points Projection Model

The player model consumes the frozen team-level match environment; it does not
refit or alter it. `FixtureOutlook` ([`lms.py:172`](../src/bym/lms.py)) for a
Gameweek is computed once and reused across every player's projection — the
same "score matrix computed once per gameweek, not once per entrant or
candidate" discipline CLAUDE.md already enforces for LMS
(`test_the_model_runs_once_per_gameweek_not_once_per_entrant`) applies here:
a gameweek's outlook is fetched once and every one of ~600 players' projections
reads it, never refit per player.

Projection is **per player per fixture first**, then aggregated to Gameweek.
This is required for Blank and Double Gameweeks and for reconstructability.

## 6.1 Components

- **Minutes / appearance model** — estimate `p_appearance`, `p_start`,
  `p_60_plus` and expected minutes from historical starts/minutes plus
  **pre-deadline** availability evidence. Historical API-Football and/or an
  approved research dataset can supply development labels; a future approved
  live source may supply current form. Confirmed XIs published after the FPL
  deadline are training/evaluation evidence only for that Gameweek.
- **Goal model** — derive a player goal intensity / expected count from the
  team's goal λ and the player's estimated scoring share. A probability of
  "at least one goal" is not sufficient because braces/hat-tricks score more.
- **Assist model** — expected assist count or equivalent event distribution,
  informed by historical involvement and xA where permitted. Understat-shaped
  features may be auditioned only from a source/dataset whose usage scope
  allows modelling.
- **Clean-sheet model** — team clean-sheet probability from the frozen score
  matrix, conditioned on the player's expected minutes/60-minute eligibility.
- **Goals-conceded model** — GK/DEF deductions require the opponent goal-count
  distribution, not only `p_clean_sheet`.
- **Goalkeeper saves / penalties** — model if source data supports it;
  otherwise expose the omitted component and uncertainty.
- **DefCon** — probability/expected points from CBIT/CBIRT distributions only
  when a permitted source supplies the relevant actions. Research-only
  datasets may estimate the oracle/value ceiling but cannot silently become a
  production dependency.
- **Bonus** — estimate published BPS components and then bonus-place
  probabilities relative to other players in the fixture. Treat this as
  lower-confidence because the underlying Opta event variables and their
  future outcomes are only partially observed/modelled.
- **Cards / own goals / penalty misses** — include when historically supported;
  otherwise the model must state that they are omitted rather than hide them
  in a generic residual.

## 6.2 Output Distribution

Phase 1 requires expected points for optimisation, but the engine must retain
sufficient variance/distribution information for:

- captain and vice-captain risk;
- bench/autosub expectation;
- chip evaluation;
- Phase 3 private-league Monte Carlo.

The distribution may be represented analytically or by reproducible simulation
samples; it must not be reconstructed later from xP alone.

## 6.3 Configuration Boundary

The FPL projection model gets `FPLConfig`, in the same mould as `LMSConfig`
([`config.py:108`](../src/bym/config.py)): provisional, independently
versioned, decision-layer only, carrying no backtest/validation claim until
§12 says otherwise. It may consume `FROZEN_PHASE1_V1`; it may never modify
that frozen configuration or write FPL-derived features back into the match
model.

---

# 7. Squad Optimisation Engine

Given Gameweek player projections and current state, optimise the legal squad,
starting XI, bench order, captain and vice-captain.

## 7.1 Hard Constraints

```text
squad:             exactly 2 GK / 5 DEF / 5 MID / 3 FWD
club limit:        <= 3 players per Premier League club
starting XI:       11 players
                   exactly 1 GK
                   >= 3 DEF
                   >= 2 MID
                   >= 1 FWD
captain:           exactly 1 starter
vice-captain:      exactly 1 different starter
```

## 7.2 Budget Constraint — Transfer Cash Flow, Not Squad Repricing

A budget equation of the form `sum(current_price of final squad) <= bank +
sum(selling_price of current squad)` is invalid for retained players because it
implicitly sells and rebuys them at different prices.

For a transfer plan:

```text
cash_available = current_bank
                 + Σ selling_price(players_out)

cash_required  = Σ current_buy_price(players_in)

require cash_required <= cash_available
```

Retained players have zero transfer cash flow. Initial-squad construction uses
the normal £100.0m current-price budget instead.

## 7.3 Transfer Cost

```text
n_transfers = count(players_out) = count(players_in)
paid_transfers = max(0, n_transfers - free_transfers_available)
hit_cost = -4 * paid_transfers
```

Chip semantics can change this and must be modelled explicitly (§9).

## 7.4 Autosubs and Captaincy

Optimising only the nominal XI is insufficient. Expected squad points must
account for:

- probability that starters make no appearance;
- FPL formation-legal automatic substitutions in bench order;
- goalkeeper substitution separately;
- captain nonappearance and vice-captain promotion.

For Phase 1, an ILP/MIP solver can optimise legal deterministic choices while
a deterministic evaluation function computes expected points under these
appearance contingencies. Do not force all uncertainty into linear constraints
if a clean evaluate-after-solve formulation is simpler and testable.

Phase 1 remains a **single-Gameweek** optimiser. It must not call a one-week
transfer recommendation a season-optimal move; horizon planning starts in §8.

---

# 8. Transfer Engine & Free-Transfer Banking

Phase 2 extends §7 over a rolling 2–4 Gameweek horizon, with explicit state
transitions for squad, bank, selling prices and up to five banked free
transfers — the FPL-shaped sibling of `season_simulation.py`'s Monte Carlo
season simulator (SPEC.md §12-14), which already exists to judge a candidate
by its effect over a horizon rather than in isolation, over a materially larger
state space here (squad composition × prices × chip availability, not one club
pick per week).

It must answer:

- whether a transfer's expected gain over the horizon exceeds its hit cost and
  opportunity cost;
- whether using a free transfer now is better than banking it;
- whether a move creates a future budget constraint because retained-player
  selling value differs from market price;
- whether fixture blanks/doubles materially change the horizon;
- whether waiting risks a budget-invalid plan after a plausible price move.

Price-change information is a stochastic constraint/risk input, not an excuse
to optimise predicted team value instead of FPL points.

State transition semantics for Wildcard/Free Hit/free-transfer banking must be
season-versioned and regression-tested against the official rules rather than
embedded as folklore. In particular: **playing a Wildcard or Free Hit must
preserve banked free transfers, not reset them to zero or one** — the
"obviously correct" wipe-on-chip behaviour is wrong for 2026/27 (§2) and needs
its own regression test, not just a comment, since a planner that gets this
wrong silently costs a manager transfers they believed were safely banked.

---

# 9. Chip Timing

Phase 2. Chip selection is a sequencing problem over the remaining half-season
and eventually the remaining season. The optimiser must respect:

- two sets of WC/FH/BB/TC, with first-half expiry and second-half refresh;
- one chip per Gameweek;
- Wildcard and Free Hit both have no effective use in GW1 (§2); Free Hit is
  additionally not usable in consecutive Gameweeks;
- chip-specific effects on transfer state — **neither chip wipes banked free
  transfers** (§2, §8); this must be regression-tested the same way the
  budget/hit-cost maths in §7.2/§7.3 is, not assumed obvious;
- Blank/Double Gameweeks and fixture uncertainty;
- loss of option value when a chip is consumed early.

Candidate chip value should be evaluated against an explicit **no-chip baseline
for the same future worlds**, using common random numbers where simulation is
used. `fpl_chip_plan.confidence` remains HIGH/MEDIUM/LOW and must degrade as
fixture uncertainty and horizon increase.

A Gameweek-30 chip call made in Gameweek 5 is a watch item, not an instruction.

---

# 10. Mates-Relative Optimisation (Effective-Ownership-aware)

Phase 3 is the differentiator — the same reasoning that justifies SPEC.md
§11-14's opponent-aware, mates-relative strategy layer for Super 6/LMS applies
one level down at player granularity. But this must not overstate what is
knowable before a Gameweek deadline.

## 10.1 Current Opponent Squads Are Not Exact Pre-Deadline

FPL deliberately hides other managers' current Gameweek teams until the
Gameweek has started. Therefore, before the deadline the system can observe a
mate's **previous locked squad/history**, but cannot know their new transfers,
starting XI or captain with certainty.

The opponent pipeline is therefore:

```text
previous observed squad
    + transfer/captain history
    + current prices/fixtures/injuries
    -> posterior over likely current squad / XI / captain
    -> private-league simulation

post-deadline actual picks
    -> evaluation of the opponent model
    -> never fed back into the same-GW decision
```

This mirrors Super 6's opponent-prediction discipline. No current-GW opponent
fact discovered after deadline may be backfilled into the recommendation that
preceded it.

## 10.2 Ownership Is Not Effective Ownership

Published player ownership percentage is not EO by itself. EO also depends on
captaincy and the chosen reference cohort and can exceed 100%.

- **Field ownership** may be read from an approved game-state source.
- **Field EO** requires a defined cohort plus captaincy estimates/data and must
  identify that cohort.
- **Private-league EO before deadline** is predicted from opponent-state
  distributions, not declared exact.
- **Private-league actual EO after deadline** can be computed from the locked
  squads/picks and is used for evaluation/live explanation only.

## 10.3 Objective

For a Classic primary league, the Phase 3 objective is:

```text
argmax P(Finish 1st in primary mini-league |
         our decisions,
         opponent-state distributions,
         player point distributions,
         chips/transfers remaining)
```

Nearer-term expected rank gain can be reported, but it is not the sole season
objective unless explicitly configured.

A player owned by every mate is **not worth zero**. Much of their ordinary
score may cancel in relative terms, but captaincy, benching, transfer timing and
future ownership paths still matter. Likewise a differential earns no bonus
for novelty; it matters only through the simulated league-outcome distribution.

Head-to-Head leagues require a separate weekly-match objective and remain out
of Phase 3 v1.

---

# 11. Real-Time Reforecasting / Deadlines

FPL has one Gameweek decision deadline: **90 minutes before the first fixture**.
The deadline is a first-class field when available from an approved game-state
source; otherwise it must be imported from a permitted source/user input and
stored with provenance.

Recommended cadence:

| Time before deadline | Refresh |
| --- | ---: |
| 3+ days | Daily |
| 24 hours | Every 2–3 hours |
| 6 hours | Hourly |
| 90 minutes | Final decision run and freeze |
| Material pre-deadline injury/press update | Immediate |
| Price change around 00:00 UK | Re-check budget validity |

### 11.1 Confirmed Lineups Are Post-Deadline Evidence

A cadence rule of "confirmed lineups → immediate current-GW reforecast" would
be dead code for FPL and must not appear here: official team lineups are
normally published after the FPL deadline for the opening fixture, and all
later-fixture lineups are necessarily later still. They may be used to:

- evaluate the minutes/start model after deadline;
- explain live points;
- update future-Gameweek player state;
- train future model versions using correct historical timestamps.

They may **never** alter the already-frozen recommendation for that Gameweek.
This boundary requires a regression test analogous to the Super 6 historical
availability deadline gate.

### 11.2 Decision Freeze

At the deadline, persist an immutable recommendation snapshot containing the
exact squad/transfer/captain/chip recommendation and every input version. Later
news, actual opponent picks or confirmed lineups create new observed events but
do not mutate the pre-deadline record.

### 11.3 Gameweek Lifecycle

§11.2 covers the pre-deadline half of a Gameweek's life. `fpl_gameweek_state`
(§4) closes the other end just as rigorously:

```text
UPCOMING
 |
 v  FPL deadline (-90m before first fixture)
LOCKED
 |
 v  kickoff of first fixture
IN_PROGRESS
 |
 v  final fixture of the Gameweek ends
PROVISIONAL
 |
 v  09:00 UK, day after the final fixture (§2's Finalisation rule)
FINAL
```

The transition is one-directional. A Gameweek never reverts from
`PROVISIONAL` back to `IN_PROGRESS`, nor from `FINAL` back to `PROVISIONAL` —
if a genuine post-finalisation correction ever occurs it is a new observed
event, not a mutation of the existing record, matching §11.2's discipline for
the pre-deadline side.

---

# 12. Validation & Holdout Scoping

`assert_holdout_unspent` remains mode-agnostic. The 2025/26 season is spent and
subsequently contaminated for some parameter investigations; FPL does not get
a special exemption merely because its decision layer is new.

## 12.1 Development and Validation

Use only seasons permitted by the repository's existing validation protocol.
Any FPL backtest on development/previous validation data must be labelled for
what it is; it cannot acquire "holdout" status through wording.

Player-level evaluation must be **walk-forward and deadline-causal**:

- player stats available only after a historical match cannot affect an earlier
  prediction;
- confirmed XIs after the FPL deadline cannot affect that Gameweek;
- current prices/ownership must come from timestamped historical snapshots if
  they are used historically;
- season-specific FPL positions and prices must be reconstructed by season;
- a permanent evaluation ledger entry (actual points, captain outcome,
  transfer gain, DefCon, bonus, mini-league position) may only be written once
  `fpl_gameweek_state.status = FINAL` (§4, §11.3) for that Gameweek — a
  `PROVISIONAL` result may be shown in the UI but must not be treated as
  ground truth for calibration, since BPS/DefCon figures genuinely still move
  during the post-match Opta review window.

## 12.2 Historical Dataset and Benchmark Rules

Historical community datasets are evidence, not automatically causal feature
stores.

- `vaastav_fpl_historical.xP` / historical `ep_this` is prohibited as a
  same-Gameweek feature without proof it was captured before that Gameweek's
  deadline; by default drop it or lag it.
- Post-match player stats are labels/features only for **subsequent**
  predictions in walk-forward order.
- Research-only defensive datasets may be used for DefCon feature feasibility
  and oracle/value studies but cannot qualify a production source.
- OpenFPL may be used as an independent benchmark on compatible historical
  samples. It does not become a training label and its published results are
  not evidence that `bym` itself is calibrated.
- Every harvested/static dataset must have a frozen manifest containing source,
  upstream description, season range, retrieval/commit identity, file hashes,
  allowed usage scope and known timing/leakage caveats.

The source review must happen **before** using a dataset to select a model
component. A dataset that later fails source-policy review cannot be rescued by
having already produced an attractive ablation result.

## 12.3 2026/27 Prospective Discipline

If an `FPL_MODEL_V1` is frozen before it begins making live recommendations,
record its decisions prospectively and append-only. Any model changed using
2026/27 outcomes becomes a new version and is evaluated prospectively from that
point onward; it may not rewrite V1.

If the FPL engine is not frozen before the season starts, then 2026/27 cannot
later be described as a pristine season-long holdout for that evolving model.
Use pre-declared evaluation windows/versions instead.

## 12.4 Metrics

At minimum:

- player xP calibration by position and minutes band;
- MAE / log score or CRPS for player-point distributions where appropriate;
- captain recommendation value;
- squad/starting-XI expected and realised points;
- transfer net gain after hits over the declared horizon;
- optimiser vs simple baselines;
- private-league `P(finish 1st)` calibration once Phase 3 exists.

Do not select a player model solely on realised FPL points from a small number
of changed picks; use distribution/calibration metrics as well as decision
metrics, following the lesson from the Super 6 ablation ladder.

---

# 13. Source Acquisition Policy for FPL

This section must resolve before Phase 1 live acquisition. It deliberately does
**not** assume approval.

## 13.1 The Endpoint Is Undocumented, Not an Official Public API Contract

`fantasy.premierleague.com/api/*` exposes JSON used by the FPL website and many
community tools. The Premier League does not publish an official supported API
contract for those endpoints, so schemas may change without notice.

Public endpoints can expose game/player/fixture/manager information. However,
the authenticated "my team" state needed to read a manager's **current
unsubmitted squad, bank, free transfers and chip state before the deadline** is
not the same as public locked historical picks and requires authentication in
community-documented usage.

`bym` will not automate Premier League login, retain session cookies, scrape
credentials, or impersonate the user's browser. Therefore Phase 1 should assume
**the user's mutable pre-deadline squad state is supplied by user import/manual
entry**, even if selected public read-only FPL endpoints are later approved.

## 13.2 Terms / Retention Gate

The current Premier League Website/App Terms permit reasonable private/personal
download/printing but also expressly restrict other reuse, including creating
a database containing material downloaded or obtained from the Website/App
without prior written approval.

Therefore:

```text
fantasy_premier_league
    default status: REVIEW_REQUIRED
```

A `bym sources approve` command ([`registry.py:304`](../src/bym/sources/registry.py))
is an **internal attestation that the human has completed the source-policy
review**; `assert_approved` ([`registry.py:274`](../src/bym/sources/registry.py))
is what actually blocks every worker until that happens. Approval is not
permission granted by the Premier League and must not be represented as such.

If the review concludes the intended automated querying or persistence is not
permitted, mark the source `PROHIBITED` for that purpose and use the fallback
below. If approval is granted internally, the SourceRecord must state exactly
which endpoints/purposes/retention are approved; approval must not automatically
cover authenticated endpoints or bulk historical storage.

## 13.3 Fallback Is a Full FPL-State Import, Not Just "My Squad"

Approved generic football sources cover fixtures/results/status, so those do
**not** need to come from FPL itself. `football_data_org` may provide the
fixture list and the deadline can be derived from the frozen season rule.

What remains FPL-specific is the transfer candidate universe and user's squad
economics:

```text
player id / name / club / season-scoped FPL position
current FPL buy price for every candidate player
explicit completeness attestation for that candidate universe
user purchase + selling price for owned players
bank + free transfers + chip availability
current squad / XI / bench / captain / vice
(optional) FPL ownership / price-change evidence

plus, from approved generic sources where used:
Gameweek fixtures + status
derived/stored deadline with rule version + provenance
```

If direct FPL reads are prohibited, a `FPLGameStateProvider` implementation must
populate that state from a **permitted provider or explicit user import**
(JSON/CSV/paste). Phase 1 cannot claim to work by asking only for the user's
squad while lacking legal current prices for every potential replacement.

Where no permitted full market source is available, a fixed-squad
**lineup/captain-only** mode is allowed. It must state that it does not make a
transfer recommendation, must not introduce an unowned player, and remains
separate from the full-market optimiser described above.

A manager-selected **shortlist-only** comparison is also permitted: it may
compare hold against a bounded set of explicitly nominated, one-for-one
transfers. It must require current/selling prices only for the existing fifteen
and nominated targets, label every result `SHORTLIST_ONLY`, enforce ordinary
FPL affordability/composition/club limits, include a hold option, and never
claim that its preferred option is the best available market transfer.

## 13.4 Data Minimisation

Do not persist whole raw FPL payloads by default. Store normalised fields
required for reconstruction plus source/time/schema version and a content hash.
Raw payload retention is allowed only when the SourceRecord explicitly permits
it. See §15.

---

# 14. The FPL Boundary

The agent is recommendation-only, restating SPEC.md §3/§22's Sky boundary for
this mode because the reasoning is identical and the rule must be too.

It shall never:

- submit a transfer;
- activate/cancel a chip;
- change captain/vice-captain/bench order;
- automate login or maintain an authenticated FPL browser/session on the
  user's behalf;
- attempt to reveal another manager's hidden pre-deadline changes.

The UI may record the user's own confirmation that they made a recommended
change manually. Read access, if separately approved, never implies write or
authentication automation is in scope.

---

# 15. Persistence

Logical stores:

```text
fpl_player
fpl_player_season
fpl_player_match_stats
fpl_fixture_projection
fpl_gameweek_projection
fpl_squad_snapshot
fpl_transfer_plan
fpl_chip_plan
fpl_mini_league
fpl_mini_league_entrant_state
fpl_game_state_snapshot
fpl_recommendation_snapshot
fpl_research_dataset_manifest
fpl_gameweek_state
```

`fpl_game_state_snapshot` stores the **normalised minimum** required to recreate
a recommendation, not an unconditional copy of a provider's response:

```text
source_id
source_schema_version
observed_at
content_hash
normalised_state
raw_payload_location    nullable, only if source policy explicitly permits
```

Every recommendation must trace to exact projection inputs and exact game state.
Prices, ownership and squad state change over time, so "latest row" is never a
valid reconstruction strategy.

Deadline snapshots are immutable. Post-deadline actual picks/results are new
records, not updates to the decision record.

`fpl_gameweek_state` is the settlement-side counterpart: one row per
season/Gameweek, `status` moving one-directionally through
`UPCOMING -> LOCKED -> IN_PROGRESS -> PROVISIONAL -> FINAL` (§11.3). Every
permanent evaluation write joins against this row and refuses to run until
`status = FINAL`.

`fpl_research_dataset_manifest` freezes any static/community dataset used for
model selection or benchmarking:

```text
dataset_id
source_id
usage_scope                 DEVELOPMENT | RESEARCH_ONLY | BENCHMARK_ONLY
season_range
retrieved_at
upstream_description
version_or_commit
file_hashes{}
known_leakage_caveats[]
terms_checked_at
```

---

# 16. Agent Output

Illustrative, not final UI copy:

```text
FPL — GAMEWEEK 14
Deadline: Sat 11:00   Gameweek status: LOCKED
Bank: £2.3m   Free transfers: 1
Data state: prices CURRENT | squad USER-CONFIRMED | opponent state PREDICTED

RECOMMENDED TRANSFER
OUT: Player A (MID, sell £7.4m)
IN:  Player B (MID, buy £7.1m)
Hit: 0
Projected net gain, next 4 GW: +6.4
Downside: Player B has higher start-risk in GW15
Budget after move: £2.6m

START / BENCH
XI xP including autosub expectation: 63.8
Bench order: GK-X / Player E / Player F / Player G

CAPTAIN
Player C — xP 9.8
Vice: Player D — xP 8.1
Captain nonappearance risk: LOW

PRIVATE LEAGUE — PHASE 3
P(finish 1st): 31% -> 34% under recommended plan
Opponent current-GW squads: PREDICTED, not yet observable

CHIP WATCH
Bench Boost — GW27 candidate
Confidence: MEDIUM
Reason: projected double/fixture swing; 13-GW horizon remains uncertain
```

The UI must keep distinct:

- expected points;
- ownership / predicted effective ownership;
- recommendation confidence;
- model/data completeness;
- private-league outcome probability.

Never collapse them into one pseudo-precision score.

---

# 17. Alerts

FPL-specific material alerts:

```text
🚨 BUDGET RISK
Your planned transfer becomes unaffordable if Player B rises £0.1m tonight.
Price-change evidence: HIGH / MEDIUM / LOW according to approved source.

⚠️ PRE-DEADLINE AVAILABILITY
Player A in your squad is reported out.
Source certainty: CONFIRMED / LIKELY / REPORTED
Recommendation recalculated before deadline.

ℹ️ POST-DEADLINE LINEUP
Player A is not in the confirmed XI.
No current-GW action possible; recorded for live explanation/model evaluation.
```

Alerts stop recommending current-Gameweek actions once the deadline has passed.
Routine noise is suppressed.

---

# 18. Delivery Phases

The project follows the same sequencing already established for Super 6:
**engine and evidence first; UI after the contracts are real and tested.**

## Phase 0 — Source & Rules Freeze

```text
Verify 2026/27 rules and scoring constants
Resolve FPL game-state source policy (§13)
Freeze the initial data-source role matrix from §5
Live-verify Big Balls 2026/27 player coverage before any production use,
    treating the existing xG-payload-empty probe finding as the reason to
    verify, not as background noise (§5.2)
Register/review research datasets separately from production feeds
Define historical data availability and timestamp/causality rules
Freeze FPL source registry records, usage scopes and terms_checked_at
```

## Phase 1A — Core Projection Engine

```text
Season-scoped player identity
Historical player-stat ingestion from approved/development-scoped sources
Reduced model allowed if DefCon has no production-grade source
Per-fixture player projection (§6)
Per-Gameweek aggregation / distribution
Single-GW legal squad + XI + bench + captain optimiser (§7)
User/permitted game-state import
NO UI
NO transfer horizon
NO chips
NO mates-relative optimisation
```

## Phase 1B — Backtest, Prospective Record & Contracts

```text
Walk-forward player projection evaluation (§12)
Dataset-manifest + xP/lookahead regression tests
Deadline-causality tests
OpenFPL/simple independent benchmarks where reproducible
Simple optimiser baselines
Prospective immutable recommendation snapshots
Frontend-independent read models / API contracts
```

## Phase 1C — Basic UI

Only after Phase 1A/1B contracts are stable:

```text
Current squad
Player xP / uncertainty
Recommended XI / bench / captain
Single-Gameweek transfer suggestion
Deadline / data-source status
Reasoning / downside
```

## Phase 2 — Transfer & Chip Planning

```text
2–4 GW transfer horizon (§8)
Free-transfer banking
Chip timing (§9)
Price/budget risk alerts
Blank/Double GW planning
```

## Phase 3 — Beat the Mates

```text
Classic primary mini-league state
Opponent previous-squad observation
Opponent current-GW transfer/captain prediction
Post-deadline actual-pick capture for evaluation
Monte Carlo P(finish 1st) objective
Differential/template explanation as output, not objective shortcuts
```

## Phase 4 — Model Improvement

```text
Player model component ablations
Bonus / DefCon calibration where permitted data exists
Versioned prospective challengers
Single-use holdout only if an actually untouched season/window exists
```

No phase may mutate `FROZEN_PHASE1_V1`.

---

# 19. Guardrails

In addition to SPEC.md §22:

- never submit any FPL action (§14);
- never automate FPL login/session handling in this mode;
- never query a `REVIEW_REQUIRED` or `PROHIBITED` source in production;
- `EVALUATED` / `RESEARCH_ONLY` / `BENCHMARK_ONLY` evidence cannot silently
  become a production dependency;
- internal source approval must not be described as provider permission;
- never persist raw provider payloads unless the SourceRecord explicitly allows
  that retention;
- every static/community dataset used in model selection must have a frozen
  `fpl_research_dataset_manifest`;
- never use historical FPL `xP`/`ep_this` as a same-Gameweek feature unless its
  pre-deadline observation time is proven;
- compute each Gameweek's frozen `FixtureOutlook` once and reuse it;
- never let FPL/player features modify `FROZEN_PHASE1_V1`;
- never use information first known after the Gameweek deadline to alter that
  Gameweek's recommendation;
- confirmed lineups are post-deadline evidence for FPL and cannot trigger a
  current-GW recommendation change;
- other managers' hidden pre-deadline squads/captains are uncertain state, not
  facts; actual post-deadline picks are evaluation evidence only;
- season-specific FPL element IDs, positions and prices must never be treated
  as stable player identity;
- transfer budget maths must use outgoing selling prices and incoming buy
  prices, not reprice retained players;
- expected points, ownership/EO, confidence and league-outcome probability stay
  separate quantities;
- chip/horizon recommendations carry explicit HIGH/MEDIUM/LOW confidence;
- playing a Wildcard or Free Hit never wipes banked free transfers, and never
  earns an extra one for that Gameweek (§2, §8, §9);
- a permanent evaluation ledger entry may only be written once
  `fpl_gameweek_state.status = FINAL`; `PROVISIONAL` results are display-only
  (§4, §11.3, §12.1);
- `assert_holdout_unspent` remains unconditional and mode-agnostic;
- production modules must not import research/oracle helpers intended only for
  historical experiments.

---

# 20. Decisions Log

## Decisions

- **2026-08-17 — v0.1:** full multi-phase FPL addendum drafted.
- **2026-08-17 — v0.2 review:** FPL API use is no longer presumed approved.
  `fantasy_premier_league` stays `REVIEW_REQUIRED` until a human review; the
  architecture now supports a full game-state import/provider abstraction.
- **2026-08-17 — v0.2 review:** authenticated "my team" access is out of scope;
  the user's mutable pre-deadline squad state is user-supplied unless a future
  separately approved integration is designed.
- **2026-08-17 — v0.2 review:** confirmed lineups are explicitly post-deadline
  evidence and cannot affect the same Gameweek recommendation.
- **2026-08-17 — v0.2 review:** Phase 3 private-league opponent state changed
  from "exact current squads" to previous observed state + predicted current
  state, with post-deadline actual picks used only for evaluation.
- **2026-08-17 — v0.2 review:** private-league v1 supports Classic leagues and
  uses `P(finish 1st)` as the season objective. H2H requires a separate model.
- **2026-08-17 — v0.2 review:** player model split into per-fixture and
  per-Gameweek projections to handle blanks/doubles and preserve distributions.
- **2026-08-17 — v0.2 review:** optimiser budget semantics changed to transfer
  cash flow, fixing the retained-player repricing error.
- **2026-08-17 — v0.2 review:** FBref is not an approved player-model source;
  current Sports Reference predictive/AI-use restrictions require permission
  before it can be considered for automated modelling. (Superseded by the v0.3
  source strategy, which does not depend on FBref at all — see below.)
- **2026-08-17 — v0.2 review:** engine-first delivery restored: Phase 1A/1B
  precede UI Phase 1C.
- **2026-08-17 — v0.3 data review:** player-performance data is no longer a
  Phase 1 blocker. Existing approved/evaluated feeds plus separately governed
  research datasets are sufficient to build and audition the player model.
- **2026-08-17 — v0.3 data review:** `api_football` is a historical development
  source on the current free account; 2026/27 current-season access remains
  paywalled and is not required for Phase 1A.
- **2026-08-17 — v0.3 data review:** `big_balls_sports_data` is a zero-cost
  live player-data candidate but remains `EVALUATED`; published quota is 1,000
  requests/day and 2026/27 field coverage must be live-verified before
  approval/use.
- **2026-08-17 — v0.3 data review:** Vaastav FPL historical data and FPL Core
  Insights may be evaluated under explicit research scopes; neither is promoted
  to production merely because the files are public.
- **2026-08-17 — v0.3 data review:** OpenFPL is an independent benchmark only.
  Historical FPL `xP` is treated as potentially lookahead-contaminated unless a
  pre-deadline timestamp proves otherwise.
- **2026-08-17 — v0.3 data review:** the main remaining live acquisition gap is
  FPL-specific current prices/positions and user squad economics, not generic
  football fixtures or player-performance history.
- **2026-08-17 — v0.4 consolidation:** confirmed the §5.2 registry table
  directly against `config/source_registry.json` (not just `registry.py`'s
  seed defaults) — `api_football`, `football_data_org`, `tavily`, and
  `the_odds_api` are genuinely `APPROVED` today, not `REVIEW_REQUIRED`. This
  makes the "not a Phase 1 blocker" claim in v0.3 verified rather than merely
  asserted.
- **2026-08-17 — v0.4 consolidation:** downgraded `big_balls_sports_data` from
  "preferred" to "candidate, with a documented reason for skepticism" — its own
  registry entry already records a live-probe rejection (as an xG source) that
  found an advertised field silently empty in every payload tested (§5.2). The
  Phase 0 live-verification step is unchanged, but now cites why it's there.
- **2026-08-17 — v0.4 consolidation:** re-attached concrete file:line anchors
  from v0.1 that v0.2/v0.3 had dropped (`FixtureOutlook` at `lms.py:172`,
  `LMSConfig` at `config.py:108` as the `FPLConfig` pattern, `registry.py:274`/
  `registry.py:304` for the approval gate, `season_simulation.py` as the
  horizon-planning precedent for §8) — §3.2, §6, §6.3, §8, §13.2.
- **2026-08-17 — v0.4 consolidation:** this document replaces `SPEC_FPL.md`
  v0.1/`SPEC_FPL_2.md` v0.2/`SPEC_FPL_3.md` v0.3 as three separate files with
  one evolving document, matching `SPEC.md`'s own convention. The three
  earlier files are deleted; their content is preserved via this Decisions Log
  and git history rather than as live parallel files.
- **2026-08-17 — v0.5 review, three rule corrections:** Wildcard has no
  effective use in GW1 either, not only Free Hit — it becomes available from
  GW2 since squad-building transfers are already unlimited/free until the
  opening deadline (§2). Verified against search results describing the
  official FPL help content and 2026/27 season-start coverage; the wording in
  §2 states the mechanism (why), not just the rule, so it survives a future
  season where the specific reason might change.
- **2026-08-17 — v0.5 review, three rule corrections:** playing a Wildcard or
  Free Hit preserves banked free transfers rather than wiping them, and does
  not itself earn an extra free transfer for the Gameweek played (§2). Made
  explicit as a regression-test requirement in §8 and §9, not left as prose
  a future implementer could miss — confirmed via Premier League chip
  guidance and Fantasy Football Scout's explainer on this exact question for
  both chips.
- **2026-08-17 — v0.5 review, three rule corrections:** replaced the looser
  "scoring can be revised after matches" finalisation wording with the
  precise 2026/27 rule: Gameweek lockdown is 09:00 UK the day after the final
  fixture, extended specifically for Opta's BPS/DefCon review window (was one
  hour after the last whistle in prior seasons). §2 now states PROVISIONAL vs
  FINAL as the two states a result can be in.
- **2026-08-17 — v0.5 review, architectural addition:** added
  `fpl_gameweek_state` (§4) and the `UPCOMING -> LOCKED -> IN_PROGRESS ->
  PROVISIONAL -> FINAL` lifecycle (§11.3), giving the settlement side of a
  Gameweek the same immutable-record rigour §11.2 already gives the
  pre-deadline decision side. §12's validation rules and §19's guardrails now
  both require `status = FINAL` before any permanent evaluation write —
  actual points, captain outcome, transfer gain, DefCon and bonus can all
  still move during the PROVISIONAL window.
- **2026-08-17 — v0.5 review:** Open Question 7 reframed from "is
  price-change modelling worth it" to "does an independent price model add
  incremental value beyond the official Price Change Predictor" — the
  Predictor is confirmed to be a genuine official 2026/27 feature (15-minute
  updates, explicitly non-guaranteed), which raises the bar for building a
  competing model rather than consuming/presenting its output.
- **2026-08-17 — v0.5 review:** marked **approved for Phase 0/1A start**.
  Further architecture revision is not expected unless implementation
  surfaces a real contradiction; the acceptance principle at the end of this
  document is the standard Phase 0/1A work is now held to.

## Open Questions

1. Will `big_balls_sports_data` pass source-policy review and live 2026/27
   verification for the player fields needed by the live minutes/attacking
   model, given the prior probe already found one advertised field (xG)
   silently absent in every payload tested?
2. What permitted source/import supplies current **FPL** prices and
   season-specific FPL positions for the complete candidate universe if direct
   FPL reads remain `REVIEW_REQUIRED`/become `PROHIBITED`?
3. Does the research-only DefCon evidence show enough incremental predictive
   value to justify qualifying/buying a production CBIT/CBIRT source, or should
   `FPL_MODEL_V1` deliberately omit DefCon?
4. Does direct Understat ingestion add material value beyond the approved
   historical/API data and benchmark datasets, enough to justify its separate
   source-policy review?
5. How much predictive value does an approximate bonus model add once minutes,
   goal/assist distributions and clean-sheet probability are present?
6. Does the user want Classic mini-league cups or H2H strategy later, or is the
   primary Classic table the only Phase 3 objective?
7. Does an independent price-change model add incremental decision value
   beyond the official Price Change Predictor (updates every 15 minutes,
   explicitly "evidence, not a guarantee" — §2), or does consuming/presenting
   its output plus a budget-at-risk scenario already capture the available
   value? Expectation is "probably not," but that conclusion should be
   earned by comparison once the predictor is available as an input, not
   assumed here.

## Acceptance Principle

FPL mode does not earn production status because the optimiser returns a legal
15-player squad. It must demonstrate, in order:

```text
permitted/reproducible data
    -> calibrated player projections
    -> legal optimiser with correct FPL economics
    -> prospective recommendation snapshots
    -> stable application contracts
    -> UI
    -> later private-league strategy
```

The system should be able to explain not only **what** it recommends, but which
parts are quantitative model signal, which are game rules, which are uncertain
opponent assumptions, and which data was actually knowable before the deadline.
