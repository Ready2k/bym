# 37. UI/UX Experience Specification

## v0.5 — Decision Experience Layer

This section defines how a human interacts with the Super 6 Always-On Prediction Agent.

It does **not** alter the functional architecture (§1–27) or mathematical pipeline (§28–36). It defines how those capabilities are presented, understood and controlled.

The primary UX objective is:

> **Make the best six predictions obvious, make changes impossible to miss, and make every recommendation explainable without forcing the user to understand the mathematics behind it.**

The application is a decision-support system, not a statistical workbench.

The normal weekly user should be able to open the application, understand the current position and recommended six scores, inspect anything unusual, and leave within approximately one minute.

Detailed modelling information remains available through progressive disclosure.

---

# 37.1 Experience Principles

## Principle 1 — Recommendation First

The first thing the user sees is:

1. What should I predict?
2. Has anything changed?
3. How confident are we?
4. What do I need to do before the deadline?

Not:

* λ values;
* Elo coefficients;
* Dixon-Coles parameters;
* bookmaker overround;
* Monte Carlo configuration.

Those remain accessible, but never compete with the actual decision.

---

## Principle 2 — Explain, Never Mystify

Every recommendation must answer:

> **Why this score?**

Every changed recommendation must additionally answer:

> **Why did it change?**

Every strategic differential must additionally answer:

> **Why are we deliberately not taking the model's highest-EV score?**

The UI must never present an unexplained AI recommendation.

---

## Principle 3 — Probability Is Not Confidence

The interface must preserve the distinction established in §20:

```text
RESULT
Probability of the selected match outcome.

SCORE
Probability of the exact selected score.

EV
Expected Super 6 points.

CONF
HIGH | MEDIUM | LOW model confidence.
```

A score such as `2-0` must never visually appear to have a probability such as `74%` merely because the overall prediction is rated HIGH confidence.

---

## Principle 4 — Progressive Disclosure

The information hierarchy is:

```text
Level 1
What should I pick?

Level 2
Why?

Level 3
What alternatives were considered?

Level 4
What evidence changed the model?

Level 5
What did the statistical models actually calculate?

Level 6
Full provenance / model / feature / source audit trail
```

Most interactions should remain at Levels 1–3.

Levels 4–6 exist for investigation and trust.

---

## Principle 5 — Calm Until Something Matters

The system is always on.

The UI must not feel always noisy.

Routine refreshes, background data collection and unchanged forecasts are visually quiet.

Prominence is reserved for:

* prediction changes;
* significant probability movements;
* confirmed lineup effects;
* important market movement;
* deadline risk;
* missing/stale data;
* strategy changes;
* manual action required.

---

## Principle 6 — Strategy Must Be Visible

NORMAL, ATTACK and DEFEND are not hidden implementation details.

The current strategy should always be visible because it explains *why* a recommendation may differ from maximum raw expected points.

However, strategy labels must always be accompanied by plain language.

Example:

```text
ATTACK

You're currently 8 points behind.
One controlled differential increases our simulated chance
of finishing first from 24.7% to 27.9%.
```

Never merely:

```text
MODE = ATTACK
```

---

## Principle 7 — Human Control Is Explicit

The agent recommends.

The user decides what is actually submitted to Super 6.

The system must visually distinguish:

```text
MODEL PICK
STRATEGY PICK
USER PICK
SUBMITTED PICK
```

These may sometimes all be identical.

When they are not, the distinction must be obvious and auditable.

The application never implies that it has submitted predictions to Sky.

---

# 38. Primary User Journey

The weekly lifecycle is:

```text
Sky publishes round
        ↓
User configures six fixtures + deadline
        ↓
Agent begins monitoring
        ↓
Initial forecasts generated
        ↓
Agent continuously updates evidence
        ↓
User reviews recommendations
        ↓
Late team/news/market updates
        ↓
Final recommendation
        ↓
User manually enters predictions in Super 6
        ↓
User marks picks as submitted
        ↓
Matches played
        ↓
Results scored
        ↓
Opponent predictions imported
        ↓
League/model performance updated
        ↓
Next round
```

The UI should explicitly reflect which stage the current round is in.

---

# 39. Global Information Architecture

Primary navigation:

```text
Home
Round
League
Opponents
Performance
Activity
Settings
```

Secondary / expert navigation:

```text
Model Lab
Sources
System Health
```

Desktop:

