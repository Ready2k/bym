"""Private-league and opponent read models.

The module deliberately keeps imported opponent selections separate from
forecasting.  A league table is calculated only from recorded selections and
known results; an estimated chance of winning is calculated only from supplied
simulation scenarios.  In particular, it never turns a small amount of
opponent history into a made-up probability.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .scoring import result_of, super6_points


SIX_FIXTURES = 6
DEFAULT_OPPONENT_HISTORY_PATH = Path("data/opponent_history.json")

Scoreline = tuple[int, int]


def _validated_scoreline(value: Sequence[int]) -> Scoreline:
    if len(value) != 2:
        raise ValueError("A scoreline must contain exactly home and away goals")
    home, away = value
    if (
        isinstance(home, bool)
        or isinstance(away, bool)
        or not isinstance(home, int)
        or not isinstance(away, int)
        or home < 0
        or away < 0
    ):
        raise ValueError("Scoreline goals must be non-negative integers")
    return home, away


def _validated_picks(picks: Sequence[Sequence[int]]) -> tuple[Scoreline, ...]:
    if len(picks) != SIX_FIXTURES:
        raise ValueError(f"Super 6 opponent entries must contain {SIX_FIXTURES} picks")
    return tuple(_validated_scoreline(pick) for pick in picks)


@dataclass(frozen=True)
class OpponentRound:
    """The six scorelines one participant entered for one round.

    These records are append-only in the JSON store.  Keeping the round key
    allows profiles to use history without overwriting an old set of picks.
    """

    participant: str
    season: int
    round_number: int
    picks: tuple[Scoreline, ...]
    recorded_at: str

    def __post_init__(self) -> None:
        if not self.participant.strip():
            raise ValueError("Participant name cannot be empty")
        if self.season < 0 or self.round_number < 1:
            raise ValueError("Season and round number must be valid")
        object.__setattr__(self, "picks", _validated_picks(self.picks))

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["picks"] = [list(pick) for pick in self.picks]
        return payload


def load_opponent_history(
    path: Path = DEFAULT_OPPONENT_HISTORY_PATH,
) -> tuple[OpponentRound, ...]:
    """Load the local, user-provided opponent-pick history.

    A missing file is simply no imported history.  Malformed files fail
    loudly rather than producing a plausible but incorrect league table.
    """
    if not path.exists():
        return ()
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("Unsupported opponent-history file")
    rounds = raw.get("rounds")
    if not isinstance(rounds, list):
        raise ValueError("Opponent-history rounds must be a list")
    records = tuple(
        OpponentRound(
            participant=item["participant"],
            season=item["season"],
            round_number=item["round_number"],
            picks=tuple(tuple(pick) for pick in item["picks"]),
            recorded_at=item["recorded_at"],
        )
        for item in rounds
    )
    _assert_unique_rounds(records)
    return records


def save_opponent_history(
    history: Iterable[OpponentRound],
    path: Path = DEFAULT_OPPONENT_HISTORY_PATH,
) -> None:
    """Persist a complete, already validated opponent-pick history."""
    records = tuple(history)
    _assert_unique_rounds(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"version": 1, "rounds": [record.to_dict() for record in records]},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def record_opponent_round(
    participant: str,
    season: int,
    round_number: int,
    picks: Sequence[Sequence[int]],
    *,
    recorded_at: str | None = None,
    path: Path = DEFAULT_OPPONENT_HISTORY_PATH,
) -> OpponentRound:
    """Append six entered picks for a participant; never silently replace one."""
    record = OpponentRound(
        participant=participant,
        season=season,
        round_number=round_number,
        picks=_validated_picks(picks),
        recorded_at=recorded_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    history = load_opponent_history(path)
    key = (record.participant.casefold(), record.season, record.round_number)
    if any(
        (item.participant.casefold(), item.season, item.round_number) == key
        for item in history
    ):
        raise ValueError(
            f"Picks are already recorded for {record.participant} in "
            f"season {season}, round {round_number}"
        )
    save_opponent_history((*history, record), path)
    return record


def history_for_participant(
    history: Iterable[OpponentRound], participant: str
) -> tuple[OpponentRound, ...]:
    """Return a participant's recorded rounds in chronological order."""
    folded = participant.casefold()
    return tuple(
        sorted(
            (item for item in history if item.participant.casefold() == folded),
            key=lambda item: (item.season, item.round_number, item.recorded_at),
        )
    )


def _assert_unique_rounds(history: Iterable[OpponentRound]) -> None:
    seen: set[tuple[str, int, int]] = set()
    for item in history:
        key = (item.participant.casefold(), item.season, item.round_number)
        if key in seen:
            raise ValueError("Opponent history contains a duplicate participant round")
        seen.add(key)


