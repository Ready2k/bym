# Phase 2, Increment 2 — Market movement + scheduler

Detects statistically meaningful market changes, decides whether a
forecast needs recalculating, and explains what changed. It does **not**
adjust probabilities.

## The double-counting rule

The frozen model already consumes the latest consensus through its
`w = 0.5` blend, so a moved market changes `P(score)` mechanically on the
next forecast run. The movement detector therefore controls **attention**,
never probabilities:

```text
new odds snapshot
        ↓
market blend changes P(score)     ← automatic, frozen, unchanged
        ↓
movement detector decides:        ← this increment
    activity record?
    immediate reforecast?
    alert?
```

Nothing in this increment touches λ, `w`, or any model parameter. Applying
a second market adjustment would count the same information twice (§32).

## Movement measurement

Measured in **de-vigged consensus probability space**, never raw decimal
odds. The primary human-facing figure is the absolute percentage-point
change; relative change is recorded but never drives classification,
because `2% → 3%` is `+50%` and means nothing.

Reference points, each independently available or absent:

```text
previous_poll      the immediately preceding snapshot
first_observed     the earliest consensus WE hold
1.5h_ago / 6h_ago / 24h_ago
```

`first_observed` is deliberately not called "opening odds". We never saw
the bookmakers' opening price; we saw our first poll. A reference with no
snapshot within tolerance is reported missing rather than substituted with
whatever happens to be nearest.

## Strength and agreement stay separate

```text
movement_strength   how far the consensus moved (max |Δpp| across H/D/A)
market_agreement    how much the bookmakers agree (from dispersion)
```

Never collapsed into one `MARKET_SCORE = 78`. A 7pp move with tight
agreement is a real signal; the same move with wide dispersion may just
mean half the books have updated. Averaging them destroys exactly the
distinction that makes the second case suspicious. A test asserts the two
move independently.

Materiality bands are **provisional operational config**, carrying no
model-training claim:

```text
movement_threshold_pp        3.0   → MATERIAL
urgent_movement_threshold_pp 6.0   → MAJOR
dispersion_threshold         0.05
high_dispersion_threshold    0.10
```

Calibrate these from the movement distribution observed prospectively
during 2026/27 — not from football folklore.

## market_divergence — two different things

§20's confidence ingredient is now non-placeholder, and stores both
observations separately:

```text
model_vs_market   total variation distance between the PRE-calibration
                  Dixon-Coles H/D/A and the market consensus.
                  "Our football model disagrees with the bookmakers."

book_dispersion   spread across bookmakers.
                  "The bookmakers disagree with each other."
```

Using the pre-calibration model probabilities matters: comparing the
*blended* output to the market would measure how hard `w` pulled, which is
a property of the blend, not a disagreement. A test pins this.

The derived `divergence_factor` is a category (LOW/MEDIUM/HIGH/UNKNOWN),
not a scalar — §20 requires CONF itself to be categorical, and a
precise-looking number from two loosely-calibrated inputs would imply
accuracy that does not exist. Full five-factor CONF still awaits
calibration testing (§16).

## Structured events

```text
MARKET_SNAPSHOT          routine, hidden from the activity feed by default
MARKET_MOVEMENT          material move
MARKET_MAJOR_MOVEMENT    urgent move
FORECAST_RECALCULATED    a fixture was reforecast
PREDICTION_CHANGED       the recommended score changed
CONFIDENCE_CHANGED       CONF changed (separately — §20, §68)
POLL_FAILED              no market view, or the poll itself failed
```

Emitted as JSONL with fields already separated, so the eventual UI reads
them directly instead of reverse-engineering meaning from timestamps.
Prediction and confidence changes are distinct events because probability
and confidence are distinct dimensions — a moved market can change
`P(score)` without changing CONF.

## Scheduler

**Interim deviation from §26:** SQLite, not Postgres. For a single-user
system running ~10 jobs a week, SQLite gives identical restart-safety
(ACID, transactional claim) with no server to operate, and the schema and
SQL port to Postgres by changing the connection and placeholder style.
§26's intent — jobs persisted, `next_run_at` recomputed as the deadline
approaches — is met. Recorded here rather than quietly substituted.

```text
jobs(id, job_type, round_number, round_path, deadline_at,
     next_run_at, status, attempt_count, last_run_at, last_error)
```

Restart safety: jobs are claimed transactionally before running, and any
job left `RUNNING` by a crash is reclaimed to `PENDING` on startup. A
failing poll logs `POLL_FAILED`, records the error, and reschedules rather
than killing the loop. Polling retires itself once the deadline passes.

```bash
super6 watch --round rounds/round_01.json --seasons 2021 2026 --data data --loop
```

```bash
super6 jobs
```

```bash
super6 activity
```

For unattended operation, run `super6 tick` from cron or systemd instead
of `--loop`; state lives in the database either way.

## Acceptance criteria

> Given an active manually configured round, the system automatically
> polls approved odds sources on the deadline cadence, persists timestamped
> bookmaker and consensus snapshots, detects material consensus movement,
> recalculates affected forecasts only when required, records
> model-vs-market divergence and bookmaker dispersion, and produces
> structured activity/change events without double-counting market
> information in the forecasting model.

Met. All listed test cases pass:

| Case | Test |
|---|---|
| small move → snapshot only | `test_small_move_is_snapshot_only` |
| large move → reforecast | `test_large_move_triggers_reforecast` |
| large move + same pick → no alert | `test_large_move_same_pick_emits_no_prediction_change` |
| large move + different pick | `test_large_move_different_pick_emits_prediction_changed` |
| stale book → median stable, dispersion up | `test_one_stale_book_keeps_median_stable` |
| all books move together → low dispersion | `test_all_books_moving_together_keeps_dispersion_low` |
| quota low → 30m cadence | `test_quota_low_degrades_cadence` |
| restart → polling continues | `test_restart_continues_scheduled_polling` |
| deadline passed → polling stops | `test_polling_stops_after_deadline` |

Still outstanding, as in Increment 1: **the live network path is
unverified** pending an API key, and `the_odds_api` remains
`REVIEW_REQUIRED` until you register and approve it.

## Next

Increment 3 — expected/confirmed lineups, injuries and suspensions. That
is when market movement can stop merely reporting *that* Newcastle went
22% → 29% and start attempting to answer *why*.