```text
┌───────────────────┬────────────────────────────────────┐
│                   │                                    │
│ Home              │                                    │
│ Round             │          Page content              │
│ League            │                                    │
│ Opponents         │                                    │
│ Performance       │                                    │
│ Activity          │                                    │
│                   │                                    │
│ ─────────────     │                                    │
│ Model Lab         │                                    │
│ Sources           │                                    │
│ Settings          │                                    │
│                   │                                    │
└───────────────────┴────────────────────────────────────┘
```

Mobile:

```text
Home | Round | League | More
```

The application must be fully usable on mobile because final prediction review will often occur close to kickoff rather than while sitting obediently in front of a workstation.

---

# 40. Home — Round Command Centre

Home is the default landing screen.

Its job is to answer:

```text
What is happening?
What are my six picks?
What changed?
Am I ready?
```

## 40.1 Header

Example:

```text
SUPER 6 — ROUND 14

Saturday 18 October
Deadline 14:00

02:17:43 remaining

● Monitoring active
Last data refresh: 11:42
```

As deadline approaches:

```text
> 24h       normal
< 6h        increasingly prominent
< 90m       countdown emphasised
< 15m       urgent
Expired     round locked
```

Deadline prominence must increase without turning the interface into an airport evacuation panel.

---

## 40.2 League Position Summary

Top-level card:

```text
YOUR LEAGUE

2nd
147 pts

Leader: Dave
154 pts

Gap        -7
Exact      -2

P(Finish 1st)
27.9%

Strategy
ATTACK
```

The primary season KPI is `P(Finish 1st)` rather than merely current league position.

Where opponent modelling is immature:

```text
P(Finish 1st): 24%

Opponent confidence: LOW
Season estimate still heavily prior-based.
```

This prevents artificial precision.

---

## 40.3 Six Prediction Cards

The central element of the entire application.

Desktop may use a 2 × 3 grid.

Mobile uses a vertical list.

Example:

```text
┌──────────────────────────────────────────┐
│ Arsenal                     Everton      │
│                                          │
│              2  –  0                     │
│                                          │
│ RESULT      SCORE       EV       CONF     │
│ 68.3%       13.7%       1.78     HIGH     │
│                                          │
│ Model pick: 2-0                          │
│ Strategy:   unchanged                    │
│                                          │
│ ✓ Stable for 19h                         │
│                                          │
│ Why this pick?                         > │
└──────────────────────────────────────────┘
```

Team names are primary.

The score is visually dominant.

Probability figures are secondary.

---

## 40.4 Changed Prediction State

A changed recommendation is visually distinct:

```text
┌──────────────────────────────────────────┐
│ Chelsea                   Newcastle      │
│                                          │
│              1  –  0                     │
│                                          │
│ CHANGED 12:47                            │
│ Previously 2-0                           │
│                                          │
│ Confirmed Chelsea lineup is weaker       │
│ offensively than forecast.               │
│                                          │
│ Exact score: 11.2% → 14.1%              │
│ Confidence: MEDIUM → HIGH                │
│                                          │
│ Review change                          > │
└──────────────────────────────────────────┘
```

Changes remain marked until acknowledged or until the user reviews them.

---

## 40.5 Strategic Differential State

Where Strategy Pick ≠ Model Pick:

```text
┌──────────────────────────────────────────┐
│ Fulham                    Brentford      │
│                                          │
│              2  –  1                     │
│                                          │
│ ◆ STRATEGIC DIFFERENTIAL                 │
│                                          │
│ Model pick                     1-1       │
│ Recommended                    2-1       │
│                                          │
│ EV cost                       -0.08       │
│ P(Finish 1st)                 +3.2pp      │
│                                          │
│ Why take this risk?                     >│
└──────────────────────────────────────────┘
```

Strategic deviation should be visually impossible to confuse with ordinary model output.

---

## 40.6 Void / Postponed Fixture State

Per §2.2, a fixture that is postponed, abandoned, or not completed with at least 90 minutes played is **VOID**: it contributes no points and is excluded from prediction-performance statistics, but must stay visible and logged rather than silently disappearing.

```text
┌──────────────────────────────────────────┐
│ Leeds                     Tottenham      │
│                                          │
│              VOID                        │
│                                          │
│ ⊘ POSTPONED                              │
│                                          │
│ Match postponed — pitch inspection       │
│ failed, 11:20.                           │
│                                          │
│ This fixture will not score points and  │
│ is excluded from model performance.     │
│                                          │
│ Your last prediction                    │
│ 1-2                                     │
│                                          │
│ Details                                 >│
└──────────────────────────────────────────┘
```

