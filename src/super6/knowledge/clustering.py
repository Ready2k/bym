"""Story clustering and corroboration (Increment 4C).

Five sites republishing the same PA copy are **one** piece of evidence,
not five. Without this the system discovers the remarkable statistical
principle that rumours become facts if enough websites copy them.

Clustering signals, all deterministic:

    canonical URL        same story, different tracking parameters
    title similarity     Jaccard over normalised title tokens
    wire attribution     "PA Media", "Reuters" etc. in the byline
    publication proximity

Corroboration is then counted in **evidence families**, not documents.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta

from ..db import from_iso
from .models import WIRE_AGENCIES, EvidenceDocument, canonical_url

TITLE_SIMILARITY_THRESHOLD = 0.8
WIRE_PROXIMITY = timedelta(hours=24)

_STOPWORDS = {
    "the", "a", "an", "of", "for", "to", "in", "on", "and", "is", "as",
    "at", "by", "with", "from", "after", "ahead",
}


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", (title or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def title_similarity(a: str, b: str) -> float:
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def is_wire_copy(document: EvidenceDocument) -> bool:
    haystack = f"{document.byline} {document.snippet}".lower()
    return any(agency in haystack for agency in WIRE_AGENCIES)


def _published(document: EvidenceDocument):
    try:
        return from_iso(document.published_at) if document.published_at else None
    except (ValueError, AttributeError):
        return None


def same_story(a: EvidenceDocument, b: EvidenceDocument) -> bool:
    """Whether two documents are copies of one story."""
    if canonical_url(a.url) == canonical_url(b.url):
        return True

    similarity = title_similarity(a.title, b.title)
    if similarity >= TITLE_SIMILARITY_THRESHOLD:
        # Near-identical headlines on different domains are syndication
        # unless they are far apart in time.
        pa, pb = _published(a), _published(b)
        if pa is None or pb is None:
            return True
        return abs(pa - pb) <= WIRE_PROXIMITY

    # Wire copy with a moderately similar headline, published close
    # together, is the classic syndication pattern.
    if is_wire_copy(a) and is_wire_copy(b) and similarity >= 0.5:
        pa, pb = _published(a), _published(b)
        if pa is not None and pb is not None and abs(pa - pb) <= WIRE_PROXIMITY:
            return True
    return False


def assign_clusters(documents: list[EvidenceDocument]) -> list[EvidenceDocument]:
    """Assign a `story_cluster_id` to every document, in place."""
    clusters: list[list[EvidenceDocument]] = []
    for document in documents:
        for cluster in clusters:
            if any(same_story(document, member) for member in cluster):
                cluster.append(document)
                break
        else:
            clusters.append([document])

    for cluster in clusters:
        seed = min(canonical_url(d.url) for d in cluster)
        cluster_id = hashlib.sha256(seed.encode()).hexdigest()[:16]
        for document in cluster:
            document.story_cluster_id = cluster_id
    return documents


@dataclass(frozen=True)
class CorroborationReport:
    documents: int
    evidence_families: int
    independent: bool
    authorities: tuple[str, ...]

    def describe(self) -> str:
        if self.documents == self.evidence_families:
            return f"{self.evidence_families} independent source(s)"
        return (
            f"{self.documents} documents but only {self.evidence_families} "
            "independent evidence family/families — the rest are syndicated "
            "copies of the same story"
        )


def corroboration(documents: list[EvidenceDocument]) -> CorroborationReport:
    """Count independent evidence families, not documents (§4.8)."""
    clustered = assign_clusters(list(documents))
    families = {d.story_cluster_id for d in clustered}
    return CorroborationReport(
        documents=len(clustered),
        evidence_families=len(families),
        independent=len(families) > 1,
        authorities=tuple(sorted({d.origin_authority for d in clustered})),
    )
