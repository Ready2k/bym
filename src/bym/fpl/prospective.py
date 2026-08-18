"""Prospective Gameweek-by-Gameweek measurement for FPL (SPEC_FPL.md
§11.2, §12.1, §12.3, §18 Phase 1B).

Mirrors `bym.prospective`'s discipline for Super 6/LMS: there is no
unspent FPL holdout — no Gameweek has ever been played under this
baseline — so the only honest evaluation from Gameweek 1 onward is
prospective: record each arm's decision *before* the deadline, score it
only once the Gameweek has reached FINAL status, never reconstruct a
decision after seeing results (§12.3: "record its decisions prospectively
and append-only").

Arms are rows, not columns, so later phases append without disturbing the
frozen comparison:

    BASELINE_FPL_001   the frozen baseline (fpl.projection.MODEL_VERSION,
                        docs/MANIFEST_FPL.md)
    SIMPLE_XP          highest-nominal-xP captain/XI, no contingency
                        awareness — the fair, nameable baseline
                        fpl.backtest.fpl_decision_quality already
                        compares against, not a strawman invented here
    SUBMITTED          what the user actually set in their own FPL squad
                        — the identical Sky-boundary discipline SPEC.md's
                        Super 6 SUBMITTED arm already uses: recorded only
                        as the user's own confirmation, never inferred
                        or automated (SPEC_FPL.md §14)

The ledger is append-only. A recorded row is never rewritten except to
fill in the actual outcome and its points once a Gameweek reaches FINAL
(§4, §11.3) — `score_gameweek` refuses outright otherwise via
`fpl.causality.assert_final`, the same discipline
`validation.assert_holdout_unspent` applies to a season's holdout,
restated here for one Gameweek's settlement.

This module builds the RECORDING infrastructure only. It does not run
itself against 2026/27 — there is no live data to record yet, and
fabricating a run would be exactly the "acquire holdout status through
wording" §12.1 warns against.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .causality import GameweekSettlement, assert_final

BASELINE_ARM = "BASELINE_FPL_001"
SIMPLE_XP_ARM = "SIMPLE_XP"
SUBMITTED_ARM = "SUBMITTED"

DEFAULT_ARM_REGISTRY = Path("results/fpl_prospective_arms.json")

# Arms present from Gameweek 1. Later arms must be registered *before*
# their first Gameweek and can never be backfilled — an arm that appears
# retrospectively is indistinguishable from one fitted to known results.
BASE_ARMS = (BASELINE_ARM, SIMPLE_XP_ARM, SUBMITTED_ARM)

COLUMNS = [
    "season",
    "gameweek",
    "player_id",
    "arm",
    "starter",
    "captain",
    "predicted_points",
    "recorded_at",
    "actual_points",
    "status",
]

OK_STATUS = "OK"

_TEXT_COLUMNS = ("arm", "recorded_at", "status")
_BOOL_COLUMNS = ("starter", "captain")


def _normalise_dtypes(ledger: pd.DataFrame) -> pd.DataFrame:
    for col in _TEXT_COLUMNS:
        if col in ledger.columns:
            ledger[col] = ledger[col].astype(object)
    for col in _BOOL_COLUMNS:
        if col in ledger.columns:
            ledger[col] = ledger[col].astype(bool)
    return ledger


def load_arm_registry(path: Path = DEFAULT_ARM_REGISTRY) -> dict[str, str]:
    """arm -> ISO date it was registered."""
    if not path.exists():
        today = datetime.now(timezone.utc).date().isoformat()
        return {arm: today for arm in BASE_ARMS}
    return json.loads(path.read_text())


def register_arm(
    arm: str, registered_at: str | None = None, path: Path = DEFAULT_ARM_REGISTRY
) -> dict[str, str]:
    """Pre-register a measurement arm. Refuses to re-register, so a
    registration date cannot be quietly moved earlier."""
    registry = load_arm_registry(path)
    if arm in registry:
        raise ValueError(
            f"'{arm}' is already registered ({registry[arm]}). A registration "
            "date may not be changed — it is what proves the arm was not "
            "created after seeing results."
        )
    registry[arm] = registered_at or datetime.now(timezone.utc).date().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    return registry


def assert_arm_registered(arm: str, path: Path = DEFAULT_ARM_REGISTRY) -> str:
    registry = load_arm_registry(path)
    if arm not in registry:
        raise ValueError(
            f"Arm '{arm}' is not registered. Register it with "
            "`register_arm` BEFORE its first Gameweek — arms cannot be "
            "backfilled, because an arm added after the fact is "
            "indistinguishable from one fitted to known results."
        )
    return registry[arm]


def load_ledger(path: Path) -> pd.DataFrame:
    if not path.exists():
        return _normalise_dtypes(pd.DataFrame(columns=COLUMNS))
    return _normalise_dtypes(pd.read_csv(path))


def save_ledger(ledger: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger.to_csv(path, index=False)


def already_recorded(ledger: pd.DataFrame, season: int, gameweek: int, arm: str) -> bool:
    if ledger.empty:
        return False
    return bool(
        (
            (ledger["season"] == season)
            & (ledger["gameweek"] == gameweek)
            & (ledger["arm"] == arm)
        ).any()
    )


def record_squad(
    ledger: pd.DataFrame,
    season: int,
    gameweek: int,
    arm: str,
    squad_ids: frozenset[int],
    starter_ids: frozenset[int],
    captain_id: int,
    predicted_points_by_player: dict[int, float],
    arm_registry_path: Path = DEFAULT_ARM_REGISTRY,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Append one arm's full 15-player squad decision for a Gameweek —
    bench players are recorded too (starter=False), not just the XI,
    since their predicted-vs-actual matters for autosub evaluation.
    Refuses to overwrite an arm already recorded for that Gameweek (a
    prospective record that can be revised is not a prospective record)
    and refuses an unregistered arm outright.
    """
    assert_arm_registered(arm, arm_registry_path)
    if already_recorded(ledger, season, gameweek, arm):
        raise ValueError(
            f"{arm} already recorded for season {season} gameweek {gameweek}. "
            "Prospective picks are append-only; delete the rows manually "
            "and document why if this is genuinely a correction."
        )
    recorded_at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    rows = [
        {
            "season": season,
            "gameweek": gameweek,
            "player_id": player_id,
            "arm": arm,
            "starter": player_id in starter_ids,
            "captain": player_id == captain_id,
            "predicted_points": predicted_points_by_player.get(player_id, 0.0),
            "recorded_at": recorded_at,
            "actual_points": pd.NA,
            "status": pd.NA,
        }
        for player_id in squad_ids
    ]
    combined = pd.concat([ledger, pd.DataFrame(rows)], ignore_index=True)
    return _normalise_dtypes(combined)


