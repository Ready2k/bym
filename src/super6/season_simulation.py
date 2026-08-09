"""Season Monte Carlo simulator (SPEC.md §12-14).

`league.recommend_strategy` and `strategy.py` evaluate candidate pick sets
for the **current round only**, against the standings as they exist right
now. §13 asks for more: a candidate should be judged by its effect on
P(Finish 1st) *at the end of the season*, not just this round, because a
pick that is optimal in isolation can still be the wrong one once the
rounds still to come are taken into account.

Known vs unknown rounds (§13.2). The current round has real fixtures
(`RoundRecommendation`, already forecast). Every further round this season
does not — Sky has not selected its six fixtures yet. Those are
represented by the **Round Opportunity Model**: `sample_synthetic_round`
draws six fixtures from the historical corpus and forecasts them with the
*current* fitted parameters, standing in for "some six fixtures Sky will
eventually pick". This deliberately does not yet match the fuller
characteristics §13.2 describes (favourite strength, home/away balance,
competitiveness, goal totals) beyond being drawn from competitions the
model already knows — that refinement is a documented follow-on, gated by
§28 like everything else here, not assumed to matter until shown to.

Common random numbers (§13.3). The remaining-season Monte Carlo draw —
every future scoreline, ours and every opponent's, real and synthetic —
is generated once per optimisation run and shared across every candidate
for the *current* round. Only the current round's own contribution
differs between candidates, so their P(Finish 1st) estimates are paired,
not independently noisy, which is what makes the significance test below
meaningful rather than comparing two numbers that could differ by chance
alone.

Opponent behaviour (§11). A rival's pick for the current round uses their
*actual* recorded selection when it has been imported; otherwise, and for
every fixture in every future round (nobody has picked those yet), it is
drawn from `opponent_intelligence.predict_pick_distribution` — a genuine
per-world random draw, because their eventual pick is exactly as uncertain
to us as it is to them right now. A rival with no evidence at all falls
back to sampling from our own model's matrix, a defensible "assume roughly
model-informed" default, not a claim about their actual tendencies.

Our own continuation in future rounds is assumed to be the model's
max-EV pick every time — deciding *this* round is the only thing this
module is for, and the assumption is identical for every candidate and
every world, so it cancels out of the comparison and only calibrates the
absolute stakes (how much a bigger or smaller lead is actually worth
carrying into the rest of the season).

Season-tiebreak-aware (§2.1, §13.1): ties on final points are broken by
exact-score count before splitting, exactly like the real Season League
rule — not points alone, which `league.estimate_finish_first` uses for
its simpler, single-round case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from . import opponent_intelligence as oi
from . import strategy
from .league import LeagueStanding, OpponentProfile, OpponentRound, Scoreline, StrategyCandidate
from .model.dixon_coles import DixonColesParams
from .recommend import RoundRecommendation, recommend_round
from .round import Fixture, Round
from .scoring import super6_points

DEFAULT_SIMULATIONS = 2000
DEFAULT_SEED = 20260809
_FAR_FUTURE = "2099-01-01T00:00:00+00:00"

ParamRouter = Callable[[str, str], DixonColesParams]


# ---------------------------------------------------------------------------
# §13.1 Season State


@dataclass(frozen=True)
class SeasonState:
    points: int
    exact_scores: int
    position: int
    leader_points: int
    leader_exact_scores: int
    points_gap: int  # own - leader; 0 for the leader, negative while trailing
    exact_score_gap: int
    rounds_remaining: int


def season_state(
    standings: Sequence[LeagueStanding], participant: str, rounds_remaining: int
) -> SeasonState | None:
    """§13.1. `None` when there is nothing to compute from yet — no standings,
    or the participant has not appeared in them, rather than a fabricated
    zero-everything state."""
    if rounds_remaining < 0:
        raise ValueError("rounds_remaining cannot be negative")
    if not standings:
        return None
    own = next((row for row in standings if row.participant == participant), None)
    if own is None:
        return None
    leader = min(standings, key=lambda row: row.rank)
    return SeasonState(
        points=own.points,
        exact_scores=own.exact_scores,
        position=own.rank,
        leader_points=leader.points,
        leader_exact_scores=leader.exact_scores,
        points_gap=own.points - leader.points,
        exact_score_gap=own.exact_scores - leader.exact_scores,
        rounds_remaining=rounds_remaining,
    )


# ---------------------------------------------------------------------------
# §13.2 Round Opportunity Model


def sample_synthetic_round(
    matches: pd.DataFrame,
    router: ParamRouter,
    cfg,
    *,
    seed: int,
    slot: int,
) -> RoundRecommendation:
    """One representative "unknown future round" (§13.2): six fixtures
    drawn from the historical fixture pool, forecast with the *current*
    fitted parameters. `slot` distinguishes one future-round placeholder
    from another (round 2 of the remaining season vs round 3, ...) with a
    reproducible, distinct draw for each.
    """
    pairs = matches[["home_team", "away_team"]].drop_duplicates()
    if len(pairs) < 6:
        raise ValueError("historical corpus has too few distinct fixtures to sample a round")
    rng_state = int(np.random.default_rng(seed + slot).integers(0, 2**32 - 1))
    chosen = pairs.sample(n=6, random_state=rng_state)
    fake_round = Round(
        round_number=-1 - slot,
        deadline_at=pd.Timestamp(_FAR_FUTURE).to_pydatetime(),
        fixtures=tuple(
            Fixture(str(row.home_team), str(row.away_team))
            for row in chosen.itertuples(index=False)
        ),
        source="SYNTHETIC_ROUND_OPPORTUNITY_MODEL",
    )
    return recommend_round(router, fake_round, cfg)


# ---------------------------------------------------------------------------
# Season-length Monte Carlo


def _round_points(picks: Sequence[Scoreline], results: Sequence[Scoreline | None]) -> int:
    return sum(
        super6_points(*pick, *actual)
        for pick, actual in zip(picks, results)
        if actual is not None
    )


def _round_exact(picks: Sequence[Scoreline], results: Sequence[Scoreline | None]) -> int:
    return sum(
        1
        for pick, actual in zip(picks, results)
        if actual is not None and tuple(pick) == tuple(actual)
    )


def _predicted_series(
    profile: OpponentProfile | None,
    bias: oi.FavouriteBiasProfile | None,
    average_profile: OpponentProfile,
    average_rate: float | None,
    recommendation: RoundRecommendation,
    rng: np.random.Generator,
    count: int,
) -> list[list[Scoreline]]:
    """One drawn pick per fixture per simulated world for one opponent
    against one round — `series[fixture_index][world_index]`. Falls back to
    our own model's matrix wherever the rival has no evidence to predict
    from (see module docstring)."""
    series: list[list[Scoreline]] = []
    for fixture in recommendation.fixtures:
        distribution: dict[str, float] = {}
        if profile is not None and bias is not None:
            favoured = oi.result_of(fixture.pick_home, fixture.pick_away)
            distribution = oi.predict_pick_distribution(
                profile, bias, average_profile, average_rate, favoured
            )
        if distribution:
            scores = list(distribution)
            weights = np.array([distribution[score] for score in scores], dtype=float)
            weights = weights / weights.sum()
            draws = rng.choice(len(scores), size=count, p=weights)
            picks = [
                tuple(int(part) for part in scores[draw].split("-", 1)) for draw in draws
            ]
        else:
            probs = np.asarray(fixture.matrix.probs, dtype=float).ravel()
            probs = probs / probs.sum()
            size = fixture.matrix.probs.shape[0]
            draws = rng.choice(probs.size, size=count, p=probs)
            picks = [(int(draw // size), int(draw % size)) for draw in draws]
        series.append(picks)
    return series


@dataclass(frozen=True)
class PairedComparison:
    """§13.3: paired-difference significance test against a baseline."""

    mean_gain: float
    lower_bound: float
    upper_bound: float
    simulations: int

    def accepts(self, minimum_gain: float) -> bool:
        return self.lower_bound > 0 and self.mean_gain >= minimum_gain


def paired_comparison(
    candidate_wins: Sequence[float], baseline_wins: Sequence[float], *, confidence: float = 0.95
) -> PairedComparison:
    """§13.3: `D_i = Win_i(candidate) - Win_i(baseline)`, `ΔP = mean(D)`,
    with a normal-approximation confidence interval on the paired
    differences — not two separately-estimated standard errors, which
    would ignore that every candidate shares the same simulated worlds."""
    if len(candidate_wins) != len(baseline_wins):
        raise ValueError("candidate and baseline win series must be the same length")
    n = len(candidate_wins)
    if n == 0:
        raise ValueError("paired_comparison needs at least one simulated world")
    diffs = [c - b for c, b in zip(candidate_wins, baseline_wins)]
    mean_gain = sum(diffs) / n
    if n > 1:
        variance = sum((d - mean_gain) ** 2 for d in diffs) / (n - 1)
        se = (variance / n) ** 0.5
    else:
        se = 0.0
    z = {0.90: 1.6448536269514722, 0.95: 1.9599639845400545, 0.99: 2.5758293035489004}.get(
        confidence
    )
    if z is None:
        raise ValueError("confidence must be one of 0.90, 0.95, 0.99")
    return PairedComparison(
        mean_gain=round(mean_gain, 4),
        lower_bound=round(mean_gain - z * se, 4),
        upper_bound=round(mean_gain + z * se, 4),
        simulations=n,
    )


@dataclass(frozen=True)
class SeasonCandidateResult:
    name: str
    finish_first_probability: float
    expected_final_points: float
    win_shares: tuple[float, ...]  # per-world outcome, kept for pairing


@dataclass(frozen=True)
class SeasonSimulationResult:
    participant: str
    baseline: SeasonCandidateResult
    candidates: tuple[SeasonCandidateResult, ...]  # includes baseline, ranked best first
    simulations: int


def simulate_remaining_season(
    current_recommendation: RoundRecommendation,
    candidates: Sequence[StrategyCandidate],
    standings: Sequence[LeagueStanding],
    participant: str,
    current_round_picks: Sequence[OpponentRound],
    profiles: Mapping[str, OpponentProfile],
    favourite: Mapping[str, oi.FavouriteBiasProfile],
    average_profile: OpponentProfile | None,
    average_rate: float | None,
    synthetic_rounds: Sequence[RoundRecommendation] = (),
    *,
    simulations: int = DEFAULT_SIMULATIONS,
    seed: int = DEFAULT_SEED,
) -> SeasonSimulationResult | None:
    """§13: simulate the rest of the season once, score every candidate's
    current-round pick set against the same shared worlds (§13.3), and
    return every candidate ranked by simulated P(Finish 1st).

    `synthetic_rounds` are pre-built Round Opportunity placeholders
    (`sample_synthetic_round`) for every remaining round beyond the
    current one — this function stays free of the historical corpus and
    the fitted model themselves, so it can be tested without either.
    Pass none of them to fall back to a current-round-only simulation
    (still season-*state*-aware, just without any future-round horizon).
    """
    if not candidates:
        return None
    if len({c.name for c in candidates}) != len(candidates):
        raise ValueError("candidate names must be unique")
    opponents = tuple(row.participant for row in standings if row.participant != participant)
    if participant not in {row.participant for row in standings} or not opponents:
        return None

    base_points = {row.participant: row.points for row in standings}
    base_exact = {row.participant: row.exact_scores for row in standings}

    rng = np.random.default_rng(seed)
    current_scenarios = strategy.simulate_scenarios(current_recommendation, simulations, seed)

    actual_by_opponent = {
        record.participant: record
        for record in current_round_picks
        if record.round_number == current_recommendation.round_number
    }

    # Opponent totals are identical for every candidate — only our own
    # current-round contribution changes — so they are accumulated once.
    opponent_final_points = {name: [0] * simulations for name in opponents}
    opponent_final_exact = {name: [0] * simulations for name in opponents}
    for name in opponents:
        profile = profiles.get(name)
        bias = favourite.get(name)
        if name in actual_by_opponent:
            picks = actual_by_opponent[name].picks
            for world in range(simulations):
                opponent_final_points[name][world] = base_points[name] + _round_points(
                    picks, current_scenarios[world].results
                )
                opponent_final_exact[name][world] = base_exact[name] + _round_exact(
                    picks, current_scenarios[world].results
                )
        elif profile is not None and bias is not None and average_profile is not None:
            series = _predicted_series(
                profile, bias, average_profile, average_rate, current_recommendation, rng, simulations
            )
            for world in range(simulations):
                picks = [series[f][world] for f in range(len(series))]
                opponent_final_points[name][world] = base_points[name] + _round_points(
                    picks, current_scenarios[world].results
                )
                opponent_final_exact[name][world] = base_exact[name] + _round_exact(
                    picks, current_scenarios[world].results
                )
        else:
            # No evidence and no actual picks: carry them forward flat
            # rather than inventing a score for a fixture set they may
            # not even enter — consistent with "missing results are not
            # treated as a zero-point loss" elsewhere in this codebase.
            for world in range(simulations):
                opponent_final_points[name][world] = base_points[name]
                opponent_final_exact[name][world] = base_exact[name]

    our_synthetic_points = [base_points[participant]] * simulations
    our_synthetic_exact = [base_exact[participant]] * simulations
    for slot, synthetic in enumerate(synthetic_rounds):
        synthetic_scenarios = strategy.simulate_scenarios(synthetic, simulations, seed + 1000 * (slot + 1))
        our_pick = [(f.pick_home, f.pick_away) for f in synthetic.fixtures]
        for world in range(simulations):
            our_synthetic_points[world] += _round_points(our_pick, synthetic_scenarios[world].results)
            our_synthetic_exact[world] += _round_exact(our_pick, synthetic_scenarios[world].results)
        for name in opponents:
            profile = profiles.get(name)
            bias = favourite.get(name)
            if profile is None or bias is None or average_profile is None:
                continue
            series = _predicted_series(
                profile, bias, average_profile, average_rate, synthetic, rng, simulations
            )
            for world in range(simulations):
                picks = [series[f][world] for f in range(len(series))]
                opponent_final_points[name][world] += _round_points(
                    picks, synthetic_scenarios[world].results
                )
                opponent_final_exact[name][world] += _round_exact(
                    picks, synthetic_scenarios[world].results
                )

    results: list[SeasonCandidateResult] = []
    for candidate in candidates:
        win_shares: list[float] = []
        final_points: list[int] = []
        for world in range(simulations):
            our_points = our_synthetic_points[world] + _round_points(
                candidate.picks, current_scenarios[world].results
            )
            our_exact = our_synthetic_exact[world] + _round_exact(
                candidate.picks, current_scenarios[world].results
            )
            totals = {participant: (our_points, our_exact)}
            for name in opponents:
                totals[name] = (opponent_final_points[name][world], opponent_final_exact[name][world])
            # §2.1: ties on points are broken by exact-score count, then split.
            best = max(totals.values())
            leaders = [name for name, score in totals.items() if score == best]
            win_shares.append(1 / len(leaders) if participant in leaders else 0.0)
            final_points.append(our_points)
        results.append(
            SeasonCandidateResult(
                name=candidate.name,
                finish_first_probability=round(sum(win_shares) / simulations, 4),
                expected_final_points=round(sum(final_points) / simulations, 2),
                win_shares=tuple(win_shares),
            )
        )

    baseline = next((r for r in results if r.name == "MAX_EV"), results[0])
    ranked = sorted(
        results, key=lambda r: (-r.finish_first_probability, -r.expected_final_points, r.name)
    )
    return SeasonSimulationResult(
        participant=participant, baseline=baseline, candidates=tuple(ranked), simulations=simulations
    )


# ---------------------------------------------------------------------------
# §12 mode labelling


@dataclass(frozen=True)
class SeasonStrategyRecommendation:
    mode: str  # NORMAL | ATTACK | DEFEND
    chosen: SeasonCandidateResult
    baseline: SeasonCandidateResult
    comparison: PairedComparison


def recommend_season_strategy(
    simulation: SeasonSimulationResult,
    *,
    minimum_gain: float = 0.01,
) -> SeasonStrategyRecommendation:
    """§12: NORMAL/ATTACK/DEFEND as labels attached to whichever candidate
    the paired significance test (§13.3) actually accepts — never a
    standing "gap > X" rule. The best-simulated candidate only overrides
    the max-EV baseline when its P(Finish 1st) gain clears both the lower
    confidence bound and `minimum_gain`; otherwise the baseline stands and
    the mode is NORMAL by construction.

    Label meaning is descriptive, not decisive: ATTACK names a winning
    candidate that differentiates away from the max-EV pick (§12's
    higher-variance case), DEFEND names one that correlates with the
    field instead (lower-variance); the simulation already made the
    actual choice before either word is attached to it.
    """
    best = simulation.candidates[0]
    baseline = simulation.baseline
    if best.name == baseline.name:
        comparison = paired_comparison(best.win_shares, baseline.win_shares)
        return SeasonStrategyRecommendation("NORMAL", baseline, baseline, comparison)
    comparison = paired_comparison(best.win_shares, baseline.win_shares)
    if not comparison.accepts(minimum_gain):
        return SeasonStrategyRecommendation("NORMAL", baseline, baseline, comparison)
    mode = "DEFEND" if best.name.startswith("CORRELATE") else "ATTACK"
    return SeasonStrategyRecommendation(mode, best, baseline, comparison)