@dataclass(frozen=True)
class OpponentProfile:
    participant: str
    rounds: int
    picks: int
    home_result_rate: float
    draw_result_rate: float
    away_result_rate: float
    average_home_goals: float
    average_away_goals: float
    average_total_goals: float
    common_scores: tuple[tuple[str, int], ...]


def opponent_profiles(history: Iterable[OpponentRound]) -> tuple[OpponentProfile, ...]:
    """Describe observed pick tendencies, with counts implied by history only."""
    grouped: dict[str, list[OpponentRound]] = {}
    for item in history:
        grouped.setdefault(item.participant, []).append(item)
    profiles: list[OpponentProfile] = []
    for participant, rounds in grouped.items():
        picks = [score for rnd in rounds for score in rnd.picks]
        total = len(picks)
        outcomes = [result_of(*score) for score in picks]
        common: dict[str, int] = {}
        for home, away in picks:
            label = f"{home}-{away}"
            common[label] = common.get(label, 0) + 1
        profiles.append(
            OpponentProfile(
                participant=participant,
                rounds=len(rounds),
                picks=total,
                home_result_rate=round(outcomes.count("H") / total, 4),
                draw_result_rate=round(outcomes.count("D") / total, 4),
                away_result_rate=round(outcomes.count("A") / total, 4),
                average_home_goals=round(sum(score[0] for score in picks) / total, 2),
                average_away_goals=round(sum(score[1] for score in picks) / total, 2),
                average_total_goals=round(sum(sum(score) for score in picks) / total, 2),
                common_scores=tuple(
                    sorted(common.items(), key=lambda pair: (-pair[1], pair[0]))[:3]
                ),
            )
        )
    return tuple(sorted(profiles, key=lambda profile: profile.participant.casefold()))


@dataclass(frozen=True)
class LeagueStanding:
    participant: str
    rank: int
    points: int
    exact_scores: int
    fixtures_scored: int
    rounds_scored: int


ResultKey = tuple[int, int, int]


def league_standings(
    history: Iterable[OpponentRound],
    results: Mapping[ResultKey, Scoreline | None],
) -> tuple[LeagueStanding, ...]:
    """Build a private league table from entered picks and actual results.

    ``None`` means a VOID fixture and is excluded, matching the core scoring
    semantics. Missing results are not treated as a zero-point loss.
    """
    totals: dict[str, dict[str, int | set[tuple[int, int]]]] = {}
    for record in history:
        entry = totals.setdefault(
            record.participant,
            {"points": 0, "exact": 0, "fixtures": 0, "rounds": set()},
        )
        for fixture_idx, pick in enumerate(record.picks):
            key = (record.season, record.round_number, fixture_idx)
            if key not in results or results[key] is None:
                continue
            actual = _validated_scoreline(results[key])  # type: ignore[arg-type]
            entry["points"] = int(entry["points"]) + super6_points(*pick, *actual)
            entry["exact"] = int(entry["exact"]) + int(pick == actual)
            entry["fixtures"] = int(entry["fixtures"]) + 1
            rounds = entry["rounds"]
            assert isinstance(rounds, set)
            rounds.add((record.season, record.round_number))
    ordered = sorted(
        totals.items(),
        key=lambda pair: (-int(pair[1]["points"]), -int(pair[1]["exact"]), pair[0].casefold()),
    )
    table: list[LeagueStanding] = []
    previous: tuple[int, int] | None = None
    rank = 0
    for index, (participant, values) in enumerate(ordered, start=1):
        score = (int(values["points"]), int(values["exact"]))
        if score != previous:
            rank = index
            previous = score
        rounds = values["rounds"]
        assert isinstance(rounds, set)
        table.append(
            LeagueStanding(
                participant=participant,
                rank=rank,
                points=score[0],
                exact_scores=score[1],
                fixtures_scored=int(values["fixtures"]),
                rounds_scored=len(rounds),
            )
        )
    return tuple(table)


@dataclass(frozen=True)
class PointsScenario:
    """One externally supplied simulation outcome expressed as round points."""

    scenario_id: str
    participant_points: Mapping[str, int]


@dataclass(frozen=True)
class FinishFirstEstimate:
    participant: str
    probability: float
    expected_final_points: float
    simulations: int
    tie_handling: str = "Ties split equally between leaders."


