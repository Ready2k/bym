"""Field-completeness / identity-resolution gate for a harvested corpus
(SPEC_FPL.md §5.2, §18 Phase 1B — the check the user's 2026-08-17 harvest
approval explicitly required: "if a component used by BASELINE_FPL_001
has unexpectedly poor historical coverage, stop before scoring the
model. Don't let the projection engine quietly interpret provider
missingness as footballing zero.")

Two field groups, reported separately because they gate differently:

- **BASELINE_FPL_001-critical**: `minutes`, `goals`, `assists`, GK
  `saves`, and player-identity resolution — every one of these directly
  feeds `fpl.projection.historical_profile`/`fpl.scoring`. Poor coverage
  here is a reason to stop before running the baseline for real.
- **captured, not yet used**: `shots`, `tackles`, `interceptions` — this
  ingester persists them (useful for a future DefCon/xG audition, §5.4),
  but BASELINE_FPL_001 does not read them at all, so their coverage,
  however poor, cannot invalidate the baseline itself — it only tells a
  future refinement what it would be working with.

`minutes`/`goals`/`assists` are NOT NULL by schema (`fpl_player_match_
stats`, migration 0008) and therefore always report 100% "field present"
— that is a true, not a misleading, number: this ingester never persists
a row without them (§4's ingest.py drops zero-minute non-appearances
entirely rather than writing a hollow row). The genuinely diagnostic
numbers are the nullable fields and the identity-resolution rate.

This module only REPORTS. It never freezes a corpus, never runs a model,
and never decides anything on the user's behalf — `gate_result` is
returned for a human to read, not acted on automatically.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import player_store
from .player_match_stats import PlayerMatchStats, stats_for_season
from .players import Position

# BASELINE_FPL_001-critical thresholds — provisional defaults, not
# backtested; a human reviews the actual numbers regardless; these only
# decide whether `gate_result.passed` is a friendly green light.
MIN_PLAYER_ID_RESOLVED_PCT = 95.0
MIN_GK_SAVES_PCT = 70.0


@dataclass(frozen=True)
class SeasonFieldCompleteness:
    season: int
    n_rows: int
    n_players: int
    n_gk_rows: int
    minutes_pct: float
    starts_pct: float  # NOT a nullness check — % of rows with minutes>=60
    goals_pct: float
    assists_pct: float
    saves_pct_of_gk_rows: float  # GK rows only; NaN if n_gk_rows == 0
    shots_pct: float
    tackles_pct: float
    interceptions_pct: float
    player_id_resolved_pct: float  # % of DISTINCT players with a provider id


def _pct_present(rows: list[PlayerMatchStats], field: str) -> float:
    if not rows:
        return float("nan")
    return 100.0 * sum(1 for r in rows if getattr(r, field) is not None) / len(rows)


def field_completeness(conn: sqlite3.Connection, season: int) -> SeasonFieldCompleteness:
    rows = stats_for_season(conn, season)
    n = len(rows)
    player_ids = {r.player_id for r in rows}

    resolved = 0
    gk_rows: list[PlayerMatchStats] = []
    for pid in player_ids:
        ps = player_store.get_player_season(conn, pid, season)
        if ps and "api_football" in ps.external_ids:
            resolved += 1
        if ps and ps.position == Position.GK.value:
            gk_rows.extend(r for r in rows if r.player_id == pid)

    return SeasonFieldCompleteness(
        season=season,
        n_rows=n,
        n_players=len(player_ids),
        n_gk_rows=len(gk_rows),
        minutes_pct=_pct_present(rows, "minutes"),
        starts_pct=(100.0 * sum(1 for r in rows if r.minutes >= 60) / n) if n else float("nan"),
        goals_pct=_pct_present(rows, "goals"),
        assists_pct=_pct_present(rows, "assists"),
        saves_pct_of_gk_rows=_pct_present(gk_rows, "saves"),
        shots_pct=_pct_present(rows, "shots"),
        tackles_pct=_pct_present(rows, "tackles"),
        interceptions_pct=_pct_present(rows, "interceptions"),
        player_id_resolved_pct=(100.0 * resolved / len(player_ids)) if player_ids else float("nan"),
    )


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reasons: list[str]  # empty iff passed


def gate_check(
    reports: list[SeasonFieldCompleteness],
    min_player_id_resolved_pct: float = MIN_PLAYER_ID_RESOLVED_PCT,
    min_gk_saves_pct: float = MIN_GK_SAVES_PCT,
) -> GateResult:
    """Checks only the BASELINE_FPL_001-critical fields — shots/tackles/
    interceptions coverage is reported but never fails this gate, since
    the baseline does not read them (see module docstring)."""
    reasons: list[str] = []
    for r in reports:
        if r.n_rows == 0:
            reasons.append(f"season {r.season}: no rows harvested at all")
            continue
        if r.player_id_resolved_pct < min_player_id_resolved_pct:
            reasons.append(
                f"season {r.season}: player_id_resolved={r.player_id_resolved_pct:.1f}% "
                f"< {min_player_id_resolved_pct}%"
            )
        if r.n_gk_rows > 0 and r.saves_pct_of_gk_rows < min_gk_saves_pct:
            reasons.append(
                f"season {r.season}: GK saves coverage={r.saves_pct_of_gk_rows:.1f}% "
                f"< {min_gk_saves_pct}% (of {r.n_gk_rows} GK rows)"
            )
    return GateResult(passed=not reasons, reasons=reasons)


def render_markdown(reports: list[SeasonFieldCompleteness], gate: GateResult) -> str:
    def fmt(pct: float) -> str:
        return "n/a" if pct != pct else f"{pct:.1f}%"  # NaN != NaN

    header = " | ".join(f"{r.season}/{(r.season + 1) % 100:02d}" for r in reports)
    lines = [
        "# FPL Corpus Field-Completeness / Identity Gate",
        "",
        "## BASELINE_FPL_001-critical fields",
        "",
        f"| Field | {header} |",
        "|---" * (len(reports) + 1) + "|",
        "| minutes | " + " | ".join(fmt(r.minutes_pct) for r in reports) + " |",
        "| starts (60+ min rate) | " + " | ".join(fmt(r.starts_pct) for r in reports) + " |",
        "| goals | " + " | ".join(fmt(r.goals_pct) for r in reports) + " |",
        "| assists | " + " | ".join(fmt(r.assists_pct) for r in reports) + " |",
        "| GK saves (of GK rows) | " + " | ".join(fmt(r.saves_pct_of_gk_rows) for r in reports) + " |",
        "| player ID resolved | " + " | ".join(fmt(r.player_id_resolved_pct) for r in reports) + " |",
        "",
        "## Captured, not read by BASELINE_FPL_001 (future DefCon/xG audition evidence only)",
        "",
        f"| Field | {header} |",
        "|---" * (len(reports) + 1) + "|",
        "| shots | " + " | ".join(fmt(r.shots_pct) for r in reports) + " |",
        "| tackles | " + " | ".join(fmt(r.tackles_pct) for r in reports) + " |",
        "| interceptions | " + " | ".join(fmt(r.interceptions_pct) for r in reports) + " |",
        "",
        "## Row/player counts",
        "",
        f"| | {header} |",
        "|---" * (len(reports) + 1) + "|",
        "| rows | " + " | ".join(str(r.n_rows) for r in reports) + " |",
        "| distinct players | " + " | ".join(str(r.n_players) for r in reports) + " |",
        "| GK rows | " + " | ".join(str(r.n_gk_rows) for r in reports) + " |",
        "",
        "## Gate verdict",
        "",
        "**PASSED**" if gate.passed else "**FAILED — stop before scoring the model**",
        "",
    ]
    if gate.reasons:
        lines += [f"- {reason}" for reason in gate.reasons]
    return "\n".join(lines) + "\n"