def score_gameweek(
    ledger: pd.DataFrame,
    season: int,
    gameweek: int,
    actual_points_by_player: dict[int, float],
    gameweek_status: str,
) -> pd.DataFrame:
    """Fill in actual points for every arm recorded for this Gameweek.

    Refuses outright unless `gameweek_status == "FINAL"`
    (`fpl.causality.assert_final`) — a PROVISIONAL result may be shown
    elsewhere in the UI but must never be written into this permanent
    evaluation ledger, since BPS/DefCon figures genuinely still move
    during Opta's post-match review window (§4, §11.3, §12.1).
    """
    assert_final(
        GameweekSettlement(status=gameweek_status),
        context=f"fpl_prospective ledger write season={season} gameweek={gameweek}",
    )
    ledger = _normalise_dtypes(ledger.copy())
    mask = (ledger["season"] == season) & (ledger["gameweek"] == gameweek)
    if not mask.any():
        raise ValueError(f"No recorded squads for season {season} gameweek {gameweek}")
    for player_id, points in actual_points_by_player.items():
        rows = mask & (ledger["player_id"] == player_id)
        ledger.loc[rows, "actual_points"] = points
        ledger.loc[rows, "status"] = OK_STATUS
    return ledger


def standings(ledger: pd.DataFrame) -> pd.DataFrame:
    """Running comparison across arms: realised squad points per Gameweek
    — the sum of every STARTER's actual points, with the captain's
    actual points counted twice (the real FPL doubling rule), for every
    scored (`status == OK`) Gameweek. Bench points are excluded here
    deliberately (autosub coverage is a separate, already-evaluated
    concern — `fpl.backtest.fpl_decision_quality` — not re-derived here).
    """
    scored = ledger[ledger["status"] == OK_STATUS].copy()
    if scored.empty:
        return pd.DataFrame(columns=["arm", "gameweeks", "points", "pts/gameweek"])
    scored["actual_points"] = scored["actual_points"].astype(float)

    rows = []
    for arm, g in scored.groupby("arm"):
        total_points = 0.0
        n_gameweeks = 0
        for (season, gameweek), gw_group in g.groupby(["season", "gameweek"]):
            starters = gw_group[gw_group["starter"]]
            gw_points = starters["actual_points"].sum()
            captain_rows = starters[starters["captain"]]
            if not captain_rows.empty:
                gw_points += captain_rows["actual_points"].sum()  # doubled
            total_points += gw_points
            n_gameweeks += 1
        rows.append(
            {
                "arm": arm,
                "gameweeks": n_gameweeks,
                "points": round(total_points, 2),
                "pts/gameweek": round(total_points / n_gameweeks, 2) if n_gameweeks else 0.0,
            }
        )
    return pd.DataFrame(rows).sort_values("pts/gameweek", ascending=False)
