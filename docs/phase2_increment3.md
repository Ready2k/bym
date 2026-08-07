# Phase 2, Increment 3 — Team Availability Intelligence

Understands and explains team news. **Does not convert team news into λ
adjustments** — that is Increment 3D at the earliest, and only after a
fitted, versioned impact model earns its place under §28.

## The boundary, enforced not merely stated

We have a validated `team strength + market -> P(score)` model. We have no
validated function saying "striker absent -> λ_home −0.27". The spec
sketches such examples, but the number must come from a fitted model, not
from an LLM with strong opinions about centre-forwards — §22 forbids
altering probabilities without an explainable, reproducible trigger.

So there is **no code path** from an availability event to a model
parameter. `availability/impact.py` is the single place such a path would
ever be added, gated on `QUANTITATIVE_IMPACT_MODEL_ENABLED = False`. Two
tests hold the line:

- `test_availability_alone_cannot_change_lambda` — the assessment returns
  zero deltas and `apply_to_lambdas` is a no-op.
- `test_forecast_path_has_no_availability_input` — `recommend_fixture`'s
  signature is asserted to be exactly `{params, home, away, cfg, odds}`,
  so team news has no argument through which to reach the model.

Route 2 of §3.7 is what actually happens today: record the event, flag
review, possibly affect CONF, never touch λ. Every emitted event carries
`quantitative_impact: NONE DIRECTLY` so the eventual UI cannot imply
otherwise.

## Separations maintained

This project is becoming a campaign against seductive single-number
scores, and Increment 3 adds three more:

```text
source_reliability   how much we trust the source
claim_status         how certain the claim is
```

An excellent journalist saying "expected to miss out" is
`HIGH` reliability, `LIKELY` status — never CONFIRMED. Test asserts that
source authority does not upgrade a hedged claim.

```text
expected_lineup      has a source, generated_at, confidence
confirmed_lineup     has a source, announced_at, and NO confidence
```

Separate objects, not one table with a status flag. A `Lineup` with
`kind=CONFIRMED` and a confidence value raises — the confirmed XI is the
fact against which expectations are judged.

```text
movement_strength / market_agreement    (Increment 2)
model_vs_market / book_dispersion       (Increment 2)
source_reliability / claim_status       (Increment 3)
```

## Reconciliation

Priority is **confirmation level, then source authority, then recency** —
never "latest article wins". The worked example:

```text
10:00  journalist   Palmer doubtful      REPORTED / MEDIUM
12:30  club         Palmer available     LIKELY   / HIGH
13:45  confirmed XI Palmer starts        CONFIRMED/ HIGH
                    -> CONFIRMED_STARTER
```

A later low-status rumour cannot displace the confirmed XI. All three
observations remain in the record — the chain is what answers "why did we
think he was out?" three weeks later.

Contradictions between claims at the *same* priority band are surfaced as
`TEAM_NEWS_CONFLICT`; a superseded lower-priority claim is not a conflict,
merely history.

## Player resolution

Same philosophy as team resolution: exact → registered alias → normalised
exact → unresolved. **Never fuzzy.**

`J. Williams` in a squad containing Joe Williams and Jordan Williams
resolves to `AMBIGUOUS`, which is treated exactly like unresolved. It does
not pick whichever scores highest on a string-similarity function. A
surname that matches but whose initial rules every candidate out is a
contradiction, not a near-miss to be resolved optimistically.

Unresolved claims are still *recorded* — we know something was reported
about that team — but attach to no player identity, emit
`PLAYER_UNRESOLVED`, and yield no player-specific inference.

## Market-movement attribution

Temporal association only. The strongest verdict is `PLAUSIBLE`, and the
system says so in words:

> The market move began shortly before Chelsea confirmed an attacking
> absence. That is a plausible explanation, but the system cannot
> establish that the team news caused the repricing.

Worth noting the direction that makes this necessarily weak: markets
routinely move *before* news is public, because someone knew first. News
arriving shortly after a move is therefore at least as suggestive as news
arriving before it, and neither is proof. An unexplained move stays
unexplained rather than being assigned the nearest available story.

News about a team not in the fixture cannot explain that fixture's move.

## feature_completeness

The §20 CONF ingredient is now real, mapping to §62's data-quality states:

```text
GOOD       confirmed lineups for both teams + market
DEGRADED   expected lineups only, or market missing
LIMITED    historical/team data only
STALE      required data has not refreshed within tolerance
```

Categorical, feeding `HIGH | MEDIUM | LOW`. There are no confidence units.

## Persistence (§26 updated)

SPEC §26 now reads: relational persistence, SQLite as the Phase 1/2
single-node default, PostgreSQL as the scale/concurrency migration target,
with application code forbidden from depending on SQLite-specific
semantics. The four requirements are implemented in `db.py` and asserted
in `tests/test_db.py`:

```text
foreign_keys = ON     enforced; a dangling supersedes_event_id is rejected
WAL mode              readers don't block the writer
schema migrations     versioned via PRAGMA user_version, applied in order
UTC timestamps        ISO8601 with explicit offset, converted not relabelled
```

Migration triggers stay concrete: multiple instances, multiple workers,
multi-user access, meaningful concurrent writes, hosted HA. Not "Postgres
feels more architectural".

## Acceptance criteria

| Requirement | Test |
|---|---|
| versioned availability events | `test_events_persist_as_versioned_records` |
| expected/confirmed lineups distinct | `test_expected_and_confirmed_are_distinct_objects` |
| confirmed supersedes without deleting | `test_spec_worked_example`, `test_history_is_preserved_not_overwritten` |
| authority vs certainty separate | `test_reliability_and_claim_status_are_separate` |
| deterministic player resolution | `tests/test_players.py` (10 cases) |
| unresolved never silently maps | `test_ambiguous_surname_is_not_guessed`, `test_unresolved_player_emits_event_and_stores_no_identity` |
| material lineup differences | `test_material_lineup_difference_detected`, `test_single_key_absence_is_material_even_if_only_one_change` |
| temporal market association | `test_market_move_attributed_to_nearby_news` |
| availability cannot modify λ | `test_availability_alone_cannot_change_lambda`, `test_forecast_path_has_no_availability_input` |
| completeness reflects availability | `test_completeness_reflects_lineup_availability` |
| structured events emitted | throughout |
| idempotent repeat ingestion | `test_repeated_identical_source_event_is_idempotent` |
| correction/retraction is history | `test_correction_creates_history_rather_than_mutation`, `test_retraction_preserves_the_original` |
| deadline stops monitoring | `test_polling_stops_after_deadline` (Increment 2) |

## What is deliberately absent

**No source ingestion adapters.** 3B's reconciliation and storage are
built and tested, but nothing yet *fetches* injuries or lineups from a
real feed — no permitted structured source is registered for it, and
inventing one would breach §18.1. The ingestion API takes structured
claims; wiring a source to it requires registering that source first.

This means Increment 3 is currently exercised by tests and manual entry
only. That is the honest state, not a gap I have papered over.

## Next

Increment 4 — the News/Knowledge Agent, consuming broader reports and
trying to explain the structured signals gathered by 2 and 3. Then, only
after prospective evidence accumulates, 3D: quantitative player-impact
modelling, where injuries finally earn permission to alter λ.
