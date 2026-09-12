"""Pre-deadline, mates-relative FPL simulation for Phase 3.

Rivals' current Gameweek teams are deliberately not treated as facts.  The
input records their last *locked* squad plus a probability distribution over
manager-supplied likely current states.  Every decision option is then scored
against the same sampled player outcomes and rival-state worlds, so a higher
result is an apples-to-apples relative comparison rather than hindsight.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path


SCOPE = "PRE_DEADLINE_NAMED_OPTIONS_ONLY"


class Phase3InputError(ValueError):
    """A prospective private-league input would overstate what is known."""


@dataclass(frozen=True)
class PlayerProjection:
    name: str
    expected_points: dict[int, float]


@dataclass(frozen=True)
class ManagerPlan:
    label: str
    squad: tuple[str, ...]
    starters: tuple[str, ...]
    captains: dict[int, str]


@dataclass(frozen=True)
class RivalScenario:
    label: str
    probability: float
    plan: ManagerPlan


@dataclass(frozen=True)
class Rival:
    name: str
    starting_points: float
    previous_locked_gameweek: int
    previous_locked_squad: tuple[str, ...]
    transfer_history: tuple[dict, ...]
    scenarios: tuple[RivalScenario, ...]


@dataclass(frozen=True)
class Phase3Input:
    observed_at: str
    gameweeks: tuple[int, ...]
    our_starting_points: float
    our_options: tuple[ManagerPlan, ...]
    rivals: tuple[Rival, ...]
    players: dict[str, PlayerProjection]
    trials: int
    seed: int


def _plan(payload: dict, *, default_label: str) -> ManagerPlan:
    try:
        result = ManagerPlan(
            label=str(payload.get("label", default_label)),
            squad=tuple(str(value) for value in payload["squad"]),
            starters=tuple(str(value) for value in payload["starters"]),
            captains={int(key): str(value) for key, value in payload["captains"].items()},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Phase3InputError(f"invalid manager plan: {exc}") from exc
    if len(result.squad) != 15 or len(set(name.casefold() for name in result.squad)) != 15:
        raise Phase3InputError("each manager plan needs 15 distinct squad players")
    if len(result.starters) != 11 or not set(result.starters) <= set(result.squad):
        raise Phase3InputError("each manager plan needs 11 starters from its squad")
    return result


def parse_input(payload: dict) -> Phase3Input:
    """Parse prospective-only rival assumptions and named manager options."""
    try:
        gameweeks = tuple(int(value) for value in payload["gameweeks"])
        players = {
            str(item["name"]): PlayerProjection(
                name=str(item["name"]),
                expected_points={int(key): float(value) for key, value in item["expected_points"].items()},
            )
            for item in payload["players"]
        }
        rivals = tuple(
            Rival(
                name=str(item["name"]), starting_points=float(item["starting_points"]),
                previous_locked_gameweek=int(item["previous_locked_gameweek"]),
                previous_locked_squad=tuple(str(value) for value in item["previous_locked_squad"]),
                transfer_history=tuple(item.get("transfer_history", [])),
                scenarios=tuple(
                    RivalScenario(
                        label=str(scenario.get("label", "Likely state")), probability=float(scenario["probability"]),
                        plan=_plan(scenario, default_label=str(scenario.get("label", "Likely state"))),
                    )
                    for scenario in item["scenarios"]
                ),
            )
            for item in payload["rivals"]
        )
        result = Phase3Input(
            observed_at=str(payload["observed_at"]), gameweeks=gameweeks,
            our_starting_points=float(payload["our_starting_points"]),
            our_options=tuple(_plan(item, default_label="Our option") for item in payload["our_options"]),
            rivals=rivals, players=players, trials=int(payload.get("trials", 3000)), seed=int(payload.get("seed", 202627)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Phase3InputError(f"invalid Phase 3 input: {exc}") from exc
    if len(result.gameweeks) not in (1, 2, 3, 4) or tuple(sorted(result.gameweeks)) != result.gameweeks:
        raise Phase3InputError("gameweeks must contain 1–4 increasing Gameweeks")
    if not result.our_options or not result.rivals or not 100 <= result.trials <= 20_000:
        raise Phase3InputError("supply named options, at least one rival, and 100–20,000 trials")
    if len({option.label for option in result.our_options}) != len(result.our_options):
        raise Phase3InputError("our option labels must be distinct")
    for plan in (*result.our_options, *(scenario.plan for rival in result.rivals for scenario in rival.scenarios)):
        if set(plan.captains) != set(result.gameweeks) or any(captain not in plan.starters for captain in plan.captains.values()):
            raise Phase3InputError("each plan needs a starting captain for every planned Gameweek")
        for name in plan.squad:
            if name not in result.players or set(result.players[name].expected_points) != set(result.gameweeks):
                raise Phase3InputError(f"{name} needs a projection for every planned Gameweek")
    for rival in result.rivals:
        if rival.previous_locked_gameweek >= result.gameweeks[0]:
            raise Phase3InputError("rival state must be from a previous locked Gameweek, not the current one")
        if len(rival.previous_locked_squad) != 15 or not rival.scenarios:
            raise Phase3InputError("each rival needs a previous locked fifteen and at least one scenario")
        if not math.isclose(sum(scenario.probability for scenario in rival.scenarios), 1.0, abs_tol=1e-6):
            raise Phase3InputError("each rival's scenario probabilities must sum to 1")
    return result


def load_input(path: Path) -> Phase3Input | None:
    if not path.exists():
        return None
    try:
        return parse_input(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError) as exc:
        raise Phase3InputError(f"cannot read Phase 3 input at {path}: {exc}") from exc


def _rng(seed: int, *parts: object) -> random.Random:
    digest = hashlib.sha256("|".join(map(str, (seed, *parts))).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _poisson(mean: float, rng: random.Random) -> int:
    """Small-mean Poisson sampler; FPL xP inputs are non-negative."""
    if mean <= 0:
        return 0
    if mean > 30:  # stable normal approximation for defensive input errors/high doubles
        return max(0, round(rng.gauss(mean, math.sqrt(mean))))
    threshold = math.exp(-mean)
    count, product = 0, 1.0
    while product > threshold:
        count += 1
        product *= rng.random()
    return count - 1


def _plan_points(plan: ManagerPlan, data: Phase3Input, trial: int) -> float:
    score = 0.0
    for gameweek in data.gameweeks:
        for player in plan.starters:
            mean = data.players[player].expected_points[gameweek]
            score += _poisson(mean, _rng(data.seed, trial, gameweek, player))
        captain = plan.captains[gameweek]
        mean = data.players[captain].expected_points[gameweek]
        score += _poisson(mean, _rng(data.seed, trial, gameweek, captain))
    return score


def _pick_scenario(rival: Rival, data: Phase3Input, trial: int) -> RivalScenario:
    draw = _rng(data.seed, "rival", rival.name, trial).random()
    cumulative = 0.0
    for scenario in rival.scenarios:
        cumulative += scenario.probability
        if draw <= cumulative:
            return scenario
    return rival.scenarios[-1]


def _effective_ownership(option: ManagerPlan, data: Phase3Input) -> list[dict]:
    weights: dict[str, float] = {}
    managers = len(data.rivals) + 1
    def add(plan: ManagerPlan, weight: float) -> None:
        for gameweek in data.gameweeks:
            for player in plan.starters:
                weights[player] = weights.get(player, 0.0) + weight
            captain = plan.captains[gameweek]
            weights[captain] = weights.get(captain, 0.0) + weight
    add(option, 1.0)
    for rival in data.rivals:
        for scenario in rival.scenarios:
            add(scenario.plan, scenario.probability)
    divisor = managers * len(data.gameweeks)
    return [
        {"player": player, "effective_ownership": round(100 * weight / divisor, 1)}
        for player, weight in sorted(weights.items(), key=lambda item: (-item[1], item[0]))
    ]


def build_plan(data: Phase3Input) -> dict:
    """Evaluate named choices against the same uncertain rival-state worlds."""
    options: list[dict] = []
    for option in data.our_options:
        sole_lead = tied_lead = 0
        totals: list[float] = []
        for trial in range(data.trials):
            ours = data.our_starting_points + _plan_points(option, data, trial)
            rival_totals = [rival.starting_points + _plan_points(_pick_scenario(rival, data, trial).plan, data, trial) for rival in data.rivals]
            totals.append(ours)
            best_rival = max(rival_totals)
            if ours > best_rival:
                sole_lead += 1
            if ours >= best_rival:
                tied_lead += 1
        options.append({
            "label": option.label,
            "p_sole_lead_after_horizon": round(sole_lead / data.trials, 4),
            "p_lead_or_tied_after_horizon": round(tied_lead / data.trials, 4),
            "expected_total_after_horizon": round(sum(totals) / data.trials, 2),
            "effective_ownership": _effective_ownership(option, data),
        })
    chosen = max(options, key=lambda item: item["p_sole_lead_after_horizon"])
    return {
        "scope": SCOPE, "observed_at": data.observed_at, "gameweeks": list(data.gameweeks),
        "objective": "P(sole league lead after supplied horizon)",
        "recommended_option": chosen["label"], "options": options,
        "message": "Rival current teams are probability-weighted pre-deadline scenarios, not observed facts. Player outcomes and rival worlds are shared across every option.",
        "limitations": [
            "This estimates the supplied horizon, not season-long P(finish first).",
            "Effective ownership is for this named private-league cohort and includes captaincy.",
            "Post-deadline actual rival picks are evaluation evidence only and must not rewrite this recommendation.",
        ],
    }
