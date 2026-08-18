"""One day's FPL historical-corpus harvest (docs/SPEC_FPL.md §5.2, §5.6;
Phase 1B corpus build, human-approved 2026-08-17 scope — see
docs/MANIFEST_FPL.md). Mirrors `harvest_until_settled.py`'s shape for a
different harvest, but stays a single day's work per invocation rather
than sleeping until the next quota window: this one is meant to be
invoked once daily by a scheduler, not left running.

    .venv/bin/python scripts/fpl_daily_harvest.py

Idempotent and restart-safe by construction (`fpl_fixture_harvest_state`
is the resumability ledger — see `bym.fpl.harvest`): running this twice
in one day does no harm, it simply finds nothing left to do within the
daily cap the second time.

Capped at 85 requests/day (not `api_football`'s full 100) — the quota is
shared with the live service's own lineup/injury polling, not FPL-
exclusive. Seasons in `harvest.HARVEST_SEASONS` are finished in order,
earliest first (currently 2022/23, then 2023/24, then 2024/25 — see that
constant's docstring for why it stops there rather than tracking whatever
season is current).

When ALL seasons reach a terminal state, this script writes the field-
completeness/identity gate report to `results/fpl_corpus_report.md` and
prints `FPL_HARVEST_COMPLETE` — the signal a scheduled invocation of this
script should look for to stop rescheduling itself. It does NOT run any
model evaluation and does NOT freeze `FPL_PLAYER_CORPUS_DEV_V1` — both
stay a deliberate, separate human step after reading that report.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bym import db  # noqa: E402
from bym.fpl import corpus_report, harvest  # noqa: E402
from bym.secrets import get_key  # noqa: E402

DB_PATH = Path("data/bym.db")
DAILY_CAP = 85
GATE_REPORT_PATH = Path("results/fpl_corpus_report.md")
COMPLETE_MARKER = "FPL_HARVEST_COMPLETE"


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{stamp}] {message}", flush=True)


def write_gate_report(conn) -> corpus_report.GateResult:
    reports = [corpus_report.field_completeness(conn, s) for s in harvest.HARVEST_SEASONS]
    gate = corpus_report.gate_check(reports)
    GATE_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    GATE_REPORT_PATH.write_text(corpus_report.render_markdown(reports, gate))
    log(f"gate report written to {GATE_REPORT_PATH} — verdict: "
        f"{'PASSED' if gate.passed else 'FAILED, see reasons in the report'}")
    return gate


def main() -> int:
    api_key = get_key("API_FOOTBALL_KEY")
    if not api_key:
        log("No API-Football key found.")
        return 1

    log(
        f"current season: {harvest.current_season()} — harvesting historical "
        f"seasons {harvest.HARVEST_SEASONS} (provider cap: "
        f"{harvest.PROVIDER_MAX_FETCHABLE_SEASON})"
    )

    with db.connect(DB_PATH) as conn:
        if harvest.harvest_complete(conn):
            log("All seasons already complete — nothing to harvest today.")
            write_gate_report(conn)
            log(COMPLETE_MARKER)
            return 0

        result = harvest.run_daily_harvest(conn, api_key, daily_cap=DAILY_CAP)

        for season, batch in result.season_results.items():
            log(
                f"season {season}: attempted={batch.attempted} fetched={batch.fetched} "
                f"no_data={batch.no_data} retryable={batch.retryable_failures} "
                f"final_failures={batch.final_failures} "
                f"stopped_reason={batch.stopped_reason!r}"
            )
        log(
            f"quota used today: {result.requests_used_today_before} -> "
            f"{result.requests_used_today_after}"
        )
        for season in harvest.HARVEST_SEASONS:
            log(f"  {season} status: {harvest.harvest_status_counts(conn, season, harvest.HARVEST_COMPETITION)}")

        if result.all_seasons_complete:
            log("All seasons just reached a terminal state.")
            write_gate_report(conn)
            log(COMPLETE_MARKER)
        else:
            log("Not yet complete — resume tomorrow's quota window.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
