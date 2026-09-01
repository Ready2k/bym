import pytest

from bym.fpl.shortlist import SHORTLIST_ONLY, ShortlistInputError, recommend_from_shortlist

from test_fpl_optimiser import basic_pool, candidate


def _selling_prices(squad):
    return {player.player_id: player.price for player in squad}


def test_recommends_only_a_feasible_shortlisted_same_position_transfer():
    squad = basic_pool()
    target = candidate(99, "DEF", "ClubZ", points=12.0, price=4.0)

    advice = recommend_from_shortlist(
        squad, _selling_prices(squad), [target], bank=0.0, free_transfers=1
    )

    assert advice.scope == SHORTLIST_ONLY
    assert advice.recommended.transfer_in_id == 99
    outgoing = next(player for player in squad if player.player_id == advice.recommended.transfer_out_id)
    assert outgoing.position == "DEF"
    assert advice.recommended.transfer_hit_cost == 0.0
    assert advice.recommended.selection.transfers_in == frozenset()
    assert advice.recommended.selection.transfers_out == frozenset()


def test_hold_wins_when_the_shortlisted_transfer_is_not_an_improvement():
    squad = basic_pool()
    target = candidate(99, "DEF", "ClubZ", points=0.1, price=4.0)

    advice = recommend_from_shortlist(
        squad, _selling_prices(squad), [target], bank=0.0, free_transfers=1
    )

    assert advice.recommended.is_hold


def test_excludes_unaffordable_and_three_per_club_shortlisted_changes():
    squad = [
        candidate(11, "DEF", "ClubA", points=4.5, price=4.0)
        if player.player_id == 11 else player
        for player in basic_pool()
    ]
    unaffordable = candidate(99, "DEF", "ClubZ", points=100.0, price=99.0)
    # ClubA already has its goalkeeper and three defenders.  Only replacing a
    # ClubA defender can avoid creating a fourth player from that club.
    capped = candidate(100, "DEF", "ClubA", points=0.1, price=4.0)

    advice = recommend_from_shortlist(
        squad, _selling_prices(squad), [unaffordable, capped], bank=0.0, free_transfers=1
    )

    assert advice.recommended.is_hold
    assert len(advice.options) == 3  # hold + two legal ClubA-for-ClubA swaps
    assert all(option.transfer_in_id != 99 for option in advice.options)


def test_requires_selling_prices_for_every_owned_player():
    squad = basic_pool()
    selling_prices = _selling_prices(squad)
    del selling_prices[1]

    with pytest.raises(ShortlistInputError, match="missing selling price"):
        recommend_from_shortlist(squad, selling_prices, [], bank=0.0, free_transfers=1)


def test_applies_a_hit_when_no_free_transfer_remains():
    squad = basic_pool()
    target = candidate(99, "DEF", "ClubZ", points=20.0, price=4.0)

    advice = recommend_from_shortlist(
        squad, _selling_prices(squad), [target], bank=0.0, free_transfers=0
    )

    assert advice.recommended.transfer_in_id == 99
    assert advice.recommended.transfer_hit_cost == 4.0
    assert advice.recommended.net_expected_points == pytest.approx(
        advice.recommended.selection.net_expected_points - 4.0
    )
