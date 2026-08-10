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

1. **§4's free routes** — the shots-based proxy and the "is there really
   no free xG source?" research, both wrongly filed under a budget
   decision that only applies to a paid feed. The cheapest work left.
2. Items 1 and 2 wait on elapsed rounds rather than on anyone's time.
   §5 whenever a conversational layer would genuinely help.

§4 leads to an ablation, and with the 2025/26 holdout spent an ablation
can now only *retire* a candidate, not promote one — promotion needs
prospective evidence. That is a reason to run one (retiring a candidate
cheaply is worth it) but not a reason to expect a model change at the
end. Three candidates have now been retired this way: Shin's method,
MOV-Elo (ablation_007), and totals + Asian handicap (ablation_008).

Five items left this file on 2026-08-09. The round-file rename is done —
`rounds/` now holds one real round per file, named
`<season>_round_<NN>.json`, and nothing there reads as a template.
Team-specific opponent bias is built: the design question it was blocked
on was answered in favour of a market-sourced club-reputation signal
(bookmaker odds already persisted with each forecast) rather than the
fitted model's own team strength, so `opponent_intelligence.py` keeps its
independence from the forecasting core. See `team_bias_profiles`.
Shin's method and MOV-Elo were run through the ablation ladder and both
rejected (results/ablation_007). §19's `opponent_snapshot` /
`strategy_prediction` linkage is built: `strategy_history.py` persists
every round-level and season-length recommendation append-only, with the
raw picks it deviated from, the opponent data that existed at the time,
and the `forecast_version` it was taken against. The WCAG audit is done
— see [accessibility_audit.md](accessibility_audit.md). One real level-A
failure was found and fixed (scrollable tables were unreachable by
keyboard); everything else passed, with the keyboard-interaction and
screen-reader passes explicitly recorded as not yet done.

**On items 1 and 2 below.** Neither can be finished without data that
only accrues by playing rounds. What *was* buildable — and was built the
same day — is step 1 of each: the measurement that makes the missing data
start accumulating, rather than having to be reconstructed later from
whatever survived. Both were pure prerequisites, adopting no model and
adjusting no forecast, so §28 is untouched. `super6 readiness` prints
both counters, so "not yet" now has a number attached instead of being
re-argued from memory:

```bash
super6 readiness
```

---

## 1. Full §13.2 Round Opportunity Model characteristic-matching

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
1. ~~Accumulate real Super 6 rounds through 2026/27 to know what
   "observed characteristics" actually look like for *this* private
   league's rounds specifically (not just Premier League fixtures in
   general).~~ **Built 2026-08-09** — `round_characteristics.py` measures
   division mix, favourite strength, home/away balance, competitiveness
   and expected goals from each round's stored forecast, and
   `observed_profile` pools them into the target a matched sampler would
   reproduce. Derived from `prediction_history`, not stored separately,
   so every round already recorded is characterised retroactively and
   every future round is characterised for free. `super6 readiness`
   prints the counter. What remains is elapsed rounds, nothing else.
2. Build a stratified sampler (e.g. bucket historical fixtures by
   home-win-probability band or division mix, then sample within
   buckets matching the real rounds seen so far).
3. Ablation-test the stratified sampler against the current naive
   uniform draw (§28) before adopting it.

**Effort.** Medium, and now purely time-blocked. Step 1 is done and
accruing; steps 2-3 are a couple of days' work whenever there are enough
rounds to match against — `matching_readiness` names ~20 as the point
where an ablation would have any power, against 1 today.

---

## 2. Evidence → λ (Increment 5 player-impact model)

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
1. ~~Accumulate enough (claim, outcome) history to fit something real —
   e.g. regressing goal output against confirmed absences, once enough
   rounds have both a recorded claim and a recorded result.~~ **Built
   2026-08-09** — `claim_outcomes.py` joins each recorded claim to the
   result of the fixture it was about, via the forecast that names that
   fixture. Both halves were already being recorded and neither was
   joined to the other, so no pairs existed to fit on; they now accrue
   automatically. `super6 readiness` prints the counter. What remains is
   claims and results actually landing.
2. Build versioning/backtesting infrastructure for the impact model
   itself, matching the rigor already applied to the goal-rate model
   (§29-32).
3. Validate it beats "no adjustment" (§28) before promoting it out of
   shadow.

**Effort.** Large, and still the riskiest item on this list — a wrong
impact model here could actively degrade forecasts rather than just fail
to help. Step 1 is done and accruing, so this is now time-blocked like
§13.2 above, but steps 2-3 remain most of the work and none of it should
start before there is a defensible sample: `fitting_readiness` names ~200
confirmed absences, against 0 today. Note the claim side needs the
knowledge extractor actually running (Ollama configured, Tavily key
present) — no claims are recorded while it is off, so this counter can
sit at zero indefinitely without that.

---

## 3. BTTS / correct-score markets (§6)

**What.** Only `h2h` and opt-in `totals` are fetched live today
(`sources/odds_api.py`).

**Asian handicap and totals are done — tested 2026-08-10, both rejected.**
See [results/ablation_008_totals_and_handicap.md](../results/ablation_008_totals_and_handicap.md).
The historical odds *were* in the corpus all along at ~100% coverage; the
parser never read them, and this item wrongly recorded that as "no data
exists". Both markets are now parsed into the canonical frame (pre-match
and closing) and both constraints are implemented behind
`totals_blend` / `handicap_blend`, neither promoted.

