"""Shared API quota accounting (SPEC.md §25).

Two sources with different quota periods now exist (The Odds API: 500
credits/month; API-Football: 100 requests/day plus 10/minute), so the
metering lives here rather than being reimplemented per client.

The accounting is deliberately local-first with reconciliation against
whatever the provider reports in its response headers: the header is
authoritative and catches drift from failed or out-of-band calls, but
waiting for a header before refusing a request would mean discovering the
limit by exceeding it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_QUOTA_DIR = Path("config")


class QuotaExceededError(RuntimeError):
    """Raised when a request would exceed the configured budget."""


class RateLimitError(RuntimeError):
    """Raised when a request would breach a short-window rate limit."""


@dataclass(frozen=True)
class QuotaSpec:
    source_id: str
    limit: int
    period: str  # "month" | "day"
    per_minute: int | None = None

    @property
    def path(self) -> Path:
        return DEFAULT_QUOTA_DIR / f"{self.source_id}_quota.json"


def period_key(period: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if period == "month":
        return now.strftime("%Y-%m")
    if period == "day":
        return now.strftime("%Y-%m-%d")
    raise ValueError(f"Unknown quota period '{period}'")


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save(state: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def used(spec: QuotaSpec, path: Path | None = None, now: datetime | None = None) -> int:
    state = load(path or spec.path)
    return int(state.get(period_key(spec.period, now), {}).get("used", 0))


def remaining(
    spec: QuotaSpec, path: Path | None = None, now: datetime | None = None
) -> int:
    return max(spec.limit - used(spec, path, now), 0)


def _recent_calls(state: dict, now: datetime) -> list[str]:
    cutoff = now - timedelta(minutes=1)
    return [
        ts
        for ts in state.get("recent_calls", [])
        if datetime.fromisoformat(ts) > cutoff
    ]


def check(
    spec: QuotaSpec,
    cost: int = 1,
    path: Path | None = None,
    now: datetime | None = None,
) -> None:
    """Raise if this request would exceed the budget or the rate limit."""
    now = now or datetime.now(timezone.utc)
    path = path or spec.path
    consumed = used(spec, path, now)
    if consumed + cost > spec.limit:
        raise QuotaExceededError(
            f"{spec.source_id}: request would cost {cost}; "
            f"{consumed}/{spec.limit} already used this {spec.period}. "
            "Wait for the reset or raise the budget deliberately (§25)."
        )
    if spec.per_minute is not None:
        state = load(path)
        if len(_recent_calls(state, now)) + cost > spec.per_minute:
            raise RateLimitError(
                f"{spec.source_id}: {spec.per_minute} requests/minute limit "
                "would be breached. Back off and retry shortly."
            )


def record(
    spec: QuotaSpec,
    cost: int = 1,
    reported_remaining: str | int | None = None,
    path: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Record spend, reconciling against the provider's own header."""
    now = now or datetime.now(timezone.utc)
    path = path or spec.path
    state = load(path)
    entry = state.setdefault(period_key(spec.period, now), {"used": 0})
    entry["used"] = int(entry.get("used", 0)) + cost
    entry["last_call_at"] = now.isoformat(timespec="seconds")
    if reported_remaining is not None:
        try:
            entry["api_reported_remaining"] = int(reported_remaining)
        except (TypeError, ValueError):
            pass
    if spec.per_minute is not None:
        recent = _recent_calls(state, now)
        recent.extend([now.isoformat()] * cost)
        state["recent_calls"] = recent
    save(state, path)
    return entry


def constrained(
    spec: QuotaSpec,
    fraction: float = 0.15,
    path: Path | None = None,
    now: datetime | None = None,
) -> bool:
    """True when the remaining budget is below `fraction` of the limit —
    the signal to degrade cadence rather than go blind later (§25)."""
    return remaining(spec, path, now) < spec.limit * fraction