def estimate_finish_first(
    standings: Sequence[LeagueStanding],
    scenarios: Sequence[PointsScenario],
    *,
    participant: str,
    minimum_scenarios: int = 100,
) -> FinishFirstEstimate | None:
    """Estimate P(finish first) solely from complete supplied scenarios.

    ``None`` is an intentional result: it says there is not enough complete
    scenario data to show an estimate, rather than filling the gap with an
    ungrounded percentage.
    """
    if minimum_scenarios < 1:
        raise ValueError("minimum_scenarios must be positive")
    base = {row.participant: row.points for row in standings}
    if participant not in base or len(base) < 2 or len(scenarios) < minimum_scenarios:
        return None
    names = set(base)
    if len({scenario.scenario_id for scenario in scenarios}) != len(scenarios):
        raise ValueError("Simulation scenario IDs must be unique")
    if any(set(scenario.participant_points) != names for scenario in scenarios):
        return None
    shares: list[float] = []
    final_points: list[int] = []
    for scenario in scenarios:
        if any(
            isinstance(points, bool) or not isinstance(points, int) or points < 0
            for points in scenario.participant_points.values()
        ):
            raise ValueError("Simulation points must be non-negative integers")
        totals = {name: base[name] + scenario.participant_points[name] for name in names}
        maximum = max(totals.values())
        leaders = [name for name, points in totals.items() if points == maximum]
        shares.append(1 / len(leaders) if participant in leaders else 0.0)
        final_points.append(totals[participant])
    return FinishFirstEstimate(
        participant=participant,
        probability=round(sum(shares) / len(shares), 4),
        expected_final_points=round(sum(final_points) / len(final_points), 2),
        simulations=len(scenarios),
    )


@dataclass(frozen=True)
class ScorelineScenario:
    """One simulated six-fixture result set for evaluating strategy picks."""

    scenario_id: str
    results: tuple[Scoreline | None, ...]

    def __post_init__(self) -> None:
        if len(self.results) != SIX_FIXTURES:
            raise ValueError(f"A strategy scenario needs {SIX_FIXTURES} fixture results")
        for result in self.results:
            if result is not None:
                _validated_scoreline(result)


@dataclass(frozen=True)
class StrategyCandidate:
    name: str
    picks: tuple[Scoreline, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Strategy candidate name cannot be empty")
        object.__setattr__(self, "picks", _validated_picks(self.picks))


@dataclass(frozen=True)
class StrategyEvaluation:
    name: str
    expected_round_points: float
    finish_first_probability: float
    simulations: int


@dataclass(frozen=True)
class StrategyRecommendation:
    participant: str
    recommended: StrategyEvaluation
    alternatives: tuple[StrategyEvaluation, ...]
    tie_handling: str = "Ties split equally between leaders."


def recommend_strategy(
    standings: Sequence[LeagueStanding],
    round_picks: Sequence[OpponentRound],
    candidates: Sequence[StrategyCandidate],
    scenarios: Sequence[ScorelineScenario],
    *,
    participant: str,
    season: int,
    round_number: int,
    minimum_scenarios: int = 100,
) -> StrategyRecommendation | None:
    """Rank supplied candidate picks by simulated chance of finishing first.

    Opponent picks and results must cover every league participant and every
    simulated fixture.  The function therefore returns ``None`` for partial
    imports or too few scenarios rather than pretending a strategy is proven.
    """
    if minimum_scenarios < 1:
        raise ValueError("minimum_scenarios must be positive")
    if not candidates or len(scenarios) < minimum_scenarios:
        return None
    if len({candidate.name for candidate in candidates}) != len(candidates):
        raise ValueError("Strategy candidate names must be unique")
    base = {row.participant: row.points for row in standings}
    opponents = set(base) - {participant}
    if participant not in base or not opponents:
        return None
    current = {
        record.participant: record
        for record in round_picks
        if record.season == season and record.round_number == round_number
    }
    if set(current) != opponents:
        return None
    if len({scenario.scenario_id for scenario in scenarios}) != len(scenarios):
        raise ValueError("Simulation scenario IDs must be unique")

    evaluations: list[StrategyEvaluation] = []
    for candidate in candidates:
        first_place_shares: list[float] = []
        round_points: list[int] = []
        for scenario in scenarios:
            increments: dict[str, int] = {}
            for name, record in current.items():
                increments[name] = _score_pick_set(record.picks, scenario.results)
            increments[participant] = _score_pick_set(candidate.picks, scenario.results)
            totals = {name: base[name] + increments[name] for name in base}
            maximum = max(totals.values())
            leaders = [name for name, points in totals.items() if points == maximum]
            first_place_shares.append(1 / len(leaders) if participant in leaders else 0.0)
            round_points.append(increments[participant])
        evaluations.append(
            StrategyEvaluation(
                name=candidate.name,
                expected_round_points=round(sum(round_points) / len(round_points), 2),
                finish_first_probability=round(
                    sum(first_place_shares) / len(first_place_shares), 4
                ),
                simulations=len(scenarios),
            )
        )
    ordered = sorted(
        evaluations,
        key=lambda item: (-item.finish_first_probability, -item.expected_round_points, item.name),
    )
    return StrategyRecommendation(
        participant=participant,
        recommended=ordered[0],
        alternatives=tuple(ordered[1:]),
    )


def _score_pick_set(
    picks: Sequence[Scoreline], results: Sequence[Scoreline | None]
) -> int:
    return sum(
        super6_points(*pick, *actual)
        for pick, actual in zip(picks, results)
        if actual is not None
    )