The card keeps the user's last prediction visible (for their own record) but replaces the score display with the fixture status, and the reason line always states plainly that the fixture is excluded from scoring — this is the point at which a user would otherwise wonder why their round total looks short. `ABANDONED` fixtures follow the same card treatment, with the reason line reflecting when in the match it was stopped.

In §44.1 (Round → Predictions) and §51.1 (Round Summary), a void fixture is listed with a `VOID` marker rather than a score or points value, and is excluded from the round's points/exact-score/correct-result totals — never counted as 0 points against a real prediction, which would look identical to a wrong guess and misrepresent performance (§16-17).

---

# 41. Fixture Detail

Selecting a fixture opens its detailed prediction view.

The default view remains human-readable.

## 41.1 Summary

```text
ARSENAL v EVERTON

Recommendation
2-0

Result probability
Arsenal win       68.3%

Exact-score probability
2-0               13.7%

Expected Super 6 points
1.78

Confidence
HIGH
```

---

## 41.2 Why This Pick?

Plain-language explanation:

```text
WHY 2-0?

Arsenal are strongly favoured to win.

The model expects approximately:
Arsenal     2.1 goals
Everton     0.8 goals

2-0 produces the highest expected Super 6 score
from the current probability distribution.

Supporting factors:

↑ Arsenal home strength
↑ Everton defensive weakness
↑ Market support for Arsenal
↔ Team news broadly as expected

No material contradictory signal detected.
```

Technical model vocabulary is deliberately translated into football language here.

---

## 41.3 Alternative Scores

Show only meaningful alternatives:

```text
ALTERNATIVES

        Score probability   Super 6 EV

2-0          13.7%            1.78    ← PICK
2-1          12.5%            1.74
1-0          11.9%            1.72
3-0           8.3%            1.62
1-1           9.4%            1.01
```

A user can therefore see whether a recommendation is obvious or marginal.

---

## 41.4 Score Probability Explorer

Expert disclosure:

```text
          AWAY GOALS
HOME      0      1      2      3
0        4.1    3.2    1.1    0.3
1        9.2    8.7    3.4    1.0
2       13.7   12.5    5.2    1.7
3        8.3    7.9    3.8    1.4
```

Heatmap representation is preferred.

Selectable score cells reveal:

```text
P(score)
P(result)
EV
ranking
```

This belongs below the explanation, not above it.

---

# 42. Evidence & Intelligence Panel

Every fixture has a chronological evidence feed.

Example:

```text
MATCH INTELLIGENCE

12:31  CONFIRMED TEAM
       Everton striker ruled out
       Source: Official Everton FC
       Reliability: CONFIRMED
       Impact: HIGH

       λ Everton
       0.96 → 0.81

────────────────────────────────

10:12  MARKET
       Arsenal win probability
       65.1% → 68.0%

       Material movement detected

────────────────────────────────

Yesterday 17:42
       Manager press conference
       No material forecast impact
```

Default display shows impact in football terms.

Expert expansion may show model terms such as λ changes.

Source provenance must always remain accessible.

---

# 43. Prediction Timeline

Every fixture includes its forecast history:

```text
PREDICTION HISTORY

Mon 09:00       2-1
Initial model

Thu 18:12       2-0
Arsenal defensive availability improved

Sat 12:47       1-0
Confirmed attacking absences
```

Selecting an entry opens:

```text
Model version
Feature snapshot
Odds snapshot
News snapshot
Opponent snapshot
Raw model recommendation
Strategy recommendation
Manual override if any
```

This is the UX surface for the reproducibility requirement in §19.

---

# 44. Round Screen

The Round screen provides a fuller operational view than Home.

Tabs:

```text
Predictions
Intelligence
Changes
Setup
Results
```

## 44.1 Predictions

Full six-match analysis.

Includes:

* recommendation;
* model pick;
* strategy pick;
* user pick;
* result probability;
* exact-score probability;
* EV;
* confidence;
* stability;
* latest change.

---

## 44.2 Intelligence

Round-wide events ordered by significance:

```text
HIGH IMPACT

Chelsea confirmed lineup
Prediction changed 2-0 → 1-0

MEDIUM IMPACT

Liverpool market movement
No prediction change

LOW IMPACT

Weather update
Model impact negligible
```

