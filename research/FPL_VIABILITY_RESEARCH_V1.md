# FPL_VIABILITY_RESEARCH_V1 — should `bym` add a Fantasy Premier League mode?

**Registered: 2026-08-17, before any spec was drafted.**

This is a **viability study, not a spec**. It exists to answer one question
before committing engineering time: is Fantasy Premier League (FPL) a
mode `bym` *can* build well, given what already exists (Dixon-Coles core,
source-registry gate, the Sky boundary, the mates-relative strategy
layer), and what would it actually require?

## The question

> Squad-picking, budget-constrained, chip-timed fantasy football is a
> structurally different problem from "predict six scorelines" or "survive
> one pick a week." Does `bym`'s existing machinery transfer, or would
> this be a second product wearing the same CLI?

## Summary verdict

**Conditionally viable, but not a small addition — and one open question
has to be resolved before any code is written, not after.**

It is the largest mode `bym` would have added: LMS reused the match model
wholesale and only added a decision layer (`LMSConfig`, provisional,
no freeze — `config.py:107-149`). FPL needs a genuinely new **player-level**
data pipeline and a genuinely new **optimization** engine (squad selection
under budget/formation/club-limit constraints), neither of which exist
anywhere in the codebase today. But the part of `bym` that *is* distinctive
— optimizing against mates in a mini-league rather than against the whole
field — maps onto FPL's template/effective-ownership dynamic more directly
than it maps onto Super 6 or LMS, and no mainstream FPL tool does that
well. That's the case for building it. The case against is mostly the data
source, below.

## What FPL actually is (mechanics, 2026/27 season)

- Squad: 15 players, £100.0m budget, 2 GK / 5 DEF / 5 MID / 3 FWD, max 3
  players per Premier League club.
