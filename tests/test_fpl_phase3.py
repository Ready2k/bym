from __future__ import annotations

import pytest

from bym.fpl import phase3


def _plan(label: str, squad: list[str], starters: list[str]) -> dict:
    return {"label": label, "squad": squad, "starters": starters, "captains": {"3": starters[0], "4": starters[0]}}


def payload() -> dict:
    players = [f"P{number}" for number in range(1, 17)]
    standard = players[:15]
    improved = [*players[:10], "P16", *players[11:15]]
    return {
        "observed_at": "2026-09-01T09:03:00+01:00", "gameweeks": [3, 4],
        "our_starting_points": 100,
        "our_options": [
            _plan("Hold", standard, standard[:11]),
            _plan("Named upgrade", improved, [*players[:10], "P16"]),
        ],
        "players": [
            {"name": name, "expected_points": {"3": 8.0 if name == "P16" else 2.0, "4": 8.0 if name == "P16" else 2.0}}
            for name in players
        ],
        "rivals": [{
            "name": "Mate", "starting_points": 101, "previous_locked_gameweek": 2,
            "previous_locked_squad": standard,
            "transfer_history": [{"gameweek": 2, "transfers": 1, "captain": "P1"}],
            "scenarios": [
                {**_plan("Likely hold", standard, standard[:11]), "probability": 0.7},
                {**_plan("Possible upgrade", improved, [*players[:10], "P16"]), "probability": 0.3},
            ],
        }],
        "trials": 500, "seed": 7,
    }


def test_phase3_selects_the_named_option_with_best_predeadline_lead_probability():
    plan = phase3.build_plan(phase3.parse_input(payload()))

    assert plan["scope"] == "PRE_DEADLINE_NAMED_OPTIONS_ONLY"
    assert plan["recommended_option"] == "Named upgrade"
    assert plan["objective"] == "P(sole league lead after supplied horizon)"
    assert plan["options"][0]["effective_ownership"][0]["effective_ownership"] <= 200
    assert "not observed facts" in plan["message"]


def test_phase3_refuses_to_treat_current_gameweek_rival_state_as_known():
    data = payload()
    data["rivals"][0]["previous_locked_gameweek"] = 3

    with pytest.raises(phase3.Phase3InputError, match="previous locked"):
        phase3.parse_input(data)