Events with no material impact may be collapsed by default.

---

## 44.3 Changes

A dedicated diff view:

```text
SINCE YOUR LAST VISIT

2 predictions changed
1 confidence changed
3 new intelligence events

CHE v NEW
2-0 → 1-0

ARS v EVE
MEDIUM → HIGH

No other recommendations changed.
```

This is particularly important for an always-on system.

The user should never need to manually compare today's six scores with yesterday's six scores.

---

# 45. Round Setup

Because Sky is deliberately outside the system boundary, round creation is explicitly manual.

The UX must make this task quick.

## 45.1 Create Round

```text
CREATE SUPER 6 ROUND

Round number
[ 14 ]

Deadline
[ Sat 18 Oct ] [ 14:00 ]

Fixtures

1 [ Arsenal        ] v [ Everton         ]
2 [ Chelsea        ] v [ Newcastle       ]
3 [ Fulham         ] v [ Brentford       ]
4 [ Leeds          ] v [ Tottenham       ]
5 [ Man City       ] v [ Bournemouth     ]
6 [ West Ham       ] v [ Aston Villa     ]

[ Create & Start Monitoring ]
```

Team fields use autocomplete against known teams.

Duplicate fixtures are blocked.

Exactly six fixtures are required.

Deadline must be confirmed explicitly.

---

## 45.2 Deadline Confirmation

Before activation:

```text
CHECK DEADLINE

Round 14 closes:

Saturday 18 October
14:00 UK time

This time controls monitoring frequency and
final prediction alerts.

[ Back ]              [ Confirm ]
```

Human confirmation is desirable because deadline correctness is operationally critical.

---

# 46. Submission Workflow

The system cannot know that predictions were entered into Sky unless the user tells it.

Therefore the application maintains submission state.

States:

```text
NOT READY
READY
CHANGED SINCE REVIEW
MARKED AS SUBMITTED
CHANGED AFTER SUBMISSION
ROUND CLOSED
```

## 46.1 Ready to Enter

When predictions are stable enough for review:

```text
YOUR SIX PICKS

ARS 2-0 EVE
CHE 1-0 NEW
FUL 2-1 BRE
LEE 1-2 TOT
MCI 3-0 BOU
WHU 1-2 AVL

Golden Goal
Minute 9

[ Copy Picks ]

[ Mark as Entered in Super 6 ]
```

"Copy Picks" copies compact plain text.

No button is labelled "Submit" because the application does not submit anything to Sky.

---

## 46.2 Mark as Entered

Confirmation:

```text
Have you entered these predictions in Super 6?

ARS 2-0 EVE
CHE 1-0 NEW
...

Golden Goal: 9

[ Not yet ]

[ Yes — mark as entered ]
```

Store:

```text
submitted_at
submitted_prediction_version
```

---

## 46.3 Prediction Changes After Submission

Critical state:

```text
⚠ PREDICTIONS CHANGED

You marked your Super 6 picks as entered at 12:38.

Chelsea v Newcastle has since changed:

Entered        2-0
Recommended    1-0

Deadline in 47 minutes.

[ Keep 2-0 ]

[ Use 1-0 ]

[ Review reasoning ]
```

Selecting `Use 1-0` updates the application's intended pick but still requires the user to manually change Sky.

The UI must say this explicitly:

```text
Remember to update this prediction in Super 6.
```

---

# 47. Golden Goal UX

Golden Goal is kept deliberately simple.

Main display:

```text
GOLDEN GOAL

9

Sky minute 9
= goal between 8:00 and 8:59
```

Secondary disclosure:

```text
Expected first-goal distribution

1–5       19%
6–10      24%
11–15     26%
16–20     15%
21+       16%

Recommended minute: 9

Chosen to minimise expected tiebreak distance,
not because minute 9 is individually the most likely.
```

The Sky minute/clock-time distinction must always be displayed together to prevent off-by-one confusion.

---

# 48. League Screen

This screen answers:

> **Are we winning?**

## 48.1 Table

```text
PRIVATE LEAGUE

       Player      Pts   Exact   Gap    P(1st)

1      Dave        154    11      —      37%
2      James       147     9     -7      28%
3      Steve       144    10    -10      19%
4      Chris       132     8    -22       9%
...
```

Current user highlighted.

---

## 48.2 Season Position

Charts:

