# Beat Your Mates — Always-On Prediction Agent
## Full Specification v0.4.1 — Single Vision Document
**§1-27: Functional Architecture Baseline (FROZEN, as of v0.3.2).** **§28-36: Mathematical & Algorithmic Pipeline (v0.4.1).** Originally developed as two documents; combined here into one so there is a single source of truth for the system. The architecture sections are not reopened by the math sections — §28-36 specify the mathematics inside boxes §1-27 already drew.
### 1. Primary Objective
The system shall **maximise the probability of finishing first in the private Super 6 competition, subject to its specific scoring, tiebreak and prize rules**, over the complete 2026/27 season.
The objective is therefore:
**Win the league, not simply predict football matches accurately.**
Formally, the optimisation target is:
**argmax P(Finish 1st | predictions, standings, opponents, rounds remaining)**
not simply:
**argmax E[points]**
Early in the season these two targets are almost identical — with little standings information and many rounds left, maximising expected points is close to optimal. Late in the season, with standings, rivals' tendencies, and a shrinking number of rounds known, they can diverge sharply — this is precisely where opponent modelling (§11) and the strategy engine (§12-13) earn their keep.
The agent must optimise for:
1. Expected Super 6 points.
2. Exact-score probability.
3. Relative performance against known competitors.
4. Current league position.
5. Remaining rounds.
6. Appropriate risk-taking when leading or trailing.
Secondary objectives are:
* maximise overall Super 6 season score;
* maximise exact-score hits;
* optimise Golden Goal predictions for round-prize tiebreaks (§2, §15);
* measure whether the system is genuinely outperforming simple baselines.

**Season timeline:** round deadlines are never hard-coded into this spec or the system. Sky's own channels have shown conflicting times for the same round — the promotional rules schedule states 15:00 for Round 1, while the live Super 6 homepage has separately shown 14:00 — and the authoritative rule is simply "first fixture kick-off / advertised Closing Time," set by Sky at its own discretion each round. Round timing is therefore config, not spec: see §9.1 for how the system captures it.

## 1.1 Objective Priority Ordering
Where objectives compete for development effort or a live trade-off, this is the priority order (referenced from §2.1, §12.1, §15):
```text
PRIORITY 1
Private-league season victory

PRIORITY 2
Super 6 points
Exact-score count

PRIORITY 3
Golden Goal / national round prizes

PRIORITY 4
100 Club opportunities
```
Lower-priority objectives are never allowed to silently override a higher one (§12.1 formalises this for the 100 Club specifically). §15 (Golden Goal) is real but Priority 3 — it earns a simple, working model before it earns a sophisticated one.
---
# 2. Super 6 Scoring Model
For each fixture:
* Correct exact score = **5 points**
* Correct result but wrong score = **2 points**
* Incorrect result = **0 points**
Golden Goal is used as a tiebreaker rather than contributing normal points.

**Definition of P(result).** For a candidate scoreline *s* with result *R_s* (home win / draw / away win), `P(result) = P(R_s)` is the model's probability of *R_s* occurring at all — home win, draw, or away win, summed across every scoreline sharing that outcome. Critically, `P(R_s)` **includes** `P(S=s)` itself: the exact scoreline is one of the outcomes that makes up its own result. This is why the two forms of the formula below are equivalent rather than double-counting.

For a candidate scoreline:
**ExpectedPoints = (5 × P(exact score)) + (2 × P(correct result but wrong score))**
Equivalent:
**ExpectedPoints = 2 × P(result) + 3 × P(exact score)**

Worked example — predicting 2-1 (home win):
```text
P(home win) = 60%      i.e. P(R_2-1) = 0.60
P(2-1)      = 12%      i.e. P(S=2-1) = 0.12

EV(2-1) = 2(0.60) + 3(0.12) = 1.56

Equivalently:
EV(2-1) = 5(0.12) + 2(0.60 - 0.12) = 5(0.12) + 2(0.48) = 0.60 + 0.96 = 1.56
```
The `0.60 - 0.12` term is `P(correct result, wrong score)` — every home-win scoreline other than 2-1 itself. This is also why an exact score is worth 5 points rather than a naive 7 (5 for the exact match, plus 2 for "also getting the result right") — the exact score and the correct-result-only case are mutually exclusive outcomes, not stacked ones.

The system must calculate this for every plausible scoreline rather than automatically selecting the single most probable score.
This distinction matters because the scoreline with the highest raw probability is not necessarily the scoreline with the highest Super 6 expected value.

**Verified against Sky Sports Super 6 official rules (Aug 2026):** 2 points for correct result, 5 points for exact scoreline, 30 points max per round (all six exact) — confirming the point values above are correct. Maximum jackpot requires all six exact scores; if unwon, a secondary prize goes to the round's highest scorer.

