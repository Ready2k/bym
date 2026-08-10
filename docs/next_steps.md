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

1. Nothing here is both ready and clearly worth doing. Items 1 and 2
   wait on elapsed rounds rather than on anyone's time; §3 needs a data
   source that may not exist; §4 is now purely a budget decision; §5 is
   marked low priority by the spec itself. This is the honest state of a
   backlog that has been worked down rather than a queue to keep pulling
   from.

With the 2025/26 holdout spent, an ablation can now only *retire* a
candidate, not promote one — promotion needs prospective evidence. That
is still a reason to run one, and four candidates have been retired this
way: Shin's method and MOV-Elo (ablation_007), totals and Asian handicap
(ablation_008), and the shots-based xG proxy (ablation_009).

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

## 4. xG — free routes exhausted, paid route is your decision

**What.** §7's standing candidate: expected goals as a model input.

**Shin's method and MOV-Elo** were the other two candidates once filed
here; both were run through the ablation ladder on 2026-08-09 and
rejected ([results/ablation_007](../results/ablation_007_shin_and_mov_elo.md)).

**Sources evaluated — dated, so this is not re-derived a fourth time.**
This item previously asserted "no confirmed free source exists" without
ever checking. Each candidate below was actually probed, and the same
three keep being re-proposed:

| source | evaluated | Championship? | xG? | verdict |
|---|---|---|---|---|
| [Understat](https://understat.com/) | 2026-08-10 | **No** — 6 top-tier leagues | yes, good | **Blocked on terms.** `robots.txt` is `User-agent: *` / `Disallow: /`, no API. §18.1 gates on terms and `sky_super6` is PROHIBITED on exactly this basis |
| [FBref](https://fbref.com/) | 2026-08-10 | had it | **gone** | Stats Perform terminated the Opta feed **2026-01-20** and required removal. Historical data remains, never updates. Also 403s automated fetches |
| API-Football | 2026-08-08 (live probe) | yes | not in registered purposes | **Current seasons paywalled** on the free tier (2025+ rejected, "try from 2022 to 2024"). The 100/day cap is the lesser problem |
| [Big Balls Sports Data](https://bigballsdata.com/) | 2026-08-10 (live key) | **No** — `/v1/leagues` returns 8, England = EPL only | **absent from payload** | 0 of 30 finished EPL matches had any `team_stats`; the 1-in-40 that did carried 28 fields with no xG. Lineups `available=false`. Fixtures stop 2026-05-24. Registered `REVIEW_REQUIRED`; key kept in `secrets/` as a cross-reference, nothing queries it |

The binding constraint is the **Championship — 59% of the corpus**. A
Premier-League-only feed cannot serve a model fitted across both
divisions — *unless* the per-division argument below is taken up, which
would make a Premier-League-only feed genuinely usable. Every PL-only
source found so far is blocked for some *other* reason, so that route
stays open in principle and closed in practice.

**The zero-cost proxy was built and tested, and failed.** Shots and shots
on target are in the free corpus at 100% coverage for both divisions.
`model/shots.py` turns them into a goal expectation and fits a second
Dixon-Coles on that smoother target; ablation_009 rejects it at every
blend weight, with log loss degrading monotonically.

The reason is worth carrying forward: the proxy has no shot-quality
information, so it **persistently** underrates strong teams (Man City
+0.39 goals/match above its shot profile) and overrates weak ones —
year-over-year residual correlation +0.19, so bias rather than noise. See
[results/ablation_009](../results/ablation_009_shot_proxy.md).

**Read that result the right way.** It is *not* evidence that real xG
would fail here. The proxy fails precisely because it lacks chance
quality, which is the thing real xG adds. It establishes only that the
free route is exhausted.

**"A PL-only feed would cover most Super 6 fixtures anyway" — asked and
answered, 2026-08-10.** The reasoning is sound and worth recording,
because it is the one argument that would make the Championship gap
survivable: Super 6 leans heavily Premier League (the one round observed
so far is 6/6 E0), the model already fits *per division* (ablation_002),
so an E0-only xG signal is architecturally coherent rather than a patch —
the E0 fit would use xG, the E1 fit goals — and a fixture without xG
would degrade to the goal-fitted model, the same graceful degradation
missing odds already get.

What it needs is a PL xG feed that is *otherwise* usable, and none of
the candidates is. **Understat fails on terms, not coverage.** Its
`robots.txt` is
`User-agent: *` / `Disallow: /` — automated access is disallowed
site-wide, and there is no API. §18.1 gates every outbound query on
checked terms, and `sky_super6` is already PERMANENTLY PROHIBITED on
exactly this basis ("Sky's terms prohibit automated and unauthorised
manual data extraction"). Applying that rule consistently, Understat
cannot be registered APPROVED. The precedent is the project's own.

**Big Balls Sports Data fails on payload, not terms.** It is a legitimate
API with a free tier, so the terms objection genuinely does not apply —
but the advertised xG is not in any response (see the table above), which
leaves nothing to feed the E0 fit. Its key is kept in `secrets/` as a
cross-reference on results and scores; it is registered
`REVIEW_REQUIRED` and nothing queries it.

Two smaller notes for whoever revisits this: Understat does not cover the
Champions League either (six domestic leagues only), and such fixtures are
outside the E0/E1 corpus anyway — already handled by
`MARKET_ONLY_FALLBACK`. And a promoted team would carry goal-based E1
history into an xG-based E0 fit, so §29.4's priors would straddle two
units; surmountable if the xG source is calibrated to goals, but it is a
real wrinkle rather than a detail.

**Unblock.** Only a budget decision remains — a Championship-covering
feed at ~£30/month against §25's £10 target, from a source whose terms
permit programmatic use. That is genuinely yours, and now it is the
*only* part that is: the two engineering questions underneath it have
been answered.

**Effort.** Not effort — a decision. And note that even with a feed,
promotion needs prospective evidence: the 2025/26 holdout is spent, so an
ablation could only retire the candidate, not adopt it.

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
