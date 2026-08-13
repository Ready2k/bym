"""Harvest AVAILABILITY_ORACLE_RESEARCH_V1 until the request queue settles.

Runs a harvest, and when the daily quota is exhausted sleeps until just
after the next UTC reset and resumes. All state lives in the research
database, so killing this at any point loses nothing — restarting picks
up exactly where it stopped.

    .venv/bin/python scripts/harvest_until_settled.py

Safe to run under nohup, systemd, launchd, or simply left in a terminal.
It exits 0 when every request has reached a terminal state.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bym.research import harvest as harvest_mod  # noqa: E402
from bym.research import store as research_store  # noqa: E402
from bym.secrets import get_key  # noqa: E402
from bym.sources import api_football  # noqa: E402
from bym.sources.quota import remaining  # noqa: E402

DB = Path("research/data/oracle_v1.db")
# A little past midnight UTC, so a clock skew of a few seconds does not
# have us asking for quota that has not reset yet.
RESET_BUFFER_MINUTES = 2
MAX_WINDOWS = 30  # guard against an infinite loop if something is stuck


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{stamp}] {message}", flush=True)


def next_reset(now: datetime) -> datetime:
    return (now + timedelta(days=1)).replace(
        hour=0, minute=RESET_BUFFER_MINUTES, second=0, microsecond=0
    )


def settled(conn) -> tuple[int, int]:
    counts = research_store.progress(conn)
    total = sum(counts.values())
    done = sum(counts.get(s, 0) for s in research_store.TERMINAL_STATES)
    return done, total


def main() -> int:
    api_key = get_key("API_FOOTBALL_KEY")
    if not api_key:
        log("No API-Football key found.")
        return 1

    for window in range(1, MAX_WINDOWS + 1):
        with research_store.connect(DB) as conn:
            done, total = settled(conn)
            if total and done >= total:
                log(f"QUEUE SETTLED: {done}/{total} requests terminal.")
                harvest_mod.derive_facts(conn)
                log("Facts derived. Ready for freeze and Pass A.")
                return 0

            log(f"window {window}: {done}/{total} settled, harvesting...")
            result = harvest_mod.harvest(conn, api_key)
            log(result.describe())
            for note in result.notes[:3]:
                log(f"  ! {note}")
            harvest_mod.derive_facts(conn)
            done, total = settled(conn)
            log(f"progress: {done}/{total} settled")

            if done >= total:
                log("QUEUE SETTLED.")
                return 0

            # Nothing fetched and nothing failed usefully: stuck rather
            # than quota-bound, so stop instead of spinning for days.
            if result.attempted == 0 and remaining(api_football.QUOTA) > 10:
                log("No progress and quota available — stopping to avoid a spin.")
                return 2

        now = datetime.now(timezone.utc)
        wake = next_reset(now)
        hours = (wake - now).total_seconds() / 3600
        log(f"quota spent; sleeping {hours:.1f}h until {wake.isoformat()}")
        time.sleep(max((wake - now).total_seconds(), 60))

    log(f"Stopped after {MAX_WINDOWS} windows without settling.")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
