import pytest

from bym.fpl import autosub
from bym.fpl.optimiser import (
    OptimiserCandidate,
    OptimiserInfeasibleError,
    optimise_squad,
)

PRICE = 4.0  # uniform cheap price so 15 * 4.0 = 60.0 never binds against a 100.0 budget


def candidate(player_id, position, club, points, price=PRICE, p_appearance=1.0):
    return OptimiserCandidate(
        player_id=player_id, position=position, club=club, price=price,
        expected_points=points, p_appearance=p_appearance,
    )


def basic_pool():
    """2 GK / 5 DEF / 5 MID / 3 FWD spread over enough clubs that the
    3-per-club limit never binds, with clearly separated points so the
    optimum is unambiguous."""
    pool = []
    pool += [candidate(1, "GK", "ClubA", 6.0), candidate(2, "GK", "ClubB", 3.0)]
    def_points = [5.0, 4.5, 4.0, 3.5, 3.0]
    for i, pts in enumerate(def_points):
        pool.append(candidate(10 + i, "DEF", f"Club{chr(65 + i)}", pts))
    mid_points = [8.0, 7.0, 6.0, 5.0, 4.0]
    for i, pts in enumerate(mid_points):
        pool.append(candidate(20 + i, "MID", f"Club{chr(70 + i)}", pts))
    fwd_points = [9.0, 6.0, 3.0]
    for i, pts in enumerate(fwd_points):
        pool.append(candidate(30 + i, "FWD", f"Club{chr(75 + i)}", pts))
    return pool


def test_squad_has_legal_composition():
    result = optimise_squad(basic_pool(), bank=100.0)
    assert len(result.squad_ids) == 15
    by_pos = {1: "GK", 2: "GK"}
    positions = {c.player_id: c.position for c in basic_pool()}
    counts = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
    for pid in result.squad_ids:
        counts[positions[pid]] += 1
    assert counts == {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}


def test_starting_xi_satisfies_formation_minimums():
    result = optimise_squad(basic_pool(), bank=100.0)
    positions = {c.player_id: c.position for c in basic_pool()}
    assert len(result.starter_ids) == 11
    counts = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
    for pid in result.starter_ids:
        counts[positions[pid]] += 1
    assert counts["GK"] == 1
    assert counts["DEF"] >= 3
    assert counts["MID"] >= 2
    assert counts["FWD"] >= 1
    assert sum(counts.values()) == 11


def test_captain_is_the_highest_scoring_starter_and_differs_from_vice():
    result = optimise_squad(basic_pool(), bank=100.0)
    points = {c.player_id: c.expected_points for c in basic_pool()}
    best_starter = max(result.starter_ids, key=lambda pid: points[pid])
    assert result.captain_id == best_starter
    assert result.vice_captain_id != result.captain_id
    assert result.vice_captain_id in result.starter_ids


def test_bench_is_the_squad_minus_the_starting_xi():
    result = optimise_squad(basic_pool(), bank=100.0)
    assert set(result.bench_order) == result.squad_ids - result.starter_ids
    assert len(result.bench_order) == 4


def test_initial_build_has_no_transfers_and_no_hit():
    result = optimise_squad(basic_pool(), bank=100.0, current_squad=None)
    assert result.transfers_in == frozenset()
    assert result.transfers_out == frozenset()
    assert result.hit_cost == 0.0
    assert result.net_expected_points == result.xi_expected_points


def test_budget_constraint_excludes_an_unaffordable_squad():
    pool = basic_pool()
    # A 4th, cheap forward gives the solver a real choice: it need not
    # take the expensive top scorer to satisfy the exact FWD=3 quota.
    pool.append(candidate(33, "FWD", "ClubX", 2.0))
    pool = [
        c if c.player_id != 30 else candidate(30, "FWD", "ClubY", 20.0, price=90.0)
        for c in pool
    ]
    # 65.0 comfortably covers 15 cheap (4.0) players (60.0) but not 14
    # cheap players plus the 90.0 forward (146.0).
    result = optimise_squad(pool, bank=65.0)
    total_price = sum(c.price for c in pool if c.player_id in result.squad_ids)
    assert total_price <= 65.0
    assert 30 not in result.squad_ids  # unaffordable within the tight budget


def test_club_limit_caps_selection_at_three():
    pool = basic_pool()
    # Add 5 more, higher-scoring defenders all on one club, on top of the
    # existing 5 — 10 DEF candidates total, exactly 5 required, so the
    # solver must leave at least 2 of the stacked club's out even though
    # they score best, to respect the <=3-per-club cap.
    for i, pts in enumerate([9.0, 8.0, 7.0, 6.0, 5.0]):
        pool.append(candidate(40 + i, "DEF", "Stacked", pts))
    result = optimise_squad(pool, bank=100.0)
    stacked_in_squad = [pid for pid in result.squad_ids if 40 <= pid <= 44]
    assert len(stacked_in_squad) <= 3


