# BASELINE_FPL_001 — FROZEN FPL BASELINE MANIFEST

Frozen: 2026-08-17, before any walk-forward evaluation has looked at a
single result. Mirrors `MANIFEST.md`'s discipline for
`PHASE1_MODEL_V1` — this document defines exactly what was frozen. Any
change to the scope below produces a *different* baseline and must be
recorded as a new version (`FPL_002`, ...), never as a silent edit to
this one, per SPEC_FPL.md §18's "features should have to explain why they
deserve electricity" and its explicit gate: *"then do not change it while
evaluating it."*

## Scope

```text
MATCH ENVIRONMENT
    FROZEN_PHASE1_V1 (config.py) — consumed, never modified (§19)
    team goal lambda_home / lambda_away
    clean-sheet probabilities (p_home_clean_sheet / p_away_clean_sheet)
    — all three read off FixtureOutlook (lms.py), computed once per
      gameweek and reused for every player (§6, §19)

PLAYER MODEL  (fpl/projection.py)
    historical minutes (HistoricalProfile.expected_minutes)
    p_appearance
    p_start            (proxy: p_60_plus — no confirmed/expected lineup
                         signal feeds this ingester yet)
    p_60_plus
    historical goal share    (goal_lambda = team_lambda * goal_share)
    historical assist share  (assist_lambda = team_lambda * assist_share)

SCORING  (fpl/scoring.py)
    appearance points (1 / 2, the 60-minute threshold)
    goals by position (GK 10, DEF 6, MID 5, FWD 4)
    assists (3, position-independent)
    clean sheets by position, gated on p_60_plus
    GK saves (+1 per 3), where the ingested source supports it

OMITTED (explicitly, not silently)  — ExpectedPointsBreakdown.omitted_components
    xG/xA enhancement
    bonus
    DefCon
    cards / own goals / penalty misses / goals-conceded deduction

DECISION LAYER  (fpl/optimiser.py, fpl/autosub.py)
    legal 15-player squad (2 GK / 5 DEF / 5 MID / 3 FWD, <=3/club)
    legal starting XI (1 GK, >=3 DEF, >=2 MID, >=1 FWD)
    transfer economics (§7.2 cash-flow budget, §7.3 hit cost)
    appearance-aware autosubs (fpl.autosub.apply_autosubs)
    optimised bench order (fpl.autosub.optimise_bench_order)
    captain/vice contingency (fpl.autosub.optimise_captaincy)
    top-K squad-candidate search (default top_k=10, PROVISIONAL — see
        "Open item" below) so nominal squad selection is not blindly
        trusted to also maximise evaluated_expected_points
```

## Explicit modelling assumptions (not hidden in the maths)

- **Player appearance events are independent Bernoulli trials**
  (`fpl/autosub.py`). No correlation between teammates' fitness — same-
  team rotation, match postponement, clustered injury/illness, and
  goalkeeper/backup relationships are all known, named simplifications,
  not modelled. A correlated-appearance model is a future `FPL_00N`
  challenger, evaluated against this baseline like any other refinement,
  never assumed silently.
- **Team goal-share/assist-share rates are unconditional-per-team-fixture
  averages** (`fpl/projection.py`), derived from summed player-level
  goals across all of a team's sampled fixtures — not a separate
  team-goals table. Undercounts goals scored via an opponent's own goal
  (not attributed to anyone by this ingester).
- **`points_if_appears` is the algebraic inverse of the unconditional
  rate** (`expected_points / p_appearance`), used only inside the
  autosub/captaincy evaluator — not a new model, the exact inverse of how
  the unconditional rate was built.

## Entry point

`bym.fpl.projection.MODEL_VERSION == "BASELINE_FPL_001"` — every
`FixtureProjection`/`GameweekProjection` this baseline produces carries
this stamp. `bym.fpl.optimiser.optimise_squad` is the frozen decision
entry point; its `top_k` default (10) is provisional pending the
stability sweep below, not itself part of what "frozen" protects — only
the scoring/projection/autosub *logic* is frozen, the same way
`refit_every_days` is a backtest parameter, not part of `PHASE1_MODEL_V1`
itself.

## Provenance

```text
Test suite:   973 tests passing when the BASELINE_FPL_001 scope above was
              frozen (tests/test_fpl_*.py + the FixtureOutlook boundary
              regression in tests/test_lms.py). Phase 1B's walk-forward
              harness, persistence, metrics, decision-quality and
              prospective-recording infrastructure landed afterward on
              top of this same frozen scope — 1026 passing at that point
              — without changing anything listed above.
Git:          uncommitted at freeze time — record the commit SHA here
              once this work lands, matching MANIFEST.md's convention
              of pinning provenance to a real commit, not a working tree
```

## Open item — historical data corpus is NOT yet frozen

Unlike `PHASE1_MODEL_V1` (which had real, hashed CSV files to pin), this
baseline's CODE is frozen but its **input corpus is not yet pulled**:
`fpl.ingest.ingest_fixture_player_stats` exists and is tested against
mocked payloads, but no live `api_football` historical pull (2022-2024,
the approved free-tier scope — §5.2, §5.6) has actually run against this
codebase's database. Walk-forward evaluation results reported before that
pull happens are necessarily synthetic/illustrative, not this baseline's
real performance.

Pulling the real corpus spends real, metered `api_football` quota
(100 requests/day) against the user's account — that is a live-resource
decision, not a code change, and is called out separately rather than
run silently as part of "freezing the baseline."

## Interpretation thresholds — to be agreed before the corpus pull

Following `MANIFEST.md`'s discipline of recording interpretation
thresholds *before* seeing results: Phase 1B's metrics table (§18) should
have its "good enough to keep" bar agreed once the real corpus size is
known (a walk-forward evaluation over a handful of fixtures cannot
support the same confidence as one over several seasons), not retrofitted
after the numbers come in.