- Starting XI + bench each gameweek, one captain (points ×2) and one
  vice-captain (auto-promoted if captain doesn't play).
- Scoring: goals, assists, clean sheets, appearance points, cards, saves
  (GK), penalty misses/saves, **and**, new since 2025/26 and unchanged for
  2026/27, **defensive contribution (DefCon)** points — +2 for 10+
  CBIT actions (defenders) or 12+ CBIRT actions (mid/fwd) in a match.
  The Bonus Points System (BPS) was also retuned for 2026/27 to reduce
  overlap with DefCon (tackled players no longer lose BPS; CBI now returns
  1 BPS per 3 actions, not 2; GK bonus criteria widened).
- Transfers: 1 free transfer/gameweek (banks up to a cap), extra
  transfers cost −4 points each.
- Chips: **two of each** this season structure — Wildcard, Free Hit,
  Bench Boost, Triple Captain — one set usable in the first half of the
  season (through GW19), one in the second half. Only one chip per
  gameweek.
- Prices move day-to-day based on aggregate transfer activity — the
  mechanism is undocumented by FPL and only reverse-engineered by the
  community.
- Scale: 10m+ entries as of 2022/23, still growing — this is a mature,
  heavily-instrumented market, not a niche.

Sources: [Premier League — 2026/27 Fantasy rule changes](https://www.premierleague.com/en/news/4679873/all-you-need-to-know-about-changes-to-fpl-for-202627), [Premier League — Defensive Contributions explained](https://www.premierleague.com/en/news/4361991/whats-new-in-202526-fantasy-defensive-contributions), [Premier League — two sets of chips](https://www.premierleague.com/en/news/4362027/whats-new-in-202526-fantasy-two-sets-of-chips), [Fantasy Football Scout — 2026/27 rule changes](https://www.fantasyfootballscout.co.uk/2026/07/20/fpl-2026-27-5-rule-changes-new-features-announced).

## The data source problem — the load-bearing risk

`bym` already has a named precedent for exactly this situation:
`sky_super6` is seeded `PROHIBITED` in the registry (`registry.py:226-239`)
because Sky's terms forbid automated extraction of its own game data —
which is why round fixtures/deadlines are **hand-entered**, never scraped
(§9.1), and why "the agent never submits anything to Sky" is a hard rule.

FPL's data — prices, ownership %, live points, chip state, bonus-point
awards — lives at `fantasy.premierleague.com/api/*`. It is public,
unauthenticated, and widely used by the hobbyist community (the whole
`fpl`/`fpl-optimiser` GitHub ecosystem runs on it), but it sits under
**premierleague.com's Terms of Use**, which explicitly prohibit, in terms
that read like a template for what got Sky marked `PROHIBITED`:

> "The Website and App must not be used in any other way, including for
> commercial purposes, and you may not otherwise reproduce, re-utilise or
> redistribute it" — including "creating a database (electronic or
> otherwise) that includes material downloaded or otherwise obtained from
> the Website or App."

Source: [Premier League — Terms of Use](https://www.premierleague.com/en/terms-and-conditions).

There is no official, documented, licensed alternative for FPL-specific
game state (prices, ownership, live/bonus points, chip status) — that
data only exists inside FPL's own system. This is different from match
results, where `football_data_co_uk` (the one currently `APPROVED`
source) gives an independent, redistribution-permitted mirror. **No
independent mirror exists for FPL's own game economy.**

Two ways this resolves, and they lead to very different specs:

1. **Registry review says PROHIBITED, same as Sky.** `bym` never queries
   `fantasy.premierleague.com`. The mode falls back to the same pattern
   Super 6 already uses for Sky: the *user's own* squad, bank, chip
   status, and prices are entered or pasted into the UI by the user
   (their own account, their own data, no automated extraction), and
   `bym` supplies projections and recommendations only. This is buildable
   today without any registry change, at the cost of the user doing more
   manual data entry per gameweek than Super 6 requires.
2. **Registry review promotes it to APPROVED** on the reasoning that
   personal, non-commercial, non-redistributed use for a single private
   mini-league (the same framing that already governs how `bym` treats
   Sky data, never republished) falls outside what the clause is aimed
   at. This is a **deliberate human judgment call**, per `registry.py`'s
   existing design — not something to default into by writing the client
   first and asking later.

Either way, **player-level match stats for the projection model** (goals,
assists, minutes, xG/xA, defensive actions for DefCon) are a separate,
easier question — `understat.com` and `fbref.com` publish this
independently of FPL and would need their own registry entries, most
likely `REVIEW_REQUIRED` → `APPROVED` on terms similar to
`football_data_co_uk`, not `PROHIBITED`.

## What already exists that transfers

- **Match-level forecasts.** `FixtureOutlook` (`lms.py:171-211`) already
  wraps `score_matrix()`/`DixonColesParams` — clean-sheet probability and
  goal expectancy per fixture, computed once per gameweek. Directly
  reusable for the clean-sheet and goal-involvement components of a
  player-points projection; no need to refit anything at match level.
- **The frozen/provisional split.** `FROZEN_PHASE1_V1` stays untouched;
  an `FPLConfig` in the LMS mould — provisional, decision-layer only, no
  freeze or holdout claim until backtested — is the right shape for a
  first cut (`config.py:107-149` is the template).
- **The "recommend, never submit" boundary.** Already a hard rule for
  Sky; the identical boundary applies to FPL by the same logic and should
  be stated as explicitly.
- **Source gating itself.** The registry's `REVIEW_REQUIRED` →
  `approve()` CLI flow (`registry.py:274-321`) is exactly the mechanism
  to route the decision above through, rather than deciding it in code.
- **The mates-relative strategy layer.** §11–14's opponent-aware,
  Monte-Carlo season simulation exists *because* beating a private league
  of known mates is a different optimization target than maximizing raw
  expected points against the whole field. FPL's template/differential/
  effective-ownership dynamic (a player's real value depends on what your
  mates already own, not just his raw xP) is the same shape of problem,
  one level down at player granularity. This is the one part of the
  existing engine that isn't just reusable but is *the* differentiator —
  most FPL optimizer tools (linear-programming xP maximizers, see below)
  are field-agnostic and would need this bolted on; `bym` would start
  with it.

## What does not exist and would be net-new

- **Player-level stats ingestion.** Nothing in the codebase tracks goals,
  assists, minutes, or defensive actions per player — `availability/`
  is boolean-ish lineup/injury status only, explicitly disconnected from
  scoring (`QUANTITATIVE_IMPACT_MODEL_ENABLED` is off,
  `availability/model.py:1-9`). A new ingestion pipeline (understat/fbref,
  per above) and a new per-player points-projection model are required
  from zero.
- **Squad optimization.** Nothing resembling an ILP/knapsack solver
  exists; Super 6 and LMS both pick from a handful of discrete fixture
  outcomes, not a 15-of-~600-player combinatorial squad under budget,
  formation, and 3-per-club constraints. This is genuinely new machinery,
  not an extension of `recommend.py`.
- **Transfer/chip planning over a horizon.** `season_simulation.py`
  plans LMS club usage over a season; an FPL equivalent (when to wildcard,
  when to bench-boost, price-change timing) is conceptually adjacent but
  operates over a much larger state space (squad composition × prices ×
  chip availability) and would need its own module, not a parameter
  tweak to the existing one.
- **Validation.** `assert_holdout_unspent` is keyed by season only
  (`validation.py:32-54`), not by mode — any FPL-specific claim (e.g. "our
  bonus-points model beats template") that wants to use the 2025/26
  holdout trips the same spent gate Super 6 and LMS already share. A
  from-scratch player-points model would need its own untouched
  evaluation season or accept read-only reuse of an already-published
  result.
- **UI.** LMS's page (~500 lines JS + ~330 lines `web.py` dict-building +
  ~190 lines state handling) is the closest comparable, and a squad
  builder / transfer planner / chip-timing UI is a bigger surface than
  that, not smaller — LMS is one pick a week, FPL is fifteen picks plus a
  captaincy plus a bench order plus a chip decision, every week.

## Competitive landscape

The market is mature and largely free. Established, well-used tools
already do most of what a naive "xP model + optimizer" would offer:

- **Expected-points + linear-programming optimizers** are a solved,
  commodity pattern — multiple open-source implementations
  ([`spinalwiz/fpl-optimiser`](https://github.com/spinalwiz/fpl-optimiser))
  and commercial tools (FPL Review, FPL Form, FPL Oracle, Fantasy
  Football Fix) already do PuLP/OR-Tools-style squad selection against a
  budget. Building the same thing with no differentiation would be
  competing on a feature that's already free.
- **Content/community layer** (Let's Talk FPL, Fantasy Football Scout,
  and a long tail of YouTube/Twitter/Reddit creators) drives the
  "template team" — a set of highly-owned players the whole field
  converges on. This is measured as **effective ownership** (ownership %
  + captaincy %, can exceed 100%) and is the basis for
  template-vs-differential strategy: template picks track the field,
  differentials are the only way to gain relative rank. Any recommender
  that ignores EO and just maximizes raw xP will converge on the same
  template picks everyone else already has — fine for overall rank,
  useless for beating a private mini-league of mates who likely also own
  the template.
- `bym`'s angle, if it builds this, is the same one that justifies
  §11–14 for Super 6/LMS: optimize relative to a known, small group of
  mates, not the anonymous field of 10m+. That needs the mates' actual
  squads as input (obtainable, ironically, more easily than the ToU
  question above — public mini-leagues expose member squads via the same
  API) and is not something the existing commodity optimizers do.

## Effort shape, relative to what's already been built

Bigger than the LMS addition (which reused the match model entirely and
added a decision layer) and closer to a second Phase-1-sized build: new
data source(s) and registry decision, new player-projection model, new
squad-optimization engine, new transfer/chip planning module, new
validation scoping, and a UI surface larger than LMS's. The one thing
that *shrinks* the lift is that the match-level core (Dixon-Coles,
`FixtureOutlook`, the frozen/provisional config split, the source-registry
mechanism, the opponent-aware strategy layer) is already built and none
of it needs to be re-derived — FPL consumes it as an input, it doesn't
replace it.

## Recommendation

1. Resolve the source-registry question first, deliberately, before
   writing an implementation spec that assumes an answer — this is a
   `bym sources approve`-shaped decision (or an explicit "stays
   `PROHIBITED`, user enters their own squad state" decision), not a
   detail to settle while coding.
2. If proceeding, scope Phase 1 narrowly: reuse `FixtureOutlook` for
   clean-sheet/goal inputs, add a minimal player-projection model off one
   newly-`APPROVED` stats source, and a squad optimizer — without the
   mates-relative layer, transfer-horizon planning, or chip timing, all
   of which are natural Phase 2+ in the same shape Super 6 went through
   its own phases.
3. Treat the mates-relative optimization (point 4 above) as the actual
   reason to build this rather than use an existing free tool — without
   it, this mode duplicates commodity tooling that already exists for
   free.
