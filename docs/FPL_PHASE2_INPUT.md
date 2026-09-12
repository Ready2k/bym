# FPL Phase 2 input (free, local workflow)

Phase 2 needs a small, manager-controlled JSON file at
`data/fpl_phase2_input.json`. It is optional: without it, the FPL dashboard
shows that the horizon planner is ready but makes no future recommendation.

This is not a full-market export. It contains only the current fifteen, any
players you choose to compare, their actual selling/buy values, and a 2–4
Gameweek projection sheet. It can be built from the FPL screens and your own
judgement; no paid service or FPL login automation is required.

```json
{
  "observed_at": "2026-09-01T09:03:00+01:00",
  "gameweeks": [3, 4, 5],
  "bank": 0.0,
  "free_transfers": 2,
  "chips_available": [
    "wildcard_first_half",
    "free_hit_first_half",
    "bench_boost_first_half",
    "triple_captain_first_half"
  ],
  "owned": [
    {
      "name": "Example midfielder",
      "position": "MID",
      "club": "Example Club",
      "selling_price": 5.0,
      "projections": [
        {"gameweek": 3, "expected_points": 3.8, "fixtures": 1, "fixture_status": "CONFIRMED"},
        {"gameweek": 4, "expected_points": 3.5, "fixtures": 1, "fixture_status": "UNCERTAIN"},
        {"gameweek": 5, "expected_points": 0.0, "fixtures": 0, "fixture_status": "CONFIRMED"}
      ]
    }
  ],
  "shortlist": [
    {
      "name": "Named candidate only",
      "position": "MID",
      "club": "Another Club",
      "buy_price": 5.2,
      "projections": [
        {"gameweek": 3, "expected_points": 4.5, "fixtures": 1, "fixture_status": "CONFIRMED"},
        {"gameweek": 4, "expected_points": 4.0, "fixtures": 1, "fixture_status": "UNCERTAIN"},
        {"gameweek": 5, "expected_points": 0.0, "fixtures": 0, "fixture_status": "CONFIRMED"}
      ]
    }
  ],
  "price_risks": [
    {
      "name": "Named candidate only",
      "gameweek": 3,
      "adverse_buy_price": 5.3,
      "probability": 0.4
    }
  ]
}
```

Use 15 legal `owned` players (2 GK, 5 DEF, 5 MID, 3 FWD) and any number of
explicitly named shortlist candidates. Every player needs exactly one
projection for every listed Gameweek. `fixtures` is `0` for a blank and `2`
or higher for a double; uncertain fixture information must say `UNCERTAIN`.

Run the same local planner outside the dashboard with:

```bash
uv run bym fpl-plan --input data/fpl_phase2_input.json
```

The output is always `SHORTLIST_ONLY_HORIZON`. Wildcard and Free Hit remain
watch items until a complete market and comparable alternative squads are
supplied; Bench Boost and Triple Captain are compared against the exact same
no-chip path.
