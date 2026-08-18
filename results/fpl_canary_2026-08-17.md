# FPL Corpus Harvest — 5-Fixture Live Canary

Run: 2026-08-17, against `data/bym.db`, before any full-scale harvest.
Scope: human-approved 2026-08-17, `historical_player_match_stats` purpose,
2022/23 + 2023/24 Premier League (E0) only — see docs/MANIFEST_FPL.md.

## Report

```text
requested fixtures: 5
successful:          5
player rows:         149
unresolved players:  0
ambiguous players:   0
missing required:    minutes=0, goals=0, assists=0, kickoff_at=0
credits consumed:    8   (2 fixture-list calls + 5 stats calls + 1 idempotent re-fetch)
```

## Fixtures sampled

Different clubs, different dates, one season (2022/23 fixture list had
enough spread on its own for 5 non-overlapping clubs — 2023/24 held in
reserve for the real harvest):

| fixture_id | date | home | away |
|---|---|---|---|
| 867946 | 2022-08-05 | Crystal Palace | Arsenal |
| 867947 | 2022-08-06 | Fulham | Liverpool |
| 867951 | 2022-08-06 | Newcastle | Nottingham Forest |
| 867948 | 2022-08-06 | Bournemouth | Aston Villa |
| 867952 | 2022-08-06 | Tottenham | Southampton |

## Checklist

- **Fixture → player records returned**: yes, 149 rows across 5 fixtures
  (29–32 players per fixture — full squads, both teams).
- **Stable API-Football player IDs**: yes — `external_ids["api_football"]`
  captured per player-season (e.g. Vicente Guaita -> `"18835"`).
- **Home/away/team mapping**: correct for all 5 (verified against the
  fixture list's own home/away labels).
- **Minutes**: present for every row (0 missing).
- **Starts/substitution state**: confirmed — 14 of 29 rows in the
  Crystal Palace v Arsenal fixture alone have `minutes < 90` (e.g. Luka
  Milivojević, 15 min), a real substitution signal, not just full-match
  players.
- **Goals / assists**: present and plausible (e.g. Eric Dier scored for
  Spurs vs Southampton, matching the real result of that fixture).
- **Shots**: present for outfield players who took one; `None` (not `0`)
  for goalkeepers and players without a recorded shot — the parser
  correctly distinguishes "not provided" from "zero", not fabricating
  either direction.
- **Tackles/interceptions**: present for most outfield players; `None`
  for goalkeepers and a handful of outfield rows where the provider
  didn't populate the field — flagged as a real data-quality
  characteristic to quantify in the field-completeness report before
  scoring the model (§18 gate), not a bug in this ingester.
- **`kickoff_at`**: present on every persisted row, matching the fixture
  list's own kickoff time exactly.
- **No future row survives the causal cutoff**: verified against real
  data — `historical_profile(..., before=cutoff)` for a player with only
  one ingested fixture correctly returns `appearances=1`, matching the
  uncut profile exactly (nothing later existed to exclude in this small
  sample); the exclusion mechanism itself is already proven against
  synthetic data by `tests/test_fpl_walk_forward.py` and
  `tests/test_fpl_projection.py`.
- **Idempotent re-fetch**: re-ingesting fixture 867946 changed the stored
  row count for 0 -> 0 (29 before, 29 after) — no duplication.
- **Quota accounting**: 8 credits consumed for 8 actual requests (2
  fixture-list + 5 stats + 1 re-fetch) — exact, no drift.

## Verdict

Clean. Proceeding to the full 2022/23 + 2023/24 harvest.