```text
League position by round
Points gap to leader by round
P(Finish 1st) by round
Exact-score gap by round
```

Avoid showing all four simultaneously by default.

Primary chart:

**P(Finish 1st) over time**

because that is the system's actual strategic objective.

---

## 48.3 Strategy Explanation

```text
CURRENT STRATEGY

ATTACK

You are 8 points behind the leader.

The maximum-EV six picks give:
P(Finish 1st) = 24.7%

Current optimised picks give:
P(Finish 1st) = 27.9%

Cost:
-0.07 expected points

Benefit:
+3.2 percentage points chance of finishing first
```

---

# 49. Opponents

The Opponents screen must feel analytical rather than creepy.

It analyses prediction behaviour supplied through the permitted sources defined in §11.

## 49.1 Opponent List

```text
OPPONENTS

Dave
Prediction history     84 fixtures
Model confidence       MEDIUM

Steve
Prediction history     72 fixtures
Model confidence       MEDIUM

Chris
Prediction history     18 fixtures
Model confidence       LOW
```

---

## 49.2 Opponent Profile

Example:

```text
DAVE

Model influence
MEDIUM
████████░░

Prediction tendencies

Home win            +11% vs league
Draw                  -6%
Average goals         2.86

Favourite bias
HIGH

Common scores

2-1        21%
2-0        17%
1-1        14%

Observed tendencies

• favours strong favourites
• predicts fewer draws than the group
• frequently chooses 2-1
```

Never imply certainty where the Bayesian posterior is weak.

For low history:

```text
Insufficient individual evidence.

Predictions currently rely mostly on
league-average opponent behaviour.
```

---

## 49.3 Current-Round Opponent Forecast

When enough evidence exists:

```text
ARSENAL v EVERTON

Likely Dave picks

2-0       31%
2-1       26%
1-0       14%
Other     29%
```

This only becomes prominent when opponent influence is sufficiently supported.

---

# 50. Opponent Data Entry

After a round closes, competitor predictions may be entered.

Methods:

```text
Manual
Paste text
Import CSV
Import shared message
```

Example text import:

```text
Dave
ARS 2-0 EVE
CHE 2-1 NEW
FUL 1-1 BRE
...
```

The system parses the input and always presents a confirmation screen before persistence:

```text
IMPORT PREVIEW

Dave

Arsenal v Everton       2-0
Chelsea v Newcastle     2-1
...

6 predictions detected
0 errors

[ Cancel ]       [ Import ]
```

Ambiguous team names require user confirmation.

Never silently guess.

---

# 51. Results & Post-Round Review

Once matches complete, the interface transitions from prediction to learning.

## 51.1 Round Summary

```text
ROUND 14

Your score
14 pts

Private league rank
2nd of 9

Exact scores
2

Correct results
2

Misses
2
```

If any fixture in the round was VOID (§2.2, §40.6), the summary adds a line — `Void 1` — and that fixture is excluded from the exact-scores/correct-results/misses breakdown entirely rather than counted as a miss, since it never had a real result to be right or wrong about.

---

## 51.2 Fixture Review

```text
ARSENAL 2-0 EVERTON

Predicted
2-0

Actual
2-0

+5 POINTS

Model exact probability
13.7%

Classification
Correct exact score
```

Another:

```text
CHELSEA 2-1 NEWCASTLE

Predicted
1-0

Actual
2-1

+2 POINTS

Classification
Correct result, wrong score
```

---

## 51.3 Strategy Review

Strategic choices are judged on process as well as outcome.

Example:

```text
FULHAM v BRENTFORD

Model pick
1-1

Strategy pick
2-1

Actual
0-0

Result
0 pts

Decision quality
RATIONAL / UNSUCCESSFUL

At selection time:

P(Finish 1st) gain      +3.2pp
EV cost                 -0.08
Confidence bound        passed

The outcome does not invalidate the decision.
```

This directly supports the guardrail against learning bad lessons from randomness.

---

# 52. Performance Screen

The Performance screen answers:

> **Is this machine actually better than guessing?**

Primary comparison:

```text
CURRENT SEASON

Agent               9.8 pts / round
Market baseline      9.1
Poisson baseline     8.7
Human baseline       8.3
```

Supporting metrics:

```text
Exact-score rate
Result accuracy
Brier score
Log loss
Calibration
Average EV
```

---

## 52.1 Model Evolution

Show the ablation ladder:

```text
MODEL TESTING

Poisson
8.61 pts/round

Dixon-Coles
8.84
+0.23 ✓ retained

DC + Elo
8.91
+0.07 ✓ retained

DC + Market
9.06
+0.15 ✓ retained
```

Where a component fails:

```text
xG uplift
9.03

Previous model
9.06

-0.03

NOT PROMOTED
```

This makes the §28 philosophy visible rather than theoretical.

---

# 53. Activity Centre

A unified chronological activity stream.

Filters:

```text
All
Predictions
Intelligence
Markets
Lineups
Strategy
System
Manual actions
```

Example:

```text
12:47
Chelsea v Newcastle
Prediction changed 2-0 → 1-0

12:31
Confirmed Chelsea lineup received

11:52
Round strategy recalculated
NORMAL → ATTACK

10:14
Market movement detected
Liverpool win +4.2pp

09:00
Forecast run completed
No material changes
```

Routine no-change events are hidden by default.

---

# 54. Alerts & Notifications

Alert severity:

## Critical

Requires user action.

Examples:

```text
Prediction changed after user marked round submitted.
Deadline less than 60 minutes away.
Round deadline changed manually.
Required fixture data unavailable close to deadline.
```

## Important

Material decision change.

Examples:

```text
Recommended score changed.
Strategy changed.
Confirmed lineup materially affected forecast.
Large unexplained market move.
```

## Informational

Interesting but non-actionable.

Examples:

```text
Confidence increased.
New news item with low model impact.
Market movement below action threshold.
```

Informational events remain in Activity but should not generate push notifications by default.

---

## 54.1 Notification Channels

Initial UX should support:

```text
In-app
Email
```

Optional future channels:

```text
Push notification
Messaging integration
```

Notification destination must not affect modelling.

---

## 54.2 Notification Example

```text
SUPER 6 — PICK CHANGED

Chelsea v Newcastle

2-0 → 1-0

Confirmed lineup weakened Chelsea's expected attack.

Exact-score probability:
11.2% → 14.1%

Confidence:
MEDIUM → HIGH

Deadline:
47 minutes

Review in Super 6 Agent
```

---

# 55. Manual Overrides

The user can override any recommendation.

Selecting a different score produces:

```text
MODEL RECOMMENDS
2-0

YOU SELECTED
2-1

Difference

Model score probability
2-0       13.7%
2-1       12.5%

Super 6 EV
2-0       1.78
2-1       1.74

[ Use 2-1 ]
```

No patronising warning.

The system records:

```text
manual_override
previous_pick
new_pick
timestamp
optional_reason
```

Optional reason examples:

```text
Gut feeling
Team knowledge
Following market
Competition strategy
Other
```

Reason entry is never mandatory.

---

# 56. Trust & Explainability

Every major number must have an explanation available.

For example:

```text
P(Finish 1st)     [?]
EV                [?]
Confidence        [?]
Result probability [?]
Exact probability [?]
```

Selecting the explanation gives concise language first.

Example:

```text
EXPECTED POINTS — 1.78

This is the average Super 6 score this prediction
would earn if the match could be replayed many times
under the model's current probabilities.

It is not the number of points we expect to score
in this one actual match.
```

A further link:

```text
View calculation
```

reveals:

```text
2 × P(home win)
+
3 × P(2-0)

= 2 × 0.683
+
3 × 0.137

= 1.777
```

---

# 57. Model Lab

Model Lab is deliberately separated from normal use.

Audience:

* developer;
* model reviewer;
* curious user investigating performance.

Tabs:

```text
Models
Backtests
Calibration
Team Strength
Score Matrix
Simulation
```

Examples:

```text
MODEL VERSION
dc-elo-market-0.7.2

Training cutoff
2026-08-01

ξ
0.0041

ρ
-0.083

Elo θ
0.116

Market calibration
Result-constrained / Shin

Tail tolerance
0.00001
```

This screen may expose mathematical terminology because the user explicitly chose to enter Model Lab.

---

# 58. Sources

Source management reflects §18.1.

Example:

```text
SOURCES

Football-Data.co.uk

TYPE
Dataset

STATUS
APPROVED

LAST UPDATE
Today 06:00

────────────────────

Sportmonks

TYPE
API

STATUS
REVIEW REQUIRED

COST
€29/month

[ Review ]
```

Prohibited sources remain visible:

```text
Sky Super 6 scraping

STATUS
PROHIBITED

Reason
External-to-Sky architectural boundary.
```

