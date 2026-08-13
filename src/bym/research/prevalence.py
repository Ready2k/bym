"""Pass A — information prevalence.

Runs **before** anything touches prediction performance, because it may
settle the economics on its own:

    200 fixtures
      -> 41 contain meaningful availability information
      -> 9 of those are decision-time plausible

makes a EUR29/month feed a difficult business case regardless of how well
the model would have used it.

This module counts. It does not model, and it does not look at forecast
accuracy — that is Pass B, and it only runs if Pass A shows something.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .deadline import Knowability


@dataclass
class PrevalenceReport:
    sampled: int = 0
    harvested: int = 0
    with_injury_data: int = 0
    with_lineup_data: int = 0
    informative: int = 0
    decision_time_plausible: int = 0
    total_absences: int = 0
    total_doubts: int = 0
    by_stratum: dict[str, int] = field(default_factory=dict)
    reasons: dict[str, int] = field(default_factory=dict)

    @property
    def informative_rate(self) -> float:
        return self.informative / self.harvested if self.harvested else 0.0

    @property
    def usable_rate(self) -> float:
        return (
            self.decision_time_plausible / self.harvested
            if self.harvested
            else 0.0
        )

    def describe(self) -> str:
        lines = [
            "PASS A — INFORMATION PREVALENCE",
            "",
            f"  sampled fixtures            {self.sampled}",
            f"  harvested                   {self.harvested}",
            f"  with injury data            {self.with_injury_data}",
            f"  with lineup data            {self.with_lineup_data}",
            "",
            f"  informative                 {self.informative} "
            f"({self.informative_rate:.1%} of harvested)",
            f"  AND decision-time plausible {self.decision_time_plausible} "
            f"({self.usable_rate:.1%} of harvested)",
            "",
            f"  total confirmed absences    {self.total_absences}",
            f"  total doubts                {self.total_doubts}",
        ]
        if self.reasons:
            lines += ["", "  why fixtures were informative:"]
            for reason, count in sorted(self.reasons.items(), key=lambda x: -x[1]):
                lines.append(f"    {count:4d}  {reason}")
        if self.by_stratum:
            lines += ["", "  informative by stratum:"]
            for stratum, count in sorted(self.by_stratum.items()):
                lines.append(f"    {stratum}: {count}")
        lines += [
            "",
            "  Pass B (predictive value) is NOT run by this report.",
        ]
        return "\n".join(lines)


def compute(conn: sqlite3.Connection) -> PrevalenceReport:
    import json

    report = PrevalenceReport()
    report.sampled = int(
        conn.execute("SELECT COUNT(*) c FROM research_sample").fetchone()["c"]
    )

    rows = conn.execute(
        "SELECT f.*, s.stratum FROM research_fixture_facts f "
        "JOIN research_sample s ON s.fixture_id = f.fixture_id"
    ).fetchall()
    report.harvested = len(rows)

    for row in rows:
        report.with_injury_data += int(bool(row["has_injury_data"]))
        report.with_lineup_data += int(bool(row["has_lineup_data"]))
        report.total_absences += int(row["absent_count"] or 0)
        report.total_doubts += int(row["doubt_count"] or 0)

        if not row["would_change_feature"]:
            continue
        report.informative += 1
        report.by_stratum[row["stratum"]] = (
            report.by_stratum.get(row["stratum"], 0) + 1
        )
        for reason in json.loads(row["change_reasons"] or "[]"):
            # collapse counts so "3 confirmed absence(s)" groups with "1"
            key = "".join(c for c in reason if not c.isdigit()).strip()
            report.reasons[key] = report.reasons.get(key, 0) + 1

        # Informative AND plausibly knowable before the round locked.
        if (
            row["lineup_knowability"] == Knowability.PRESUMED_KNOWABLE.value
            or row["injury_knowability"] == Knowability.PRESUMED_KNOWABLE.value
        ):
            report.decision_time_plausible += 1

    return report