The reason they fail is worth keeping, because it is not "the market
knows nothing": model and market disagree by ~5-6pp on goal count and
supremacy, but with ~zero *mean* difference. The disagreement is
symmetric, so correcting toward the market adds variance without removing
bias. A candidate has to be better in a *directional* way to survive
blending, which is what the 1X2 market was and these are not.

**Still blocked: BTTS and correct score.** Neither appears in the corpus
in any season — unlike the two above, that claim was checked and holds.

**Unblock.** Either find a supplementary historical-odds source covering
them, or accumulate live 2026/27 data prospectively and treat a season of
it as the validation set (slow, but requires no new data source).

**Effort.** Small to fetch. The real cost is validating any use of them,
and on current evidence the prior should be modest: two closely related
markets have now been tested and neither helped.

---

## 4. xG (Shin's method and MOV-Elo: retired 2026-08-09)

**What.** Standing ablation candidates from §7, §29.4, §32.1.

**Shin's method and MOV-Elo are done** — both implemented and run through
the ladder on the 2024/25 validation season, both **rejected**. See
[results/ablation_007_shin_and_mov_elo.md](../results/ablation_007_shin_and_mov_elo.md)
and the VALIDATION.md decisions table. Shin's is indistinguishable from
proportional devigging here (only 1 of 932 picks changed; paired log loss
−0.00015, CI straddling zero), largely because w=0.5 dilutes half of any
devig difference before it reaches a probability. MOV-Elo is marginally
worse than *plain* Elo, which means ablation_003's redundancy verdict is
about what per-division α/β already capture rather than about rating
quality. Both stay available behind flags (`devig="shin"`,
`use_elo=True, elo_mov=True`) and neither is in the frozen model path.

**Correction, 2026-08-09.** This item said flatly "xG needs a budget
decision — your manual approval per §25, not an engineering call". That
bundled three separate questions together and pushed all of them onto the
one person who could not answer two of them. Only the third is actually a
budget decision:

1. **A zero-cost proxy is already possible and nobody has tried it.**
   `data/` carries shots, shots on target and corners at ~100% coverage
   for every season and both divisions (`HS`, `AS`, `HST`, `AST`, `HC`,
   `AC`). A shot-based goal-expectation proxy is buildable and ablatable
   today at no cost. It is *not* xG — no shot locations, no chance
   quality, and shot counts are a much cruder signal — but §28 does not
   require a candidate to be the best possible version of itself before
   being tested. If a crude proxy adds nothing, that is informative about
   whether paying for the real thing is worth it.
2. **"No confirmed free source exists" was asserted, never researched.**
   It should not be carried as established fact. Checking what free or
   licence-compatible xG sources exist is an engineering task, not a
   spending decision.
3. **A paid feed is the only part that is genuinely yours.** Sportmonks
   and similar breach the £10/month target (§25), and spending money is
   your call — but it should be the *last* question asked here, not the
   first.

**Unblock.** Try (1), do the research in (2), and only bring (3) to a
budget decision if the cheap routes look insufficient *and* the proxy
suggested there is signal worth paying for.

**Effort.** (1) small. (2) an afternoon of research. (3) not effort at
all — a decision.

**Worth knowing before adding another rung here.** With the 2025/26
holdout spent, no ablation on the validation season can promote anything
on its own any more — a win there could not be confirmed on unseen data,
and VALIDATION.md is explicit that those scores are biased upward. Running
a rung is still worth it to *retire* a candidate, as ablation_007 did.
Promoting one now needs prospective evidence (`results/prospective_*`).

---

## 5. "Ask the agent" conversational entry (§67)

**What.** A chat-style Q&A layer over the existing forecast/strategy
data ("Why have we gone 1-0 rather than 2-0?").

**Why deferred.** The spec itself marks this low priority — "the
application is not primarily a chatbot" — and everything else on this
list has a stronger claim on effort.

**Unblock.** Nothing blocking; build whenever it'd be genuinely useful.
The one hard constraint from §67: answers must come from the same
persisted forecast/strategy data the structured UI already shows —
"the conversational agent must not create a separate truth."

**Two things to be clear-eyed about before calling this a quick win.**

*It is Ollama-dependent, in its interesting form.* A genuinely
conversational layer needs an LLM, and Ollama is the one available
(Settings → Knowledge extractor). With the extractor off there is no
model to answer with. A non-LLM version — a fixed set of questions
answered from `transparency.py`'s existing explainability payloads — has
no such dependency and no hallucination risk, but is a help panel rather
than a conversation. Worth deciding which one is actually wanted before
starting, because they share almost no implementation.

*The LLM is not the hard part; §67's constraint is.* A model paraphrasing
persisted data can state a number that contradicts the structured UI on
the same screen, which is precisely the "separate truth" §67 forbids —
and it fails in the most damaging possible way, by sounding authoritative.
Every answer needs to be traceable to a stored value, which in practice
means quoting the persisted figure rather than letting the model restate
it. That discipline, not the chat UI, is the work.

**Effort.** Medium — the UI-facing part is simple, the discipline of
sourcing every answer from already-persisted data (rather than
recomputing something new and possibly inconsistent) is the real work.

---
