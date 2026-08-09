# Next Steps

A backlog of deliberately deferred work — not bugs, not oversights. Every
item here was cut for a stated reason (usually §28's ablation discipline,
missing data, or a design question that needs a human answer, not more
code). This file exists so revisiting one later doesn't require
re-deriving why it was skipped, and says what would actually unblock it.

Status source of truth stays [engine_status.md](engine_status.md)
(what's built) and [README.md](../README.md) ("What's deliberately not
here yet"). This file adds the layer those don't carry: **how to
unblock each one**, and roughly how much it costs. When an item here
gets built, it moves out of this file and into a commit + those two
docs, the same way everything else in this project has landed.

## Suggested order

Given §1.1's priority order (private-league win > Super 6 points/exact
scores > Golden Goal/national prizes > 100 Club) and that measurement is
prospective-only now (2025/26 holdout is spent and grid-searched,
VALIDATION.md):

1. Rename `rounds/example_round.json` — five minutes, removes a real
   ambiguity.
2. §13.2 Round Opportunity Model characteristic-matching — the season
   simulator's actual remaining weakness.
3. Team-specific opponent bias — blocked on a design decision, not
   effort; resolve the decision, the code is small.
4. Evidence → λ (Increment 5) — the largest unbuilt piece of the spec
   and the riskiest to get wrong; needs real data accumulation first
   regardless of when it's picked up.
5. Everything else below — lower leverage, worth doing opportunistically
   rather than in a fixed order.

---

## 1. Rename `rounds/example_round.json`

**What.** The file currently holds real-looking 2026/27 Round 1 fixtures
and odds, under a filename that says "example."

**Why it matters.** Ambiguous to a future reader — or you, in six
months — whether it's a template to copy or live data currently in use.
`.claude/launch.json` and the README's usage examples both point at it
by name.

**Unblock.** Once Sky publishes a round for real, copy the live file to
something unambiguous (e.g. `rounds/round_01.json` or a date-stamped
name) and update the two references above. No code change.

**Effort.** Trivial.

---

## 2. Full §13.2 Round Opportunity Model characteristic-matching

**What.** `season_simulation.sample_synthetic_round` currently draws six
fixtures uniformly from the historical corpus (any pairing, any era) and
forecasts them with the current fitted parameters. §13.2 wants sampling
that matches *observed* Super 6 characteristics instead — league mix,
favourite-strength distribution, home/away balance, fixture
competitiveness, expected-goals distribution.

**Why deferred.** The basic season simulator had to exist first (it now
does — §12-13). §28 says don't add matching complexity until there's a
baseline to test it against; there is one now.

**Unblock.**
1. Accumulate real Super 6 rounds through 2026/27 to know what
   "observed characteristics" actually look like for *this* private
   league's rounds specifically (not just Premier League fixtures in
   general).
2. Build a stratified sampler (e.g. bucket historical fixtures by
   home-win-probability band or division mix, then sample within
   buckets matching the real rounds seen so far).
3. Ablation-test the stratified sampler against the current naive
   uniform draw (§28) before adopting it.

**Effort.** Medium, but genuinely time-blocked, not effort-blocked —
step 1 needs a season's worth of real rounds before step 2 has anything
to match against.

---

## 3. Team-specific opponent bias (favours big clubs, underrates promoted teams)

**What.** §11 wants detection of whether a rival systematically favours
"big" clubs or underrates promoted teams — independent of any single
fixture's forecast.

**Why deferred.** No club-reputation signal exists to build this from.
Two candidate sources, both with a real cost:
- The model's own fitted attack/defence strength (`α`) as a "big club"
  proxy — but `league.py`/`opponent_intelligence.py` are deliberately
  model-independent by design (see their module docstrings); wiring in
  a model dependency breaks that separation.
- A hand-maintained list of "big six"-style clubs — rejected as
  unprincipled and something that goes stale every transfer window.

**Unblock.** This needs a decision from you, not more code: is it
acceptable for `opponent_intelligence.py` to start depending on the
fitted model's team-strength output (breaking the current
football-model / opponent-model separation), or should this wait for an
explicit, separately-sourced club-reputation signal? Once that's
decided, the implementation itself is small — it's the same shape as
`favourite_bias_profiles`, just keyed on team identity instead of
fixture-relative favourite status.

**Effort.** Small once the design question is answered.

---

## 4. Evidence → λ (Increment 5 player-impact model)

**What.** Converting a knowledge-agent or availability claim (e.g.
"striker ruled out") into an actual adjustment to `λ_home`/`λ_away`,
per §8's worked example.

