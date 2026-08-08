# Engine Status

Status levels, used strictly:

```text
BUILT           code exists
TESTED OFFLINE  exercised against mocks/recorded payloads
LIVE VERIFIED   a real network call has succeeded
ACTIVE          running and influencing output
SHADOW          running, recorded, NOT influencing output
```

An adapter existing is **not** "live".

## Component status

| Component | Status | Notes |
|---|---|---|
| Phase 1 forecast core | BUILT, TESTED OFFLINE, **ACTIVE** | `PHASE1_MODEL_V1`, frozen, holdout spent |
| Prospective ledger | BUILT, TESTED OFFLINE, ACTIVE | append-only, arms pre-registered |
| The Odds API adapter | **LIVE VERIFIED** | 20 UK books, consensus confirmed |
| API-Football adapter | **BLOCKED — free plan** | adapter correct vs 2024; current season paywalled |
| Tavily adapter | **LIVE VERIFIED** | `topic=news` required for dates |
| Market movement / divergence | BUILT, TESTED OFFLINE | needs live odds to exercise |
| Scheduler | BUILT, TESTED OFFLINE | restart-safe, SQLite, three job types |
| Always-on orchestration | BUILT, TESTED OFFLINE | odds + availability + event-driven knowledge |
| Submission state (§46) | BUILT, TESTED OFFLINE, ACTIVE | drift detection after entry |
| Availability intelligence | BUILT, TESTED OFFLINE | **no usable live feed** — see API-Football |
| Knowledge agent | BUILT, TESTED OFFLINE | **SHADOW** by default |
| Ollama extractor | BUILT, TESTED OFFLINE, **LIVE VERIFIED** | `llama3.1:8b` on Desktop-HomePC.local; extraction confirmed |
| API read-model contracts | BUILT, TESTED OFFLINE | frozen for the future UI |
| **UI (§37+)** | **DEFERRED — not started** | deliberate |
| **Increment 5 player-impact model** | **DEFERRED — not started** | deliberate |

## Ollama

```text
Host:  Desktop-HomePC.local  (configurable via OLLAMA_BASE_URL)
Role:  structured claim extraction ONLY
Mode:  shadow by default
```

Ollama has **no route** to `λ_home`, `λ_away`, `P(score)`, the market
blend weight `w`, Dixon-Coles parameters, forecast probabilities or
strategy scores. It receives evidence text and emits candidate claims
against the existing `CandidateClaim` schema; those claims then pass the
same deterministic validators as any other extractor.

Forbidden output fields (`impact_score`, `lambda_adjustment`,
`confidence`, `weight`) are named explicitly and fail validation loudly
rather than being silently dropped.

Configuration, all environment-driven and safe by default:

```bash
export CLAIM_EXTRACTOR=ollama
export CLAIM_EXTRACTOR_MODE=shadow
export OLLAMA_BASE_URL=http://Desktop-HomePC.local:11434
export OLLAMA_MODEL=llama3.1:8b
```

Health check:

```bash
super6 health
```

Diagnoses DNS failure, connection refused, timeout, missing model and
unexpected API responses separately, and never prints credentials — a URL
containing embedded auth is reduced to its hostname before it reaches any
log or provenance record.

## Shadow mode

Turning the LLM on is deliberate **twice**: an extractor must be
configured, and then promoted out of shadow. In shadow mode claims are
retrieved, extracted, validated, stored with full provenance, and marked
`promoted = 0` — they never enter reconciled availability state.

```bash
super6 shadow-report
```

reports counts only: emitted, accepted, rejected, promoted, failed runs,
and rejection reasons by category. **No AI-generated quality score is
produced**, because manual review of real football reporting is the point.

## Provenance

Every extraction run records provider, model, model digest where the
server supplies one, host, prompt version, extractor version, mode,
requested/completed timestamps, evidence hash and outcome counts. No
secrets, no article content — retention remains metadata plus a
400-character audit snippet.

## Live verification

```bash
super6 verify-live
```

