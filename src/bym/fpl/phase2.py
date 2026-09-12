"""Bounded Phase 2 FPL transfer and chip planning.

The planner consumes an explicit 2–4 Gameweek projection sheet.  It does not
fetch FPL, infer a hidden full market, or turn a manager-provided shortlist
into a global transfer claim.  That boundary makes the transfer banking,
selling-value, blank/double, price-risk and chip arithmetic useful even in the
free manual-snapshot workflow.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .lineup import recommend_lineup
from .optimiser import OptimiserCandidate
from .transfer_horizon import (
    CHIP_TOKENS,
    FREE_HITS,
    WILDCARDS,
    HorizonStep,
    OwnedPlayer,
    Transfer,
    TransferHorizonError,
    TransferState,
    TransferTarget,
    transition,
)

SCOPE = "SHORTLIST_ONLY_HORIZON"
FIXTURE_STATUSES = frozenset({"CONFIRMED", "UNCERTAIN"})


class Phase2InputError(ValueError):
    """The manager-provided, prospective Phase 2 input is incomplete."""


@dataclass(frozen=True)
class Projection:
    gameweek: int
    expected_points: float
    fixtures: int
    fixture_status: str


@dataclass(frozen=True)
class HorizonAsset:
    name: str
    position: str
    club: str
    price: float
    projections: tuple[Projection, ...]


@dataclass(frozen=True)
class PriceRisk:
    name: str
    gameweek: int
    adverse_buy_price: float
    probability: float | None


@dataclass(frozen=True)
class Phase2Input:
    observed_at: str
    gameweeks: tuple[int, ...]
    bank: float
    free_transfers: int
    chips_available: frozenset[str]
    owned: tuple[HorizonAsset, ...]
    shortlist: tuple[HorizonAsset, ...]
    price_risks: tuple[PriceRisk, ...]


@dataclass(frozen=True)
class PlannedStep:
    gameweek: int
    transfer_out: str | None
    transfer_in: str | None
    hit_cost: float
    projected_points: float
    bank_after: float
    free_transfers_after: int
    captain: str
    bench: tuple[str, ...]


@dataclass(frozen=True)
class TransferPlan:
    steps: tuple[PlannedStep, ...]
    projected_points: float
    total_hit_cost: float
    price_risks: tuple[dict, ...]
    fixture_alerts: tuple[dict, ...]


@dataclass(frozen=True)
class ChipOption:
    chip: str
    gameweek: int
    status: str
    confidence: str
    no_chip_points: float | None
    chip_points: float | None
    incremental_points: float | None
    reason: str


@dataclass(frozen=True)
class Phase2Plan:
    scope: str
    observed_at: str
    gameweeks: tuple[int, ...]
    best_transfer_plan: TransferPlan
    alternatives: tuple[dict, ...]
    chip_options: tuple[ChipOption, ...]
    message: str


def _parse_projection(payload: dict) -> Projection:
    try:
        result = Projection(
            gameweek=int(payload["gameweek"]),
            expected_points=float(payload["expected_points"]),
            fixtures=int(payload.get("fixtures", 1)),
            fixture_status=str(payload.get("fixture_status", "UNCERTAIN")).upper(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Phase2InputError(f"invalid player projection: {exc}") from exc
    if result.gameweek < 1 or result.fixtures < 0 or result.fixture_status not in FIXTURE_STATUSES:
        raise Phase2InputError("projection needs a positive GW, non-negative fixtures and valid fixture status")
    return result


def _asset(payload: dict, *, price_key: str) -> HorizonAsset:
    try:
        result = HorizonAsset(
            name=str(payload["name"]), position=str(payload["position"]), club=str(payload["club"]),
            price=float(payload[price_key]),
            projections=tuple(_parse_projection(item) for item in payload["projections"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Phase2InputError(f"invalid horizon asset: {exc}") from exc
    if not result.name or result.price < 0:
        raise Phase2InputError("assets need a name and non-negative price")
    return result


def parse_input(payload: dict) -> Phase2Input:
    """Validate a prospective, manager-controlled Phase 2 input document."""
    try:
        gameweeks = tuple(int(value) for value in payload["gameweeks"])
        result = Phase2Input(
            observed_at=str(payload["observed_at"]), gameweeks=gameweeks,
            bank=float(payload["bank"]), free_transfers=int(payload["free_transfers"]),
            chips_available=frozenset(payload.get("chips_available", [])),
            owned=tuple(_asset(item, price_key="selling_price") for item in payload["owned"]),
            shortlist=tuple(_asset(item, price_key="buy_price") for item in payload.get("shortlist", [])),
            price_risks=tuple(
                PriceRisk(
                    name=str(item["name"]), gameweek=int(item["gameweek"]),
                    adverse_buy_price=float(item["adverse_buy_price"]),
                    probability=None if item.get("probability") is None else float(item["probability"]),
                )
                for item in payload.get("price_risks", [])
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Phase2InputError(f"invalid Phase 2 input: {exc}") from exc
    if len(result.gameweeks) not in (2, 3, 4) or tuple(sorted(result.gameweeks)) != result.gameweeks:
        raise Phase2InputError("gameweeks must contain 2–4 increasing Gameweeks")
    if any(b != a + 1 for a, b in zip(result.gameweeks, result.gameweeks[1:])):
        raise Phase2InputError("gameweeks must be consecutive")
    if result.bank < 0 or not 0 <= result.free_transfers <= 5:
        raise Phase2InputError("bank/free transfers are outside valid FPL ranges")
    if result.chips_available - CHIP_TOKENS:
        raise Phase2InputError("chips_available contains an unknown 2026/27 chip token")
    names = [asset.name.casefold() for asset in (*result.owned, *result.shortlist)]
    if len(names) != len(set(names)):
        raise Phase2InputError("owned and shortlist player names must be distinct")
    for asset in (*result.owned, *result.shortlist):
        if {item.gameweek for item in asset.projections} != set(result.gameweeks):
            raise Phase2InputError(f"{asset.name} needs one projection for every planned Gameweek")
    return result


def load_input(path: Path) -> Phase2Input | None:
    if not path.exists():
        return None
    try:
        return parse_input(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError) as exc:
        raise Phase2InputError(f"cannot read Phase 2 input at {path}: {exc}") from exc


def _assets_by_id(data: Phase2Input) -> tuple[dict[int, HorizonAsset], TransferState, dict[int, TransferTarget]]:
    assets: dict[int, HorizonAsset] = {}
    owned: list[OwnedPlayer] = []
    for player_id, asset in enumerate(data.owned, start=1):
        assets[player_id] = asset
        owned.append(OwnedPlayer(player_id, asset.position, asset.club, asset.price))
    targets: dict[int, TransferTarget] = {}
    for player_id, asset in enumerate(data.shortlist, start=1001):
        assets[player_id] = asset
        targets[player_id] = TransferTarget(player_id, asset.position, asset.club, asset.price)
    return assets, TransferState(
        gameweek=data.gameweeks[0], squad=tuple(owned), bank=data.bank,
        free_transfers=data.free_transfers, chips_available=data.chips_available,
    ), targets


def _projection(asset: HorizonAsset, gameweek: int) -> Projection:
    return next(item for item in asset.projections if item.gameweek == gameweek)


def _score(state: TransferState, gameweek: int, assets: dict[int, HorizonAsset]) -> tuple[float, str, tuple[str, ...]]:
    candidates = [
        OptimiserCandidate(
            player_id=player.player_id, position=player.position, club=player.club,
            price=player.selling_price, expected_points=_projection(assets[player.player_id], gameweek).expected_points,
        )
        for player in state.squad
    ]
    selection = recommend_lineup(candidates)
    return (
        selection.net_expected_points,
        assets[selection.captain_id].name,
        tuple(assets[player_id].name for player_id in selection.bench_order),
    )


def _actions(state: TransferState, targets: dict[int, TransferTarget]) -> tuple[HorizonStep, ...]:
    owned_ids = {player.player_id for player in state.squad}
    options = [HorizonStep()]
    for target_id, target in targets.items():
        if target_id in owned_ids:
            continue
        for outgoing in state.squad:
            if outgoing.position == target.position:
                options.append(HorizonStep((Transfer(outgoing.player_id, target),)))
    return tuple(options)


def _price_alerts(data: Phase2Input, steps: tuple[PlannedStep, ...], states_before: tuple[TransferState, ...], assets: dict[int, HorizonAsset]) -> tuple[dict, ...]:
    alerts: list[dict] = []
    risk_by_key = {(risk.name.casefold(), risk.gameweek): risk for risk in data.price_risks}
    for step, state in zip(steps, states_before):
        if not step.transfer_in or not step.transfer_out:
            continue
        risk = risk_by_key.get((step.transfer_in.casefold(), step.gameweek))
        if risk is None:
            continue
        outgoing = next(player for player in state.squad if assets[player.player_id].name == step.transfer_out)
        affordable = state.bank + outgoing.selling_price >= risk.adverse_buy_price
        if not affordable:
            alerts.append({
                "gameweek": step.gameweek, "player": step.transfer_in,
                "status": "BUDGET_INVALID_IF_PRICE_MOVES",
                "detail": f"£{risk.adverse_buy_price:.1f}m adverse price would exceed recorded cash after selling {step.transfer_out}.",
                "probability": risk.probability,
            })
    return tuple(alerts)


def _fixture_alerts(data: Phase2Input, assets: dict[int, HorizonAsset], steps: tuple[PlannedStep, ...], states: tuple[TransferState, ...]) -> tuple[dict, ...]:
    alerts: list[dict] = []
    for step, state in zip(steps, states):
        for player in state.squad:
            projection = _projection(assets[player.player_id], step.gameweek)
            if projection.fixtures != 1 or projection.fixture_status == "UNCERTAIN":
                alerts.append({
                    "gameweek": step.gameweek, "player": assets[player.player_id].name,
                    "fixtures": projection.fixtures, "fixture_status": projection.fixture_status,
                    "kind": "BLANK" if projection.fixtures == 0 else "DOUBLE" if projection.fixtures > 1 else "UNCERTAIN_FIXTURE",
                })
    return tuple(alerts)


def _chip_options(data: Phase2Input, best: TransferPlan, states: tuple[TransferState, ...], assets: dict[int, HorizonAsset]) -> tuple[ChipOption, ...]:
    options: list[ChipOption] = []
    for step, state in zip(best.steps, states):
        captain = next(player for player in state.squad if assets[player.player_id].name == step.captain)
        bench_points = sum(_projection(assets[player.player_id], step.gameweek).expected_points for player in state.squad if assets[player.player_id].name in step.bench)
        captain_points = _projection(assets[captain.player_id], step.gameweek).expected_points
        uncertain = any(_projection(assets[player.player_id], step.gameweek).fixture_status == "UNCERTAIN" for player in state.squad)
        for chip in sorted(data.chips_available):
            if chip.endswith("first_half") != (step.gameweek <= 19):
                continue
            if chip in WILDCARDS or chip in FREE_HITS:
                options.append(ChipOption(chip, step.gameweek, "WATCH", "LOW", None, None, None, "Needs a full transfer universe and same-world squad alternatives; not estimated from a shortlist."))
            elif chip.startswith("bench_boost"):
                options.append(ChipOption(chip, step.gameweek, "EVALUATED", "LOW" if uncertain else "MEDIUM", step.projected_points, step.projected_points + bench_points, bench_points, "Compared against the same no-chip transfer path."))
            else:
                options.append(ChipOption(chip, step.gameweek, "EVALUATED", "LOW" if uncertain else "MEDIUM", step.projected_points, step.projected_points + captain_points, captain_points, "Compared against the same no-chip transfer path."))
    return tuple(options)


def build_plan(data: Phase2Input) -> Phase2Plan:
    """Enumerate hold/one-shortlisted-transfer paths across 2–4 Gameweeks."""
    assets, initial, targets = _assets_by_id(data)
    routes: list[tuple[tuple[PlannedStep, ...], tuple[TransferState, ...], tuple[TransferState, ...], float, float]] = []

    def visit(state: TransferState, index: int, steps: tuple[PlannedStep, ...], before: tuple[TransferState, ...], after: tuple[TransferState, ...], points: float, hits: float) -> None:
        if index == len(data.gameweeks):
            routes.append((steps, before, after, points, hits))
            return
        gameweek = data.gameweeks[index]
        for action in _actions(state, targets):
            try:
                applied = transition(state, action)
            except TransferHorizonError:
                continue
            projected, captain, bench = _score(applied.state_after, gameweek, assets)
            transfer_out = action.transfers[0].player_out_id if action.transfers else None
            transfer_in = action.transfers[0].player_in.player_id if action.transfers else None
            visit(
                applied.state_after, index + 1,
                steps + (PlannedStep(
                    gameweek,
                    assets[transfer_out].name if transfer_out else None,
                    assets[transfer_in].name if transfer_in else None,
                    applied.hit_cost,
                    projected - applied.hit_cost,
                    applied.state_after.bank,
                    applied.state_after.free_transfers,
                    captain,
                    bench,
                ),),
                before + (state,), after + (applied.state_after,), points + projected - applied.hit_cost, hits + applied.hit_cost,
            )

    visit(initial, 0, (), (), (), 0.0, 0.0)
    if not routes:
        raise Phase2InputError("no legal path exists for the provided squad and shortlist")
    # Hold paths are generated before moves; that makes a tie a deliberate
    # hold rather than arbitrary shortlist churn.
    best_steps, before, after, points, hits = max(routes, key=lambda route: route[3])
    best = TransferPlan(
        steps=best_steps, projected_points=points, total_hit_cost=hits,
        price_risks=_price_alerts(data, best_steps, before, assets),
        fixture_alerts=_fixture_alerts(data, assets, best_steps, after),
    )
    # A concise decision surface needs hold and every feasible named first
    # move compared in the same horizon.  Keep the best continuation for each
    # first move; dumping every combinatorial continuation would make a four-
    # week shortlist unusable while adding no distinct current decision.
    by_first_action: dict[tuple[str | None, str | None], tuple] = {}
    for route in routes:
        first = route[0][0]
        key = (first.transfer_out, first.transfer_in)
        incumbent = by_first_action.get(key)
        if incumbent is None or route[3] > incumbent[3]:
            by_first_action[key] = route
    alternatives = []
    for route in sorted(by_first_action.values(), key=lambda item: item[3], reverse=True):
        steps, _, states_after, route_points, route_hits = route
        first = steps[0]
        alternatives.append({
            "label": "Hold" if first.transfer_in is None else f"{first.transfer_out} → {first.transfer_in}",
            "selected": steps == best_steps,
            "projected_points": round(route_points, 2),
            "difference_from_selected": round(route_points - points, 2),
            "total_hit_cost": route_hits,
            "end_bank": round(states_after[-1].bank, 2),
            "free_transfers_after_horizon": states_after[-1].free_transfers,
        })
    chips = _chip_options(data, best, after, assets)
    return Phase2Plan(
        scope=SCOPE, observed_at=data.observed_at, gameweeks=data.gameweeks,
        best_transfer_plan=best, alternatives=tuple(alternatives), chip_options=chips,
        message="Projected from the supplied squad, named shortlist and 2–4 GW sheet only; omitted players are never searched.",
    )


def as_payload(plan: Phase2Plan) -> dict:
    return {
        "scope": plan.scope, "observed_at": plan.observed_at, "gameweeks": list(plan.gameweeks), "message": plan.message,
        "best_transfer_plan": {
            "projected_points": round(plan.best_transfer_plan.projected_points, 2),
            "total_hit_cost": plan.best_transfer_plan.total_hit_cost,
            "steps": [{
                "gameweek": step.gameweek, "transfer_out": step.transfer_out, "transfer_in": step.transfer_in,
                "hit_cost": step.hit_cost, "projected_points": round(step.projected_points, 2),
                "bank_after": round(step.bank_after, 2),
                "free_transfers_after": step.free_transfers_after,
                "captain": step.captain, "bench": list(step.bench),
            } for step in plan.best_transfer_plan.steps],
            "price_risks": list(plan.best_transfer_plan.price_risks),
            "fixture_alerts": list(plan.best_transfer_plan.fixture_alerts),
        },
        "alternatives": list(plan.alternatives),
        "chip_options": [option.__dict__ for option in plan.chip_options],
    }
