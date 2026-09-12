# FPL Phase 3 opponent input (free, private-league workflow)

Phase 3 is optional and local. Save a manager-provided file as
`data/fpl_phase3_input.json`, then run:

```bash
uv run bym fpl-relative --input data/fpl_phase3_input.json
```

It compares only the named versions of **your** plan against probability-
weighted versions of each rival's current plan. The rival's last locked squad
must be from an earlier Gameweek: other managers' current pre-deadline moves,
captains and lineups are hidden by FPL and must never be entered as facts.

```json
{
  "observed_at": "2026-09-01T09:03:00+01:00",
  "gameweeks": [3, 4],
  "our_starting_points": 130,
  "our_options": [
    {
      "label": "Hold",
      "squad": ["15 player names"],
      "starters": ["11 starter names"],
      "captains": {"3": "Captain name", "4": "Captain name"}
    },
    {
      "label": "Named shortlist move",
      "squad": ["15 player names after that move"],
      "starters": ["11 starter names"],
      "captains": {"3": "Captain name", "4": "Captain name"}
    }
  ],
  "players": [
    {"name": "Player name", "expected_points": {"3": 4.2, "4": 3.8}}
  ],
  "rivals": [
    {
      "name": "Mate",
      "starting_points": 128,
      "previous_locked_gameweek": 2,
      "previous_locked_squad": ["their 15 locked GW2 names"],
      "transfer_history": [{"gameweek": 2, "transfers": 1, "captain": "Captain name"}],
      "scenarios": [
        {
          "label": "Likely hold",
          "probability": 0.7,
          "squad": ["15 assumed names"],
          "starters": ["11 assumed starters"],
          "captains": {"3": "Captain name", "4": "Captain name"}
        },
        {
          "label": "Possible transfer",
          "probability": 0.3,
          "squad": ["15 assumed names"],
          "starters": ["11 assumed starters"],
          "captains": {"3": "Captain name", "4": "Captain name"}
        }
      ]
    }
  ],
  "trials": 3000,
  "seed": 202627
}
```

Each scenario's probabilities must sum to 1. Each player appearing in an
option or rival scenario needs a projection for every listed Gameweek.

The result is `P(sole league lead after supplied horizon)`, **not** a claim of
season-long `P(finish first)`. Simulations use shared player-outcome and rival-
state worlds across your options; their differences therefore measure the
named decision, not sampling noise. Effective ownership is explicitly for this
private-league cohort and includes captaincy.

After the Gameweek deadline, actual rival squads can be retained as evaluation
evidence only. Do not modify the earlier scenario input or recommendation.
