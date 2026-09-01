import pytest

from bym.fpl.lineup import LineupInputError, recommend_lineup

from test_fpl_optimiser import basic_pool


def test_selects_a_legal_xi_captain_and_bench_from_the_fixed_fifteen():
    squad = basic_pool()

    result = recommend_lineup(squad)

    assert result.squad_ids == frozenset(player.player_id for player in squad)
    assert len(result.starter_ids) == 11
    assert len(result.bench_order) == 4
    assert result.captain_id in result.starter_ids
    assert result.vice_captain_id in result.starter_ids
    assert result.captain_id != result.vice_captain_id
    assert result.transfers_in == frozenset()
    assert result.transfers_out == frozenset()
    assert result.hit_cost == 0.0


def test_rejects_anything_other_than_one_complete_squad():
    with pytest.raises(LineupInputError, match="exactly 15"):
        recommend_lineup(basic_pool()[:-1])
