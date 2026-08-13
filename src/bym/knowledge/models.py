"""Knowledge agent domain model (Increment 4).

Three separations that the rest of this increment depends on:

**Acquisition source vs origin source.** Tavily found the page; Chelsea FC
said the thing. Reliability derives from the origin, never the retriever.

**Three clocks.** News is full of stale quotes republished as fresh
stories, so a Friday article reporting a Wednesday press conference must
not become a fresh Friday injury update merely because the calendar
advanced:

    published_at   when the article appeared
    event_at       when the claimed event actually happened
    observed_at    when we found it

**Candidate vs accepted.** An LLM emits *candidates*. A deterministic
validator decides what survives, and the existing availability
reconciliation decides what is currently true.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse, urlunparse


class OriginAuthority(str, Enum):
    """§18's source hierarchy, applied to the *origin* of a claim."""

    OFFICIAL = "OFFICIAL"          # club / competition / league
    TRUSTED = "TRUSTED"            # established football news service
    JOURNALIST = "JOURNALIST"      # named, established reporter
    OTHER_MEDIA = "OTHER_MEDIA"
    SOCIAL = "SOCIAL"
    UNKNOWN = "UNKNOWN"


# Mapped to the availability model's Reliability, which reconciliation uses.
AUTHORITY_TO_TIER = {
    OriginAuthority.OFFICIAL: "OFFICIAL_CLUB",
    OriginAuthority.TRUSTED: "STRUCTURED_PROVIDER",
    OriginAuthority.JOURNALIST: "ESTABLISHED_JOURNALIST",
    OriginAuthority.OTHER_MEDIA: "OTHER_MEDIA",
    OriginAuthority.SOCIAL: "SOCIAL",
    OriginAuthority.UNKNOWN: "SOCIAL",
}

OFFICIAL_DOMAIN_MARKERS = ("chelseafc.com", "arsenal.com", "manutd.com",
                           "liverpoolfc.com", "mancity.com", "tottenhamhotspur.com",
                           "premierleague.com", "efl.com", "thefa.com")
TRUSTED_DOMAINS = ("bbc.co.uk", "bbc.com", "skysports.com", "theathletic.com",
                   "theguardian.com", "reuters.com", "pressassociation.com")
SOCIAL_DOMAINS = ("x.com", "twitter.com", "facebook.com", "instagram.com",
                  "reddit.com", "tiktok.com")

# Agencies whose copy is routinely syndicated verbatim (§4.8).
WIRE_AGENCIES = ("pa media", "press association", "reuters", "associated press",
                 "afp", "pa sport")


class ClaimType(str, Enum):
    AVAILABILITY = "AVAILABILITY"
    EXPECTED_LINEUP = "EXPECTED_LINEUP"
    CONTEXT = "CONTEXT"


class ClaimVerdict(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class Relationship(str, Enum):
    CORROBORATES = "CORROBORATES"
    CONTRADICTS = "CONTRADICTS"
    SUPERSEDES = "SUPERSEDES"
    DUPLICATE_OF = "DUPLICATE_OF"


# The only availability values a candidate claim may carry. Deliberately
# excludes anything meaning "confirmed available" — see the no-negative-
# inference rule in `validate`.
ALLOWED_AVAILABILITY = {
    "DOUBT", "INJURY", "SUSPENSION", "RETURN",
    "EXPECTED_STARTER", "EXPECTED_BENCH", "CONFIRMED_ABSENT",
}
ALLOWED_CERTAINTY = {"RUMOUR", "REPORTED", "LIKELY", "CONFIRMED"}
ALLOWED_EXPECTED_ROLE = {"STARTER", "BENCH", "ABSENT"}

# Sentinel the agent emits when a search finds nothing. It must never be
# convertible into a positive availability claim (§4.5).
NO_EVIDENCE = "NO_CURRENT_EVIDENCE_FOUND"


def canonical_url(url: str) -> str:
    """Strip query strings, fragments and tracking noise so syndicated
    copies of one story collapse together (§4.8)."""
    try:
        parts = urlparse(url.strip().lower())
    except ValueError:
        return url.strip().lower()
    netloc = parts.netloc.removeprefix("www.")
    path = parts.path.rstrip("/")
    return urlunparse(("https", netloc, path, "", "", ""))


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def classify_authority(url: str, byline: str = "") -> OriginAuthority:
    """Authority of the *origin* domain — never of the retriever."""
    domain = domain_of(url)
    if not domain:
        return OriginAuthority.UNKNOWN
    if any(marker in domain for marker in OFFICIAL_DOMAIN_MARKERS):
        return OriginAuthority.OFFICIAL
    if any(marker in domain for marker in SOCIAL_DOMAINS):
        return OriginAuthority.SOCIAL
    if any(marker in domain for marker in TRUSTED_DOMAINS):
        return OriginAuthority.TRUSTED
    if byline.strip():
        return OriginAuthority.JOURNALIST
    return OriginAuthority.OTHER_MEDIA


def content_hash(text: str) -> str:
    """Stable hash of page content, so unchanged pages cause no repeat LLM
    work (§4.12)."""
    normalised = re.sub(r"\s+", " ", (text or "")).strip().lower()
    return hashlib.sha256(normalised.encode()).hexdigest()[:32]


@dataclass
class EvidenceDocument:
    """One retrieved page. Only a minimal audit snippet is persisted —
    never a full copy of a copyrighted article."""

    url: str
    title: str
    acquisition_source: str          # e.g. "tavily"
    origin_domain: str
    origin_authority: str
    retrieved_at: str
    content_hash: str
    published_at: str | None = None
    canonical: str = ""
    byline: str = ""
    snippet: str = ""                # minimal, audit-sized
    story_cluster_id: str | None = None
    id: int | None = None

    # Full text is held in memory for extraction and validation but is
    # deliberately NOT a persisted field.
    content: str = field(default="", repr=False, compare=False)


@dataclass
class CandidateClaim:
    """What an extractor may emit. Enums only — no impact scores, no
    lambda adjustments, no confidence percentages."""

    claim_type: str
    player_name: str | None = None
    player_external_id: str | None = None
    team: str | None = None
    availability: str | None = None
    certainty: str | None = None
    expected_role: str | None = None
    event_at: str | None = None
    supporting_evidence: str = ""
    source_url: str = ""
    expires_at: str | None = None
    quantitative_impact: str = "NONE DIRECTLY"

    # Set by the validator, never by the extractor.
    verdict: str = ClaimVerdict.ACCEPTED.value
    rejection_reason: str = ""
    evidence_id: int | None = None
    story_cluster_id: str | None = None
    id: int | None = None