def test_transfer_mode_uses_selling_price_not_current_price():
    """§7.2: a retained/sold player's cash effect must use selling price,
    not current price — a player owned when its price has risen must not
    be treated as repurchasable at today's higher price."""
    pool = basic_pool()
    # Own player 10 (a DEF), whose current price has risen to 8.0 but
    # whose selling price (profit-shared) is only 6.0.
    pool = [c if c.player_id != 10 else candidate(10, "DEF", "ClubA", 5.0, price=8.0) for c in pool]
    current_squad = {pid: 6.0 for pid in [1, 10, 20, 21, 22, 23, 24, 30, 31, 32, 2, 11, 12, 13, 14]}
    # A tiny bank forces the optimiser to rely on selling proceeds if it
    # wants to transfer player 10 out for someone else.
    result = optimise_squad(pool, bank=0.1, current_squad=current_squad, free_transfers=5)
    # Regardless of the outcome, the squad must be affordable using
    # selling prices for anyone sold, not current prices.
    sold = current_squad.keys() - result.squad_ids
    bought = result.squad_ids - current_squad.keys()
    proceeds = sum(current_squad[pid] for pid in sold)
    cost = sum(c.price for c in pool if c.player_id in bought)
    assert cost <= 0.1 + proceeds + 1e-9


def test_hit_cost_applied_when_transfers_exceed_free_transfers():
    pool = basic_pool()
    current_squad = {pid: PRICE for pid in [1, 10, 11, 12, 13, 14, 20, 21, 22, 23, 24, 30, 31, 32, 2]}
    # Make an unowned candidate dramatically better so the optimiser
    # wants to transfer for it even at a -4 cost.
    pool.append(candidate(99, "FWD", "ClubZ", 50.0))
    pool = [c for c in pool if c.player_id != 32] + [candidate(32, "FWD", "ClubY", 3.0)]
    result = optimise_squad(pool, bank=100.0, current_squad=current_squad, free_transfers=0)
    assert result.n_transfers >= 1
    assert result.hit_cost == pytest.approx(4.0 * result.n_transfers)
    assert result.net_expected_points == pytest.approx(result.xi_expected_points - result.hit_cost)


def test_no_hit_when_transfers_stay_within_free_transfers():
    pool = basic_pool()
    current_squad = {pid: PRICE for pid in [1, 10, 11, 12, 13, 14, 20, 21, 22, 23, 24, 30, 31, 32, 2]}
    result = optimise_squad(pool, bank=100.0, current_squad=current_squad, free_transfers=15)
    assert result.hit_cost == 0.0


def test_missing_owned_player_from_candidates_is_rejected():
    pool = basic_pool()
    with pytest.raises(ValueError, match="not in candidates"):
        optimise_squad(pool, bank=100.0, current_squad={999: 5.0})


def test_infeasible_pool_raises_a_clear_error():
    pool = [c for c in basic_pool() if c.position != "GK"]  # no goalkeepers at all
    with pytest.raises(OptimiserInfeasibleError):
        optimise_squad(pool, bank=100.0)


# ---- §7.4 autosub/captaincy contingency wiring -----------------------------


def test_evaluated_expected_points_matches_nominal_when_appearance_is_certain():
    """Degenerate sanity check: with every candidate's p_appearance
    defaulted to 1.0 (basic_pool's default), fpl.autosub's contingency
    evaluator has nothing to contest — no player can fail to appear, so
    the evaluated figure must equal the ILP's own nominal figure exactly,
    and the ILP's own captain choice (best nominal scorer) must already
    be what the evaluator would have picked too."""
    result = optimise_squad(basic_pool(), bank=100.0)
    assert result.evaluated_expected_points == pytest.approx(result.xi_expected_points)