**Why deferred.** No validated impact model exists. Guessing at
adjustment magnitudes would violate §22 ("never alter predictions
without an explainable, reproducible trigger") and §28's ablation
discipline — there's currently no code path from an event to a
parameter at all (`QUANTITATIVE_IMPACT_MODEL_ENABLED = False`,
`docs/phase2_increment3.md`).

**Unblock.**
1. Accumulate enough (claim, outcome) history to fit something real —
   e.g. regressing goal output against confirmed absences, once enough
   rounds have both a recorded claim and a recorded result.
2. Build versioning/backtesting infrastructure for the impact model
   itself, matching the rigor already applied to the goal-rate model
   (§29-32).
3. Validate it beats "no adjustment" (§28) before promoting it out of
   shadow.

**Effort.** Large, and the riskiest item on this list — a wrong impact
model here could actively degrade forecasts rather than just fail to
help. Needs real data accumulation before it's responsibly attemptable
regardless of engineering time invested.

---

## 5. §19 `opponent_snapshot` / `strategy_prediction` linkage

**What.** A `forecast_run` record currently can't say which strategy
recommendation was chosen (raw model pick vs. strategy-adjusted pick),
or what opponent data existed at the moment a decision was made.

**Why deferred.** No strategy recommendation is persisted anywhere
today — `_strategy_payload`/`_season_strategy_payload` in `web.py`
compute it fresh on every page load. There's nothing to link a snapshot
to yet.

**Unblock.** Persist strategy/season-strategy recommendations the same
way `prediction_history.py` already persists forecasts (a parallel
append-only snapshot store, or an extension of the existing one), then
link `forecast_run` → `strategy_run` the way the spec's `forecast_run`
example already links to `feature_snapshot`/`odds_snapshot`.

**Effort.** Medium — well-scoped, reuses `prediction_history.py`'s
existing pattern directly.

---

## 6. Asian handicap / BTTS / correct-score markets (§6)

**What.** Only `h2h` and opt-in `totals` are fetched today
(`sources/odds_api.py`).

**Why deferred.** No historical odds for these markets exist in the
Football-Data.co.uk backtesting corpus, so nothing fetched could be
validated per §28 even once collected.

**Unblock.** Either find a supplementary historical-odds source that
covers these markets, or accumulate live 2026/27 data prospectively and
treat a season of it as the validation set (slow, but requires no new
data source).

**Effort.** Small to fetch — the pattern mirrors the totals odds just
added. The real cost is in validating any actual use of them, not the
fetch itself.

---

## 7. xG / Shin's method / MOV-Elo

**What.** Standing ablation candidates from §7, §29.4, §32.1.

**Why deferred.**
- **xG** — no confirmed free source exists; paid options (e.g.
  Sportmonks) breach the £10/month target (§25).
- **Shin's method / MOV-Elo** — simply haven't been run through the
  ablation ladder yet.

**Unblock.**
- xG needs a budget decision — this requires your manual approval per
  §25, not an engineering call.
- Shin's method and MOV-Elo need someone to actually run the ablation
  (`ablation_00X`-style script, see `results/`) — the training data for
  both already exists, unlike xG.

**Effort.** Shin's/MOV-Elo — small, could be picked up anytime.
xG — blocked on a budget decision, not effort.

---

## 8. "Ask the agent" conversational entry (§67)

**What.** A chat-style Q&A layer over the existing forecast/strategy
data ("Why have we gone 1-0 rather than 2-0?").

**Why deferred.** The spec itself marks this low priority — "the
application is not primarily a chatbot" — and everything else on this
list has a stronger claim on effort.

**Unblock.** Nothing blocking; build whenever it'd be genuinely useful.
The one hard constraint from §67: answers must come from the same
persisted forecast/strategy data the structured UI already shows —
"the conversational agent must not create a separate truth."

**Effort.** Medium — the UI-facing part is simple, the discipline of
sourcing every answer from already-persisted data (rather than
recomputing something new and possibly inconsistent) is the real work.

---

## 9. WCAG accessibility audit

**What.** A formal accessibility conformance pass.

**Why deferred.** Never done. Only ad hoc `aria-live`/`aria-label`/
skip-link markup exists today, added incidentally rather than audited.

**Unblock.** Run an automated audit (e.g. axe-core against the local
UI) plus a manual keyboard-navigation and screen-reader pass, then fix
what it finds.

**Effort.** Small–medium, mostly labour rather than design work — no
open questions to resolve first, unlike most items above.
