# AVAILABILITY_ORACLE_RESEARCH_V1 — pre-registration

**Registered: 2026-08-08, before any availability data was harvested and
before any result was seen.**

This is a **research study, not a model change**. It never touches
`PHASE1_MODEL_V1`, never enters the production forecast path, and its
findings cannot promote anything on their own.

## The question

> If the model had **perfect** knowledge of final team availability, is
> there enough predictive signal to care about availability at all?

Deliberately **not** "does availability improve our forecasts at the Super
6 deadline?" — that is Stage B, and it is a harder question that only
matters if the answer here is yes.

## Why this is an oracle study and not an ablation

Sky's rules lock the entire round at the **first fixture's kick-off**.
API-Football documents confirmed XIs appearing 20–40 minutes before each
**individual** fixture. So for a typical round:

```text
Round locks          15:00 Saturday

Fixture A  15:00 Sat   XI ~14:40   PRESUMED KNOWABLE
Fixture B  15:00 Sat   XI ~14:40   PRESUMED KNOWABLE
Fixture C  17:30 Sat   XI ~17:10   FUTURE INFORMATION
Fixture D  14:00 Sun   XI ~13:40   FUTURE INFORMATION
Fixture E  16:30 Sun   XI ~16:10   FUTURE INFORMATION
Fixture F  20:00 Mon   XI ~19:40   FUTURE INFORMATION
```

Two of six. Using all six and reporting "lineups improve predictions"
would be time travel, so this study **admits** it uses future information
and is named accordingly.

A second limitation, stated up front: a historical API response proves a
lineup or injury **existed**, not **when it became public**.
API-Football supplies no historical `published_at` for either. So even
the same-kick-off fixtures are `PRESUMED_KNOWABLE`, never proven. The
enforcement lives in `super6.research.deadline` with 13 regression tests.

## Design

**Corpus.** API-Football free tier, seasons 2022/23 and 2023/24 —
development data only. The 2024/25 validation season is excluded to keep
it clean for any future work; 2025/26 is the spent holdout and is
untouchable.

**Screening sample: 200 fixtures**, stratified:

```text
100 Premier League   /  100 Championship
split across          2022/23 and 2023/24
distributed across    early / middle / late season
balanced on           home and away strength
```

**Cost.** Two calls per fixture (injuries + lineups) = 400 calls. Free
tier allows 100/day, so ~4 days of background collection. The batch `ids`
parameter is paid-only, so calls are per-fixture.

## Pre-registered metrics

Primary:

```text
Brier score
log loss
Super 6 points per fixture
```

Secondary:

```text
exact-score %
result accuracy
pick-change rate
```

**And the one that may decide it:**

```text
number of fixtures where the availability feature changed the pick
```

Recorded because "200 fixtures, availability changed 4 picks" makes the
expected benefit negligible regardless of whether those four happened to
win. A large effect on a handful of fixtures is not a large effect.

## Decision rule, fixed in advance

```text
Oracle shows no credible improvement
    -> STOP. No SportDevs, no Sportmonks, no DSG, no GBP25/month.
       The availability gap closes permanently and the free stack
       becomes the settled architecture, not a compromise.

Oracle shows a credible effect
    -> expand the harvest to the full development corpus,
       THEN open Stage B: how much of that value survives the
       decision-time constraint?
       ONLY THEN qualify vendors.
```

"Credible" is deliberately not a p-value threshold set before seeing the
data's variance. It means: an effect large enough to matter on the
primary metrics, surviving the paired comparison methodology used
throughout this project (§13.3/§36), on a sample where the feature
actually changed enough picks to be measurable.

## What this study cannot conclude

- That availability data is **acquirable in time**. That is Stage B.
- That any vendor supplies it. That is procurement, after Stage B.
- That the effect would survive on 2026/27. Any production availability
  feature remains prospective/shadow through the season regardless (§4 of
  the roadmap; there is no unspent holdout).

## Status

```text
Pre-registered      2026-08-08
Deadline harness    BUILT + TESTED (13 regression tests)
Harvest             NOT STARTED
Analysis            NOT STARTED
```
