from __future__ import annotations

import pytest

from bym.fpl import phase2


def _asset(name: str, position: str, *, price_key: str, price: float, points: tuple[float, float] = (3.0, 3.0), fixtures: tuple[int, int] = (1, 1), status: str = "CONFIRMED") -> dict:
    return {
        "name": name, "position": position, "club": f"{name} Club", price_key: price,
        "projections": [
            {"gameweek": 3, "expected_points": points[0], "fixtures": fixtures[0], "fixture_status": status},
            {"gameweek": 4, "expected_points": points[1], "fixtures": fixtures[1], "fixture_status": status},
        ],
    }


def payload() -> dict:
    positions = ["GK", "GK", *(["DEF"] * 5), *(["MID"] * 5), *(["FWD"] * 3)]
    owned = [
        _asset(f"Owned {index}", position, price_key="selling_price", price=5.0,
               points=(1.0, 1.0) if index == 13 else (3.0, 3.0))
        for index, position in enumerate(positions, start=1)
    ]
    return {
        "observed_at": "2026-09-01T09:03:00+01:00", "gameweeks": [3, 4],
        "bank": 0.2, "free_transfers": 2,
        "chips_available": ["bench_boost_first_half", "triple_captain_first_half", "wildcard_first_half", "free_hit_first_half"],
        "owned": owned,
        "shortlist": [_asset("Target FWD", "FWD", price_key="buy_price", price=5.1, points=(8.0, 7.0))],
        "price_risks": [{"name": "Target FWD", "gameweek": 3, "adverse_buy_price": 5.3, "probability": 0.45}],
    }


def test_phase2_selects_the_best_path_only_from_the_named_shortlist():
    plan = phase2.build_plan(phase2.parse_input(payload()))

    assert plan.scope == "SHORTLIST_ONLY_HORIZON"
    assert plan.best_transfer_plan.steps[0].transfer_in == "Target FWD"
    assert plan.best_transfer_plan.steps[0].transfer_out == "Owned 13"
    assert plan.best_transfer_plan.steps[1].transfer_in is None
    assert plan.best_transfer_plan.total_hit_cost == 0.0
    payload_result = phase2.as_payload(plan)
    first = payload_result["best_transfer_plan"]["steps"][0]
    assert first["bank_after"] == pytest.approx(0.1)
    assert first["free_transfers_after"] == 2
    assert {option["label"] for option in payload_result["alternatives"]} >= {
        "Hold", "Owned 13 → Target FWD",
    }
    assert next(option for option in payload_result["alternatives"] if option["selected"])["difference_from_selected"] == 0


def test_phase2_surfaces_budget_invalid_price_risk_and_blank_double_alerts():
    data = payload()
    data["owned"][0]["projections"][0]["fixtures"] = 0
    data["owned"][1]["projections"][1]["fixtures"] = 2
    plan = phase2.build_plan(phase2.parse_input(data))

    assert plan.best_transfer_plan.price_risks[0]["status"] == "BUDGET_INVALID_IF_PRICE_MOVES"
    kinds = {alert["kind"] for alert in plan.best_transfer_plan.fixture_alerts}
    assert {"BLANK", "DOUBLE"} <= kinds


def test_phase2_compares_bench_boost_and_triple_captain_to_the_same_baseline():
    plan = phase2.build_plan(phase2.parse_input(payload()))
    evaluated = [option for option in plan.chip_options if option.status == "EVALUATED"]

    assert {option.chip for option in evaluated} == {"bench_boost_first_half", "triple_captain_first_half"}
    assert all(option.incremental_points is not None and option.incremental_points >= 0 for option in evaluated)
    assert all(option.no_chip_points is not None and option.chip_points is not None for option in evaluated)
    assert any(option.status == "WATCH" and option.chip.startswith("wildcard") for option in plan.chip_options)


def test_phase2_refuses_missing_gameweek_projection():
    data = payload()
    data["shortlist"][0]["projections"].pop()
    with pytest.raises(phase2.Phase2InputError, match="every planned Gameweek"):
        phase2.parse_input(data)