They cannot be enabled from a normal toggle.

---

# 59. System Health

The application should expose operational health without demanding constant attention.

Summary:

```text
SYSTEM

Football data       ✓ Current
Odds                ✓ Current
News                 ✓ Current
Scheduler            ✓ Running
Model                ✓ Ready
Database             ✓ Healthy

Last forecast        4 min ago
Next scheduled check 13 min
Monthly cost         £4.17 / £10 target
```

Warnings:

```text
Odds data stale
Last update 3h 18m

Forecast still available
Confidence reduced to MEDIUM.
```

System failures should affect `feature_completeness` and therefore CONF where appropriate.

---

# 60. Settings

Groups:

```text
League
Notifications
Prediction behaviour
Sources
Budget
System
```

## League

```text
League name
Player name
Competitors
Private-league scoring
Tiebreak rules
```

This is where §27's remaining private-league rule is configured.

---

## Notifications

```text
Prediction changes       ON
Strategy changes         ON
Confirmed lineup impact  ON
Market movement          ON
Deadline reminder        ON

Informational updates    OFF
```

---

## Budget

```text
Target monthly spend     £10
Hard ceiling             £40

This month               £4.17
```

Paid source promotion requires a deliberate confirmation flow.

---

# 61. Empty States

Empty states should always explain the next action.

Example:

```text
NO ACTIVE ROUND

When Sky publishes the next six fixtures,
add them here and the agent will begin monitoring.

[ Create Round ]
```

Opponent empty state:

```text
NO OPPONENT HISTORY YET

Add your mates' predictions after each deadline.

The system will gradually learn their tendencies
as evidence accumulates.

[ Import Predictions ]
```

---

# 62. Data Quality States

The user must know when a recommendation rests on incomplete data.

Possible status:

```text
DATA QUALITY

GOOD
All primary sources current.

DEGRADED
Odds unavailable.

LIMITED
Only historical/team data available.

STALE
Required data has not refreshed within tolerance.
```

Prediction remains available whenever mathematically valid.

Missing optional data reduces confidence rather than necessarily blocking the recommendation.

---

# 63. Responsive Behaviour

## Desktop

Optimised for:

* six-card overview;
* side-by-side comparison;
* charts;
* model investigation.

## Tablet

Two-column prediction grid.

## Mobile

Single-column flow prioritising:

```text
Deadline
League status
Six picks
Changes
Golden Goal
```

Expert data collapses into drawers.

No horizontal table should be required for the primary weekly workflow.

---

# 64. Accessibility

Minimum requirements:

* WCAG 2.2 AA target;
* keyboard navigable;
* focus states visible;
* all status colours also represented by text/icon;
* no red/green-only distinction;
* minimum readable touch targets;
* screen-reader labels for score controls;
* charts have textual summaries;
* countdown does not continuously announce changes to assistive technologies;
* motion optional/reduced according to OS preference.

---

# 65. Visual Design Direction

The interface should feel:

```text
Sports intelligence
+
professional analytics
+
consumer simplicity
```

Not:

```text
Sports betting site
Bloomberg terminal
generic admin console
AI chatbot
```

Recommended visual language:

* dark or neutral surfaces with strong contrast;
* large score typography;
* team identity clear but restrained;
* compact cards;
* generous spacing around primary decisions;
* semantic colour used sparingly;
* graphs secondary to decisions;
* minimal decorative animation.

Confidence:

```text
HIGH
MEDIUM
LOW
```

must use labels as well as visual treatment.

Strategy:

```text
NORMAL
ATTACK
DEFEND
```

should have distinct treatment but never imply gambling risk categories.

---

# 66. Interaction Language

The system speaks plainly.

Prefer:

```text
Prediction changed because Chelsea named a weaker attacking lineup.
```

over:

```text
Feature vector mutation caused λ_home degradation.
```

Prefer:

```text
This alternative slightly reduces expected points but increases our simulated chance of winning the league.
```

over:

```text
Portfolio utility optimisation selected a lower-EV state.
```

Technical terms are available in the expert layer.

---

# 67. AI / Agent Interaction

The application is not primarily a chatbot.

The agent may provide a contextual conversational entry point:

```text
Ask the agent
```

Examples:

```text
Why have we gone 1-0 rather than 2-0?

Which prediction are you least confident about?

What changed since this morning?

Why are we in ATTACK mode?

What is Dave most likely to predict?

Would 3-1 Liverpool be a sensible alternative?

How has the model performed on promoted teams?
```

