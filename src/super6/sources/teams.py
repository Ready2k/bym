"""Team-name mapping between data sources.

The Odds API uses full club names ("Manchester City"); Football-Data.co.uk
uses short forms ("Man City"); the user types whatever Sky displayed. A
mismatch here silently drops a fixture's odds, so matching is explicit and
its failures are loud (§50: "Ambiguous team names require user
confirmation. Never silently guess.").

Resolution order: exact match, then explicit alias, then normalised match,
then give up and report — never fuzzy-match into a wrong club, since
"United" and "City" prefixes make near-misses genuinely dangerous.
"""

from __future__ import annotations

import re
import unicodedata

# Football-Data.co.uk canonical short name -> known aliases from other
# sources. Extend as new clubs appear rather than loosening the matcher.
ALIASES: dict[str, tuple[str, ...]] = {
    "Man City": ("Manchester City", "Man. City"),
    "Man United": ("Manchester United", "Manchester Utd", "Man Utd"),
    "Tottenham": ("Tottenham Hotspur", "Spurs"),
    "Wolves": ("Wolverhampton Wanderers", "Wolverhampton"),
    "Newcastle": ("Newcastle United", "Newcastle Utd"),
    "West Ham": ("West Ham United", "West Ham Utd"),
    "Brighton": ("Brighton and Hove Albion", "Brighton & Hove Albion"),
    "Nott'm Forest": ("Nottingham Forest", "Notts Forest"),
    "Sheffield United": ("Sheffield Utd", "Sheffield United"),
    "Sheffield Weds": ("Sheffield Wednesday",),
    "Leeds": ("Leeds United", "Leeds Utd"),
    "Leicester": ("Leicester City",),
    "Norwich": ("Norwich City",),
    "Stoke": ("Stoke City",),
    "Swansea": ("Swansea City",),
    "Cardiff": ("Cardiff City",),
    "Hull": ("Hull City",),
    "Birmingham": ("Birmingham City",),
    "Coventry": ("Coventry City",),
    "Derby": ("Derby County",),
    "Ipswich": ("Ipswich Town",),
    "Luton": ("Luton Town",),
    "Preston": ("Preston North End",),
    "QPR": ("Queens Park Rangers",),
    "Blackburn": ("Blackburn Rovers",),
    "Bolton": ("Bolton Wanderers",),
    "Bristol City": ("Bristol City",),
    "Huddersfield": ("Huddersfield Town",),
    "Middlesbrough": ("Middlesbrough FC",),
    "Millwall": ("Millwall FC",),
    "Plymouth": ("Plymouth Argyle",),
    "Rotherham": ("Rotherham United",),
    "Oxford": ("Oxford United",),
    "Portsmouth": ("Portsmouth FC",),
    "Wycombe": ("Wycombe Wanderers",),
    "Charlton": ("Charlton Athletic",),
    "Wrexham": ("Wrexham AFC",),
    "Bournemouth": ("AFC Bournemouth",),
    "West Brom": ("West Bromwich Albion", "West Bromwich"),
}

_SUFFIXES = (" fc", " afc", " football club")


def normalise(name: str) -> str:
    """Lowercase, strip accents/punctuation and common club suffixes."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    for suffix in _SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class AliasCollisionError(ValueError):
    """Two canonical names claim the same alias."""


def _alias_index() -> dict[str, str]:
    """Build the alias lookup, refusing to silently resolve collisions.

    Without this check a duplicate alias is won by whichever entry is
    declared last, which routes a real club to the wrong canonical name
    and quietly drops its odds. Raising at import beats debugging a
    missing fixture at 14:55 on a Saturday.
    """
    index: dict[str, str] = {}
    for canonical, aliases in ALIASES.items():
        for name in (canonical, *aliases):
            key = normalise(name)
            existing = index.get(key)
            if existing is not None and existing != canonical:
                raise AliasCollisionError(
                    f"'{name}' is claimed by both '{existing}' and "
                    f"'{canonical}'. Give each club exactly one canonical "
                    "name in ALIASES."
                )
            index[key] = canonical
    return index


_ALIAS_INDEX = _alias_index()


def resolve(name: str, known_teams: set[str]) -> str | None:
    """Map an external team name onto a known canonical team name.

    Returns None when no confident match exists — the caller must surface
    that rather than guessing (§50).
    """
    if name in known_teams:
        return name

    normalised_known = {normalise(t): t for t in known_teams}

    canonical = _ALIAS_INDEX.get(normalise(name))
    if canonical and canonical in known_teams:
        return canonical
    if canonical and normalise(canonical) in normalised_known:
        return normalised_known[normalise(canonical)]

    direct = normalised_known.get(normalise(name))
    if direct:
        return direct

    return None


def match_event(
    home: str, away: str, known_teams: set[str]
) -> tuple[str, str] | None:
    """Resolve both sides of a fixture, or None if either is unresolvable."""
    resolved_home = resolve(home, known_teams)
    resolved_away = resolve(away, known_teams)
    if resolved_home is None or resolved_away is None:
        return None
    return resolved_home, resolved_away
