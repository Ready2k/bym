"""Autosub and captain/vice-captain non-appearance contingency (§7.4).

Optimising only the nominal XI's summed expected points understates what
actually happens when a starter blanks: FPL promotes the first
formation-legal bench player in the manager's chosen bench order, the
reserve goalkeeper only ever covers the starting goalkeeper, and the
vice-captain's multiplier applies only if the captain records no
appearance at all. This module is the "deterministic evaluation
function" §7.4 asks for, layered on top of an already-legal squad/XI
rather than folded into the ILP itself — enumerating appearance
contingencies inside the solver would trade a small, fast MILP for the
"entire FPL universe encoded in PuLP" §7.4 explicitly warns against.

Two derived quantities feed every function here, both already available
from a `FixtureProjection`/`ExpectedPointsBreakdown` pair:

- `p_appearance` — already on the projection.
- `points_if_appears` — the CONDITIONAL expected points given the player
  does appear, i.e. `expected_points / p_appearance`. This is a
  legitimate inverse of how `fpl.projection`'s rates were built, not a
  new assumption: every rate there is already unconditional
  (E[X] = P(appear)·E[X|appear] + P(absent)·0, because a player who does
  not appear this fixture contributes zero goals/assists/saves/etc by
  construction), so dividing the unconditional figure by P(appear) is
  the exact algebraic inverse.

BASELINE_FPL_001 ASSUMPTION (stated explicitly, not left implicit in the
maths): every player's appearance this Gameweek is an INDEPENDENT
Bernoulli trial. `_expected_base_points`'s 2^15 enumeration multiplies
each player's own `p_appearance`/`(1 - p_appearance)` straight down the
squad with no covariance term anywhere, and `expected_captain_bonus`'s
closed form is only valid because it assumes the same thing for exactly
two players. This is a real simplification, not a claim of realism — it
is fine for a first baseline and wrong in at least these specific,
known ways:

- same-team rotation (a manager rests two midfielders together);
- match postponement (an entire fixture's players all miss together);
- clustered injuries/illness (a training-ground knock, a squad flu bug);
- goalkeeper/backup relationships (a fit reserve keeper is *more* likely
  to play when the starter is *known* to be out, not independently so).

None of these are modelled now. They are named here as explicit future
audition candidates for a later `FPL_00N` challenger — never assumed
away silently, and never added to this baseline opportunistically just
because the idea is sitting right here. Per the same discipline that
gates every other refinement (§18: "start deliberately boring, audition
refinements one at a time"), a correlated-appearance model earns its way
in only by beating BASELINE_FPL_001 on a pre-registered metric, not by
sounding more sophisticated.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations, product

from .players import Position

MIN_DEFENDERS_IN_XI = 3
MIN_MIDFIELDERS_IN_XI = 2
MIN_FORWARDS_IN_XI = 1


@dataclass(frozen=True)
class ContingencyPlayer:
    """One squad member as the autosub/captain evaluator sees them."""

    player_id: int
    position: str  # GK | DEF | MID | FWD
    p_appearance: float
    points_if_appears: float


def _position_counts(players: list[ContingencyPlayer]) -> dict[str, int]:
    counts = {"DEF": 0, "MID": 0, "FWD": 0}
    for p in players:
        if p.position in counts:
            counts[p.position] += 1
    return counts


def _meets_formation_minimum(counts: dict[str, int]) -> bool:
    return (
        counts["DEF"] >= MIN_DEFENDERS_IN_XI
        and counts["MID"] >= MIN_MIDFIELDERS_IN_XI
        and counts["FWD"] >= MIN_FORWARDS_IN_XI
    )


def apply_autosubs(
    starters: list[ContingencyPlayer],
    reserve_gk: ContingencyPlayer | None,
    outfield_bench_order: list[ContingencyPlayer],
    appeared: set[int],
) -> list[ContingencyPlayer]:
    """The players who actually earn points this Gameweek, given exactly
    who did and didn't appear.

    Matches the official rule as documented by Premier League/community
    sources: the starting goalkeeper is replaced only by the reserve
    goalkeeper, and never by an outfield sub; each outfield gap is filled
    by walking `outfield_bench_order` ONCE, in priority order — a
    candidate comes on only if doing so keeps the resulting XI at or
    above the 3 DEF / 2 MID / 1 FWD minimum (checked against the team as
    currently confirmed, not with lookahead for other still-open gaps —
    the same single-pass, non-backtracking behaviour FPL itself is
    documented to use). A bench player skipped for one gap is not
    reconsidered for a later one.

    If BOTH goalkeepers fail to appear, no goalkeeper plays this
    Gameweek — a known, accepted FPL edge case, not an error here.
    """
    playing: list[ContingencyPlayer] = []

    starting_gk = next((s for s in starters if s.position == Position.GK.value), None)
    if starting_gk is not None:
        if starting_gk.player_id in appeared:
            playing.append(starting_gk)
        elif reserve_gk is not None and reserve_gk.player_id in appeared:
            playing.append(reserve_gk)

    outfield_starters = [s for s in starters if s.position != Position.GK.value]
    confirmed_outfield = [s for s in outfield_starters if s.player_id in appeared]
    gaps = len(outfield_starters) - len(confirmed_outfield)
    playing.extend(confirmed_outfield)

    for sub in outfield_bench_order:
        if gaps <= 0:
            break
        if sub.player_id not in appeared:
            continue
        candidate_playing = playing + [sub]
        if _meets_formation_minimum(_position_counts(candidate_playing)):
            playing = candidate_playing
            gaps -= 1
        # else: this bench player does not come on at all this Gameweek —
        # skipped permanently, never retried against a later gap.

    return playing


def resolve_captain_multiplier(
    captain_id: int, vice_captain_id: int, appeared: set[int]
) -> int | None:
    """Which player (if any) receives the captain's doubled multiplier
    this Gameweek (§2: "vice-captain takes over only if the captain
    records no appearance"). `None` when neither appears — the multiplier
    is simply not applied to anyone, it does not fall through further."""
    if captain_id in appeared:
        return captain_id
    if vice_captain_id in appeared:
        return vice_captain_id
    return None


def _expected_base_points(
    starters: list[ContingencyPlayer],
    reserve_gk: ContingencyPlayer | None,
    outfield_bench_order: list[ContingencyPlayer],
) -> float:
    """E[sum of points actually earned by whoever ends up playing],
    excluding any captain/vice bonus — exact enumeration over every
    appear/absent combination of every squad member (starters + full
    bench), weighted by joint probability under the independence
    assumption. 15 players -> 2^15 = 32768 combinations, cheap enough for
    exact computation rather than Monte Carlo sampling."""
    squad = starters + ([reserve_gk] if reserve_gk is not None else []) + outfield_bench_order
    total = 0.0
    for mask in product((True, False), repeat=len(squad)):
        p_realisation = 1.0
        appeared: set[int] = set()
        for player, did_appear in zip(squad, mask):
            p_realisation *= player.p_appearance if did_appear else (1.0 - player.p_appearance)
            if did_appear:
                appeared.add(player.player_id)
        if p_realisation == 0.0:
            continue
        playing = apply_autosubs(starters, reserve_gk, outfield_bench_order, appeared)
        total += p_realisation * sum(p.points_if_appears for p in playing)
    return total


def expected_captain_bonus(captain: ContingencyPlayer, vice: ContingencyPlayer) -> float:
    """E[captain multiplier's extra point contribution], in closed form.

    This is independent of bench order and of every other player's
    appearance: it depends only on the captain's and vice's OWN
    appearance events (§2's failover is a two-player rule), so no
    enumeration over the rest of the squad is needed —
    `P(captain appears)·points(captain) + P(captain absent)·P(vice
    appears)·points(vice)`.
    """
    return (
        captain.p_appearance * captain.points_if_appears
        + (1.0 - captain.p_appearance) * vice.p_appearance * vice.points_if_appears
    )


def optimise_bench_order(
    starters: list[ContingencyPlayer],
    bench: list[ContingencyPlayer],
) -> tuple[ContingencyPlayer, ...]:
    """The bench ordering (reserve GK fixed in its own slot; the 3
    outfield reserves' priority order searched exhaustively, 3! = 6
    orderings) that maximises expected BASE squad points.

    Captain/vice bonus is deliberately excluded from this search — it is
    a constant offset with respect to bench order (see
    `expected_captain_bonus`), so including it here would not change
    which ordering wins and would only add cost.
    """
    reserve_gk = next((b for b in bench if b.position == Position.GK.value), None)
    outfield = [b for b in bench if b.position != Position.GK.value]

    best_order: tuple[ContingencyPlayer, ...] = tuple(outfield)
    best_value = -1.0
    for perm in permutations(outfield):
        value = _expected_base_points(starters, reserve_gk, list(perm))
        if value > best_value:
            best_value = value
            best_order = perm

    return ((reserve_gk,) if reserve_gk is not None else ()) + best_order


def optimise_captaincy(starters: list[ContingencyPlayer]) -> tuple[int, int]:
    """The (captain_id, vice_captain_id) pair maximising expected
    captain-multiplier bonus — not simply `max(p.points_if_appears)`,
    since a high-appearance-risk top scorer can be worse than a slightly
    lower-scoring, more reliable captain once the vice failover is
    accounted for."""
    best_pair: tuple[int, int] | None = None
    best_value = -1.0
    for captain in starters:
        for vice in starters:
            if captain.player_id == vice.player_id:
                continue
            value = expected_captain_bonus(captain, vice)
            if value > best_value:
                best_value = value
                best_pair = (captain.player_id, vice.player_id)
    if best_pair is None:
        raise ValueError("optimise_captaincy requires at least two starters")
    return best_pair


def expected_squad_points(
    starters: list[ContingencyPlayer],
    bench_order: tuple[ContingencyPlayer, ...],
    captain_id: int,
    vice_captain_id: int,
) -> float:
    """The full evaluated figure §7.4 asks the optimiser objective to
    report: expected base points under autosub contingencies (using
    whatever `bench_order` is supplied — call `optimise_bench_order`
    first if it should be chosen rather than given) plus the expected
    captain-multiplier bonus for the given captain/vice pair."""
    reserve_gk = next((b for b in bench_order if b.position == Position.GK.value), None)
    outfield_bench_order = [b for b in bench_order if b.position != Position.GK.value]
    by_id = {p.player_id: p for p in starters}
    captain = by_id[captain_id]
    vice = by_id[vice_captain_id]
    return _expected_base_points(starters, reserve_gk, outfield_bench_order) + expected_captain_bonus(
        captain, vice
    )
