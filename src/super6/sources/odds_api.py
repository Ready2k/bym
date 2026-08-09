"""The Odds API client (SPEC.md §6, §18.1, §25).

Credit model: one request costs `markets x regions` credits and returns
every upcoming event for that competition — so polling both divisions with
`markets=h2h, regions=uk` costs 2 credits per cycle, not 2 per fixture.
The free tier is 500 credits/month, which the §9 cadence fits inside with
room to spare, but it is metered here anyway because free quotas are
traditionally discovered by exhausting them at 13:57 on a Saturday.

Every request is gated on the source registry (§18.1, §22): querying
before `the_odds_api` is promoted to APPROVED raises rather than
proceeding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import quota as quota_mod
from .quota import QuotaExceededError  # re-exported for existing callers
from .registry import DEFAULT_REGISTRY_PATH, assert_approved

SOURCE_ID = "the_odds_api"
BASE_URL = "https://api.the-odds-api.com/v4"

# Competition keys, verbatim from the provider's published sports list.
#
# Super 6 rounds are mostly league fixtures, but include FA Cup ties a few
# times a season — and a cup tie appears in the *cup* feed, not either
# club's league feed, so without these a cup weekend has no market at all.
SPORT_KEYS = {
    "E0": "soccer_epl",
    "E1": "soccer_efl_champ",
    "E2": "soccer_england_league1",
    "E3": "soccer_england_league2",
    "FA_CUP": "soccer_fa_cup",
    "EFL_CUP": "soccer_england_efl_cup",
}

# Cup competitions, kept separate because they are fetched only as a
# second pass: every extra competition is another billed request, and on
# an ordinary league weekend they would return nothing.
CUP_DIVISIONS: tuple[str, ...] = ("FA_CUP", "EFL_CUP")

# The provider lists no National League feed, so an EC club's cup tie can
# never be priced from this source. Recorded here rather than discovered
# as a silent gap on the one weekend it matters.
UNPRICEABLE_DIVISIONS: frozenset[str] = frozenset({"EC"})

DEFAULT_REGIONS = "uk"
DEFAULT_MARKETS = "h2h"
FREE_TIER_MONTHLY_CREDITS = 500
DEFAULT_QUOTA_PATH = Path("config/odds_api_quota.json")


@dataclass
class BookmakerOdds:
    bookmaker: str
    last_update: str  # ISO8601, straight from the API
    home: float
    draw: float
    away: float


@dataclass
class EventOdds:
    event_id: str
    sport_key: str
    commence_time: str
    home_team: str
    away_team: str
    bookmakers: list[BookmakerOdds] = field(default_factory=list)


def request_cost(markets: str = DEFAULT_MARKETS, regions: str = DEFAULT_REGIONS) -> int:
    """markets x regions, per the API's published credit model."""
    return len(markets.split(",")) * len(regions.split(","))


# Metering lives in `quota`, shared with API-Football; these remain as the
# named entry points this module's callers already use.
QUOTA = quota_mod.QuotaSpec(
    source_id=SOURCE_ID, limit=FREE_TIER_MONTHLY_CREDITS, period="month"
)


def _month_key(now: datetime | None = None) -> str:
    return quota_mod.period_key("month", now)


def load_quota(path: Path = DEFAULT_QUOTA_PATH) -> dict:
    return quota_mod.load(path)


def save_quota(quota: dict, path: Path = DEFAULT_QUOTA_PATH) -> None:
    quota_mod.save(quota, path)


def credits_used_this_month(
    path: Path = DEFAULT_QUOTA_PATH, now: datetime | None = None
) -> int:
    return quota_mod.used(QUOTA, path, now)


def record_usage(
    cost: int,
    remaining_header: str | None = None,
    path: Path = DEFAULT_QUOTA_PATH,
    now: datetime | None = None,
) -> dict:
    return quota_mod.record(QUOTA, cost, remaining_header, path, now)


def _parse_h2h(payload: list[dict]) -> list[EventOdds]:
    """Map the API payload to EventOdds.

    Outcome order is not guaranteed and outcomes are named by team (plus
    'Draw'), so they are matched by name rather than by position. An event
    whose h2h market is missing an outcome is skipped for that bookmaker
    rather than guessed at.
    """
    events: list[EventOdds] = []
    for raw in payload:
        event = EventOdds(
            event_id=raw.get("id", ""),
            sport_key=raw.get("sport_key", ""),
            commence_time=raw.get("commence_time", ""),
            home_team=raw.get("home_team", ""),
            away_team=raw.get("away_team", ""),
        )
        for bm in raw.get("bookmakers", []):
            market = next(
                (m for m in bm.get("markets", []) if m.get("key") == "h2h"), None
            )
            if market is None:
                continue
            prices = {o.get("name"): o.get("price") for o in market.get("outcomes", [])}
            home = prices.get(event.home_team)
            away = prices.get(event.away_team)
            draw = prices.get("Draw")
            if not all(isinstance(p, (int, float)) and p > 1.0 for p in (home, draw, away)):
                continue
            event.bookmakers.append(
                BookmakerOdds(
                    bookmaker=bm.get("key", bm.get("title", "unknown")),
                    last_update=market.get("last_update") or bm.get("last_update", ""),
                    home=float(home),
                    draw=float(draw),
                    away=float(away),
                )
            )
        events.append(event)
    return events


def fetch_odds(
    division: str,
    api_key: str,
    regions: str = DEFAULT_REGIONS,
    markets: str = DEFAULT_MARKETS,
    monthly_budget: int = FREE_TIER_MONTHLY_CREDITS,
    quota_path: Path = DEFAULT_QUOTA_PATH,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    timeout: int = 20,
) -> list[EventOdds]:
    """Fetch current 1X2 odds for one division.

    Refuses to run unless the source is APPROVED in the registry, and
    refuses if the request would exceed the monthly credit budget.
    """
    assert_approved(SOURCE_ID, registry_path)

    if division not in SPORT_KEYS:
        raise ValueError(
            f"No sport key for division '{division}'. Known: {sorted(SPORT_KEYS)}"
        )

    cost = request_cost(markets, regions)
    used = credits_used_this_month(quota_path)
    if used + cost > monthly_budget:
        raise QuotaExceededError(
            f"Request would cost {cost} credit(s); {used}/{monthly_budget} "
            "already used this month. Wait for the monthly reset or raise "
            "the budget deliberately (§25)."
        )

    response = requests.get(
        f"{BASE_URL}/sports/{SPORT_KEYS[division]}/odds",
        params={
            "apiKey": api_key,
            "regions": regions,
            "markets": markets,
            "oddsFormat": "decimal",
        },
        timeout=timeout,
    )
    record_usage(cost, response.headers.get("x-requests-remaining"), quota_path)
    response.raise_for_status()
    return _parse_h2h(response.json())
