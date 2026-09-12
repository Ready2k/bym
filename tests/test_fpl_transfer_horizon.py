from __future__ import annotations

import pytest

from bym.fpl.transfer_horizon import (
    Transfer,
    TransferHorizonError,
    TransferState,
    TransferTarget,
    HorizonStep,
    OwnedPlayer,
    plan_shortlist_horizon,
    transition,
)


def _squad() -> tuple[OwnedPlayer, ...]:
    positions = ["GK", "GK", *(["DEF"] * 5), *(["MID"] * 5), *(["FWD"] * 3)]
    return tuple(
        OwnedPlayer(player_id=index, position=position, club=f"Club {index}", selling_price=5.0)
        for index, position in enumerate(positions, start=1)
    )


def _state(*, gameweek: int = 5, free_transfers: int = 2, bank: float = 1.0, last_chip: str | None = None) -> TransferState:
    return TransferState(
        gameweek=gameweek,
        squad=_squad(),
        bank=bank,
        free_transfers=free_transfers,
        chips_available=frozenset({
            "wildcard_first_half", "free_hit_first_half", "bench_boost_first_half",
            "triple_captain_first_half", "wildcard_second_half", "free_hit_second_half",
            "bench_boost_second_half", "triple_captain_second_half",
        }),
        last_chip=last_chip,
    )


def _move(outgoing_id: int, incoming_id: int, position: str, *, club: str = "New Club", price: float = 5.5) -> Transfer:
    return Transfer(outgoing_id, TransferTarget(incoming_id, position, club, price))


def test_normal_transfers_use_selling_value_and_bank_the_remainder():
    state = _state(free_transfers=2, bank=0.5)
    result = transition(state, HorizonStep((_move(3, 99, "DEF", price=5.4),)))

    assert result.hit_cost == 0.0
    assert result.state_after.bank == pytest.approx(0.1)
    assert result.state_after.free_transfers == 2  # one carried, plus next GW's allocation
    assert {player.player_id for player in result.state_after.squad} >= {99}
    assert next(player for player in result.state_after.squad if player.player_id == 99).selling_price == 5.4


def test_extra_transfers_take_a_hit_and_reset_next_week_to_one_free_transfer():
    state = _state(free_transfers=2, bank=5.0)
    moves = (
        _move(3, 101, "DEF"),
        _move(8, 102, "MID"),
        _move(13, 103, "FWD"),
    )
    result = transition(state, HorizonStep(moves))

    assert result.hit_cost == 4.0
    assert result.state_after.free_transfers == 1


@pytest.mark.parametrize("chip", ["wildcard_first_half", "free_hit_first_half"])
def test_wildcard_and_free_hit_preserve_banked_free_transfers(chip: str):
    state = _state(free_transfers=2, bank=1.0)
    result = transition(state, HorizonStep((_move(3, 99, "DEF", price=5.5),), chip=chip))

    assert result.hit_cost == 0.0
    assert result.state_after.free_transfers == 3


def test_wildcard_changes_the_permanent_squad_but_free_hit_reverts_it():
    wildcard = transition(_state(), HorizonStep((_move(3, 99, "DEF"),), chip="wildcard_first_half"))
    free_hit = transition(_state(), HorizonStep((_move(3, 99, "DEF"),), chip="free_hit_first_half"))

    assert 99 in {player.player_id for player in wildcard.state_after.squad}
    assert 99 not in {player.player_id for player in free_hit.state_after.squad}
    assert free_hit.state_after.bank == 1.0


def test_free_hit_is_blocked_in_gw1_and_consecutively():
    with pytest.raises(TransferHorizonError, match="Gameweek 1"):
        transition(_state(gameweek=1), HorizonStep(chip="free_hit_first_half"))
    with pytest.raises(TransferHorizonError, match="consecutive"):
        transition(
            _state(last_chip="free_hit_first_half"),
            HorizonStep(chip="free_hit_first_half"),
        )


def test_horizon_is_limited_to_two_through_four_gameweeks():
    state = _state()
    plan = plan_shortlist_horizon(state, (HorizonStep(), HorizonStep()))

    assert plan.scope == "SHORTLIST_ONLY_HORIZON"
    assert [item.gameweek for item in plan.transitions] == [5, 6]
    assert plan.total_hit_cost == 0.0
    with pytest.raises(TransferHorizonError, match="2 to 4"):
        plan_shortlist_horizon(state, (HorizonStep(),))
