"""Free confirmed-lineup adapter for BBC Sport live football pages.

BBC's RSS products are headline feeds; the live match pages are where its
reporters publish the actual team sheets.  This deliberately accepts only a
complete, explicitly-labelled XI for a team in the active round.  It never
turns predicted-lineup prose into a confirmation.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

import requests

SOURCE_ID = "bbc_sport_live"
# The /live/ directory itself returns a 404 to non-browser clients; the
# football homepage carries the current live-page links in its rendered HTML.
LIVE_INDEX_URL = "https://www.bbc.co.uk/sport/football"
MAX_LIVE_PAGES = 8

_ALIASES = {
    "Man United": ("Man United", "Manchester United"),
    "Tottenham": ("Tottenham", "Tottenham Hotspur", "Spurs"),
    "West Ham": ("West Ham", "West Ham United"),
    "Wolves": ("Wolves", "Wolverhampton Wanderers"),
}


@dataclass(frozen=True)
class ConfirmedXI:
    team: str
    players: tuple[str, ...]
    source_url: str


def _text(markup: str) -> str:
    """Flatten BBC's rendered HTML without depending on CSS class names."""
    value = re.sub(r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>", " ", markup, flags=re.S | re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _team_names(team: str) -> tuple[str, ...]:
    return _ALIASES.get(team, (team,))


def _players(value: str) -> tuple[str, ...]:
    # BBC separates a team sheet with commas and occasionally semicolons.
    names = tuple(part.strip(" .") for part in re.split(r"[,;]", value) if part.strip(" ."))
    return names if len(names) == 11 and all(len(name) > 1 for name in names) else ()


def extract_confirmed_xis(markup: str, teams: set[str], source_url: str) -> list[ConfirmedXI]:
    """Extract complete BBC team-sheet declarations for known teams."""
    page = _text(markup)
    found: list[ConfirmedXI] = []
    for team in sorted(teams, key=len, reverse=True):
        aliases = "|".join(re.escape(name) for name in _team_names(team))
        # BBC uses both ``Team XI:`` and ``Team:``. The immediately following
        # substitutes label plus exactly eleven names rejects prediction prose.
        pattern = rf"(?:{aliases})(?:\s+(?:starting\s+)?XI)?\s*:\s*(.+?)\s+(?:Substitutes|Subs)\s*:"
        match = re.search(pattern, page, flags=re.I)
        if not match:
            continue
        players = _players(match.group(1))
        if players:
            found.append(ConfirmedXI(team, players, source_url))
    return found


def discover_live_pages(session=requests) -> list[str]:
    """Return BBC live-page URLs currently linked from the football hub."""
    response = session.get(LIVE_INDEX_URL, timeout=12, headers={"User-Agent": "BYM lineup monitor/1.0"})
    response.raise_for_status()
    paths = re.findall(r'href=["\'](/sport/football/live/[a-z0-9]+)["\']', response.text, flags=re.I)
    return list(dict.fromkeys(f"https://www.bbc.co.uk{path}" for path in paths))


def fetch_confirmed_xis(teams: set[str], session=requests) -> list[ConfirmedXI]:
    """Discover and inspect current pages, ignoring unavailable/irrelevant ones."""
    found: dict[str, ConfirmedXI] = {}
    # The football hub can retain older live pages.  The current fixtures are
    # listed first, and a small cap keeps this free fallback quick and polite.
    for url in discover_live_pages(session)[:MAX_LIVE_PAGES]:
        try:
            response = session.get(url, timeout=12, headers={"User-Agent": "BYM lineup monitor/1.0"})
            response.raise_for_status()
        except requests.RequestException:
            continue
        for lineup in extract_confirmed_xis(response.text, teams, url):
            found.setdefault(lineup.team, lineup)
        if found.keys() >= teams:
            break
    return list(found.values())
