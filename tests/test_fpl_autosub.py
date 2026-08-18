import pytest

from bym.fpl.autosub import (
    ContingencyPlayer,
    apply_autosubs,
    expected_captain_bonus,
    optimise_bench_order,
    optimise_captaincy,
    resolve_captain_multiplier,
)


def cp(player_id, position, p_appearance=1.0, points_if_appears=1.0):
    return ContingencyPlayer(
        player_id=player_id, position=position, p_appearance=p_appearance,
        points_if_appears=points_if_appears,
    )


# ---- apply_autosubs: goalkeeper ------------------------------------------


def test_gk_no_show_is_replaced_by_the_reserve_gk():
    g1, g2 = cp(1, "GK"), cp(2, "GK")
    playing = apply_autosubs([g1], g2, [], appeared={2})
    assert playing == [g2]


def test_gk_no_show_with_reserve_also_absent_leaves_no_goalkeeper():
    g1, g2 = cp(1, "GK"), cp(2, "GK")
    playing = apply_autosubs([g1], g2, [], appeared=set())
    assert playing == []


def test_reserve_gk_never_replaces_an_outfield_player():
    g1, g2 = cp(1, "GK"), cp(2, "GK")
    d1 = cp(10, "DEF")
    # g1 appears (no GK gap); g2 (reserve) also happens to have appeared,
    # but must never be used to cover an outfield gap.
    playing = apply_autosubs(
        [g1, d1], g2, [], appeared={1, 2}
    )
    assert g2 not in playing


# ---- apply_autosubs: outfield, single gap ---------------------------------


def test_def_no_show_is_covered_by_the_first_valid_bench_player():
    d1, d2, d3, d4 = cp(10, "DEF"), cp(11, "DEF"), cp(12, "DEF"), cp(13, "DEF")
    m1, m2, f1 = cp(30, "MID"), cp(31, "MID"), cp(40, "FWD")  # satisfy MID/FWD minimums
    bench1, bench2 = cp(20, "DEF"), cp(21, "MID")
    starters = [d1, d2, d3, d4, m1, m2, f1]  # 4 DEF starting -> not at the minimum
    appeared = {10, 11, 12, 30, 31, 40, 20, 21}
    playing = apply_autosubs(starters, None, [bench1, bench2], appeared)
    # d4 (13) is missing; bench1 (20) is first in order and legal -> used.
    assert bench1 in playing
    assert bench2 not in playing


def test_def_no_show_at_exactly_three_starters_requires_a_defender_sub():
    """§7.4's formation-minimum rule: with exactly 3 starting DEF, a
    midfielder cannot legally cover the gap (it would leave DEF at 2),
    so the algorithm must skip a higher-priority MID reserve and use the
    DEF reserve instead."""
    d1, d2, d3 = cp(10, "DEF"), cp(11, "DEF"), cp(12, "DEF")
    m1, m2, f1 = cp(30, "MID"), cp(31, "MID"), cp(40, "FWD")
    mid_reserve = cp(20, "MID")
    def_reserve = cp(21, "DEF")
    starters = [d1, d2, d3, m1, m2, f1]  # DEF exactly at the 3 minimum
    appeared = {10, 11, 30, 31, 40, 20, 21}  # d3 (12) is missing
    playing = apply_autosubs(starters, None, [mid_reserve, def_reserve], appeared)
    assert mid_reserve not in playing  # skipped: would leave DEF at 2
    assert def_reserve in playing


def test_mid_no_show_can_be_covered_by_a_non_mid_reserve_when_legal():
    """A sub does not need to match the missing starter's own position —
    only the resulting formation's minimums matter (§7.4)."""
    d1, d2, d3 = cp(10, "DEF"), cp(11, "DEF"), cp(12, "DEF")
    m1, m2, m3 = cp(30, "MID"), cp(31, "MID"), cp(32, "MID")  # 3 MID, min is 2
    f1 = cp(40, "FWD")
    fwd_reserve = cp(20, "FWD")
    starters = [d1, d2, d3, m1, m2, m3, f1]
    appeared = {10, 11, 12, 30, 31, 40, 20}  # m3 (32) missing
    playing = apply_autosubs(starters, None, [fwd_reserve], appeared)
    assert fwd_reserve in playing  # legal: MID stays at 2, the minimum


def test_bench_player_no_show_falls_through_to_the_next_eligible_reserve():
    d1, d2, d3, d4 = cp(10, "DEF"), cp(11, "DEF"), cp(12, "DEF"), cp(13, "DEF")
    m1, m2, f1 = cp(30, "MID"), cp(31, "MID"), cp(40, "FWD")
    absent_bench = cp(20, "DEF")
    next_bench = cp(21, "DEF")
    starters = [d1, d2, d3, d4, m1, m2, f1]
    appeared = {10, 11, 12, 30, 31, 40, 21}  # d4 (13) missing; absent_bench (20) also missing
    playing = apply_autosubs(starters, None, [absent_bench, next_bench], appeared)
    assert absent_bench not in playing
    assert next_bench in playing


def test_two_no_shows_are_covered_sequentially_by_bench_order():
    d1, d2, d3, d4, d5 = (cp(10 + i, "DEF") for i in range(5))
    m1, m2, f1 = cp(30, "MID"), cp(31, "MID"), cp(40, "FWD")
    b1, b2 = cp(20, "DEF"), cp(21, "DEF")
    starters = [d1, d2, d3, d4, d5, m1, m2, f1]  # 5 DEF starting, well above the 3 minimum
    appeared = {10, 11, 12, 30, 31, 40, 20, 21}  # d4 (13) and d5 (14) both missing
    playing = apply_autosubs(starters, None, [b1, b2], appeared)
    assert b1 in playing and b2 in playing


