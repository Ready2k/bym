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
| The Odds API adapter | BUILT, TESTED OFFLINE | `REVIEW_REQUIRED` — no live call yet |
| API-Football adapter | BUILT, TESTED OFFLINE | `REVIEW_REQUIRED` — no live call yet |
| Tavily adapter | BUILT, TESTED OFFLINE | `REVIEW_REQUIRED` — no live call yet |
| Market movement / divergence | BUILT, TESTED OFFLINE | needs live odds to exercise |
| Scheduler | BUILT, TESTED OFFLINE | restart-safe, SQLite |
| Availability intelligence | BUILT, TESTED OFFLINE | no live feed yet |
| Knowledge agent | BUILT, TESTED OFFLINE | **SHADOW** by default |
| Ollama extractor | BUILT, TESTED OFFLINE | host `desktop-pc` unreachable from the dev machine |
| API read-model contracts | BUILT, TESTED OFFLINE | frozen for the future UI |
| **UI (§37+)** | **DEFERRED — not started** | deliberate |
| **Increment 5 player-impact model** | **DEFERRED — not started** | deliberate |

## Ollama

```text
Host:  desktop-pc  (configurable via OLLAMA_BASE_URL)
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
export OLLAMA_BASE_URL=http://desktop-pc:11434
export OLLAMA_MODEL=llama3.1
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
the_odds_api   BLOCKED  (REVIEW_REQUIRED — awaiting account + approval)
api_football   BLOCKED  (REVIEW_REQUIRED — awaiting account + approval)
tavily         BLOCKED  (REVIEW_REQUIRED — awaiting account + approval)
ollama         FAILED   (desktop-pc does not resolve from the dev machine)
```

Approval remains a manual act:

```bash
super6 sources approve the_odds_api
```

`sky_super6` is permanently `PROHIBITED` and `approve()` refuses it.

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
334 passing, 6 skipped (live)
```

Network access is blocked at the socket layer for the entire ordinary
suite, so "no test makes a network call" is enforced rather than trusted.
Live tests opt back in with `SUPER6_LIVE_TESTS=1`.

## Blockers before UI work

1. Three source accounts + manual approval (The Odds API, API-Football,
   Tavily). Combined recurring cost: £0.
2. Ollama reachable from wherever the engine runs — `desktop-pc` does not
   resolve from the current dev machine.
3. A real live verification pass, which will be the first time any payload
   shape is confirmed against a live service rather than a recorded fixture.
4. At least one full engine run against live sources, ideally on a real
   Super 6 round.

Only then should §37+ UI work begin, against the frozen contracts in
`super6/api/contracts.py`.
