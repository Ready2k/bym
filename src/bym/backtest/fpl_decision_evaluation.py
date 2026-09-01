"""Reproducible Phase 1B optimiser-baseline and stability evidence.

Historical player statistics can assess the frozen projection model, but this
project has no permitted historical FPL price/squad snapshots with which to
claim a realised transfer backtest.  This module therefore tests a narrower,
honest question: does the optimiser's contingency-aware top-K search differ
from the legitimate single-solve nominal baseline, and at what search width
does that choice stabilise on a known adverse case?

It is an algorithmic regression/stability evaluation, not evidence that a
specific live transfer would have paid off.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..fpl.optimiser import OptimiserCandidate, SquadSelection
from . import fpl_decision_quality as decision_quality

CANONICAL_BANK = 64.0
DEFAULT_KS = (1, 5, 10, 25, 50, 100)


def _candidate(
    player_id: int,
    position: str,
    club: str,
    expected_points: float,
    *,
    price: float = 4.0,
    p_appearance: float = 1.0,
) -> OptimiserCandidate:
    return OptimiserCandidate(
        player_id=player_id,
        position=position,
        club=club,
        price=price,
        expected_points=expected_points,
        p_appearance=p_appearance,
    )


def canonical_bench_reliability_pool() -> list[OptimiserCandidate]:
    """One transparent stress case where nominal and evaluated choices differ.

    A slightly higher-nominal third forward is affordable only alongside two
    very unlikely defensive substitutes.  A slightly lower-nominal forward
    permits reliable defensive cover.  The case exposes exactly the blind
    spot a single nominal ILP solve has; it is deliberately not presented as
    a market-history sample.
    """
    pool = [
        _candidate(1, "GK", "ClubGK1", 1.0),
        _candidate(2, "GK", "ClubGK2", 1.0),
        _candidate(10, "DEF", "ClubD1", 1.0),
        _candidate(11, "DEF", "ClubD2", 1.0),
        _candidate(12, "DEF", "ClubD3", 5.0, p_appearance=0.5),
    ]
    pool.extend(_candidate(30 + i, "MID", f"ClubM{i}", 1.0) for i in range(5))
    pool.extend(
        [
            _candidate(40, "FWD", "ClubF1", 5.0),
            _candidate(43, "FWD", "ClubF2", 4.5),
            _candidate(41, "FWD", "ClubF3A", 3.0, price=8.0),
            _candidate(42, "FWD", "ClubF3B", 2.9, price=1.0),
            _candidate(20, "DEF", "ClubBad1", 0.1, p_appearance=0.05),
            _candidate(21, "DEF", "ClubBad2", 0.1, p_appearance=0.05),
            _candidate(22, "DEF", "ClubGood1", 0.9, price=6.0, p_appearance=0.45),
            _candidate(23, "DEF", "ClubGood2", 0.9, price=6.0, p_appearance=0.45),
        ]
    )
    return pool


@dataclass(frozen=True)
class StabilityRow:
    top_k: int
    evaluated_expected_points: float
    squad_ids: frozenset[int]
    starter_ids: frozenset[int]
    captain_id: int
    decision_changes_vs_widest: int
    squad_changed_vs_widest: bool


@dataclass(frozen=True)
class DecisionEvaluationResult:
    nominal_baseline: SquadSelection
    contingency_aware: SquadSelection
    decisions_changed_vs_nominal: int
    stability: tuple[StabilityRow, ...]
    first_stable_top_k: int


def run(ks: tuple[int, ...] = DEFAULT_KS) -> DecisionEvaluationResult:
    """Run the frozen, fixture-independent optimiser stability scenario."""
    if not ks or tuple(sorted(set(ks))) != ks or any(k < 1 for k in ks):
        raise ValueError("ks must be strictly increasing positive integers")
    pool = canonical_bench_reliability_pool()
    sweep = decision_quality.top_k_sweep(pool, bank=CANONICAL_BANK, ks=ks)
    # The first/last sweep members are exactly the same two strategies as
    # contingency_vs_nominal(top_k=max(ks)); reusing them avoids a second
    # expensive top-K enumeration solely to restate the baseline comparison.
    nominal = sweep[0].selection
    if sweep[0].top_k != 1:
        raise ValueError("ks must start at 1 so the nominal baseline is measured")
    widest = sweep[-1].selection
    rows = tuple(
        StabilityRow(
            top_k=item.top_k,
            evaluated_expected_points=item.selection.evaluated_expected_points,
            squad_ids=item.selection.squad_ids,
            starter_ids=item.selection.starter_ids,
            captain_id=item.selection.captain_id,
            decision_changes_vs_widest=decision_quality.decisions_changed(
                item.selection, widest
            ),
            squad_changed_vs_widest=item.selection.squad_ids != widest.squad_ids,
        )
        for item in sweep
    )
    stable_rows = [row for row in rows if not row.squad_changed_vs_widest and row.decision_changes_vs_widest == 0]
    first_stable = stable_rows[0].top_k
    return DecisionEvaluationResult(
        nominal_baseline=nominal,
        contingency_aware=widest,
        decisions_changed_vs_nominal=decision_quality.decisions_changed(nominal, widest),
        stability=rows,
        first_stable_top_k=first_stable,
    )


def render_markdown(result: DecisionEvaluationResult) -> str:
    """Render a bounded claim suitable for the frozen baseline evidence log."""
    nominal = result.nominal_baseline
    aware = result.contingency_aware
    lines = [
        "# FPL BASELINE_FPL_001 — Phase 1B Optimiser Stability",
        "",
        "This is an algorithmic stress test, not a historical transfer backtest. "
        "The project has no permitted historical FPL price/squad snapshots, so "
        "it cannot infer realised market-transfer performance from player stats alone.",
        "",
        "## Legitimate baseline comparison",
        "",
        "| Strategy | Evaluated expected points | Third forward | Reliable DEF cover |",
        "|---|---:|---:|---:|",
        f"| Single nominal solve (`top_k=1`) | {nominal.evaluated_expected_points:.3f} | "
        f"{'yes' if 41 in nominal.squad_ids else 'no'} | {'yes' if {22, 23} <= nominal.squad_ids else 'no'} |",
        f"| Contingency-aware search | {aware.evaluated_expected_points:.3f} | "
        f"{'yes' if 41 in aware.squad_ids else 'no'} | {'yes' if {22, 23} <= aware.squad_ids else 'no'} |",
        "",
        f"The wider search changes {result.decisions_changed_vs_nominal} XI/captain "
        "decision(s) from the nominal-only baseline and selects the reliable-cover "
        "squad in this constructed adverse case.",
        "",
        "## Search-width stability",
        "",
        "| K | Evaluated xP | Same squad as widest search? | XI/captain differences vs widest |",
        "|---:|---:|---|---:|",
    ]
    for row in result.stability:
        lines.append(
            f"| {row.top_k} | {row.evaluated_expected_points:.3f} | "
            f"{'yes' if not row.squad_changed_vs_widest else 'no'} | "
            f"{row.decision_changes_vs_widest} |"
        )
    lines += [
        "",
        f"On this canonical case, the choice first matches the K={result.stability[-1].top_k} "
        f"search at **K={result.first_stable_top_k}**.",
        "",
        "## Boundary of this evidence",
        "",
        "This supports the need for contingency-aware top-K enumeration and records "
        "its deterministic behaviour. It does **not** justify a generalised live "
        "transfer-performance claim or a new default K on its own; that would require "
        "permitted, historical FPL price and squad snapshots.",
        "",
    ]
    return "\n".join(lines)
