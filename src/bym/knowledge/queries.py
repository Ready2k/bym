"""Bounded query planning (Increment 4.2).

Deterministic templates, not "research this fixture and tell me anything
useful". The agent is goal-driven rather than wandering around football
Twitter until the credit card catches fire.

Source priority runs official domains first, then trusted services, then
the open web — so the cheapest searches are also the most authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Priority(str, Enum):
    OFFICIAL = "OFFICIAL"
    TRUSTED = "TRUSTED"
    OPEN = "OPEN"


@dataclass(frozen=True)
class PlannedQuery:
    query: str
    priority: str
    include_domains: tuple[str, ...] = ()
    days: int | None = 7
    reason: str = ""


TEAM_TEMPLATES = (
    "{team} team news",
    "{team} injuries",
    "{team} suspension",
    "{team} press conference injury",
    "{team} expected lineup",
)

PLAYER_TEMPLATE = "{player} injury"

# Targeted follow-ups when the market moves with no known explanation.
INVESTIGATION_TEMPLATES = (
    "{team} injury news",
    "{team} suspension",
    "{team} manager comments",
    "{team} lineup news",
    "{team} fixture postponed weather",
)

TRUSTED_DOMAINS = (
    "bbc.co.uk", "skysports.com", "theathletic.com", "theguardian.com",
)


def plan_team_queries(
    team: str,
    official_domain: str | None = None,
    max_queries: int = 3,
) -> list[PlannedQuery]:
    """Routine research for one team, cheapest-and-best-first."""
    planned: list[PlannedQuery] = []
    if official_domain:
        planned.append(
            PlannedQuery(
                query=f"{team} team news",
                priority=Priority.OFFICIAL.value,
                include_domains=(official_domain,),
                reason="official club statements first (§18)",
            )
        )
    for template in TEAM_TEMPLATES[:max_queries]:
        planned.append(
            PlannedQuery(
                query=template.format(team=team),
                priority=Priority.TRUSTED.value,
                include_domains=TRUSTED_DOMAINS,
                reason="trusted football services",
            )
        )
    return planned[: max_queries + (1 if official_domain else 0)]


def plan_player_query(player: str) -> PlannedQuery:
    return PlannedQuery(
        query=PLAYER_TEMPLATE.format(player=player),
        priority=Priority.TRUSTED.value,
        include_domains=TRUSTED_DOMAINS,
        reason="named-player follow-up",
    )


def plan_investigation(
    team: str, max_queries: int = 3, days: int = 1
) -> list[PlannedQuery]:
    """Targeted queries triggered by an unexplained market move (§4.2).

    Narrower time window than routine research: a move that happened
    twenty minutes ago is not explained by last Tuesday's article.
    """
    return [
        PlannedQuery(
            query=template.format(team=team),
            priority=Priority.TRUSTED.value,
            include_domains=TRUSTED_DOMAINS,
            days=days,
            reason="market-movement investigation",
        )
        for template in INVESTIGATION_TEMPLATES[:max_queries]
    ]
