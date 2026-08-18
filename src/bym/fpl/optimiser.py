"""Single-Gameweek squad/XI/bench/captain optimiser (docs/SPEC_FPL.md §7).

An ILP over the candidate universe (`fpl.game_state.CandidatePrice`) and a
Gameweek's expected points (`fpl.aggregation.GameweekProjection`), solved
with PuLP/CBC rather than hand-rolled search — §18's explicit direction
("an ILP/MIP library, not a hand-rolled solver").

Two things this module is careful to get right because §7.2/§7.3 call out
a documented example of getting them wrong:

- **Budget is a transfer cash-flow constraint, not squad repricing.**
  Retained players never appear on either side of the budget inequality —
  only players actually bought (candidates not currently owned) draw
  down the bank, and only players actually sold (owned players not kept)
  add to it via their *selling* price, which is not the same as their
  *current* price once it has risen while owned (§7.2).
- **Transfer cost is `-4` per PAID transfer, not per net squad change.**
  `paid_transfers` is a solver variable bounded below by
  `n_transfers - free_transfers` and by 0 — the standard LP encoding of
  `max(0, ...)` inside a maximisation objective, since the solver has no
  incentive to report a paid transfer higher than the constraint forces
  (§7.3).

**A single ILP solve is not enough**, and this module used to stop there —
a review adversarial test proved it: the ILP's objective only ever scores
STARTERS (`expected_points` on `y`), never bench members, so bench
composition is entirely invisible to it. Two squads can therefore tie (or
one can even strictly beat the other) on nominal points while differing
hugely in bench *reliability* — a squad that sacrifices a sliver of
nominal points for two much-more-available reserves can legitimately
score higher once §7.4's appearance contingencies are accounted for, and
a single-shot solve has no way to discover that, because it never looks
past the first (nominal-best) squad it finds.

The fix is deterministic top-K squad enumeration, not folding §7.4's
appearance contingencies into the solver's own objective (that would
trade a small, fast MILP for "the entire FPL universe encoded in PuLP"):
solve once, add a "no-good cut" excluding the exact 15-player set just
found (`sum(x for x in that squad) <= 14` — at least one member must
change next time), re-solve, repeat up to `top_k` times or until no
further distinct squad is feasible. Each of the (at most `top_k`)
resulting (squad, nominal-XI) pairs then goes through `fpl.autosub`'s
bench-order/captaincy re-optimisation, and the FINAL answer is whichever
one maximises net evaluated points — not necessarily the first (nominal-
best) one found. `top_k` trades search thoroughness for solve time; Phase
1B is where its sensitivity (10/25/50/100) gets measured against how much
the final selection actually moves, so the smallest safe value can be
frozen deliberately rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

import pulp

from . import autosub

SQUAD_COMPOSITION = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
MAX_PER_CLUB = 3
XI_SIZE = 11
MIN_DEFENDERS_IN_XI = 3
MIN_MIDFIELDERS_IN_XI = 2
MIN_FORWARDS_IN_XI = 1
TRANSFER_HIT_COST = 4.0

# How many distinct nominal-ranked (squad, XI) candidates to generate
# before picking whichever evaluates best under §7.4's contingencies.
# Provisional — Phase 1B's stability sweep (10/25/50/100) is what should
# actually justify this number; it is not backtested.
DEFAULT_TOP_K = 10


class OptimiserInfeasibleError(RuntimeError):
    """No legal squad/XI satisfies every hard constraint — e.g. too few
    candidates at a position, or a budget too tight to reach 15 legal
    players. Not raised for "the optimum is a low score"; only for "there
    is no feasible point at all"."""


@dataclass(frozen=True)
class OptimiserCandidate:
    """One player as the optimiser sees them: current buy price (not
    selling price — see the budget note above), this Gameweek's
    unconditional expected points, and their probability of any
    appearance at all — the latter feeds the post-solve autosub/captaincy
    evaluator (`fpl.autosub`), never the ILP objective itself."""

    player_id: int
    position: str  # GK | DEF | MID | FWD
    club: str
    price: float
    expected_points: float
    p_appearance: float = 1.0


@dataclass(frozen=True)
class SquadSelection:
    squad_ids: frozenset[int]
    starter_ids: frozenset[int]
    bench_order: tuple[int, ...]  # reserve GK first, then outfield priority order
    captain_id: int
    vice_captain_id: int
    transfers_in: frozenset[int]
    transfers_out: frozenset[int]
    hit_cost: float
    xi_expected_points: float  # this candidate's naive nominal figure: starters + captain bonus
    evaluated_expected_points: float  # fpl.autosub's contingency-evaluated figure, pre-hit
    net_expected_points: float  # evaluated_expected_points - hit_cost

    @property
    def n_transfers(self) -> int:
        return len(self.transfers_in)


