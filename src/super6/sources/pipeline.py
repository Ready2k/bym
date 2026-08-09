"""Odds acquisition pipeline: fetch -> map -> de-vig -> consensus -> persist.

This is the Phase 2 Increment 1 orchestration. It replaces *manual odds
entry* with automated timestamped acquisition and changes nothing about
the model: the consensus feeds PHASE1_MODEL_V1's frozen w=0.5 exactly
where a hand-typed price used to go (§32.3 Option B). Phase 2 is not
retraining market calibration.

Fixtures whose odds cannot be obtained (no event, unresolvable team names,
every quote stale) fall back to the raw statistical model. That is the
§30/§62 contract: missing optional data degrades the forecast, it does not
block it, and it is never imputed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..round import Round
from . import consensus as consensus_mod
from . import odds_api, teams

ODDS_SNAPSHOT_PATH = Path("data/snapshots/odds_snapshots.csv")
CONSENSUS_SNAPSHOT_PATH = Path("data/snapshots/market_consensus_snapshots.csv")


@dataclass
class FixtureOdds:
    home: str
    away: str
    consensus: consensus_mod.Consensus | None
    reason: str = ""  # why it is missing, when it is


@dataclass
class AcquisitionResult:
    fixtures: list[FixtureOdds]
    credits_spent: int
    fetched_at: str
    # Competitions that could not be reached, and fixtures still unpriced
    # after the cup pass. Reported rather than swallowed: "no market" and
    # "we never looked in the right competition" are different problems.
    notes: list[str] = field(default_factory=list)

    @property
    def covered(self) -> int:
        return sum(1 for f in self.fixtures if f.consensus is not None)

    def odds_by_fixture(self) -> dict[tuple[str, str], tuple[float, float, float]]:
        return {
            (f.home, f.away): f.consensus.as_odds()
            for f in self.fixtures
            if f.consensus is not None
        }


def _append_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    header = not path.exists()
    frame.to_csv(path, mode="a", header=header, index=False)


def acquire_round_odds(
    rnd: Round,
    api_key: str,
    divisions: tuple[str, ...] = ("E0", "E1"),
    now: datetime | None = None,
    odds_snapshot_path: Path = ODDS_SNAPSHOT_PATH,
    consensus_snapshot_path: Path = CONSENSUS_SNAPSHOT_PATH,
    cup_divisions: tuple[str, ...] = odds_api.CUP_DIVISIONS,
    **fetch_kwargs,
) -> AcquisitionResult:
    """Fetch current odds for a round's six fixtures and persist both
    provenance layers. One request per competition covers all its fixtures.

    Cup competitions are a *second pass*, attempted only when the league
    feeds left a fixture unmatched. A cup tie is absent from both clubs'
    league feeds, so without this a cup weekend has no market at all — but
    every competition is another billed request, and on an ordinary
    weekend the cup feeds would return nothing. Paying for them only when
    something is missing keeps the common case free.
    """
    now = now or datetime.now(timezone.utc)
    fetched_at = now.isoformat(timespec="seconds")
    notes: list[str] = []

    per_request = odds_api.request_cost(
        fetch_kwargs.get("markets", odds_api.DEFAULT_MARKETS),
        fetch_kwargs.get("regions", odds_api.DEFAULT_REGIONS),
    )
    events: list[odds_api.EventOdds] = []
    credits = 0
    for division in divisions:
        events.extend(odds_api.fetch_odds(division, api_key, **fetch_kwargs))
        credits += per_request

    round_teams = {t for f in rnd.fixtures for t in (f.home, f.away)}

    def match_all(candidates):
        found: dict[tuple[str, str], odds_api.EventOdds] = {}
        for event in candidates:
            pair = teams.match_event(event.home_team, event.away_team, round_teams)
            if pair is not None:
                found[pair] = event
        return found

    matched = match_all(events)

    if cup_divisions and any(
        (f.home, f.away) not in matched for f in rnd.fixtures
    ):
        for division in cup_divisions:
            try:
                cup_events = odds_api.fetch_odds(division, api_key, **fetch_kwargs)
            except Exception as exc:
                # Out of season, or a key the provider has retired. Neither
                # is a failed acquisition — the league odds already fetched
                # must still reach the forecast.
                notes.append(f"{division}: {exc}")
                continue
            credits += per_request
            matched.update(
                {k: v for k, v in match_all(cup_events).items() if k not in matched}
            )
        still_missing = [
            f"{f.home} v {f.away}" for f in rnd.fixtures
            if (f.home, f.away) not in matched
        ]
        if still_missing:
            notes.append(
                "no market found in league or cup feeds for "
                + "; ".join(still_missing)
            )

    odds_rows: list[dict] = []
    consensus_rows: list[dict] = []
    results: list[FixtureOdds] = []

    for fixture in rnd.fixtures:
        key = (fixture.home, fixture.away)
        event = matched.get(key)
        if event is None:
            results.append(
                FixtureOdds(
                    fixture.home, fixture.away, None,
                    "no matching event in the odds feed",
                )
            )
            continue

        odds_rows.extend(consensus_mod.snapshot_rows(event, fetched_at))
        built = consensus_mod.build_consensus(event, now=now)
        if built is None:
            results.append(
                FixtureOdds(
                    fixture.home, fixture.away, None,
                    "no fresh bookmaker quote (all stale or unusable)",
                )
            )
            continue

        consensus_rows.append(consensus_mod.consensus_row(built, event.event_id))
        results.append(FixtureOdds(fixture.home, fixture.away, built))

    _append_csv(odds_rows, odds_snapshot_path)
    _append_csv(consensus_rows, consensus_snapshot_path)

    return AcquisitionResult(results, credits, fetched_at, notes)


def apply_to_round(rnd: Round, acquired: AcquisitionResult) -> Round:
    """Return a copy of the round with live consensus odds filled in.

    Fixtures without consensus keep whatever odds they already had
    (usually none), so the model degrades to its raw statistical forecast
    for those rather than receiving an invented price.
    """
    from dataclasses import replace

    lookup = acquired.odds_by_fixture()
    fixtures = tuple(
        replace(f, odds=lookup.get((f.home, f.away), f.odds)) for f in rnd.fixtures
    )
    return replace(rnd, fixtures=fixtures)
