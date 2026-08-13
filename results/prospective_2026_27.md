# Prospective evaluation — 2026/27

There is no unspent holdout season left. From Round 1, the only honest
evaluation is prospective: each arm's picks are recorded **before** the
deadline and scored when results arrive. Nothing is ever reconstructed
afterwards, because a pick you can reconstruct is a pick you can
accidentally improve.

Ledger: `results/prospective_ledger.csv` (append-only).

## Arms

Arms are rows, not columns, so later phases append without disturbing the
frozen comparison.

| Arm | Meaning | From |
|---|---|---|
| `PHASE1_MODEL_V1` | Frozen quantitative core, pinned to `config.FROZEN_PHASE1_V1` | Round 1 |
| `MARKET_AVAILABLE` | Favourite from the odds recorded at prediction time | Round 1 |
| `SUBMITTED` | What was actually entered into Super 6 | Round 1 |
| `PHASE2_AGENT` | Always-on intelligence layer | Phase 2 |
| `PHASE3_STRATEGY` | Opponent-aware strategic picks | Phase 3 |

`MARKET_AVAILABLE` is *exact* here, not the Friday-price proxy used in
backtesting: the odds in the round file existed before the deadline by
construction. This is a better benchmark than anything the historical
corpus could provide.

`PHASE1_MODEL_V1` is pinned to a separate frozen `ModelConfig` instance
rather than `DEFAULT_MODEL`. Phase 2+ will change the defaults; without
the pin, the baseline arm would drift and the agent would end up being
compared against itself. A test asserts the pinned values match
MANIFEST.md.

## Weekly workflow

Before the deadline, with odds filled into the round file:

```bash
bym record --round rounds/round_01.json --seasons 2021 2026 --data data --season 2026
```

Then, once picks are actually entered in Super 6, record what was
submitted (which may differ from the recommendation — that difference is
the point):

```bash
bym record --round rounds/round_01.json --seasons 2021 2026 --data data --season 2026 --submitted 2-0 1-0 2-1 1-2 3-0 1-2
```

After the matches, with `null` for any VOID fixture (§2.2):

```bash
bym score --round 1 --season 2026 --results round_01_results.json
```

Running comparison at any time:

```bash
bym ledger
```

## Rules

1. **Recording is append-only.** `record` refuses to overwrite an arm
   already recorded for a round. Correcting a genuine mistake means
   editing the CSV by hand and documenting why.
2. **VOID fixtures are excluded, never zeroed** (§2.2). A postponed match
   is not a wrong prediction, and counting it as one would understate
   every arm equally but wrongly.
3. **No Phase 2 or Phase 3 component earns the word "improvement"** until
   its arm demonstrates it here, prospectively, over enough rounds. Live
   forecasts may change from Round 1; claims may not.
4. **Sample size discipline.** Six fixtures a week is a thin sample: a
   single round tells you nothing, and ten rounds is 60 fixtures — still
   less than a tenth of the 932-fixture season used for the holdout, where
   a ±0.5 pts/round confidence interval was the best available. Expect to
   need most of a season before any arm difference is distinguishable
   from noise, and resist reading the ledger as a scoreboard before then.

## Results

_No rounds recorded yet — 2026/27 has not started._