# ---- captain / vice failover ----------------------------------------------


def test_captain_no_show_promotes_the_vice_multiplier():
    assert resolve_captain_multiplier(captain_id=1, vice_captain_id=2, appeared={2}) == 2


def test_captain_and_vice_no_show_leaves_no_multiplier():
    assert resolve_captain_multiplier(captain_id=1, vice_captain_id=2, appeared=set()) is None


def test_captain_appearing_always_takes_the_multiplier_over_the_vice():
    assert resolve_captain_multiplier(captain_id=1, vice_captain_id=2, appeared={1, 2}) == 1


# ---- bench order beats a naive highest-points-first sort ------------------


def test_optimised_bench_order_beats_naive_points_descending_sort():
    """Constructed so the naive "sort bench by points_if_appears" order
    scores WORSE in expectation than a formation-aware order: a high-
    points MID reserve is useless whenever the gap is actually a
    DEF-at-minimum shortfall (its addition is always rejected until DEF
    is restored), so putting the lower-points DEF reserve first unlocks
    both reserves in the double-gap branch instead of wasting one."""
    d1, d2 = cp(10, "DEF"), cp(11, "DEF")
    d3_risky = cp(12, "DEF", p_appearance=0.5, points_if_appears=5.0)
    m1, m2 = cp(30, "MID"), cp(31, "MID")
    m3_risky = cp(32, "MID", p_appearance=0.5, points_if_appears=4.0)
    f1 = cp(40, "FWD")
    starters = [d1, d2, d3_risky, m1, m2, m3_risky, f1]  # DEF and MID both at their minimum

    bench_def = cp(20, "DEF", points_if_appears=3.0)
    bench_mid = cp(21, "MID", points_if_appears=7.0)  # higher points than bench_def

    from bym.fpl.autosub import _expected_base_points

    naive_value = _expected_base_points(starters, None, [bench_mid, bench_def])
    smart_value = _expected_base_points(starters, None, [bench_def, bench_mid])
    # Both totals include the same constant starters-only baseline (9.5:
    # the two certain DEF/MID/FWD at 1.0 each plus the two risky starters'
    # own E[points] = 0.5*5.0 + 0.5*4.0); the bench's own contribution —
    # verified by hand — is 3.25 (naive) vs 4.0 (smart).
    assert naive_value == pytest.approx(9.5 + 3.25)
    assert smart_value == pytest.approx(9.5 + 4.0)
    assert smart_value > naive_value

    chosen = optimise_bench_order(starters, [bench_mid, bench_def])
    assert chosen == (bench_def, bench_mid)


def test_bench_order_optimiser_places_the_reserve_gk_in_its_own_fixed_slot():
    g1, g2 = cp(1, "GK"), cp(2, "GK")
    d1, d2, d3, d4 = (cp(10 + i, "DEF") for i in range(4))
    m1, m2 = cp(30, "MID"), cp(31, "MID")
    f1 = cp(40, "FWD")
    bench_outfield = cp(50, "MID", points_if_appears=2.0)
    starters = [g1, d1, d2, d3, d4, m1, m2, f1]
    chosen = optimise_bench_order(starters, [g2, bench_outfield])
    assert chosen[0] == g2
    assert chosen[1] == bench_outfield


# ---- captaincy beats a naive highest-nominal-xP sort -----------------------


def test_optimised_captaincy_beats_naive_highest_nominal_xp_pick():
    """A: reliable-ish, moderate ceiling (nominal xP = 5.0, the single
    highest nominal figure -> a naive max(xP) pick chooses A captain).
    B: high-ceiling but risky (nominal xP = 4.5, lower than A's).
    V: a solid, reliable third player usable as vice.

    Pairing the RISKY B with reliable A as vice yields a strictly higher
    expected captain bonus than naively captaining A (with B as the
    next-best-nominal vice) — the ceiling times low-but-nonzero
    appearance probability, cushioned by a strong vice fallback, beats
    nominal xP alone (verified by hand: naive = 7.25, best B-led = 8.5).
    """
    player_a = cp(1, "MID", p_appearance=0.5, points_if_appears=10.0)  # nominal 5.0
    player_b = cp(2, "FWD", p_appearance=0.2, points_if_appears=22.5)  # nominal 4.5
    player_v = cp(3, "MID", p_appearance=0.9, points_if_appears=4.8)  # nominal 4.32
    starters = [player_a, player_b, player_v]

    naive_bonus = expected_captain_bonus(player_a, player_b)  # highest two nominal xP
    assert naive_bonus == pytest.approx(7.25)

    captain_id, vice_id = optimise_captaincy(starters)
    by_id = {p.player_id: p for p in starters}
    optimal_bonus = expected_captain_bonus(by_id[captain_id], by_id[vice_id])

    assert optimal_bonus > naive_bonus
    assert captain_id == player_b.player_id
    assert optimal_bonus == pytest.approx(8.5)


def test_optimise_captaincy_requires_at_least_two_starters():
    with pytest.raises(ValueError):
        optimise_captaincy([cp(1, "MID")])
