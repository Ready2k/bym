"""Opponent-aware pick selection (SPEC.md §43-45).

`league.recommend_strategy` already ranks candidate pick sets by simulated
chance of finishing first. It needs two things this module supplies, both
of which depend on the model and so cannot live in `league`:

    simulate_scenarios   six-fixture outcomes drawn from the model's own
                         score matrices
    build_candidates     alternative pick sets worth comparing against the
                         maximum-EV one

**Why this exists at all.** The frozen model maximises expected points per
fixture, which is the right objective for one person playing alone. A
private league is not that game: you are not trying to score well, you are
trying to score better than five specific people who are themselves mostly
backing favourites. When the field converges on the same picks, the extra
points that separate you cannot come from the pick everybody shares.

That makes differentiation valuable *only* in specific circumstances —
behind with little to lose, or a rival heavily concentrated on one
scoreline — and actively harmful when leading. Nothing here decides that
trade-off by taste: candidates are generated, then scored against
simulations, and the one with the highest P(finish first) wins. If the
maximum-EV picks are best, they win, and that is the common case.

Nothing in this module changes a forecast. Scenarios are drawn from the
frozen model's distribution; candidates are re-orderings of scorelines it
already produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from . import opponent_intelligence as oi
from .league import (
    LeagueStanding,
    OpponentProfile,
    OpponentRound,
    Scoreline,
    ScorelineScenario,
    StrategyCandidate,
    StrategyRecommendation,
    recommend_strategy,
)
from .recommend import RoundRecommendation

DEFAULT_SCENARIOS = 2000

# Drawn from a fixed seed so a refresh does not silently re-rank
# candidates that are statistically tied. The determinism fix in v1.0.1
# exists because varying output was mistaken for a real change once.
DEFAULT_SEED = 20260808


@dataclass(frozen=True)
class Phase3StrategyResult:
    """The complete, recordable output of the Super 6 Phase 3 arm.

    ``picks`` is always a valid six-scoreline tuple.  The arm therefore has
    a safe prospective behaviour even when the league/opponent inputs are
    not available: it records the frozen recommendation's MAX_EV picks and
    explains why strategic evaluation was not possible.
    """

    picks: tuple[Scoreline, ...]
    selected_candidate: str
    status: str
    reason: str
    simulations: int = 0
    finish_first_probability: float | None = None
    expected_round_points: float | None = None
    recommendation: StrategyRecommendation | None = None

    @property
    def fallback(self) -> bool:
        """Whether the arm had to fall back because strategy was unavailable."""
        return self.status == "FALLBACK_MAX_EV"


def noise_floor(simulations: int, sigmas: float = 2.0) -> float:
    """How large a P(finish first) gap must be to mean anything.

    Both figures `recommend_strategy` reports are Monte Carlo estimates,
    not analytic values, so two candidates can swap places purely on the
    draw. Near p=0.5 the standard error is sqrt(0.25/n) — about 1.1pp at
    2,000 scenarios — and a deviation costs real expected points. Chasing
    a gap inside the noise would trade a certain loss for an imaginary
    gain, so anything under this margin is treated as no improvement.
    """
    if simulations < 1:
        raise ValueError("simulations must be positive")
    return sigmas * (0.25 / simulations) ** 0.5


def preferred(evaluations, simulations: int, baseline: str = "MAX_EV"):
    """The baseline unless a candidate beats it by more than the noise floor.

    Deliberately biased toward doing nothing: the maximum-EV picks are the
    default for a reason, and only evidence that survives the simulation's
    own error should override them.
    """
    scored = {evaluation.name: evaluation for evaluation in evaluations}
    if baseline not in scored:
        return max(evaluations, key=lambda e: e.finish_first_probability)
    floor = noise_floor(simulations)
    best = max(evaluations, key=lambda e: e.finish_first_probability)
    gain = best.finish_first_probability - scored[baseline].finish_first_probability
    return best if gain > floor else scored[baseline]


def generate_phase3_picks(
    recommendation: RoundRecommendation,
    *,
    standings: Sequence[LeagueStanding] = (),
    round_picks: Sequence[OpponentRound] = (),
    participant: str = "You",
    season: int,
    round_number: int,
    simulations: int = DEFAULT_SCENARIOS,
    seed: int = DEFAULT_SEED,
    max_candidates: int = 6,
) -> Phase3StrategyResult:
    """Generate the deterministic prospective ``PHASE3_STRATEGY`` arm.

    Contract:

    * the baseline is the supplied model recommendation (``MAX_EV``);
    * strategic candidates are generated only from the current round's
      imported opponent picks and the supplied standings;
    * scenarios come from the recommendation's score matrices with the
      fixed ``seed``;
    * a deviation is accepted only when it beats MAX_EV by more than the
      Monte Carlo noise floor; and
    * incomplete/invalid strategy inputs return MAX_EV with a reason rather
      than preventing the prospective round from being recorded.

    This function deliberately does not write the ledger.  Its returned
    ``picks`` can be passed directly to ``prospective.record_picks`` by the
    parent recording flow, keeping Phase 3 independent of Phase 2 and the
    market arm.
    """
    if simulations < 1:
        raise ValueError("simulations must be positive")
    if max_candidates < 1:
        raise ValueError("max_candidates must be positive")

    baseline = tuple(
        (fixture.pick_home, fixture.pick_away)
        for fixture in recommendation.fixtures
    )
    if len(baseline) != 6:
        raise ValueError("Phase 3 requires a six-fixture recommendation")

    def fallback(reason: str) -> Phase3StrategyResult:
        # StrategyCandidate validates the same six-scoreline contract used by
        # the league evaluator, so a parent cannot accidentally record a
        # malformed fallback.
        picks = StrategyCandidate(name="MAX_EV", picks=baseline).picks
        return Phase3StrategyResult(
            picks=picks,
            selected_candidate="MAX_EV",
            status="FALLBACK_MAX_EV",
            reason=reason,
        )

    if not participant.strip():
        return fallback("strategy unavailable: participant name is empty")
    if not standings:
        return fallback("strategy unavailable: no league standings supplied")
    if simulations < 100:
        return fallback("strategy unavailable: at least 100 simulations are required")

    try:
        candidates = build_candidates(
            recommendation,
            [
                record
                for record in round_picks
                if record.season == season and record.round_number == round_number
            ],
            max_candidates=max_candidates,
        )
        scenarios = simulate_scenarios(
            recommendation, count=simulations, seed=seed
        )
        strategic = recommend_strategy(
            standings,
            round_picks,
            candidates,
            scenarios,
            participant=participant,
            season=season,
            round_number=round_number,
            minimum_scenarios=100,
        )
    except (AttributeError, KeyError, IndexError, TypeError, ValueError) as exc:
        return fallback(f"strategy unavailable: {exc}")

    if strategic is None:
        return fallback(
            "strategy unavailable: standings or opponent picks do not cover "
            "the complete current round"
        )

    evaluations = (strategic.recommended, *strategic.alternatives)
    chosen = preferred(evaluations, simulations)
    by_name = {candidate.name: candidate for candidate in candidates}
    selected = by_name[chosen.name]
    status = "MAX_EV" if chosen.name == "MAX_EV" else "STRATEGY"
    reason = (
        f"Selected {chosen.name} from {chosen.simulations} deterministic "
        "scoreline simulations; deviations must clear the noise floor."
    )
    return Phase3StrategyResult(
        picks=selected.picks,
        selected_candidate=chosen.name,
        status=status,
        reason=reason,
        simulations=chosen.simulations,
        finish_first_probability=chosen.finish_first_probability,
        expected_round_points=chosen.expected_round_points,
        recommendation=strategic,
    )


@dataclass(frozen=True)
class FixtureAlternative:
    """One deviation from the maximum-EV pick, with what it costs."""

    index: int
    score: tuple[int, int]
    ev_cost: float
    opponent_share: float


def simulate_scenarios(
    recommendation: RoundRecommendation,
    count: int = DEFAULT_SCENARIOS,
    seed: int = DEFAULT_SEED,
) -> tuple[ScorelineScenario, ...]:
    """Draw `count` six-fixture outcomes from the model's score matrices.

    Fixtures are sampled independently. That is the model's own assumption
    — it fits each fixture separately and shares no correlation term — so
    introducing one here would invent structure the forecast does not have.
    """
    rng = np.random.default_rng(seed)
    per_fixture = []
    for fixture in recommendation.fixtures:
        probs = np.asarray(fixture.matrix.probs, dtype=float)
        flat = probs.ravel()
        flat = flat / flat.sum()
        size = probs.shape[0]
        draws = rng.choice(flat.size, size=count, p=flat)
        per_fixture.append([(int(d // size), int(d % size)) for d in draws])

    return tuple(
        ScorelineScenario(
            scenario_id=f"sim-{index}",
            results=tuple(per_fixture[f][index] for f in range(len(per_fixture))),
        )
        for index in range(count)
    )


def _opponent_shares(
    round_picks: list[OpponentRound], fixture_index: int
) -> dict[tuple[int, int], float]:
    """How concentrated the field is on each scoreline for one fixture."""
    if not round_picks:
        return {}
    counts: dict[tuple[int, int], int] = {}
    for record in round_picks:
        pick = tuple(record.picks[fixture_index])
        counts[pick] = counts.get(pick, 0) + 1
    return {score: n / len(round_picks) for score, n in counts.items()}


def rank_alternatives(
    recommendation: RoundRecommendation,
    round_picks: list[OpponentRound],
    per_fixture: int = 2,
) -> list[FixtureAlternative]:
    """Deviations ordered by how crowded the pick they replace is.

    A cheap deviation on a fixture where every rival has the same score
    buys more separation than an expensive one nobody else holds, so the
    ordering is by opponent concentration first and EV sacrificed second.
    """
    alternatives: list[FixtureAlternative] = []
    for index, fixture in enumerate(recommendation.fixtures):
        shares = _opponent_shares(round_picks, index)
        best_ev = fixture.ev
        crowd = shares.get((fixture.pick_home, fixture.pick_away), 0.0)
        for score, _probability, ev in fixture.alternatives(per_fixture + 1):
            home, away = (int(part) for part in score.split("-"))
            if (home, away) == (fixture.pick_home, fixture.pick_away):
                continue
            alternatives.append(
                FixtureAlternative(
                    index=index,
                    score=(home, away),
                    ev_cost=round(best_ev - ev, 4),
                    opponent_share=crowd,
                )
            )
    alternatives.sort(key=lambda a: (-a.opponent_share, a.ev_cost))
    return alternatives


def build_candidates(
    recommendation: RoundRecommendation,
    round_picks: list[OpponentRound],
    max_candidates: int = 6,
) -> tuple[StrategyCandidate, ...]:
    """Maximum-EV picks plus a few progressively bolder deviations.

    Deviations are stacked cumulatively rather than offered singly: one
    changed scoreline rarely moves P(finish first), and the question a
    trailing player actually faces is how much of the round to gamble.
    """
    base = tuple(
        (fixture.pick_home, fixture.pick_away) for fixture in recommendation.fixtures
    )
    candidates = [StrategyCandidate(name="MAX_EV", picks=base)]

    ranked = rank_alternatives(recommendation, round_picks)
    used_fixtures: set[int] = set()
    picks = list(base)
    for alternative in ranked:
        if len(candidates) >= max_candidates:
            break
        if alternative.index in used_fixtures:
            continue
        used_fixtures.add(alternative.index)
        picks[alternative.index] = alternative.score
        candidates.append(
            StrategyCandidate(
                name=f"DIFFERENTIATE_{len(used_fixtures)}",
                picks=tuple(picks),
            )
        )
    return tuple(candidates)


def build_correlate_candidate(
    recommendation: RoundRecommendation,
    profiles: Mapping[str, OpponentProfile],
    favourite: Mapping[str, oi.FavouriteBiasProfile],
    average_profile: OpponentProfile | None,
    average_rate: float | None,
) -> StrategyCandidate | None:
    """§12 DEFEND-shaped candidate: per fixture, the single scoreline the
    field is most likely to pick, pooled across every known rival's
    predicted distribution (`opponent_intelligence.predict_pick_distribution`).

    This is the opposite move to `build_candidates`' differentiation
    alternatives — matching the field instead of deviating from it, which
    only helps while *leading* (it caps the leader's own downside without
    trying to out-score anyone). Returns `None` when there is no pooled
    opponent evidence at all, rather than fabricating a "field" out of
    zero rivals; the season simulator (`season_simulation.py`) decides
    whether this candidate actually beats maximum EV, it is never assumed
    to.
    """
    if average_profile is None or not profiles:
        return None
    picks: list[tuple[int, int]] = []
    any_evidence = False
    for fixture in recommendation.fixtures:
        favoured = oi.result_of(fixture.pick_home, fixture.pick_away)
        pooled: dict[str, float] = {}
        for name, profile in profiles.items():
            bias = favourite.get(name)
            if bias is None:
                continue
            distribution = oi.predict_pick_distribution(
                profile, bias, average_profile, average_rate, favoured
            )
            for score, probability in distribution.items():
                pooled[score] = pooled.get(score, 0.0) + probability
        if not pooled:
            picks.append((fixture.pick_home, fixture.pick_away))
            continue
        any_evidence = True
        best_score = max(pooled.items(), key=lambda item: item[1])[0]
        home, away = (int(part) for part in best_score.split("-", 1))
        picks.append((home, away))
    if not any_evidence:
        return None
    return StrategyCandidate(name="CORRELATE", picks=tuple(picks))


def describe_candidate(
    candidate: StrategyCandidate, recommendation: RoundRecommendation
) -> str:
    """Plain description of how a candidate departs from maximum EV."""
    changes = [
        f"{fixture.home} v {fixture.away}: {fixture.pick} → {pick[0]}-{pick[1]}"
        for fixture, pick in zip(recommendation.fixtures, candidate.picks)
        if (fixture.pick_home, fixture.pick_away) != tuple(pick)
    ]
    if not changes:
        return "The maximum expected-value picks, unchanged."
    return "Deviates on " + "; ".join(changes) + "."
