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

from .registry import DEFAULT_REGISTRY_PATH, assert_approved

SOURCE_ID = "the_odds_api"
BASE_URL = "https://api.the-odds-api.com/v4"

# Competition keys for the two divisions Super 6 draws from.
SPORT_KEYS = {
    "E0": "soccer_epl",
    "E1": "soccer_efl_champ",
}

DEFAULT_REGIONS = "uk"
DEFAULT_MARKETS = "h2h"
FREE_TIER_MONTHLY_CREDITS = 500
DEFAULT_QUOTA_PATH = Path("config/odds_api_quota.json")


class QuotaExceededError(RuntimeError):
    """Raised when a request would exceed the configured monthly budget."""


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


def _month_key(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def load_quota(path: Path = DEFAULT_QUOTA_PATH) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_quota(quota: dict, path: Path = DEFAULT_QUOTA_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(quota, indent=2) + "\n")


def credits_used_this_month(
    path: Path = DEFAULT_QUOTA_PATH, now: datetime | None = None
) -> int:
    return int(load_quota(path).get(_month_key(now), {}).get("used", 0))


def record_usage(
    cost: int,
    remaining_header: str | None = None,
    path: Path = DEFAULT_QUOTA_PATH,
    now: datetime | None = None,
) -> dict:
    """Record local spend, and reconcile against the API's own
    x-requests-remaining header when present — the header is authoritative
    and catches drift from failed or out-of-band calls."""
    quota = load_quota(path)
    month = _month_key(now)
    entry = quota.setdefault(month, {"used": 0})
    entry["used"] = int(entry.get("used", 0)) + cost
    entry["last_call_at"] = (now or datetime.now(timezone.utc)).isoformat(
        timespec="seconds"
    )
    if remaining_header is not None:
        try:
            entry["api_reported_remaining"] = int(remaining_header)
        except (TypeError, ValueError):
            pass
    save_quota(quota, path)
    return entry


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
