# Phase 2, Increment 3B — API-Football live adapter

Structured availability: confirmed XIs, injuries and suspensions.
Registered as `REVIEW_REQUIRED`; nothing queries it until you register an
account, accept the terms, and run:

```bash
super6 sources approve api_football
```

Then export the key and detect coverage:

```bash
export API_FOOTBALL_KEY=your_key_here
```

```bash
super6 coverage --season 2026
```

## Coverage is detected, never assumed

API-Football states that coverage varies by competition and season, that a
`true` flag does not guarantee every fixture is populated, and that future
competitions can read false until the season starts. So capabilities are
detected per `(league, season)` and persisted, and a worker may query an
endpoint only where coverage is confirmed.

Three states, and the third matters:

```text
AVAILABLE   coverage confirmed → worker may query
MISSING     source does not cover this → feature simply absent
UNKNOWN     not checked yet → different action from MISSING
```

A missing capability is a state, not an error, and emphatically not an
invitation to go looking somewhere unapproved. Tests assert that a worker
facing `MISSING` spends zero requests.

## Injury mapping stays conservative

```text
"Missing Fixture"  → CONFIRMED_ABSENT   / CONFIRMED
"Questionable"     → DOUBT              / REPORTED
anything unknown   → DOUBT              / REPORTED
```

`Questionable` deliberately does **not** become LIKELY or absent — their
own documentation says the player may still play. It is recorded as a
doubt at reported certainty and left to be superseded by the confirmed XI,
which is exactly what the Increment 3 reconciliation ordering is for. A
test asserts `claim_status != LIKELY`.

Unrecognised future `type` values default to the same conservative
mapping rather than being guessed at.

## Source-provided player ids

API-Football supplies stable player identifiers, which are a better
identity than any string match we could compute — so `source_player_id`
short-circuits name resolution entirely and the `J. Williams` ambiguity
problem does not arise for this source. Name resolution remains the path
for sources that supply only names.

## Quota

Free tier: 100 requests/day, 10/minute. Metering is now shared between
both API sources in `sources/quota.py` (The Odds API's monthly budget was
previously a separate implementation — that duplication is removed, since
two divergent budget behaviours is how one of them quietly stops working).

Budget is checked **before dispatch**, then reconciled against the
provider's own remaining-header. Waiting for the header would mean
discovering the limit by exceeding it.

Expected daily usage for a matchday, well inside 100:

```text
injuries    ~4 calls   (one call covers up to 20 fixture ids)
lineups     ~12-18     (six fixtures, polled inside the window)
fixtures    ~2-4
            ~20-30
```

Confirmed-lineup polling only starts 75 minutes before kickoff — earlier
polling spends quota on empty responses, since XIs appear 20-40 minutes
out. A test asserts polling five hours early makes no request.

## The expected-lineup gap, deliberately left open

```text
expected_lineup    MANUAL / later knowledge agent
confirmed_lineup   API-Football
```

Sportmonks' expected-lineups product is ~EUR159/month on top of a ~EUR29
base — absurd for six score predictions among mates (§25). The gap is
architecturally consistent anyway: expected lineups are inherently
uncertain intelligence, confirmed XIs are structured facts, and the model
already treats those differently.

## Registry merge fix

Found while testing: the stored registry file predated `api_football`, and
`load_registry` only fell back to the seed when the file was *absent* — so
a source added in a later release would have been invisible forever, with
`assert_approved` rejecting it as unregistered and no fix but hand-editing
JSON. Seeded sources now merge into an existing registry, arriving at
their seeded status, and **stored statuses always win** so a release can
never silently approve or un-approve something you have already decided
about. Two tests cover both directions.

## Still not verified

**No live network call has been made.** Every test runs against recorded
payload shapes. The first real fetch is the verification step, and it
needs your account and key — for both sources.

## Next

Increment 4: the News/Knowledge Agent, filling what structured APIs
cannot — predicted teams, press-conference context, journalist reports,
and explaining why the structured and market signals moved.