Checks each source independently. **Approval is never bypassed**: a source
that is not `APPROVED` reports `BLOCKED` rather than being queried, and a
successful verification approves nothing. Absence is distinguished from
failure — a lineup not yet published and an empty injury list are both
`absent`, not faults.

Current live verification status:

```text
the_odds_api   LIVE VERIFIED  (20 bookmakers, de-vig + consensus confirmed)
api_football   FAILED         (free plan covers 2022-2024 ONLY; 2026 paywalled)
tavily         LIVE VERIFIED  (news topic required for published_date)
ollama         LIVE VERIFIED  (llama3.1:8b, 2 claims accepted end-to-end)
```

Approval remains a manual act:

```bash
super6 sources approve the_odds_api
```

`sky_super6` is permanently `PROHIBITED` and `approve()` refuses it.

## Always-on orchestration

The scheduler runs three job types, deliberately separate because they
have different cadences and different budgets:

```text
POLL_ODDS           §9 deadline ladder
POLL_AVAILABILITY   injuries routinely; confirmed XIs only inside the
                    75-minute pre-kickoff window
KNOWLEDGE_RESEARCH  opt-in (--with-knowledge); costs search credits
```

```bash
super6 watch --round rounds/round_01.json --seasons 2021 2026 --data data --loop
```

**Event-driven investigation.** A MAJOR market move with no availability
event nearby is the §21 "REVIEW REQUIRED — no confirmed news detected"
case. The odds worker flags it, and the knowledge layer investigates that
specific fixture — regardless of whether routine research is scheduled.
This is the one place knowledge runs unscheduled, because it is exactly
the event-driven case it exists for. A move that *is* explained by known
team news triggers nothing.

Each layer reschedules independently, and one failing layer neither stops
the others nor abandons its own job — it records the error and retries.

## Submission state (§46)

The system cannot know what you entered into Sky, so you tell it, and its
job is then to notice when its own recommendation drifts away from what
you entered.

```text
NOT_READY → READY → CHANGED_SINCE_REVIEW
                  → MARKED_AS_SUBMITTED → CHANGED_AFTER_SUBMISSION
                  → ROUND_CLOSED
```

```bash
super6 submission mark-entered --round 1 --picks 2-0 1-0 2-1 1-2 3-0 1-2 --golden-goal 9
```

```bash
super6 submission status --round 1
```

`CHANGED_AFTER_SUBMISSION` is the state the mechanism exists for, and its
wording always ends with "Remember to update these predictions in Super 6
yourself" — nothing in this system submits anything, and a test asserts
no phrasing implies otherwise.

## Engine run

```bash
super6 engine-run --round rounds/round_01.json --seasons 2021 2026 --data data
```

Produces a four-layer report — FORECAST INPUT, MARKET INTELLIGENCE,
STRUCTURED TEAM INTELLIGENCE, KNOWLEDGE INTELLIGENCE — and states
explicitly that team/news intelligence has `quantitative impact: NONE
DIRECTLY`. Every external step degrades to "absent" rather than failing
the run, so a forecast is produced with no credentials at all.

`--json` writes the `RoundSummary` contract for contract inspection.

## Tests

```text
367 passing, 6 skipped (live)
```

Network access is blocked at the socket layer for the entire ordinary
suite, so "no test makes a network call" is enforced rather than trusted.
Live tests opt back in with `SUPER6_LIVE_TESTS=1`.

## Blockers before UI work

1. Three source accounts + manual approval (The Odds API, API-Football,
   Tavily). Combined recurring cost: £0.
2. ~~Ollama~~ **DONE** — live verified 2026-08-08 on `llama3.1:8b`.
   The Windows restart cleared the blocked port.
3. A real live verification pass, which will be the first time any payload
   shape is confirmed against a live service rather than a recorded fixture.
4. At least one full engine run against live sources, ideally on a real
   Super 6 round.

Only then should §37+ UI work begin, against the frozen contracts in
`super6/api/contracts.py`.
