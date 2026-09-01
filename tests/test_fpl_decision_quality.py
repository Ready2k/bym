import pytest

from bym.backtest import fpl_decision_quality as dq
from bym.fpl.optimiser import OptimiserCandidate, optimise_squad

from test_fpl_optimiser import ADVERSARIAL_BANK, _bench_reliability_adversarial_pool, basic_pool


def candidate(player_id, position, club, points, price=4.0, p_appearance=1.0):
    return OptimiserCandidate(
        player_id=player_id, position=position, club=club, price=price,
        expected_points=points, p_appearance=p_appearance,
    )


def test_legal_xi_by_nominal_xp_respects_formation_minimums():
    pool = basic_pool()
    xi = dq.legal_xi_by_nominal_xp(pool)
    positions = {c.player_id: c.position for c in pool}
    counts = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
    for pid in xi:
        counts[positions[pid]] += 1
    assert len(xi) == 11
    assert counts["GK"] == 1
    assert counts["DEF"] >= 3
    assert counts["MID"] >= 2
    assert counts["FWD"] >= 1


def test_legal_xi_by_nominal_xp_picks_the_highest_value_players():
    pool = basic_pool()
    xi = dq.legal_xi_by_nominal_xp(pool)
    # The single best GK/MID/FWD (unambiguous top scorers in basic_pool)
    # must always make a highest-value XI.
    best_gk = max((c for c in pool if c.position == "GK"), key=lambda c: c.expected_points)
    best_fwd = max((c for c in pool if c.position == "FWD"), key=lambda c: c.expected_points)
    assert best_gk.player_id in xi
    assert best_fwd.player_id in xi


def test_evaluate_captain_choice_computes_gain_over_the_naive_pick():
    starter_nominal_xp = {1: 5.0, 2: 8.0, 3: 3.0}  # player 2 is the naive (highest-xP) captain
    actuals = {1: 10.0, 2: 1.0, 3: 2.0}  # but player 2 blanked; player 1 actually delivered
    result = dq.evaluate_captain_choice(starter_nominal_xp, actuals, chosen_captain_id=1)
    assert result.naive_captain_id == 2
    assert result.chosen_captain_actual_points == 10.0
    assert result.naive_captain_actual_points == 1.0
    assert result.gain_vs_naive == pytest.approx(9.0)


def test_evaluate_xi_choice_computes_gain_over_the_naive_legal_xi():
    pool = basic_pool()
    naive_ids = dq.legal_xi_by_nominal_xp(pool)
    # Give every player a modest actual, then boost one player NOT in the
    # naive XI so a smarter chosen XI can beat it.
    actuals = {c.player_id: 1.0 for c in pool}
    boosted_id = next(c.player_id for c in pool if c.player_id not in naive_ids)
    actuals[boosted_id] = 50.0
    chosen_ids = frozenset(naive_ids - {next(iter(naive_ids))} | {boosted_id})
    result = dq.evaluate_xi_choice(pool, chosen_ids, actuals)
    assert result.naive_xi_actual_points == pytest.approx(11.0)  # 11 players * 1.0
    assert result.chosen_xi_actual_points == pytest.approx(10.0 + 50.0)
    assert result.gain_vs_naive == pytest.approx(49.0)  # (10 + 50) - 11


def test_evaluate_transfers_nets_the_hit_cost():
    actuals = {10: 8.0, 20: 2.0}  # 10 bought in, 20 sold
    result = dq.evaluate_transfers(
        actuals, transfers_in=frozenset({10}), transfers_out=frozenset({20}), hit_cost=4.0
    )
    assert result.gross_gain == pytest.approx(6.0)  # 8 - 2
    assert result.net_gain == pytest.approx(2.0)  # 6 - 4


def test_top_k_sweep_returns_one_result_per_requested_k():
    pool = _bench_reliability_adversarial_pool()
    results = dq.top_k_sweep(pool, bank=ADVERSARIAL_BANK, ks=(1, 5, 10))
    assert [r.top_k for r in results] == [1, 5, 10]


def test_decisions_changed_is_zero_for_identical_selections():
    pool = basic_pool()
    selection = optimise_squad(pool, bank=100.0)
    assert dq.decisions_changed(selection, selection) == 0


def test_decisions_changed_counts_starter_and_captain_differences():
    pool = _bench_reliability_adversarial_pool()
    top1 = optimise_squad(pool, bank=ADVERSARIAL_BANK, top_k=1)
    top10 = optimise_squad(pool, bank=ADVERSARIAL_BANK, top_k=10)
    changed = dq.decisions_changed(top1, top10)
    assert changed > 0  # the adversarial pool is built so these genuinely differ


def test_contingency_vs_nominal_finds_a_genuinely_better_evaluated_squad():
    pool = _bench_reliability_adversarial_pool()
    comparison = dq.contingency_vs_nominal(pool, bank=ADVERSARIAL_BANK, top_k=10)
    assert (
        comparison.contingency_aware.evaluated_expected_points
        > comparison.nominal_only.evaluated_expected_points
    )
    assert comparison.decisions_changed > 0
    # top_k=1 exactly reproduces the pre-review nominal-first behaviour.
    assert 41 in comparison.nominal_only.squad_ids  # FWD3_A, the worse-evaluated pick
    assert 41 not in comparison.contingency_aware.squad_ids