Answers must use the same persisted forecast evidence shown elsewhere.

The conversational agent must not create a separate truth from the structured UI.

Where an answer changes a prediction, the normal forecast/change workflow must execute and record the change.

Conversation is an interface to the system, not a bypass around it.

---

# 68. Core UX States for a Prediction

Every fixture recommendation has exactly one lifecycle state:

```text
INITIAL
STABLE
CHANGED
USER_OVERRIDDEN
SUBMITTED
CHANGED_AFTER_SUBMISSION
CLOSED
RESULTED
```

And separately one data-health state:

```text
GOOD
DEGRADED
LIMITED
STALE
```

And one confidence state:

```text
HIGH
MEDIUM
LOW
```

These dimensions must not be conflated.

Example:

```text
Prediction state     CHANGED_AFTER_SUBMISSION
Data health          GOOD
Confidence           HIGH
```

---

# 69. UX Acceptance Criteria

The initial UI is successful if a user can:

1. Create a round containing six fixtures and deadline in under two minutes.
2. View all six recommended scores without scrolling on a typical desktop display.
3. View all six recommendations in a simple sequential flow on mobile.
4. Identify any changed prediction immediately.
5. Understand why a prediction changed within two interactions.
6. Distinguish result probability, exact-score probability and confidence.
7. See when strategy has deliberately overridden the raw model pick.
8. Understand the expected-value cost and P(Finish 1st) benefit of any strategic differential.
9. Mark predictions as manually entered into Super 6.
10. Be warned if recommendations subsequently change before the deadline.
11. Inspect every historical prediction version and its reason.
12. Import opponent selections without requiring direct Sky integration.
13. See current league position and P(Finish 1st).
14. Review round results and model performance.
15. Understand when source/data quality is degraded.
16. Reach technical model detail without exposing it during routine weekly use.

---

# 70. Phase-Aligned UI Delivery

The UI should grow with the functional delivery phases rather than attempting to build every expert screen immediately.

## UI Phase 1 — Prediction Product

Required:

```text
Home
Round setup
Six prediction cards
Fixture detail
Score alternatives
Golden Goal
Submission state
Results
Basic Performance
Settings
```

This is the minimum viable product.

---

## UI Phase 2 — Always-On Agent Experience

Add:

```text
Intelligence feed
Prediction changes
Change-since-last-visit
Alerts
Prediction timeline
Source provenance
System health
```

---

## UI Phase 3 — Beat the Mates

Add:

```text
League
Opponent profiles
Opponent import
Opponent predicted selections
P(Finish 1st)
Strategy explanation
Differential-pick UX
```

---

## UI Phase 4 — Model Transparency

Add:

```text
Model Lab
Advanced backtesting
Ablation results
Calibration
Source administration
Deep forecast reconstruction
Cost-performance analysis
```

---

# 71. Primary Screen Hierarchy

The final hierarchy is:

```text
HOME
│
├── League position / P(1st)
├── Deadline / monitoring state
├── Six picks
│   └── Fixture detail
│       ├── Why this pick?
│       ├── Alternatives
│       ├── Evidence
│       ├── Prediction history
│       └── Model detail
│
├── Golden Goal
└── Changes / actions required

ROUND
├── Predictions
├── Intelligence
├── Changes
├── Setup
└── Results

LEAGUE
├── Standings
├── P(1st)
├── Strategy
└── Season history

OPPONENTS
├── Profiles
├── Tendencies
└── Prediction forecasts

PERFORMANCE
├── Score performance
├── Baselines
└── Model evolution

ACTIVITY
└── Full audit/activity timeline

EXPERT
├── Model Lab
├── Sources
└── System Health

SETTINGS
├── League
├── Notifications
├── Budget
└── System
```

---

# 72. UI/UX Definition of Success

The system succeeds as a user experience when the user does **not** need to understand Dixon-Coles, Monte Carlo simulation, Elo, Bayesian shrinkage or market calibration to use the recommendation correctly...

...but can inspect every one of those things when asking:

> **Why?**

The interface therefore has two equally important responsibilities:

**make the decision simple**

and

**make the reasoning inspectable.**

The default experience should feel like:

```text
Here are your six picks.
Here is what changed.
Here is why.
Here is what you need to do.
```

Everything else exists one layer beneath it.
