"""Prospective round-by-round measurement for 2026/27.

There is no unspent holdout season left (VALIDATION.md), so from Round 1
the only honest evaluation is prospective: record each arm's picks
*before* the deadline, score them when results arrive, never reconstruct
them afterwards.

Arms are rows, not columns, so later phases append without disturbing the
frozen comparison:

    PHASE1_MODEL_V1   the frozen quantitative core (config.FROZEN_PHASE1_V1)
    MARKET_AVAILABLE  favourite from the odds entered at prediction time
    SUBMITTED         what the user actually entered into Super 6
    PHASE2_AGENT      (later) always-on intelligence layer
    PHASE3_STRATEGY   (later) opponent-aware strategic picks

Prospectively, MARKET_AVAILABLE is *exact* rather than the Friday-price
proxy used in backtesting: the odds recorded in the round file are, by
construction, prices that existed before the deadline.

The ledger is an append-only CSV. A recorded row is never rewritten
except to fill in the actual result and its points — recording a pick and
later editing it would defeat the entire point.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .scoring import super6_points

FROZEN_ARM = "PHASE1_MODEL_V1"
MARKET_ARM = "MARKET_AVAILABLE"
SUBMITTED_ARM = "SUBMITTED"
PHASE2_ARM = "PHASE2_AGENT"
PHASE3_ARM = "PHASE3_STRATEGY"

DEFAULT_ARM_REGISTRY = Path("results/prospective_arms.json")

# Arms present from Round 1. Later arms must be registered *before* their
# first round and can never be backfilled — an arm that appears
# retrospectively is indistinguishable from one fitted to known results.
BASE_ARMS = (FROZEN_ARM, MARKET_ARM, SUBMITTED_ARM)

COLUMNS = [
    "season",
    "round",
    "fixture_idx",
    "home_team",
    "away_team",
    "arm",
    "pred_home",
    "pred_away",
    "recorded_at",
    "actual_home",
    "actual_away",
    "points",
    "status",
]

# §2.2: a VOID fixture scores nothing and is excluded from performance
# statistics, but stays in the ledger rather than vanishing.
VOID_STATUS = "VOID"
OK_STATUS = "OK"

FAVOURITE_SCORELINES = {"H": (2, 1), "D": (1, 1), "A": (1, 2)}


# Columns holding strings. An all-empty column round-trips through CSV as
# float64, which then rejects a string assignment — so dtypes are pinned
# explicitly on load rather than inferred.
_TEXT_COLUMNS = ("home_team", "away_team", "arm", "recorded_at", "status")


def _normalise_dtypes(ledger: pd.DataFrame) -> pd.DataFrame:
    for col in _TEXT_COLUMNS:
        if col in ledger.columns:
            ledger[col] = ledger[col].astype(object)
    return ledger


def load_arm_registry(path: Path = DEFAULT_ARM_REGISTRY) -> dict[str, str]:
    """arm -> ISO date it was registered."""
    if not path.exists():
        return {arm: "2026-08-07" for arm in BASE_ARMS}
    return json.loads(path.read_text())


def register_arm(
    arm: str,
    registered_at: str | None = None,
    path: Path = DEFAULT_ARM_REGISTRY,
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


def assert_arm_registered(
    arm: str, path: Path = DEFAULT_ARM_REGISTRY
) -> str:
    registry = load_arm_registry(path)
    if arm not in registry:
        raise ValueError(
            f"Arm '{arm}' is not registered. Register it with "
            "`register_arm` BEFORE its first round — arms cannot be "
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


def market_pick(odds: tuple[float, float, float]) -> tuple[int, int]:
    """Favourite result from odds, with the fixed scoreline convention
    used throughout (H->2-1, D->1-1, A->1-2)."""
    inv = [1.0 / o for o in odds]
    result = "HDA"[inv.index(max(inv))]
    return FAVOURITE_SCORELINES[result]


def already_recorded(ledger: pd.DataFrame, season: int, rnd: int, arm: str) -> bool:
    if ledger.empty:
        return False
    return bool(
        (
            (ledger["season"] == season)
            & (ledger["round"] == rnd)
            & (ledger["arm"] == arm)
        ).any()
    )


def record_picks(
    ledger: pd.DataFrame,
    season: int,
    rnd: int,
    fixtures: list[tuple[str, str]],
    arm: str,
    picks: list[tuple[int, int]],
    arm_registry_path: Path = DEFAULT_ARM_REGISTRY,
) -> pd.DataFrame:
    """Append one arm's picks for a round. Refuses to overwrite an arm
    already recorded for that round — a prospective record that can be
    revised is not a prospective record — and refuses an unregistered arm
    outright."""
    assert_arm_registered(arm, arm_registry_path)
    if already_recorded(ledger, season, rnd, arm):
        raise ValueError(
            f"{arm} already recorded for season {season} round {rnd}. "
            "Prospective picks are append-only; delete the rows manually "
            "and document why if this is genuinely a correction."
        )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [
        {
            "season": season,
            "round": rnd,
            "fixture_idx": i,
            "home_team": home,
            "away_team": away,
            "arm": arm,
            "pred_home": ph,
            "pred_away": pa,
            "recorded_at": now,
            "actual_home": pd.NA,
            "actual_away": pd.NA,
            "points": pd.NA,
            "status": pd.NA,
        }
        for i, ((home, away), (ph, pa)) in enumerate(zip(fixtures, picks))
    ]
    combined = pd.concat([ledger, pd.DataFrame(rows)], ignore_index=True)
    return _normalise_dtypes(combined)


def score_round(
    ledger: pd.DataFrame,
    season: int,
    rnd: int,
    results: dict[int, tuple[int, int] | None],
) -> pd.DataFrame:
    """Fill in actual results and points for every arm in a round.
    `results` maps fixture_idx -> (home_goals, away_goals), or None for a
    VOID fixture (§2.2)."""
    ledger = _normalise_dtypes(ledger.copy())
    mask = (ledger["season"] == season) & (ledger["round"] == rnd)
    for idx, actual in results.items():
        rows = mask & (ledger["fixture_idx"] == idx)
        if not rows.any():
            raise ValueError(f"No recorded picks for fixture_idx {idx}")
        if actual is None:
            ledger.loc[rows, "status"] = VOID_STATUS
            ledger.loc[rows, ["actual_home", "actual_away", "points"]] = pd.NA
            continue
        ah, aa = actual
        ledger.loc[rows, "actual_home"] = ah
        ledger.loc[rows, "actual_away"] = aa
        ledger.loc[rows, "status"] = OK_STATUS
        ledger.loc[rows, "points"] = [
            super6_points(int(r["pred_home"]), int(r["pred_away"]), ah, aa)
            for _, r in ledger[rows].iterrows()
        ]
    return ledger


def standings(ledger: pd.DataFrame) -> pd.DataFrame:
    """Running comparison across arms. VOID fixtures are excluded from
    every statistic (§2.2), never counted as zero."""
    scored = ledger[ledger["status"] == OK_STATUS].copy()
    if scored.empty:
        return pd.DataFrame(
            columns=["arm", "fixtures", "rounds", "points", "pts/round", "exact", "exact%"]
        )
    scored["points"] = scored["points"].astype(int)
    scored["exact"] = (
        (scored["pred_home"] == scored["actual_home"])
        & (scored["pred_away"] == scored["actual_away"])
    ).astype(int)

    rows = []
    for arm, g in scored.groupby("arm"):
        n_rounds = g[["season", "round"]].drop_duplicates().shape[0]
        rows.append(
            {
                "arm": arm,
                "fixtures": len(g),
                "rounds": n_rounds,
                "points": int(g["points"].sum()),
                "pts/round": round(g["points"].sum() / n_rounds, 2) if n_rounds else 0.0,
                "exact": int(g["exact"].sum()),
                "exact%": round(100 * g["exact"].mean(), 2),
            }
        )
    return pd.DataFrame(rows).sort_values("pts/round", ascending=False)