@dataclass(frozen=True)
class _NominalSolution:
    """One ILP solve's raw output — a candidate (squad, nominal XI,
    nominal captain/vice) pair, before `fpl.autosub` ever gets a say."""

    squad_ids: frozenset[int]
    starter_ids: frozenset[int]
    hit_cost: float
    xi_expected_points: float
    transfers_in: frozenset[int]
    transfers_out: frozenset[int]


def _lp_status_name(status: int) -> str:
    return pulp.LpStatus[status]


def _as_contingency(pid: int, by_id: dict[int, OptimiserCandidate]) -> autosub.ContingencyPlayer:
    c = by_id[pid]
    points_if_appears = c.expected_points / c.p_appearance if c.p_appearance > 0 else 0.0
    return autosub.ContingencyPlayer(
        player_id=c.player_id, position=c.position,
        p_appearance=c.p_appearance, points_if_appears=points_if_appears,
    )


def _solve_once(
    candidates: list[OptimiserCandidate],
    bank: float,
    current_squad: dict[int, float] | None,
    free_transfers: int,
    excluded_squads: list[frozenset[int]],
) -> _NominalSolution | None:
    """One ILP solve for the legal (squad, nominal XI, nominal captain)
    maximising nominal net points, excluding every squad already found —
    the "no-good cut" step of top-K enumeration. Returns `None` once no
    further distinct legal squad exists (not an error: it just means
    fewer than `top_k` alternatives were available)."""
    prob = pulp.LpProblem("fpl_squad", pulp.LpMaximize)
    by_id = {c.player_id: c for c in candidates}
    x = {c.player_id: pulp.LpVariable(f"x_{c.player_id}", cat="Binary") for c in candidates}
    y = {c.player_id: pulp.LpVariable(f"y_{c.player_id}", cat="Binary") for c in candidates}
    cap = {c.player_id: pulp.LpVariable(f"cap_{c.player_id}", cat="Binary") for c in candidates}
    vice = {c.player_id: pulp.LpVariable(f"vice_{c.player_id}", cat="Binary") for c in candidates}

    # Squad composition (§7.1): exactly 2/5/5/3, <=3 per club.
    prob += pulp.lpSum(x.values()) == 15
    for position, count in SQUAD_COMPOSITION.items():
        prob += pulp.lpSum(x[c.player_id] for c in candidates if c.position == position) == count
    for club in {c.club for c in candidates}:
        prob += pulp.lpSum(x[c.player_id] for c in candidates if c.club == club) <= MAX_PER_CLUB

    # No-good cuts: a previously found squad must lose at least one member.
    for excluded in excluded_squads:
        prob += pulp.lpSum(x[pid] for pid in excluded if pid in x) <= len(excluded) - 1

    # Starting XI is a subset of the squad; captain/vice are a subset of
    # the XI and must be two different players (§7.1).
    for pid in by_id:
        prob += y[pid] <= x[pid]
        prob += cap[pid] <= y[pid]
        prob += vice[pid] <= y[pid]
        prob += cap[pid] + vice[pid] <= 1
    prob += pulp.lpSum(y.values()) == XI_SIZE
    prob += pulp.lpSum(y[c.player_id] for c in candidates if c.position == "GK") == 1
    prob += pulp.lpSum(y[c.player_id] for c in candidates if c.position == "DEF") >= MIN_DEFENDERS_IN_XI
    prob += pulp.lpSum(y[c.player_id] for c in candidates if c.position == "MID") >= MIN_MIDFIELDERS_IN_XI
    prob += pulp.lpSum(y[c.player_id] for c in candidates if c.position == "FWD") >= MIN_FORWARDS_IN_XI
    prob += pulp.lpSum(cap.values()) == 1
    prob += pulp.lpSum(vice.values()) == 1

    paid_transfers: pulp.LpVariable | None = None
    if current_squad is None:
        prob += pulp.lpSum(x[c.player_id] * c.price for c in candidates) <= bank
    else:
        cash_required = pulp.lpSum(
            x[c.player_id] * c.price for c in candidates if c.player_id not in current_squad
        )
        proceeds_if_sold = pulp.lpSum(
            (1 - x[pid]) * selling_price for pid, selling_price in current_squad.items()
        )
        prob += cash_required <= bank + proceeds_if_sold

        n_transfers_in = pulp.lpSum(
            x[c.player_id] for c in candidates if c.player_id not in current_squad
        )
        paid_transfers = pulp.LpVariable("paid_transfers", lowBound=0, cat="Integer")
        prob += paid_transfers >= n_transfers_in - free_transfers

    xi_points = pulp.lpSum(y[c.player_id] * c.expected_points for c in candidates)
    captain_bonus = pulp.lpSum(cap[c.player_id] * c.expected_points for c in candidates)
    objective = xi_points + captain_bonus
    if paid_transfers is not None:
        objective = objective - TRANSFER_HIT_COST * paid_transfers
    prob += objective

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if _lp_status_name(status) != "Optimal":
        return None

    squad_ids = frozenset(pid for pid, var in x.items() if round(var.value()) == 1)
    starter_ids = frozenset(pid for pid, var in y.items() if round(var.value()) == 1)

    if current_squad is None:
        transfers_in: frozenset[int] = frozenset()
        transfers_out: frozenset[int] = frozenset()
    else:
        owned_ids = set(current_squad)
        transfers_in = frozenset(squad_ids - owned_ids)
        transfers_out = frozenset(owned_ids - squad_ids)
    hit_cost = (
        TRANSFER_HIT_COST * round(paid_transfers.value())
        if paid_transfers is not None
        else 0.0
    )

    return _NominalSolution(
        squad_ids=squad_ids,
        starter_ids=starter_ids,
        hit_cost=hit_cost,
        xi_expected_points=pulp.value(xi_points) + pulp.value(captain_bonus),
        transfers_in=transfers_in,
        transfers_out=transfers_out,
    )