def _captaincy_adversarial_pool():
    """A legal 15-player pool where player A (id 201, moderate nominal
    xP, the naive-argmax pick), player B (id 203, lower nominal xP but a
    much higher ceiling and low appearance probability) and player V (id
    202, a reliable third player) are guaranteed into the starting XI by
    dominating their position pools on points, while every other
    candidate is flat filler. Numbers match
    test_fpl_autosub.py::test_optimised_captaincy_beats_naive_highest_nominal_xp_pick
    exactly (naive bonus 7.25 vs best 8.5, captain=B)."""
    pool = [candidate(1, "GK", "ClubA", 3.0), candidate(2, "GK", "ClubB", 1.0)]
    for i in range(5):
        pool.append(candidate(100 + i, "DEF", f"Club{chr(68 + i)}", 1.0))
    pool.append(candidate(201, "MID", "ClubM1", 5.0, p_appearance=0.5))   # A: nominal 5.0
    pool.append(candidate(202, "MID", "ClubM2", 4.32, p_appearance=0.9))  # V: nominal 4.32
    for i in range(3):
        pool.append(candidate(210 + i, "MID", f"ClubM{3 + i}", 1.0))
    pool.append(candidate(203, "FWD", "ClubF1", 4.5, p_appearance=0.2))  # B: nominal 4.5
    for i in range(2):
        pool.append(candidate(220 + i, "FWD", f"ClubF{2 + i}", 1.0))
    return pool


def test_captain_choice_accounts_for_appearance_risk_not_just_nominal_points():
    result = optimise_squad(_captaincy_adversarial_pool(), bank=100.0)
    assert {201, 202, 203} <= result.starter_ids  # A, V and B are all starters
    naive_captain_id = 201  # A has the single highest nominal expected_points
    assert result.captain_id != naive_captain_id
    assert result.captain_id == 203  # B — matches the hand-verified autosub test
    assert result.evaluated_expected_points > result.xi_expected_points


# ---- the global objective: nominal-optimal squad != evaluated-optimal -----
#
# The ILP alone only ever proposes the squad/XI maximising NOMINAL points;
# fpl.autosub's post-processing only re-orders the bench and re-picks
# captain/vice WITHIN that one squad. Neither step searches over
# ALTERNATIVE squads, so a squad that sacrifices a sliver of nominal
# points for a much more reliable bench can be strictly better in
# expectation and yet never get considered. This section builds a pool
# where that gap is deterministic and provable by construction, not by
# hoping the solver's tie-breaking happens to go one way.


def _as_contingency_pool(pool_by_id, pids):
    return [
        autosub.ContingencyPlayer(
            pool_by_id[pid].player_id, pool_by_id[pid].position, pool_by_id[pid].p_appearance,
            pool_by_id[pid].expected_points / pool_by_id[pid].p_appearance,
        )
        for pid in pids
    ]


def _bench_reliability_adversarial_pool():
    """A fixed core (GK, DEF1/DEF2/DEF_risky at the 3-DEF minimum, 5
    certain MID, two clearly-must-keep forwards FWD1/FWD2) plus TWO
    independent swing choices, each with a nominal-preferred option that
    only one of them can jointly afford:

    - FWD3_A (price 8.0, points 3.0) vs FWD3_B (price 1.0, points 2.9) —
      the 3rd forward slot; squad needs exactly 3 FWD from a 4-candidate
      pool {FWD1, FWD2, FWD3_A, FWD3_B}, and FWD1/FWD2's own nominal
      value is high enough that dropping either of THEM (to take both
      FWD3 variants at once) is never nominal-preferred, so this really
      is a straight FWD3_A-vs-FWD3_B choice, not a 3-of-4 free-for-all.
    - The 2 DEF bench reserves: bad (price 4.0 each, p_appearance 0.05)
      vs good (price 6.0 each, p_appearance 0.45). Both are EQUALLY good
      conditional on playing (points_if_appears = 2.0 for both, derived
      from a nominal that is scaled by their own p_appearance) — only
      reliability differs; giving them the same NOMINAL instead would
      inflate the rare bad reserve's conditional value unrealistically
      (a small nominal divided by a small probability). Both reserves'
      own NOMINAL (0.1 and 0.9) is kept below DEF1/DEF2's (1.0) so
      neither is ever nominally preferred as a STARTER over the two
      intended fixed defenders — this is not a bench-only comparison by
      accident, it is one by construction.

    With bank=64.0: FWD3_A + bad reserves costs exactly 64 (fits);
    FWD3_A + good reserves (68) and FWD3_A + mixed reserves (66) do not.
    FWD3_B + {bad, mixed, good} all fit comfortably (<=64 in every case).
    So FWD3_A can ONLY ever pair with bad reserves — the unique nominal
    optimum is exactly the worse-evaluated choice, by construction.
    DEF_risky's 50% non-appearance is only coverable by whichever DEF
    reserves were bought, so the good/bad split directly gates how often
    that gap actually gets filled.
    """
    pool = [
        candidate(1, "GK", "ClubGK1", 1.0),
        candidate(2, "GK", "ClubGK2", 1.0),  # reserve
        candidate(10, "DEF", "ClubD1", 1.0),
        candidate(11, "DEF", "ClubD2", 1.0),
        candidate(12, "DEF", "ClubD3", 5.0, p_appearance=0.5),  # DEF_risky
    ]
    for i in range(5):
        pool.append(candidate(30 + i, "MID", f"ClubM{i}", 1.0))
    pool.append(candidate(40, "FWD", "ClubF1", 5.0))   # FWD1 — must keep
    pool.append(candidate(43, "FWD", "ClubF2", 4.5))   # FWD2 — must keep
    pool.append(candidate(41, "FWD", "ClubF3A", 3.0, price=8.0))   # FWD3_A (swing)
    pool.append(candidate(42, "FWD", "ClubF3B", 2.9, price=1.0))   # FWD3_B (swing)
    pool.append(candidate(20, "DEF", "ClubBad1", 0.1, price=4.0, p_appearance=0.05))
    pool.append(candidate(21, "DEF", "ClubBad2", 0.1, price=4.0, p_appearance=0.05))
    pool.append(candidate(22, "DEF", "ClubGood1", 0.9, price=6.0, p_appearance=0.45))
    pool.append(candidate(23, "DEF", "ClubGood2", 0.9, price=6.0, p_appearance=0.45))
    return pool


