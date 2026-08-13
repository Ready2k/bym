"""Market consensus from multiple bookmakers (SPEC.md §6, §32.1).

One bookmaker's price is not the market. Each bookmaker is de-vigged
separately — margins differ, so de-vigging must happen *before*
aggregation, never after — and the consensus is the **median** across
bookmakers, so one stale or eccentric quote cannot drag the estimate
around.

Two layers are persisted, per the §19 provenance requirement:

    odds_snapshot             one row per fixture per bookmaker, with the
                              bookmaker's own last_update timestamp
    market_consensus_snapshot one row per fixture, with bookmaker_count
                              and dispersion

Dispersion (max-min spread of de-vigged home probability) is retained as a
market-quality signal for later use — wide disagreement between books is
itself information, and §20's CONF has a `market_divergence` factor
waiting for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

from ..model.market import implied_result_probs
from .odds_api import BookmakerOdds, EventOdds

# A quote older than this is excluded from the consensus (§62 STALE).
DEFAULT_STALE_AFTER = timedelta(hours=12)


@dataclass(frozen=True)
class Devigged:
    bookmaker: str
    last_update: str
    p_home: float
    p_draw: float
    p_away: float
    overround: float

    @property
    def last_update_dt(self) -> datetime | None:
        return parse_timestamp(self.last_update)


@dataclass(frozen=True)
class Consensus:
    home_team: str
    away_team: str
    calculated_at: str
    bookmaker_count: int
    p_home: float
    p_draw: float
    p_away: float
    dispersion: float
    stale_excluded: int
    oldest_quote: str | None

    def as_odds(self) -> tuple[float, float, float]:
        """Consensus probabilities back as fair decimal odds, for the
        existing odds-shaped interfaces."""
        return (1.0 / self.p_home, 1.0 / self.p_draw, 1.0 / self.p_away)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def devig(odds: BookmakerOdds) -> Devigged:
    """§32.1 proportional margin removal, applied per bookmaker."""
    p_h, p_d, p_a = implied_result_probs(odds.home, odds.draw, odds.away)
    overround = (1.0 / odds.home) + (1.0 / odds.draw) + (1.0 / odds.away)
    return Devigged(
        bookmaker=odds.bookmaker,
        last_update=odds.last_update,
        p_home=p_h,
        p_draw=p_d,
        p_away=p_a,
        overround=overround,
    )


def build_consensus(
    event: EventOdds,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
) -> Consensus | None:
    """Median consensus across a fixture's bookmakers. Returns None when
    no usable quote survives — the caller then degrades to the raw
    statistical model rather than inventing a market view."""
    now = now or datetime.now(timezone.utc)
    devigged = [devig(b) for b in event.bookmakers]

    fresh, stale = [], 0
    for d in devigged:
        ts = d.last_update_dt
        if ts is not None and now - ts > stale_after:
            stale += 1
            continue
        fresh.append(d)

    if not fresh:
        return None

    p_h = float(np.median([d.p_home for d in fresh]))
    p_d = float(np.median([d.p_draw for d in fresh]))
    p_a = float(np.median([d.p_away for d in fresh]))
    # Medians of three separate series need not sum to 1; renormalise so
    # the result is a coherent distribution for §32.3.
    total = p_h + p_d + p_a
    p_h, p_d, p_a = p_h / total, p_d / total, p_a / total

    home_probs = [d.p_home for d in fresh]
    dispersion = float(max(home_probs) - min(home_probs)) if len(fresh) > 1 else 0.0
    timestamps = [d.last_update for d in fresh if d.last_update]

    return Consensus(
        home_team=event.home_team,
        away_team=event.away_team,
        calculated_at=now.isoformat(timespec="seconds"),
        bookmaker_count=len(fresh),
        p_home=p_h,
        p_draw=p_d,
        p_away=p_a,
        dispersion=dispersion,
        stale_excluded=stale,
        oldest_quote=min(timestamps) if timestamps else None,
    )


def snapshot_rows(
    event: EventOdds, fetched_at: str
) -> list[dict]:
    """Bookmaker-level `odds_snapshot` rows (§19)."""
    rows = []
    for bm in event.bookmakers:
        d = devig(bm)
        rows.append(
            {
                "fetched_at": fetched_at,
                "event_id": event.event_id,
                "home_team": event.home_team,
                "away_team": event.away_team,
                "bookmaker": bm.bookmaker,
                "bookmaker_last_update": bm.last_update,
                "odds_h": bm.home,
                "odds_d": bm.draw,
                "odds_a": bm.away,
                "devig_h": d.p_home,
                "devig_d": d.p_draw,
                "devig_a": d.p_away,
                "overround": d.overround,
            }
        )
    return rows


def consensus_row(consensus: Consensus, event_id: str) -> dict:
    """`market_consensus_snapshot` row (§19)."""
    return {
        "calculated_at": consensus.calculated_at,
        "event_id": event_id,
        "home_team": consensus.home_team,
        "away_team": consensus.away_team,
        "bookmaker_count": consensus.bookmaker_count,
        "consensus_h": consensus.p_home,
        "consensus_d": consensus.p_draw,
        "consensus_a": consensus.p_away,
        "dispersion": consensus.dispersion,
        "stale_excluded": consensus.stale_excluded,
        "oldest_quote": consensus.oldest_quote,
    }
