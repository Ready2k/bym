"""Tavily Search + Extract client (Increment 4A).

Search and Extract only. Tavily's Answer/Research layer is deliberately
unused: taking an AI-generated research answer and feeding it to another
model destroys the provenance chain this system exists to maintain. Our
extraction contract decides what becomes a claim.

**Tavily is the acquisition source, not the authority.** A claim found via
Tavily on chelseafc.com carries the authority of Chelsea FC; the same
claim on random-transfer-rumours.biz does not become credible because a
respectable search API surfaced it. The two identities are stored
separately throughout (see `knowledge.models`).

Credits (free Researcher tier, 1000/month): basic search 1, advanced 2,
extract 1 per 5 successfully extracted pages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from .quota import QuotaSpec, check, record
from .registry import DEFAULT_REGISTRY_PATH, assert_approved

SOURCE_ID = "tavily"
BASE_URL = "https://api.tavily.com"

FREE_TIER_MONTHLY_CREDITS = 1000
QUOTA = QuotaSpec(
    source_id=SOURCE_ID, limit=FREE_TIER_MONTHLY_CREDITS, period="month"
)

SEARCH_BASIC_COST = 1
SEARCH_ADVANCED_COST = 2
EXTRACT_PAGES_PER_CREDIT = 5
MAX_EXTRACT_URLS = 20


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str
    published_at: str | None = None
    score: float | None = None


@dataclass
class ExtractedPage:
    url: str
    content: str
    title: str = ""
    published_at: str | None = None


def search_cost(depth: str = "basic") -> int:
    return SEARCH_ADVANCED_COST if depth == "advanced" else SEARCH_BASIC_COST


def extract_cost(url_count: int) -> int:
    """1 credit per 5 successfully extracted pages, rounded up."""
    if url_count <= 0:
        return 0
    return -(-url_count // EXTRACT_PAGES_PER_CREDIT)


def normalise_published(value: str | None) -> str | None:
    """Tavily returns RFC 2822 dates ("Tue, 04 Aug 2026 09:17:27 GMT").

    Found in live verification: the rest of the system stores ISO8601 with
    an explicit offset (§26), and the three-clock model is worthless if
    `published_at` silently fails to parse — so it is converted here, at
    the boundary, rather than everywhere downstream.
    """
    if not value:
        return None
    text = value.strip()
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(text).astimezone(timezone.utc).isoformat(
            timespec="seconds"
        )
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def search(
    query: str,
    api_key: str,
    depth: str = "basic",
    max_results: int = 5,
    include_domains: list[str] | None = None,
    days: int | None = None,
    topic: str = "news",
    quota_path: Path | None = None,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    now: datetime | None = None,
    timeout: int = 20,
) -> list[SearchHit]:
    """One metered, registry-gated search."""
    assert_approved(SOURCE_ID, registry_path)
    cost = search_cost(depth)
    check(QUOTA, cost, quota_path, now)

    # topic="news" is required to get published_date at all — the general
    # search topic omits the field entirely (confirmed live), which would
    # leave every claim without a publication clock.
    payload = {
        "query": query,
        "search_depth": depth,
        "max_results": max_results,
        "topic": topic,
    }
    if include_domains:
        payload["include_domains"] = include_domains
    if days is not None and topic == "news":
        payload["days"] = days

    response = requests.post(
        f"{BASE_URL}/search",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=timeout,
    )
    record(QUOTA, cost, None, quota_path, now)
    response.raise_for_status()
    return parse_search(response.json())


def parse_search(payload: dict) -> list[SearchHit]:
    hits = []
    for item in payload.get("results", []) or []:
        hits.append(
            SearchHit(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=item.get("content", "") or "",
                published_at=normalise_published(item.get("published_date")),
                score=item.get("score"),
            )
        )
    return hits


def extract(
    urls: list[str],
    api_key: str,
    quota_path: Path | None = None,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    now: datetime | None = None,
    timeout: int = 30,
) -> list[ExtractedPage]:
    """Extract page content for up to 20 URLs."""
    assert_approved(SOURCE_ID, registry_path)
    if not urls:
        return []
    if len(urls) > MAX_EXTRACT_URLS:
        raise ValueError(
            f"Tavily extracts at most {MAX_EXTRACT_URLS} URLs per request"
        )
    cost = extract_cost(len(urls))
    check(QUOTA, cost, quota_path, now)

    response = requests.post(
        f"{BASE_URL}/extract",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"urls": urls},
        timeout=timeout,
    )
    record(QUOTA, cost, None, quota_path, now)
    response.raise_for_status()
    return parse_extract(response.json())


def parse_extract(payload: dict) -> list[ExtractedPage]:
    pages = []
    for item in payload.get("results", []) or []:
        pages.append(
            ExtractedPage(
                url=item.get("url", ""),
                content=item.get("raw_content", "") or "",
                title=item.get("title", "") or "",
                published_at=normalise_published(item.get("published_date")),
            )
        )
    return pages
