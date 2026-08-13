# Phase 2, Increment 1 — The Odds API integration

Replaces manual odds entry with automated, timestamped acquisition.
**Changes nothing about the model:** the consensus feeds
`PHASE1_MODEL_V1`'s frozen `w = 0.5` exactly where a hand-typed price used
to go. This is not retraining market calibration.

## Status

| # | Item | State |
|---|---|---|
| 1 | Source registry entry | done — `REVIEW_REQUIRED`, awaiting your account |
| 2 | API client | done (offline-tested; live run needs your key) |
| 3 | PL + Championship event mapping | done |
| 4 | Bookmaker-level odds snapshots | done |
| 5 | De-vig each bookmaker | done |
| 6 | Robust market consensus | done (median) |
| 7 | Quota accounting | done |
| 8 | Stale-data detection | done (12h threshold) |
| 9 | Scheduler integration | **partial** — cadence is a tested pure function; no daemon yet |
| 10 | Feed consensus into §32 model | done |
| 11 | Prospective ledger support | done (`record --fetch-odds`) |

Item 9 is deliberately half-done: `cadence.poll_interval()` computes §9's
ladder and is fully tested, but nothing yet *runs* it on a timer. The
acceptance criterion below does not require a daemon, and a scheduler is
a meaningful piece of work in its own right — it is the next slice, not a
loose end pretended away.

## Turning it on

The source ships as `REVIEW_REQUIRED`, so every fetch is refused until you
have an account and have accepted the terms:

```bash
bym sources
```

Once registered at the-odds-api.com with the terms accepted:

```bash
bym sources approve the_odds_api
```

Then export the key and fetch:

```bash
export ODDS_API_KEY=your_key_here
```

```bash
bym predict --round rounds/round_01.json --seasons 2021 2026 --data data --fetch-odds
```

Check spend at any time:

```bash
bym quota
```

## Credit budget

One request costs `markets x regions`, and returns every upcoming fixture
in that competition — so a polling cycle covering both divisions with
`h2h` / `uk` costs **2 credits**, not 2 per fixture.

Estimated from the §9 cadence, a full week of polling one round across
both divisions costs well under 100 credits, leaving five busy rounds
inside the 500/month free tier with headroom for retries and testing. A
test asserts this stays true if the cadence ladder is ever edited.

Two budget guards are built in: `fetch_odds` refuses outright rather than
overrunning the monthly budget, and the sub-90-minute cadence degrades to
30-minute polling when constrained — spending the last credits early and
going blind at the deadline is the failure mode worth avoiding.

## Consensus, not one bookmaker

Each bookmaker is de-vigged **before** aggregation (margins differ, so
averaging raw prices would blend margins into the estimate), then the
consensus is the **median** across books. A test pins the reason: a single
eccentric quote moves the median by less than 0.05 while showing up
honestly in the dispersion figure.

Two provenance layers are persisted (§19), append-only:

```text
data/snapshots/odds_snapshots.csv
    fetched_at, event_id, fixture, bookmaker, bookmaker_last_update,
    odds_h/d/a, devig_h/d/a, overround

data/snapshots/market_consensus_snapshots.csv
    calculated_at, event_id, fixture, bookmaker_count,
    consensus_h/d/a, dispersion, stale_excluded, oldest_quote
```

`dispersion` is stored but unused for now — it is the natural input to
§20's `market_divergence` confidence factor, and to Increment 2's market
movement detection.

## Degradation, never imputation

A fixture gets no market view when the feed has no matching event, team
names cannot be resolved confidently, or every quote is stale (>12h). In
all three cases the fixture falls back to the raw statistical model and
the reason is printed. No price is ever invented — that is the §30/§62
contract, and the tests assert `odds is None` rather than a filled value.

Team-name resolution is exact → alias → normalised, and **never fuzzy**:
"Sheffield United" and "Sheffield Wednesday" are exactly the kind of pair
where a near-miss matcher would confidently return the wrong club. An
unresolvable name is reported, never guessed (§50). The alias table
refuses to load if two canonical names claim the same alias — a collision
found during development would otherwise have silently dropped
Nottingham Forest's odds.

## Acceptance criterion

> For a manually configured Super 6 round, the system can automatically
> obtain the six current 1X2 market probabilities, persist their
> provenance/timestamps, produce the consensus, and generate the same
> existing Phase 1 recommendation without manual odds entry.

Met, verified in `tests/test_pipeline.py` against a mocked feed: six
fixtures matched through name mapping, consensus probabilities summing to
1, both snapshot layers written with timestamps, snapshots appending
across polls, 2 credits per cycle, and the round's odds populated for the
frozen model.

**Live verification is outstanding** and requires your API key — the
network path itself has not been exercised against the real service.

## Next

Market movement (Increment 2) now has what it needs: timestamped
consensus snapshots accumulating per round. After that, lineups →
injuries → news → reforecast triggers → change explanations → alerts.

No component here may be called an improvement until `PHASE2_AGENT`
demonstrates it prospectively (results/prospective_2026_27.md).