ADVERSARIAL_BANK = 64.0


def test_nominal_optimal_squad_is_not_always_evaluated_optimal_by_construction():
    """Ground-truth check (no optimiser involved): confirms the pool
    above really does produce nominal(A) > nominal(B) but
    evaluated(A) < evaluated(B), so the regression below is testing a
    real property of the search, not an accident of solver tie-breaking.
    """
    pool = {c.player_id: c for c in _bench_reliability_adversarial_pool()}
    fixed_ids = [1, 10, 11, 12, 30, 31, 32, 33, 34, 40, 43]  # everyone except the two swings

    def evaluated_for(fwd3_id, bench_ids):
        starters = _as_contingency_pool(pool, fixed_ids + [fwd3_id])
        bench = _as_contingency_pool(pool, bench_ids)
        nominal = sum(c.p_appearance * c.points_if_appears for c in starters)
        order = autosub.optimise_bench_order(starters, bench)
        captain_id, vice_id = autosub.optimise_captaincy(starters)
        evaluated = autosub.expected_squad_points(starters, order, captain_id, vice_id)
        return nominal, evaluated

    nominal_a, evaluated_a = evaluated_for(41, [2, 20, 21])   # FWD3_A + bad DEF reserves
    nominal_b, evaluated_b = evaluated_for(42, [2, 22, 23])   # FWD3_B + good DEF reserves

    assert nominal_a > nominal_b
    assert evaluated_a < evaluated_b


def test_optimiser_finds_the_evaluated_optimal_squad_not_just_the_nominal_one():
    """The regression the review asked for: the ILP's single-shot nominal
    choice (FWD3_A + bad DEF reserves) must not win just because it is
    examined first — `optimise_squad`'s FINAL selection must be the one
    maximising evaluated_expected_points among every legal alternative,
    which by the ground-truth check above is FWD3_B + good DEF reserves,
    even though its nominal figure is strictly lower.
    """
    pool = _bench_reliability_adversarial_pool()
    result = optimise_squad(pool, bank=ADVERSARIAL_BANK)

    # The worse-evaluated, higher-nominal choice must have been rejected.
    assert 41 not in result.squad_ids   # FWD3_A never even bought
    assert 42 in result.starter_ids     # FWD3_B started instead
    assert {22, 23} <= result.squad_ids  # the good DEF reserves
    assert not ({20, 21} & result.squad_ids)  # not the bad ones

    # And the reported figure really is the maximum achievable, not merely
    # relabelled — cross-checked against the independent oracle above.
    pool_by_id = {c.player_id: c for c in pool}
    starters = _as_contingency_pool(pool_by_id, result.starter_ids)
    bench = _as_contingency_pool(pool_by_id, result.squad_ids - result.starter_ids)
    order = autosub.optimise_bench_order(starters, bench)
    captain_id, vice_id = autosub.optimise_captaincy(starters)
    best_possible = autosub.expected_squad_points(starters, order, captain_id, vice_id)

    assert result.evaluated_expected_points == pytest.approx(best_possible)


def test_optimiser_still_finds_the_nominal_optimum_when_top_k_is_one():
    """top_k=1 must reproduce the OLD (nominal-only) behaviour exactly —
    confirms the regression above is really about search width, not a
    change to what a single ILP solve itself computes."""
    pool = _bench_reliability_adversarial_pool()
    result = optimise_squad(pool, bank=ADVERSARIAL_BANK, top_k=1)
    assert 41 in result.starter_ids  # back to the worse, nominal-first pick
    assert {20, 21} <= result.squad_ids