def optimise_squad(
    candidates: list[OptimiserCandidate],
    bank: float,
    current_squad: dict[int, float] | None = None,
    free_transfers: int = 0,
    top_k: int = DEFAULT_TOP_K,
) -> SquadSelection:
    """Solve for the legal squad/XI/bench/captain/vice maximising NET
    EVALUATED points (§7.4-aware, not merely nominal) for one Gameweek.

    `current_squad=None` means building from scratch (pre-season/initial
    squad): full `bank` against current prices, no transfer-cost
    accounting — squad-building transfers are unlimited and free before
    the opening deadline (§2). `current_squad={player_id: selling_price}`
    means an ongoing transfer decision: every key must also appear in
    `candidates` (the candidate universe must include every owned player,
    the same contract `fpl.game_state`'s import enforces) so the solver
    can choose to retain them.

    Generates up to `top_k` distinct legal (squad, nominal-XI) candidates
    via no-good-cut enumeration, evaluates each one's contingency-aware
    expected points via `fpl.autosub`, and returns whichever has the
    highest NET evaluated figure — which need not be the first (nominal-
    best) candidate found.
    """
    if current_squad is not None:
        missing = set(current_squad) - {c.player_id for c in candidates}
        if missing:
            raise ValueError(
                f"current_squad player_id(s) {sorted(missing)} are not in "
                "candidates — the candidate universe must include every "
                "owned player (same contract fpl.game_state.parse_import "
                "enforces on import)."
            )

    by_id = {c.player_id: c for c in candidates}
    excluded_squads: list[frozenset[int]] = []
    nominal_candidates: list[_NominalSolution] = []
    for _ in range(top_k):
        solution = _solve_once(candidates, bank, current_squad, free_transfers, excluded_squads)
        if solution is None:
            break
        nominal_candidates.append(solution)
        excluded_squads.append(solution.squad_ids)

    if not nominal_candidates:
        raise OptimiserInfeasibleError(
            "No feasible squad/XI found. Check the candidate universe has "
            "enough players per position and that the budget is reachable."
        )

    best: SquadSelection | None = None
    best_net = float("-inf")
    for nc in nominal_candidates:
        starters_cp = [_as_contingency(pid, by_id) for pid in nc.starter_ids]
        bench_cp = [_as_contingency(pid, by_id) for pid in (nc.squad_ids - nc.starter_ids)]

        # §7.4 post-processing: re-optimise bench order and the captain/
        # vice pair over this candidate's fixed 15 — never re-solves the
        # squad/XI itself, that is what top-K enumeration is for.
        refined_bench_order = autosub.optimise_bench_order(starters_cp, bench_cp)
        bench_order = tuple(p.player_id for p in refined_bench_order)
        captain_id, vice_captain_id = autosub.optimise_captaincy(starters_cp)
        evaluated_expected_points = autosub.expected_squad_points(
            starters_cp, refined_bench_order, captain_id, vice_captain_id
        )
        net_evaluated = evaluated_expected_points - nc.hit_cost

        if net_evaluated > best_net:
            best_net = net_evaluated
            best = SquadSelection(
                squad_ids=nc.squad_ids,
                starter_ids=nc.starter_ids,
                bench_order=bench_order,
                captain_id=captain_id,
                vice_captain_id=vice_captain_id,
                transfers_in=nc.transfers_in,
                transfers_out=nc.transfers_out,
                hit_cost=nc.hit_cost,
                xi_expected_points=nc.xi_expected_points,
                evaluated_expected_points=evaluated_expected_points,
                net_expected_points=net_evaluated,
            )

    assert best is not None  # nominal_candidates is non-empty, loop always assigns
    return best