## 2.1 Private League vs National Game Objectives
These are related but distinct optimisation targets, and the private league's rules take strategic priority over the national game's wherever they differ (§1):
```text
Private League Objective
    maximise season points
    maximise probability of finishing first
    optimise according to private-league tie/prize rules (§27 decision #7)

National Super 6 Objectives
    maximise round points
    maximise exact-score count
    optimise Golden Goal for round-prize tiebreaks only (§15)
```
Golden Goal does **not** score points on the national game, and does **not** decide the national Season League: the official tiebreak for players tied on season points is (1) most correct exact scores, then (2) split. **This private league confirmed its own, stricter order (§27 decision #7):** points, then most correct exact scores (matching the national rule), then — where the national game would simply split — closest cumulative Golden Goal accuracy across the season. Only a tie surviving all three levels actually splits. §15's Golden Goal model therefore does carry season-long private-league strategic value here, not only round-prize value, and the season simulator (§13) must account for it as a genuine tiebreak level, not an afterthought.

## 2.2 Fixture Status Handling
The prediction and scoring engine must support the following fixture states:
```text
SCHEDULED
ACTIVE
COMPLETED
POSTPONED
ABANDONED
VOID
```
Per the official rules, a fixture that is postponed, abandoned, or not completed with at least 90 minutes played is treated as **VOID**. Void fixtures shall:
* contribute neither points nor prediction-performance statistics when assessing model accuracy (§16-17);
* be excluded from competitor behavioural profiling (§11) for that round;
* still be logged with their status and timestamp for auditability (§19, §22) rather than silently dropped, so a void fixture is distinguishable from a missing data point.
Without this, a postponed fixture would eventually enter the training/backtesting dataset as a spurious 0-0 or missing result and quietly bias the model.
---
# 3. Overall Architecture
```text
                 ┌─────────────────────────┐
                 │   Always-On Agent       │
                 │ Research / Orchestration│
                 └────────────┬────────────┘
                              │
              ┌───────────────┼────────────────┐
              │               │                │
              ▼               ▼                ▼
      Football Data      News/Knowledge     Market Data
       Collector           Collector         Collector
              │               │                │
              └───────────────┼────────────────┘
                              ▼
                    ┌──────────────────┐
                    │   Feature Store  │
                    └────────┬─────────┘
                             │
                  ┌──────────┴──────────┐
                  ▼                     ▼
          Match Forecast Model    Opponent Model
                  │                     │
                  └──────────┬──────────┘
                             ▼
                    Strategy Optimiser
                             │
                             ▼
                    Six Predictions
                    + Golden Goal
                    + Confidence
                    + Explanation
```
The LLM operates above the modelling layer.
It may research, interpret, summarise and identify changed circumstances.
It must NOT arbitrarily modify probabilities without recording why.

**Architectural boundary: the agent is completely external to Sky.** It gathers football data from permitted third-party sources (§18), models everything locally, and produces six recommended scores plus Golden Goal for the user to manually enter into Super 6. There is no automated submission, no scraping of Sky's website or private-league pages, and no dependence on undocumented Sky endpoints — Sky's website terms explicitly prohibit automated (and unauthorised manual) data extraction without written permission. This boundary is load-bearing, not incidental: it removes the single biggest legal/ToS risk in the spec and means nothing downstream depends on Sky's site structure staying stable.

**Core model pipeline:**
```text
xG / Elo / Market → λH, λA → P(score) → Super6 EV → Season Simulation → P(1st)
```
§7 covers the left-hand side conceptually (λH/λA → P(score)), §2 covers P(score) → Super6 EV, and §13 covers EV → Season Simulation → P(1st) architecturally. The mathematics behind λH/λA → P(score) itself — team strength, the goal-rate model, Dixon-Coles, and calibration — is fully specified in §29-32.
---
# 4. Data Model
Every fixture shall have a continuously updated feature record.
## 4.1 Long-Term Team Strength
Include:
| Feature                          |        Importance |
| -------------------------------- | ----------------: |
| Elo/team-strength rating         |         Very high |
| Rolling xG for                   |         Very high |
| Rolling xG against               |         Very high |
| Goals scored/conceded            |              High |
| Home/away strength               |              High |
| Strength of opposition faced     |              High |
| Expected points                  |            Medium |
| Shot quality                     |              High |
| Shots conceded                   |            Medium |
| Set-piece performance            |            Medium |
| Finishing over/under-performance |            Medium |
| Goalkeeper performance vs xG     |            Medium |
| Previous-season strength         | High early season |
Rolling statistics should preferably use several windows:
* previous 5 matches;
* previous 10 matches;
* previous 20 matches;
* current season;
* previous season.
More recent games receive greater weighting.

Long-term features are **capability-aware**, not assumed always-present — Phase 1 has no confirmed free xG source (§18, §25):
```text
xg_for                 nullable
xg_against              nullable
xg_source                nullable
feature_quality_score
```
No model may hard-require xG (§7); its absence must degrade a forecast's quality score, not block it.
---
# 5. Match Context Features
The system shall understand that Manchester City playing Liverpool after six days' rest is not statistically identical to Manchester City playing Liverpool three days after a European away game.
Capture:
| Feature                  |       Importance |
| ------------------------ | ---------------: |
| Home advantage           |        Very high |
| Rest days                |           Medium |
| Fixture congestion       |           Medium |
| European match proximity |           Medium |
| Travel                   |       Low/medium |
| Competition importance   |           Medium |
| League position          |              Low |
| Relegation/title context |       Low/medium |
| Manager change           | High temporarily |
| Tactical formation       |           Medium |
| Expected starting XI     |             High |
| Confirmed starting XI    |        Very high |
| Key player absence       |        Very high |
| Goalkeeper absence       |             High |
| Striker absence          |             High |
| Suspensions              |             High |
| Weather                  |       Low/medium |
| Referee                  |              Low |
| Derby/rivalry            |              Low |
| Historical H2H           |              Low |
Traditional head-to-head records should receive deliberately low weighting.
A match played five years ago involving different managers and twenty different players is primarily historical trivia wearing football boots.
---
# 6. Market Intelligence
Betting markets shall be treated as another forecasting model.
Capture where available:
* home/draw/away odds;
* Asian handicap;
* over/under goals;
* both teams to score;
* correct-score markets;
* opening odds;
* current odds;
* closing odds;
* bookmaker consensus;
* sharp-book prices where available.
Convert bookmaker prices into margin-adjusted implied probabilities.
Store the movement over time.
Example:
```text
Liverpool:
Opening win probability: 61%
24h before:              63%
2h before:               67%
Model forecast:          64%
```
A large unexplained market move should trigger agent investigation.
The agent should ask:
**What changed?**
Possible causes include team news, injury, weather, tactical information or simply market movement.
It should never blindly copy the market.
---
# 7. Match Forecasting Engine
The forecasting engine shall generate a complete probability distribution:
```text
0-0   5.8%
1-0  11.2%
2-0   9.7%
2-1  10.4%
1-1  12.8%
0-1   7.1%
1-2   6.4%
...
```
Recommended initial model family:
### Model A: Goal Model
Dixon-Coles / adjusted bivariate Poisson.
Outputs:
* expected home goals;
* expected away goals;
* complete score distribution.
### Model B: Team Strength Model
Uses:
* Elo;
* xG;
* xGA;
* form;
* opposition strength;
* home advantage.
### Model C: ML Model
Gradient-boosting model such as LightGBM/XGBoost trained using historical fixtures.
Outputs:
* home/draw/away probabilities;
* expected goals.
### Model D: Market Model
Uses margin-adjusted betting probabilities.
### Ensemble
Initial conceptual weighting:
```text
Statistical goal model       30%
xG/team-strength model       25%
Betting market               30%
ML contextual model          15%
```
Weights must eventually be learned through backtesting rather than permanently hard-coded.

Each model declares its actual feature requirements, so a missing feature (e.g. xG, §4.1) degrades a forecast gracefully rather than blocking it:
```text
Dixon-Coles Basic
Requires: goals, results

Enhanced Goal Model
Optional: xG, shots, shots on target, market totals
```
Phase 1 ships on Dixon-Coles Basic (§23); the Enhanced Goal Model activates automatically once xG becomes available, with no architecture change required.

**Consolidation note:** Models A-D above are the original conceptual sketch and overlapped — the Team Strength Model's Elo/xG/form features aren't a separate forecasting model from Dixon-Coles, they're how λHome/λAway are derived. §29-32 supersede this with a single consolidated pipeline (team strength → goal-rate model → Dixon-Coles matrix → market/ML calibration → final P(score)) rather than four semi-independent models. Models A-D remain here as the historical starting sketch; §29-32 are the actual specification.
---
# 8. Knowledge Agent
The research agent operates continuously around upcoming fixtures.
It searches reliable sources for:
* injuries;
* suspensions;
* manager interviews;
* predicted lineups;
* confirmed lineups;
* tactical changes;
* transfers;
* training-ground reports;
* weather;
* fixture changes;
* unusual circumstances.
Every discovered fact must contain:
```text
source
timestamp
fixture
team
player if applicable
event type
reliability
predicted impact
expiry
```
Example:
```text
Event:
Starting striker ruled out.
Reliability:
Official club statement.
Impact:
High.
Model action:
Reduce attacking strength.
Previous λ:
1.82
New λ:
1.62
```
The adjustment is therefore explainable and reproducible.
---
# 9. Real-Time Reforecasting
The agent shall progressively increase monitoring frequency as the prediction deadline approaches.
Suggested cadence:
| Time before deadline |             Refresh |
| -------------------- | ------------------: |
| 7+ days              |               Daily |
| 72 hours             |       Every 6 hours |
| 24 hours             |     Every 2-3 hours |
| 6 hours              |              Hourly |
| 90 minutes           | Every 10-15 minutes |
| Confirmed lineups    |           Immediate |
| Major breaking news  |           Immediate |
Recalculation shall occur only if source data changes.

## 9.1 Round Configuration (Manual Entry, Phase 1)
The cadence above needs two things Sky doesn't hand the system automatically: which six fixtures make up the round, and the actual closing time. Sky selects each round's six fixtures at its own discretion, and its own channels have shown inconsistent deadline times for the same round (§1). Round configuration is therefore **manual input, not derived data**:
```text
super6_round
    round_number
    deadline_at
    fixtures[6]
    source
    entered_at
```
```text
Super 6 round configuration source = MANUAL
```
The user enters the six selected fixtures and the displayed deadline once, when Sky publishes the round. From that point on the agent operates entirely from permitted football-data sources (§18) — consistent with the external-to-Sky boundary (§3), and requiring no scraping to discover Sky's fixture selection.
---
# 10. Prediction Stability
Every prediction must have:
* current recommendation;
* previous recommendation;
* confidence;
* time changed;
* reason changed;
* probability difference.
Example:
```text
ARSENAL 2-0 EVERTON
Confidence: MEDIUM
Previous:
2-1
Changed:
13:07 Saturday
Reason:
Everton's starting striker absent and Arsenal defensive
line strengthened after confirmed teams.
P(2-0)
Previous: 12.1%
Current: 14.8%
Expected Super 6 points:
Current selection: 2.17
```
Confidence is always `HIGH | MEDIUM | LOW` here, never a raw number or percentage — see §20 for the single authoritative CONF definition, which this and §21 must match exactly.
This prevents the agent continually changing predictions because one newspaper journalist has discovered the CAPS LOCK key.
---
# 11. Opponent Intelligence
This is the part that turns the project from a football predictor into a **private-league winning system**.
For every competitor store every historical prediction.
Example schema:
```text
season
round
player
fixture
home_team
away_team
pred_home
pred_away
actual_home
actual_away
points
league_position_before
league_position_after
```
**Data acquisition — decided.** No Sky scraping, ever: Sky's website terms explicitly prohibit automated and unauthorised manual data extraction (§3). Opponent history is instead accumulated from sources the system is actually permitted to use:
```text
competitor_prediction_source
    MANUAL_ENTRY          - user types in a mate's picks after deadline
    SHARED_MESSAGE          - imported from a WhatsApp/group-chat screenshot or text dump
    CSV_IMPORT               - bulk import if a mate exports/shares their own history
    USER_OWNED_HISTORY      - the user's own past predictions, always fair game
    PERMITTED_API             - only if a documented, ToS-compliant source ever exists
```
Explicitly excluded, permanently:
```text
SKY_SCRAPE
SKY_PRIVATE_API
BROWSER_AUTOMATION
```
**This does not block Phase 3.** Opponent modelling starts with zero history at Round 1 and accumulates as the season runs — after 10 rounds that's 60 predictions per mate, after 20 rounds 120, already enough to detect real tendencies:
```text
Dave:
Home-win rate        63%
Draw prediction       14%
2-1 frequency          21%
Favourite bias        +11%
Average goals         2.86

Steve:
1-1 frequency         suspiciously enormous
```
The system should progressively turn the opponent model on as confidence grows, rather than waiting for a complete dataset before doing anything. If mates share predictions in the group chat after each deadline, those are simply imported via `SHARED_MESSAGE`.

**Small-sample guardrail:** a private league is a thin sample for behavioural profiling (draw frequency, chase/defend tendencies, etc.) — the exact competitor count and number of rounds shouldn't be assumed any more than Sky's future fixture selection should (§13.2). Rather than a hard cutoff, competitor-tendency estimates use continuous Bayesian shrinkage toward the league-wide average, with an **opponent influence weight** that grows smoothly with evidence instead of switching on at a threshold:
```text
Opponent influence weight = function(
    sample_size,
    posterior_uncertainty,
    predictive_validation
)
```
```text
Very low history  → almost entirely league-average prior
Growing history   → progressively individualised
Strong evidence   → competitor-specific model dominates
```
A round-14-to-round-15 cliff (0% influence to 100% influence) would waste the shrinkage this section already specifies — the whole point of a Bayesian prior is that it degrades gracefully, not that it gates a switch.

The system shall learn individual behaviours.
For each mate calculate:
### Prediction tendencies
* home-win bias;
* favourite bias;
* draw frequency;
* upset frequency;
* average predicted goals;
* 1-0 frequency;
* 1-1 frequency;
* 2-1 frequency;
* 2-0 frequency;
* 3+ goal frequency.
### Team biases
Detect whether someone consistently:
* overestimates their own team;
* underestimates a rival;
* favours large clubs;
* underrates promoted clubs.
### Behavioural characteristics
Estimate:
* conservatism;
* volatility;
* copying of consensus;
* reaction to previous results;
* tendency to chase when behind;
* tendency to defend when ahead.
The system should eventually predict:
```text
P(Dave predicts Arsenal 2-0) = 31%
P(Dave predicts Arsenal 2-1) = 26%
P(Dave predicts Arsenal 1-0) = 14%
...
```
That becomes strategically useful.
---
# 12. League Strategy Engine
The system operates by running a season Monte Carlo simulation (§13) and choosing the prediction set that maximises simulated **P(Finish 1st)**, subject to the risk constraints below. NORMAL, DEFEND and ATTACK are **explanatory labels attached to the optimiser's output**, not hard-coded IF/THEN rules with fixed thresholds — there is no arbitrary "gap > 10 AND rounds < 8" switch anywhere in the system. The simulation naturally produces behaviour that looks like these three modes without any of them being programmed as separate branches.
## NORMAL MODE (label)
Applied when the simulation-optimal prediction set is the same as (or negligibly different from) the maximum-expected-points set — typically early season, when opponent-outcome uncertainty dominates and standings carry little information.
Optimise:
**Maximum expected Super 6 points**, which the simulation confirms is already ≈ the P(Finish 1st)-optimal choice at this point in the season.
---
## DEFEND MODE (label)
Applied when the simulation shows that, given a lead, correlating selections with the field increases P(Finish 1st) more than maximising raw EV does — because it reduces the leader's own downside variance relative to the chasing pack.
Objective:
**Reduce competitor opportunity to gain points**, as an output of the simulation, not a manually set "lead ≥ X points" rule.
---
## ATTACK MODE (label)
Applied when the simulation shows that a lower-EV, higher-variance selection increases P(Finish 1st) more than the top-EV selection does, because the user is behind and needs above-average outcomes, not average ones.
Identify fixtures where:
* several scorelines have similar expected value;
* competitors are likely to choose the obvious score;
* an alternative score has meaningful probability;
* the simulation shows the differential's P(Finish 1st) gain exceeds its EV cost.

Worked example — 8 points behind the leader ("Dave") with 6 rounds remaining, running 100,000 simulated season completions using our score distributions (§7), Dave's predicted-selection model (§11), and the remaining fixture list:
```text
Baseline (NORMAL strategy):
P(win league) = 24.7%

Try differential: 2-0 → 3-0
Expected-points loss = 0.07
P(win league) = 27.9%     -> TAKE (gain outweighs cost)

Try differential: 1-1 → 2-1
Expected-points loss = 0.31
P(win league) = 25.1%     -> DON'T (gain too small for the cost)
```
Formally: **PredictionSet\* = argmax P(Finish First)**, subject to the risk constraints below — not `argmax E[points]` in isolation (§1).

This behaviour shall NEVER be used simply to be different — every differential must show a simulated P(Finish 1st) improvement, logged alongside its EV cost (§20).
Risk constraints (still explicit config, unlike the mode boundaries themselves): a differential pick may sacrifice at most **15% of the model's expected points** for that fixture relative to the top-EV pick, and at most **one differential pick per round** in ATTACK-labelled rounds (two only when the simulation shows a P(Finish 1st) gain large enough to justify it). These caps bound the search space the simulator explores — they do not replace the simulation with a rule — and must themselves be validated in backtesting (§16) before being trusted live.

## 12.1 National Game Side Objectives (100 Club)
The 2026/27 rules include the **100 Club**: if 100 or fewer entrants nationally correctly predict a given exact scoreline, prizes become available, with larger prizes for rarer correct picks. This is a real incentive, but it must **not contaminate the private-league utility function** (§13) — it optimises against millions of unknown national players, not the known mates in the private league, and is a fundamentally different, lower-confidence bet.
The system shall surface 100 Club opportunities as a **separate, clearly labelled annotation** alongside the primary recommendation, never as a silent substitution:
```text
Primary recommendation:
2-1 Liverpool
Private-league utility: 0.83

National-game alternative:
3-1 Liverpool
100 Club opportunity: elevated
Private-league utility: 0.75
```
The private-league utility score always drives the actual submitted prediction (§20). The 100 Club annotation is informational only, to let the user knowingly trade a small amount of private-league EV for a national-prize shot if they choose to — the agent must never make that trade automatically.
---
# 13. Strategic Optimisation (Season Monte Carlo Simulation)
The strategic decision layer is a **season Monte Carlo simulator**, not a hand-weighted formula. For each candidate round-level prediction set, the system:
1. draws many (e.g. 100,000) simulated completions of the remaining season, using our score distributions (§7), the opponent prediction model (§11) for known competitors, and the remaining fixture list — known rounds forecast exactly, unknown rounds via the Round Opportunity Model (§13.2);
2. scores each simulated season under the league's actual points/tiebreak rules (§2.1, §27 decision #7);
3. estimates **P(Finish 1st)** for the candidate prediction set as the fraction of simulations where the user finishes top;
4. selects the prediction set that maximises P(Finish 1st), subject to the risk constraints in §12.

This replaces an earlier fixed-weight formulation that is worth keeping only as a **mental model** of what the simulation captures, not as something actually implemented:
```text
Utility ≈
    ExpectedSuper6Points
  + LeaguePositionAdjustment
  + DifferentiationValue
  - ExcessRiskPenalty
```
None of those four terms are hand-tuned weights in the implementation — they are consequences of the simulation, recomputed every round from the actual season state (§13.1) and opponent models (§11).

The early/late season shift in behaviour is emergent, not scheduled:
```text
Early season:
Opponent-outcome uncertainty dominates -> simulation-optimal ≈ maximum expected points

Final rounds while behind:
Simulation rewards controlled, evidence-backed differentiation

Final rounds while leading:
Simulation rewards defensive correlation with the field
```
No arbitrary week-8 (or any other) switch is hard-coded; the simulation is re-run every round and the labelled mode (§12) simply reports what it found.

## 13.1 Season State
The season simulator (§13) and the mode labels it produces (§12) must be computed from a season state record that goes beyond points/position/gap, because the Season League tiebreak (§2.1) is decided by exact-score count, not points alone. The season state shall therefore hold:
```text
points
exact_scores
correct_results
position

leader_points
leader_exact_scores

points_gap
exact_score_gap

rounds_remaining
```
Tracking `exact_score_gap` alongside `points_gap` matters for two reasons: it lets the strategy engine detect when the user is tied or close on points but behind on exact scores (i.e. exposed on the actual tiebreak), and it gives a second, independent read on whether the system is "genuinely performing well" (§16) — a user can be gaining points while quietly falling behind on the metric that decides a tied finish.

## 13.2 Known vs Unknown Future Rounds
The simulator's step 1 (§13) said it uses "the remaining fixture list" — but Sky selects each round's six fixtures at its own discretion (§9.1) and doesn't publish future rounds in advance. The simulator must therefore distinguish two kinds of future round:
```text
KNOWN ROUND
Actual six Super 6 fixtures available
→ use proper match distributions (§7)

UNKNOWN FUTURE ROUND
Super 6 selection not yet published
→ simulate score opportunities statistically
```
For unknown rounds, a **Round Opportunity Model** substitutes for exact fixture forecasts, built from historical Super 6-like rounds:
```text
expected points distribution
exact-score distribution
result-hit distribution
competitor relative-performance distribution
```
The season simulation is therefore layered:
```text
Current known round          -> exact fixture models (§7)
Any announced future rounds  -> exact fixture models (§7)
Unknown future rounds        -> Round Opportunity Model (empirical/generated)
```
As each actual Super 6 round is entered (§9.1), its generic simulation is replaced with real fixture-level forecasts and season uncertainty collapses accordingly. This is meaningfully more defensible than assuming today's simulation knows what Sky will select in April.

**What "historical Super 6-like rounds" means:** since Sky's own historical round selections are explicitly not scraped (§3, §18.1), the Round Opportunity Model can't be built from real historical Super 6 rounds directly. It instead uses a **proxy-generation process**:
```text
Historical fixture pool
        ↓
Sample six-fixture synthetic rounds
        ↓
Match observed/current Super 6 characteristics where known:
    league mix
    favourite strength
    home/away balance
    fixture competitiveness
    expected goals distribution
        ↓
Round Opportunity Distribution
```
Initially this draws representative sets of six Premier League/Championship fixtures from historical matchdays. Every manually entered actual Super 6 round (§9.1) during 2026/27 is then a genuine observation that the proxy model can calibrate against, so it gradually becomes Super-6-specific rather than a generic football proxy forever. The synthetic rounds this samples from depend on the team-strength model in §29-31 existing first — see §35.

## 13.3 Candidate Generation & Common Random Numbers
Six fixtures × many plausible scorelines makes exhaustive portfolio search intractable without pruning first. Candidate generation, per fixture:
```text
1. Calculate EV for every plausible scoreline (§2).
2. Retain:
   - top N by EV;
   - top exact-score probability;
   - strategically relevant competitor pick (§11).
3. Reject scores outside configured EV/risk bounds (§12).

Typical candidate count: 2-4 scorelines per fixture.
```
With 3 candidates per fixture across 6 fixtures, that's `3^6 = 729` candidate rounds to portfolio-optimise over — fully manageable, and a deliberate narrowing rather than every mathematically plausible scoreline.

**Common random numbers, generated once.** When comparing candidate rounds under the simulator, every candidate must be evaluated against the **same** batch of simulated futures (the same 100,000 draws of football/competitor outcomes), never independently redrawn batches. Without this, Monte Carlo sampling noise alone can masquerade as a strategic finding:
```text
2-0: P(first) 27.81%
3-0: P(first) 27.87%
```
That six-basis-point gap is far more likely to be simulator noise than signal.

This also has an implementation consequence worth stating explicitly, given worst-case candidate counts of `4^6 = 4096` portfolios (§12's 2-4-per-fixture pruning): naively re-simulating 100,000 full seasons *per candidate* is wasteful and unnecessary. Instead, the stochastic future is generated **once per optimisation run** and cached, and each candidate only substitutes its own current-round contribution into that shared future:
```text
100,000 future-season worlds
        ↓
future opponent scores
future our scores
future tiebreak outcomes
        ↓
CACHE

FinalSeasonScore(candidate)
    =
CurrentStandings
+ CurrentRound(candidate)
+ PrecomputedFutureWorld[i]
```
This makes candidate evaluation mostly matrix arithmetic against the cached future, and gives common random numbers *for free* rather than as a special case to remember.

**Significance, not just a larger number.** Because candidates share the same simulated futures, their P(Finish 1st) estimates are paired, not independent — so the two shouldn't be compared via separate standard errors. Instead compute the per-simulation difference against the baseline:
```text
D_i = Win_i(candidate) - Win_i(baseline)
ΔP(Finish 1st) = mean(D)
```
and its confidence interval directly from that paired-difference distribution. A strategic change (§12) is accepted only when:
```text
lower confidence bound of ΔP(Finish 1st) > 0
AND ΔP(Finish 1st) >= configurable_minimum_gain
```
The lower-bound test replaces the earlier, looser "P(Finish 1st) improvement > simulation uncertainty threshold" — that phrasing under-specified how the threshold was derived; the paired lower-confidence-bound is the actual mechanism. `configurable_minimum_gain` exists so a technically-significant but practically-meaningless +0.01% doesn't trigger a differential pick; its value is set via backtesting (§16). Both figures must be logged alongside every strategic recommendation (§20).
---
# 14. Whole-Round Optimisation
The agent must optimise the six matches **as a portfolio**, not six independent decisions.
Suppose the model wants:
```text
2-0
2-1
2-0
2-1
1-0
2-0
```
These might individually be perfectly defensible.
But if competitors will make almost identical selections, a chasing strategy may alter one or two high-uncertainty fixtures while leaving the strongest predictions untouched.
There must therefore be:
**Fixture-level optimisation**
followed by:
**Round-level optimisation.**
---
# 15. Golden Goal Model
Golden Goal should have its own model.
For each fixture estimate first-goal hazard using:
* predicted total goals;
* historical first-goal timing;
* attacking intensity;
* defensive weakness;
* team early-goal tendencies;
* starting lineups.
Combine the six distributions to calculate:
**Probability that the first goal anywhere occurs in minute N.**
Output:
```text
Most probable Golden Goal: 11 minutes
Probability intervals:
1-5 mins     19%
6-10 mins    24%
11-15 mins   26%
16-20 mins   15%
21+ mins     16%
```
The recommendation should optimise expected tiebreak distance rather than merely selecting the modal minute.

**Verified rule (Sky Sports Super 6, Aug 2026):** Sky defines 0:00–0:59 as the 1st minute, 1:00–1:59 as the 2nd, and so on — so a Golden Goal guess of minute *N* is scored as the band (N−1):00–(N−1):59 (e.g. guessing "6" wins if the first goal falls between 5:00 and 5:59, since minute 6 is the 6th one-minute interval starting at 5:00). This is one off from the naive N:00–N:59 reading, and the tiebreaker is closest-minute distance from that band to the actual first-goal minute, with ties splitting the prize equally. The model must therefore optimise over discrete one-minute bands, not a continuous minute value — select the band that minimises expected absolute distance to the true first-goal minute under the modelled hazard distribution, not the highest-probability band. On the national game Golden Goal only decides ties/jackpots for round-level prizes; this private league additionally uses it as a season-long tiebreak among competitors, confirmed as §27 decision #7 — the same closest-minute-distance convention, applied cumulatively (mean distance) rather than per round.
---
# 16. Historical Backtesting
Before trusting the model, run historical Super-6-style simulations.
Testing must be walk-forward:
```text
Train: seasons <= N
Predict: season N+1
```
Future information must never leak into historical predictions.
Critical metrics:
| Metric                  | Purpose                 |
| ----------------------- | ----------------------- |
| Exact score accuracy    | Super 6 performance     |
| Result accuracy         | Base performance        |
| Points per fixture      | Primary game metric     |
| Points per round        | Primary game metric     |
| Brier score             | Probability calibration |
| Log loss                | Distribution quality    |
| Calibration error       | Confidence reliability  |
| Golden Goal error       | Tiebreak quality        |
| League simulation win % | Ultimate objective      |
Baselines must include:
1. Always choose bookmaker-derived favourite.
2. Pure Poisson model.
3. Market-only model.
4. Statistical model only.
5. Full ensemble.
6. Agent-enhanced ensemble.
If our expensive AI contraption cannot beat a spreadsheet, the spreadsheet wins. It has suffered enough disrespect already.
---
# 17. Learning Loop
After every round:
```text
Predictions
     ↓
Results
     ↓
Score models
     ↓
Analyse errors
     ↓
Update calibration
     ↓
Update team ratings
     ↓
Update opponent profiles
     ↓
Prepare next round
```
The agent should classify errors.
Examples:
```text
Prediction error:
Underlying model wrong.
Prediction error:
Unexpected red card.
Prediction error:
Correct result distribution but wrong exact score.
Prediction error:
Late team news insufficiently weighted.
Prediction error:
Strategic differential unsuccessful but rational.
Prediction success:
Correct selection for wrong reasons.
```
Critically, the system must not learn bad lessons from randomness.
---
# 18. Data Sources
A practical data hierarchy should be:
### Structured Football Data
Primary API covering:
* fixtures;
* historical results;
* lineups;
* injuries;
* player statistics;
* team statistics;
* xG;
* odds;
* match events.
Sportmonks is currently a strong candidate because its Football API exposes fixtures, historical results, lineups, live events, xG, predictions, odds, coach/referee statistics and expected lineups through the same service. Its terms permit the API data to be used in an application and stored/transferred, but prohibit resale of the underlying feed — treat it as a licensed input, not a redistributable dataset.
### Historical Results / Odds
Football-Data.co.uk provides free downloadable historical result and betting-odds datasets (results back decades, odds back to roughly 2000/01) and is the primary source for the initial training and backtesting corpus (§16).
### Market Data
Either use the football provider's integrated odds or a dedicated odds service for more detailed bookmaker movement. football-data.org's free registered tier (10 requests/minute) is more than sufficient for six fixtures/round if responses are cached rather than re-fetched on every reforecast (§9).
### News
Use web retrieval from sources ranked approximately:
1. Official club/team
2. Trusted football news services
3. Established journalists
4. Other media
5. Social media
Rumours shall never receive the same weight as confirmed information.

## 18.1 Source Acquisition Policy
This formalises what was previously an open question (§27): every data source, without exception — including Sky Sports Super 6 itself — is either licensed/permitted or off-limits, never "scraped because it seemed accessible." Sky's website terms explicitly prohibit automated and unauthorised manual data extraction without written permission (§3); this policy exists so that restriction is never quietly violated by a worker script six months from now.

Preferred order:
```text
1. Licensed structured API       (e.g. Sportmonks, once promoted per §25)
2. Official public API           (e.g. football-data.org)
3. RSS / syndicated feed
4. Permitted public dataset      (e.g. Football-Data.co.uk)
5. Search/news retrieval         (§8, ranked per source above)
6. Manual/user-supplied data     (§11's competitor_prediction_source)
```
Never: an arbitrary HTML scraper against a site whose terms haven't been checked — including, explicitly, Sky Sports Super 6's own pages.

Every source must be registered with:
```text
source_id
source_name
acquisition_method:
    API
    RSS
    DATASET
    SEARCH
    MANUAL
usage_status:
    APPROVED
    REVIEW_REQUIRED
    PROHIBITED
refresh_limit
retention_policy
attribution_required
terms_checked_at
terms_url
```
A source with `usage_status: REVIEW_REQUIRED` shall not be queried by any worker (§26) until it is explicitly promoted to `APPROVED`. This record is persisted in `source_registry` (§19).
---
# 19. Persistence
Recommended logical stores:
```text
fixtures
teams
players
team_strength
match_features
odds_snapshots
news_events
model_forecasts
prediction_versions
super6_rounds
super6_predictions
super6_results
league_players
league_standings
competitor_predictions
competitor_profiles
model_performance
agent_decisions
source_registry
forecast_runs
feature_snapshots
model_versions
model_outputs
strategy_runs
source_events
```
Every forecast must be reconstructable later — this requires preserving the **inputs**, not just the output prediction. Concretely:
```text
forecast_run_4812
    │
    ├── model_version: dc_0.3.2
    ├── feature_snapshot: 91982
    ├── odds_snapshot: 22145
    ├── news_snapshot: 7712
    ├── opponent_snapshot: 188
    │
    ├── raw_prediction
    │       2-0
    │
    └── strategy_prediction
            2-1
```
This is what makes "why on earth did we predict Wolves 3-0 on 17 January?" an answerable question rather than an AI archaeology exercise: every `forecast_run` links to the exact snapshot of every upstream input, the model version that consumed it, and both the raw and strategy-adjusted output.
---
# 20. Agent Output
The normal pre-round report shall look like:
```text
SUPER 6 - ROUND 14
                     PICK    RESULT   SCORE    EV     CONF
ARS v EVE            2-0     68.3%    13.7%   1.78   HIGH
CHE v NEW            2-1     61.8%    11.9%   1.59   MED
FUL v BRE            1-1     52.4%    12.8%   1.43   LOW
LEE v TOT            1-2     58.1%    12.1%   1.53   MED
MCI v BOU            3-0     71.2%    15.4%   1.88   HIGH
WHU v AVL            1-2     55.9%    11.2%   1.47   MED
Golden Goal: minute 9 (Opta interval 8:00-8:59)
Expected round score: 9.68
Historical average: 8.4
League position: 2nd
Points gap to leader: -7
Exact-score gap to leader: -2
Rounds remaining: 11
Strategy: NORMAL
```
Column definitions — a 2-0 pick does **not** have a 74% probability, and the dashboard must never imply that it does:
* **RESULT** = P(selected outcome) — e.g. P(home win) for a home-win pick.
* **SCORE** = P(exact selected scoreline).
* **EV** = expected Super 6 points for the pick = `2 × RESULT + 3 × SCORE` (§2).
* **CONF** = a calibrated category — `HIGH | MEDIUM | LOW`, never a raw percentage or a /100 score — and this is the single authoritative definition; §10 and §21 must display it exactly this way. Internally derived from five factors persisted alongside the forecast (§19): `model_agreement`, `distribution_entropy`, `calibration_quality`, `market_divergence`, `feature_completeness`. These five factors are what §16 calibration-tests, not a single eyeballed number. Probability (RESULT/SCORE) and confidence (CONF) remain separate concepts and must never be conflated in output.
Where strategic optimisation alters the raw model recommendation:
```text
FULHAM v BRENTFORD
Model:
1-1
Recommended:
2-1
Reason:
Model probabilities are unusually flat.
1-1 probability: 12.8%
2-1 probability: 11.9%
Expected-point reduction: 0.08
Estimated competitor selection:
1-1: 43%
2-1: 13%
Strategic value:
HIGH
Recommendation:
Take controlled differential.
```
This should be rare enough that when the agent does it we understand precisely why.
---
# 21. Alerts
The agent should notify only when something material changes.
Examples:
```text
🚨 PREDICTION CHANGE
Chelsea v Newcastle
2-0 → 1-0
Reason:
Confirmed Chelsea lineup missing two expected attacking starters.
Confidence:
MEDIUM → HIGH
Exact-score probability:
11.2% → 14.1%
```
or:
```text
⚠️ REVIEW REQUIRED
Market probability for Newcastle win moved:
22% → 29%
No confirmed news detected.
Agent investigating.
```
Noise should be aggressively suppressed.
---
# 22. Guardrails
The system shall:
* timestamp every data point;
* record provenance;
* distinguish rumours from confirmed information;
* prevent future-data leakage in backtesting;
* preserve every forecast version;
* record manual overrides;
* measure forecast calibration;
* never alter predictions without an explainable trigger;
* treat competitor modelling separately from football modelling;
* avoid blindly trusting either an LLM or bookmaker odds;
* never scrape, automate, or otherwise programmatically interact with Sky Sports Super 6 itself — the agent is completely external to Sky (§3, §18.1);
* query only sources registered as `usage_status: APPROVED` in the source registry (§18.1, §19).
For the initial version the system should **recommend predictions rather than automatically submit them to Super 6**.
---
# 23. Delivery Phases
## Phase 1 - Prediction Engine
### Phase 1A (no xG dependency)
Build:
```text
Fixtures
Historical results
Odds
Team strength (goals, Elo)
Shots / shots on target where available
Poisson/Dixon-Coles Basic (§7)
Score probabilities
Expected Super 6 points
Six recommendations
Golden Goal
Round configuration (§9.1, manual entry)
```
This gets us a functioning quantitative predictor without spending a penny beyond the free tier (§25) — xG is deliberately deferred so it never blocks Phase 1.
### Phase 1B (xG uplift)
Add xG/xGA (§4.1) once a permitted source is available at an acceptable price (§18.1, §25's manual-approval gate). The Enhanced Goal Model (§7) simply starts consuming the previously-nullable xG fields — no architecture change required.
## Phase 2 - Always-On Intelligence
Add:
```text
News retrieval
Injuries
Expected teams
Confirmed teams
Market movement
Reforecasting
Change alerts
Agent explanations
```
This gives us the deadline-day advantage.
## Phase 3 - Beat the Mates
Accumulate opponent history from Round 1 onward (§11 — no import blocker) while building:
```text
Competitor histories
Prediction bias models
Expected competitor selections
Season Monte Carlo simulator (§13)
Attack/defend/normal mode labels (§12)
Whole-round optimisation
```
This is where the system stops merely being good at football forecasting and starts trying specifically to win the private league.
## Phase 4 - Self Improvement
Add:
```text
Walk-forward retraining
Model weighting
Calibration
Deeper opponent simulations (second-order effects, e.g. how mates react to each other)
Automated post-round review
Simulator/strategy calibration and tuning
```
---
# 24. Definition of Success
The system is successful only if retrospective and prospective testing demonstrates that it beats:
* naive favourite selection;
* bookmaker-only probabilities;
* simple Poisson;
* previous human prediction performance.
Primary KPI:
**Average Super 6 points per round.**
Ultimate KPI:
**Probability of finishing first in the private league.**
---
# 25. Cost & Operational Budget
An always-on agent with LLM-driven research and up-to-15-minute reforecasting (§9) has real recurring cost against a 6-fixture/week hobby league. Two thresholds, not one:
```text
Target operating cost: £10/month
Hard monthly ceiling:  £40/month
```
* **Paid-data promotion requires manual approval.** Moving any source from `REVIEW_REQUIRED`/free-tier to a paid plan (§18.1) is never automatic — the user explicitly approves it. This matters because Sportmonks currently advertises football plans starting at €29/month before any other costs, which alone would breach the £10 target and approach the £40 ceiling.
* **Cheap-tier default for structured data:** Phase 1 deliberately targets what's achievable on Football-Data.co.uk (historical results/odds), football-data.org's free tier (10 requests/minute — far more than six fixtures/round needs if cached), free/open datasets, and low-cost LLM calls, before any paid promotion is even considered.
* Reforecasting shall only consume LLM calls when underlying source data has actually changed (§9), and the sub-90-minute cadence should degrade gracefully (e.g. fall back to 30-minute polling) if the budget is approached mid-round.
* **Cost effectiveness must not be measured against raw Super 6 points alone** — a single Saturday's results are too noisy (a good £5 data source can easily score zero points that week because football did what football does). Measure instead:
```text
£ / improvement in Brier score
£ / increase in Expected Super 6 Points
£ / percentage-point increase in simulated P(Finish 1st)
```
These are logged per round and per season and evaluated against the Definition of Success (§24) — an improvement that costs more than its measurable effect on these metrics is a valid reason to simplify, independent of any single round's actual score.
---
# 26. Recommended Technical Stack
Approved, with one adjustment from the original proposal (no subprocess split, and no plain cron):
```text
bym-agent/
│
├── api/
│   FastAPI
│
├── modelling/
│   Python
│   pandas, numpy, scipy, statsmodels
│   LightGBM
│
├── workers/
│   fixture ingestion
│   market ingestion
│   news intelligence
│   forecast runner
│   season simulator
│
├── database/
│   PostgreSQL
│
├── scheduler/
│   lightweight scheduler, jobs persisted in Postgres
│
└── web/
    Next.js — optional, not needed for Phase 1
```
* **Modelling/backtesting:** Python (pandas, numpy, scipy, statsmodels or a hand-rolled Dixon-Coles implementation, LightGBM for Model C) — the ecosystem best suited to §7 and §16.
* **No subprocess/service split.** The statistical engine lives as ordinary Python modules called directly behind FastAPI — `forecast_fixture()`, `forecast_round()`, `simulate_season()`, `optimise_round()`, `analyse_opponents()` — rather than a separate service the API shells out to. Far easier to unit-test, and there's no operational reason at this scale to pay the complexity of a process boundary.
* **Persistence (§19):** relational. **SQLite is the Phase 1/2 default for single-node deployment; PostgreSQL remains the scale/concurrency migration target.** The store list in §19 is fully relational and reproducibility (§19, §22) benefits from transactional writes over flat files, but Postgres is operational ballast for a single user, ~6 fixtures and ~10 scheduled jobs a week. Application persistence must not depend on SQLite-specific semantics. Four requirements, non-negotiable at either engine: `foreign_keys = ON`, WAL mode, versioned schema migrations, and UTC timestamps everywhere — plus transactions around job claiming and state transitions. The trigger for migrating to PostgreSQL is concrete, not aesthetic: multiple application instances, multiple workers, multi-user access, meaningful concurrent writes, or a hosted HA requirement. "Postgres feels more architectural" is not a trigger.
* **Scheduling (§9):** *not* plain cron. §9's cadence is dynamic (daily → hourly → every 10-15 minutes → immediate-on-event as the deadline approaches), which cron expresses awkwardly. Use a lightweight scheduler with jobs persisted in Postgres instead, so the next-run time for each fixture can be recomputed as its deadline approaches or an event (confirmed lineup, breaking news) fires.
* **Delivery (§20-21):** still unspecified — decide whether the pre-round report and alerts land as a local file, an email, or a chat notification; this determines whether §21's alerts need a push channel.
* **Deployment:** Dockerised; runs comfortably as one FastAPI process, one Postgres instance, one worker, one scheduler, optionally Next.js — locally or on a cheap hosted box, well within the §25 budget.
---
# 27. Decisions Log
All seven previously open questions now have a settled engineering decision. None remain.

## Decisions
| # | Question | Decision |
|---|---|---|
| 1 | Opponent data | No Sky scraping. Accumulate from Round 1 via `MANUAL_ENTRY`/`SHARED_MESSAGE`/`CSV_IMPORT`/`USER_OWNED_HISTORY` (§11). Does not block Phase 3. |
| 2 | Golden Goal | Keep the model (§15), but Priority 3 (§1.1) — a simple first-goal hazard model is sufficient until it's shown to matter more. |
| 3 | Budget | £10/month target, £40/month hard ceiling; paid-data promotion requires manual approval (§25). |
| 4 | Technical stack | Python + FastAPI + Postgres + lightweight persisted scheduler; Next.js optional (§26). In practice the shipped local UI runs on stdlib `http.server` + SQLite/JSON, not FastAPI/Postgres — an implementation choice within this decision, not a reopening of it. |
| 5 | Mode thresholds | Removed. NORMAL/DEFEND/ATTACK are explanatory labels on a season Monte Carlo optimiser's output, not fixed IF/THEN thresholds (§12-13). |
| 6 | Scraping/ToS | Formal Source Acquisition Policy: API/RSS/licensed feeds first, arbitrary scraping never, every source registered with a `usage_status` (§18.1). |
| 7 | Private-league prize/tiebreak rules | **Confirmed with the league (2026-08-09).** Season League ranking: total points, then most correct exact scores (matching the national rule, §2.1), then — as this private league's own extra level, where the national game would simply split — closest cumulative Golden Goal accuracy: mean `\|guess − actual first-goal minute\|` across rounds where both are known, lower wins. Only players tied on both points and exact scores after that still split. Implemented in `league.league_standings` (`golden_goal_actual` parameter, `LeagueStanding.golden_goal_average_distance`) and carried into the season simulator's own tiebreak (§13.1, §13.3). |

## 27.1 Status
**§1-27 = Functional Architecture Baseline — FROZEN (as of v0.3.2), with §2.1's private-league tiebreak confirmed 2026-08-09 per decision #7 above.** Objective, scoring, opponent data policy (with continuous, cliff-free confidence growth — §11), source acquisition policy, cost controls, round configuration, the Round Opportunity Model for unpublished rounds, Monte Carlo significance/efficiency rules, the Football Brain / Opponent Brain / Strategy Brain separation, and the private-league tiebreak are all settled. No further structural additions belong in §1-27.

**§28-36 = Mathematical & Algorithmic Pipeline (v0.4.1)**, merged into this document so architecture and mathematics live as one Vision rather than two cross-referencing files:
```text
historical matches → team attack/defence strength → λHome / λAway → Dixon-Coles
→ P(h, a) → market calibration → EV_Super6 → candidate portfolio → P(Finish 1st)
```
Readiness assessment:
| Layer | Status |
|---|---|
| Functional architecture | 🟢 FROZEN |
| Data/persistence | 🟢 |
| Phase 1A inputs | 🟢 |
| Team strength mathematics (§29) | 🟢 |
| λ model (§30) | 🟢 |
| Dixon-Coles (§31) | 🟢 |
| Super 6 EV (§2, §33) | 🟢 |
| Backtest/ablation design (§28, §16) | 🟢 |
| Market integration (§32) | 🟢 enough to experiment |
| **Ready to start implementation** | **🟢 YES** |
The first model built should still be deliberately boring: historical results → weighted Dixon-Coles → score matrix → Super 6 EV → walk-forward backtest. Then add Elo. Test. Then market. Test. Complexity is earned one statistically significant increment at a time (§28) — nothing here licenses skipping straight to the full pipeline before the baseline is proven.
---
# 28. Design Rule
**Every modelling choice must be testable independently against a simpler baseline.** Nothing in §29-32 is adopted because it sounds right; it earns its place in the pipeline by beating the baseline in walk-forward backtesting (§16), or it gets removed. This is the ablation framework, and it runs before Phase 1 code freezes on any specific implementation:
```text
Poisson                    vs  Dixon-Coles
Dixon-Coles                vs  Dixon-Coles + Elo
DC + Elo                   vs  DC + Elo + market
Market blend                vs  market alone
Discrete rolling windows    vs  single exponential time-decay (§29.2)
Simple margin normalisation vs  Shin's method (§32.1)
Maximum-EV picks             vs  season-strategy picks
```
If adding a component makes predictions worse, the component is removed — no matter how established the idea is elsewhere in football analytics. If a fancy calibration step loses to raw bookmaker probabilities, it goes in the bin.

---
# 29. Team Strength Estimation
Two complementary strength signals feed §30's goal-rate model: goals-based attack/defence parameters (always available, Phase 1A) and an Elo rating (Phase 1A), with xG-based parameters as an optional third signal (Phase 1B, §4.1/§7).

## 29.1 Attack/Defence Parameters (Dixon-Coles / Maher parameterisation)
For each team *i*: attack strength `α_i`, and `β_i` defined as **defensive weakness / concession propensity — lower means a better defence**, plus a home-advantage term `γ`. Naming `β` a *weakness* rather than a *strength* keeps the correct sign intuitive — a strong defence has a low, negative `β`, and it must **add** to the opponent's expected goals in log-space, not subtract:
```text
log(λ_home) = μ + γ + α_home + β_away
log(λ_away) = μ + α_away + β_home
```
Read as: home expected goals rise with the home team's attack (`α_home`) and with the away team's defensive weakness (`β_away`) — never the reverse.
Identifiability constraint: `mean(α) = 0`, `mean(β) = 0` in log-space (equivalently `mean = 1` if parameterised multiplicatively). `α`, `β`, `γ`, `μ` are fit jointly with Dixon-Coles' `ρ` (§31) via weighted MLE (§29.2, §31) over the historical fixture corpus (§18).

## 29.2 Recency Weighting
§4.1 specified multiple discrete rolling windows (5/10/20 matches, current season, previous season) as a placeholder for "more recent games matter more." This formalises it as a single **exponential time-decay**:
```text
w_m(ξ) = exp(-ξ · t_m)
```
`ξ` is a **hyperparameter selected by outer walk-forward validation, not an ordinary MLE parameter fit jointly with μ/γ/α/β/ρ** — conflating the two would let two competent implementations diverge. The actual procedure:
```text
OUTER LOOP - walk-forward validation
    choose candidate ξ
        ↓
INNER FIT
    for fixed ξ:
    fit μ, γ, α[], β[], ρ (§31) by maximising
    Σ_m w_m(ξ) · log P_DC(h_m, a_m)
        ↓
score predictions on held-out period
        ↓
select ξ with best out-of-sample metric (§16)
```
Whether continuous decay actually beats the discrete-window approach is itself an ablation test (§28), not an assumption; if discrete windows win, they stay.

## 29.3 Home Advantage
`γ` is fit league-wide via the same MLE as a starting default. Team-specific home advantage is a later refinement candidate (§28's ablation framework governs whether it's worth the added parameters), not part of the default model.

## 29.4 Promoted/Relegated-Team Priors
A newly-promoted team has no top-flight history, so its first-N-games `α`/`β` estimate is noisy. Treat `α_i`, `β_i` as random effects with an empirical-Bayes prior: `N(prior_mean, prior_variance)`, where `prior_mean`/`prior_variance` are fit from the historical distribution of promoted teams' first-season parameters. Shrinkage toward that prior is proportional to games played in the current division, converging smoothly to the team's own fitted value as evidence accumulates — the same continuous-shrinkage philosophy as the opponent-confidence fix in §11, applied here to team strength instead of competitor behaviour.

## 29.5 Season-Start Shrinkage
At a season boundary, shrink each team's end-of-previous-season `α`/`β` toward the league mean by a season-transition shrinkage factor (config value, backtested) before the new season's fit begins, reflecting squad/manager turnover. This is the concrete mechanism behind §4.1's "previous-season strength: high early season" — it defines exactly how much weight that carries and how fast it decays as current-season data accumulates, again as a smooth blend rather than a cliff.

---
# 30. Goal-Rate Model — Deriving λ_home, λ_away
**This is the load-bearing section.** Once these two numbers are defensible, everything downstream (§31-36) is comparatively mechanical.

**Elo is not blended into `α`/`β`.** No canonical Elo→goals mapping exists (Elo's conventional logistic mapping is to match *outcome*, not goal supremacy), and blending one scalar into two separate parameters would risk double-counting the same team-strength information as both attacking and defensive signal. Elo is instead added as a single **match-level covariate** on top of the §29.1 parameters:
```text
d_Elo = (Elo_home - Elo_away) / 400

log(λ_home) = μ + γ + α_home + β_away + θ_H · d_Elo
log(λ_away) = μ + α_away + β_home - θ_A · d_Elo
```
Start constrained to `θ_H = θ_A = θ`, a single fitted coefficient, unless backtesting demonstrates that separate home/away coefficients earn their extra complexity. This makes the ablation clean and directly testable (§28):
```text
DC:        θ = 0
DC + Elo:  θ fitted from training data
```
No blend weight to hand-tune, no need to project Elo onto the α/β scale first, and no risk of one scalar silently contributing to both attack and defence. If Elo adds nothing once `α`/`β` are present, θ fits near zero and §28's ablation removes it.

**xG uplift (Phase 1B):** when xG-based attack/defence parameters are available (§4.1, nullable), they enter the same way as `α`/`β` — as fitted parameters, not a blended scalar — tested via "DC+Elo vs DC+Elo+xG" (§28) rather than assumed to help.

**Caps/floors:** `λ_home`, `λ_away` are clamped to a plausible range (exact bounds set from the historical goal-distribution during backtesting, not asserted here) to prevent pathological extrapolation from small-sample teams or bad data. The bounds are config, not magic numbers baked into the model code.

**Missing-feature behaviour:** if an optional covariate (Elo, xG, or any future addition) is unavailable for a fixture, its term is simply dropped from the log(λ) sum for that fixture rather than substituted with a zero-information placeholder or a league-average value, which would silently bias `λ` in a specific direction. This is where §4.1/§7's "capability-aware, degrades gracefully" promise actually gets implemented.

---
# 31. Dixon-Coles
Given `λ_home`, `λ_away` (§30), the base joint distribution is independent Poisson:
```text
P(H=h, A=a) = Poisson(h; λ_home) · Poisson(a; λ_away)
```
The Dixon-Coles low-score correction `τ(h,a; ρ)` adjusts the well-documented mismatch between independent Poisson and the empirical frequency of low scores (0-0, 1-0, 0-1, 1-1):
```text
P_DC(h,a) = τ(h,a; ρ) · Poisson(h; λ_home) · Poisson(a; λ_away)
```
`τ` is defined only for `h,a ∈ {0,1}` (standard formulation) and equals 1 elsewhere. `ρ` (and `θ` from §30) are fit jointly with `μ`, `γ`, `α`, `β` for a given `ξ`, per the outer/inner split in §29.2 — `ξ` itself is selected by outer walk-forward validation, not by this joint likelihood.

**Parameter fitting:** for a fixed `ξ` (§29.2's outer loop), maximum likelihood over `μ`, `γ`, `α`, `β`, `ρ`, `θ` jointly, maximising `Σ_m w_m(ξ) · log P_DC(h_m, a_m)`. Optimiser choice and exact identifiability constraints are implementation detail, but the fitted parameter set and its git/data version must be recorded in `model_versions` (§19) for reproducibility — every `forecast_run` traces back to exactly which fit produced it.

**Scoreline truncation and tail handling.** Residual tail probability mass (scores beyond the max-goal grid) must never be folded into the max-goal cell — that would invent exact-score probability on a real, selectable scoreline (e.g. inflating `8-8`'s probability with mass that actually belonged to `9-0`, `10-1`, etc.), and could even flip a result classification from a home/away win into a draw. Since exact-score probability feeds directly into Super 6 EV (§33), that would corrupt the one number the whole system optimises. Correct approach:
```text
max_goals chosen (dynamically or via config) such that:
P(either team scores > max_goals) < tail_tolerance
```
`tail_tolerance` is an explicit, tiny, backtested config value. The omitted mass is then handled one of two ways — **never** assigned to a real selectable scoreline:
* retain an explicit `OVERFLOW` bin for probability accounting (matrix sums to 1 including `OVERFLOW`, but `OVERFLOW` is never itself a candidate scoreline); or
* renormalise the finite grid proportionally, only when the omitted mass is already below `tail_tolerance` and therefore negligible by construction.

**Output:** the full `P(h,a)` matrix, `h,a = 0..max_goals` — this is the direct input to §33 (§2's EV formula) and to every candidate scoreline in §34.

---
# 32. Calibration / Market Blending

## 32.1 Margin Removal
Convert bookmaker decimal odds to raw implied probabilities (`1/odds`), then remove the overround. Method choice — simple proportional normalisation vs Shin's method (which corrects for favourite-longshot bias) — is itself an ablation candidate (§28), not a default assumed to be correct.

## 32.2 Market → Comparable Probabilities
After margin removal, market-implied result probabilities (H/D/A) sit on the same scale as the DC-derived result probabilities (summed from §31's matrix), so they can be directly compared or blended without further transformation.

## 32.3 Where Market Information Enters — Deliberately Undecided
Two candidate architectures, neither pre-selected here:
```text
Option A: market shifts λ
    market-implied result probability -> back-solve an implied λ adjustment
    -> blend into §30's λ derivation before Dixon-Coles runs

Option B: market calibrates final P(score)
    Dixon-Coles produces P(h,a) independently (§31)
    -> blend/calibrate final result & score probabilities against market
       probabilities post-hoc, via result-constrained reweighting (below)
```
**Option B needs a precise mechanism, not just "blend."** A bookmaker's H/D/A prices (e.g. Home 56% / Draw 25% / Away 19%) say nothing about how that 56% splits across `1-0`, `2-0`, `2-1`, `3-0`, `3-1`, etc. — only the result-level probability is informed by the market, not the within-result score distribution. So Option B is defined as **result-constrained reweighting**: blend at the H/D/A level to get market-calibrated result probabilities `q_H, q_D, q_A`, then rescale each scoreline within its result by the ratio of calibrated to original result probability, preserving Dixon-Coles' relative shape within each result:
```text
q(h,a) = p_DC(h,a) × [ q_R / p_DC(R) ]
```
for scoreline `(h,a)` belonging to result `R`. Example: if `p_DC` says home win = 50% and the market-calibrated figure is 55%, every home-win scoreline is scaled by `55/50 = 1.10`, keeping their relative proportions intact. This guarantees `Σ q(h,a) = 1`, so the result is still a coherent full score matrix that can legitimately feed Super 6 EV (§33) — correct-score market data, where available, could later inform the *within-result* rescaling too, but H/D/A prices alone must never be treated as if they contained exact-score information they don't have.

Which option wins — and the blend weight if either survives — is decided by the "DC+Elo vs DC+Elo+market" and "market blend vs market alone" ablations (§28), run via walk-forward validation. The original v0.3.1 conceptual ensemble weights (30/25/30/15, §7) were illustrative placeholders, not a spec; this section replaces "choosing pretty percentages" with weights that have to earn their place against a baseline.

---
# 33. Super 6 Optimisation
Already fully specified — §2:
```text
EV(s) = 2 · P(result_s) + 3 · P(s)
```
No new mathematics here. §31's `P(h,a)` matrix is the direct input; EV is computed for every scoreline in it, exactly as §2 already requires.

---
# 34. Round Candidate Generation
Already fully specified — §13.3: top-N by EV, top exact-score probability, competitor-relevant pick (§11), reject outside configured EV/risk bounds (§12), typically 2-4 candidates/fixture. No new mathematics here.

---
# 35. Season Simulation
Already fully specified — §13, §13.1, §13.2: known vs unknown rounds, cached future worlds, exact-score tiebreaks via the Season State (§13.1), and the proxy-generation process for synthetic rounds (§13.2). The one dependency worth stating explicitly: §13.2's synthetic-round sampling needs the team-strength model (§29-31) to exist before it can generate realistic Round Opportunity Distributions — so validate §29-31 first, then wire up §13.2's sampler against it.

---
# 36. Strategic Comparison
Already fully specified — §13.3: paired `D_i = Win_i(candidate) - Win_i(baseline)`, `ΔP(Finish 1st) = mean(D)`, acceptance requires the lower confidence bound of `ΔP(Finish 1st) > 0` **and** `ΔP(Finish 1st) ≥ configurable_minimum_gain`. No new mathematics here.

## 36.1 Where the Mathematical Work Concentrates
§33-36 are pointers back to §1-27's frozen architecture, included only so this document is a complete pipeline reference — they need no further derivation. The real mathematical content is §29-32: team strength → λ_home/λ_away → Dixon-Coles → calibration. Every design choice in those four sections is gated by §28's ablation framework before it reaches Phase 1 code. Nothing beyond this pipeline belongs here — no new feature, source, or dashboard gets added on the grounds that "while we're here."
