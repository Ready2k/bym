"""Decision-quality evaluation (SPEC_FPL.md §18 Phase 1B) — the second of
the two pre-registered metric groups, answering "was the DECISION good
given the projections," never "was the projection right" (that split is
`backtest.fpl_metrics`'s job, and the two are deliberately never mixed
into one number).

Every comparison here is against a *simple, nameable* baseline — "highest
nominal xP", "top_k=1" — never a strawman: each baseline is a legitimate
FPL strategy someone could actually run, so beating it means something.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..fpl.optimiser import OptimiserCandidate, SquadSelection, optimise_squad

MIN_DEFENDERS_IN_XI = 3
MIN_MIDFIELDERS_IN_XI = 2
MIN_FORWARDS_IN_XI = 1
XI_SIZE = 11


def legal_xi_by_nominal_xp(candidates: list[OptimiserCandidate]) -> frozenset[int]:
    """The simple baseline XI: from a FIXED 15, pick the 11 highest-
    nominal-`expected_points` players subject to formation legality (1
    GK, >=3 DEF, >=2 MID, >=1 FWD) — no appearance-risk awareness at all.

    Satisfying each position's minimum with its own best players first,
    then filling the remaining slots by pure value regardless of
    position, is optimal for this constraint shape (lower bounds per
    position plus one overall count, no per-position upper bound besides
    GK's exact 1) — no ILP needed for an 11-of-15 sub-problem this simple.
    """
    by_position: dict[str, list[OptimiserCandidate]] = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for c in candidates:
        by_position[c.position].append(c)
    for group in by_position.values():
        group.sort(key=lambda c: c.expected_points, reverse=True)

    selected: list[OptimiserCandidate] = [by_position["GK"][0]]
    selected += by_position["DEF"][:MIN_DEFENDERS_IN_XI]
    selected += by_position["MID"][:MIN_MIDFIELDERS_IN_XI]
    selected += by_position["FWD"][:MIN_FORWARDS_IN_XI]

    remaining = [
        c for c in candidates
        if c.position != "GK" and c not in selected
    ]
    remaining.sort(key=lambda c: c.expected_points, reverse=True)
    selected += remaining[: XI_SIZE - len(selected)]
    return frozenset(c.player_id for c in selected)


@dataclass(frozen=True)
class CaptainEvaluation:
    chosen_captain_id: int
    chosen_captain_actual_points: float
    naive_captain_id: int
    naive_captain_actual_points: float
    gain_vs_naive: float


def evaluate_captain_choice(
    starter_nominal_xp: dict[int, float],
    actual_points_by_player: dict[int, float],
    chosen_captain_id: int,
) -> CaptainEvaluation:
    """§18: 'Captain: realised gain vs highest-xP simple baseline'."""
    naive_captain_id = max(starter_nominal_xp, key=starter_nominal_xp.get)
    chosen_actual = actual_points_by_player.get(chosen_captain_id, 0.0)
    naive_actual = actual_points_by_player.get(naive_captain_id, 0.0)
    return CaptainEvaluation(
        chosen_captain_id=chosen_captain_id,
        chosen_captain_actual_points=chosen_actual,
        naive_captain_id=naive_captain_id,
        naive_captain_actual_points=naive_actual,
        gain_vs_naive=chosen_actual - naive_actual,
    )


@dataclass(frozen=True)
class XIEvaluation:
    chosen_xi_actual_points: float
    naive_xi_actual_points: float
    gain_vs_naive: float


def evaluate_xi_choice(
    candidates: list[OptimiserCandidate],
    chosen_starter_ids: frozenset[int],
    actual_points_by_player: dict[int, float],
) -> XIEvaluation:
    """§18: 'XI: realised points vs simple highest-xP legal XI'."""
    naive_starter_ids = legal_xi_by_nominal_xp(candidates)
    chosen_total = sum(actual_points_by_player.get(pid, 0.0) for pid in chosen_starter_ids)
    naive_total = sum(actual_points_by_player.get(pid, 0.0) for pid in naive_starter_ids)
    return XIEvaluation(
        chosen_xi_actual_points=chosen_total,
        naive_xi_actual_points=naive_total,
        gain_vs_naive=chosen_total - naive_total,
    )


@dataclass(frozen=True)
class TransferEvaluation:
    gross_gain: float
    hit_cost: float
    net_gain: float


def evaluate_transfers(
    actual_points_by_player: dict[int, float],
    transfers_in: frozenset[int],
    transfers_out: frozenset[int],
    hit_cost: float,
) -> TransferEvaluation:
    """§18: 'Transfers: net realised gain after hits'."""
    gross = sum(actual_points_by_player.get(pid, 0.0) for pid in transfers_in) - sum(
        actual_points_by_player.get(pid, 0.0) for pid in transfers_out
    )
    return TransferEvaluation(gross_gain=gross, hit_cost=hit_cost, net_gain=gross - hit_cost)


@dataclass(frozen=True)
class TopKResult:
    top_k: int
    selection: SquadSelection


def top_k_sweep(
    candidates: list[OptimiserCandidate],
    bank: float,
    current_squad: dict[int, float] | None = None,
    free_transfers: int = 0,
    ks: tuple[int, ...] = (10, 25, 50, 100),
) -> list[TopKResult]:
    """§18: 'Search: K=10/25/50/100 stability'. Re-solves once per `k` —
    if the selection and its evaluated_expected_points stop moving well
    before k=100, that stabilisation point is the evidence for freezing
    a smaller default (`fpl.optimiser.DEFAULT_TOP_K`), not a guess."""
    return [
        TopKResult(k, optimise_squad(candidates, bank, current_squad, free_transfers, top_k=k))
        for k in ks
    ]


def decisions_changed(a: SquadSelection, b: SquadSelection) -> int:
    """§18: 'Changes: number of decisions actually changed' — starters
    swapped plus a captain change, a single scalar for how much two
    selections actually differ (e.g. contingency-aware vs nominal-only,
    or between two `top_k` settings), not just whether their headline
    xP figures differ."""
    starter_diff = len(a.starter_ids.symmetric_difference(b.starter_ids))
    captain_diff = 0 if a.captain_id == b.captain_id else 1
    return starter_diff + captain_diff


@dataclass(frozen=True)
class ContingencyComparison:
    nominal_only: SquadSelection  # top_k=1, the pre-review optimiser behaviour
    contingency_aware: SquadSelection
    decisions_changed: int


def contingency_vs_nominal(
    candidates: list[OptimiserCandidate],
    bank: float,
    current_squad: dict[int, float] | None = None,
    free_transfers: int = 0,
    top_k: int = 10,
) -> ContingencyComparison:
    """§18: 'Optimiser: contingency-aware vs nominal optimiser' — `top_k=1`
    reproduces the exact pre-review single-shot behaviour (verified by
    `test_optimiser_still_finds_the_nominal_optimum_when_top_k_is_one`),
    so this is a genuine like-for-like comparison, not two different
    code paths."""
    nominal_only = optimise_squad(candidates, bank, current_squad, free_transfers, top_k=1)
    contingency_aware = optimise_squad(
        candidates, bank, current_squad, free_transfers, top_k=top_k
    )
    return ContingencyComparison(
        nominal_only=nominal_only,
        contingency_aware=contingency_aware,
        decisions_changed=decisions_changed(nominal_only, contingency_aware),
    )
