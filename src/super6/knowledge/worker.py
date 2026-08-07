"""Knowledge agent orchestration (Increment 4A-4D).

    trigger -> query plan -> Tavily search -> source selection
            -> Tavily extract -> UNTRUSTED EVIDENCE
            -> claim extractor -> schema validation -> grounding
            -> clustering -> availability pipeline -> events

The forecast is never touched. Accepted claims enter the *existing*
availability pipeline as ordinary evidence, where the same deterministic
reconciliation applies — a web report does not outrank a confirmed XI
because an LLM found it interesting.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from .. import events as ev
from ..availability import ingest as availability_ingest
from ..availability.model import ClaimStatus, EventType, Importance, LineupKind
from ..db import to_iso, utcnow
from ..sources import tavily
from ..sources.quota import QuotaExceededError, constrained
from . import clustering, store
from .config import ExtractorMode
from .extractor import ClaimExtractor, validate
from .models import (
    AUTHORITY_TO_TIER,
    CandidateClaim,
    ClaimType,
    EvidenceDocument,
    OriginAuthority,
    canonical_url,
    classify_authority,
    content_hash,
    domain_of,
)
from .queries import PlannedQuery

# Availability values a web claim may map onto. CONFIRMED_STARTER and
# CONFIRMED_BENCH are absent deliberately: only a confirmed XI from a
# structured source produces those (§4.11).
WEB_AVAILABILITY_MAP = {
    "DOUBT": EventType.DOUBT,
    "INJURY": EventType.INJURY,
    "SUSPENSION": EventType.SUSPENSION,
    "RETURN": EventType.RETURN,
    "CONFIRMED_ABSENT": EventType.CONFIRMED_ABSENT,
    "EXPECTED_STARTER": EventType.EXPECTED_STARTER,
    "EXPECTED_BENCH": EventType.EXPECTED_BENCH,
}


@dataclass
class ResearchResult:
    searches: int = 0
    extractions: int = 0
    documents_new: int = 0
    documents_skipped: int = 0
    claims_accepted: int = 0
    claims_rejected: int = 0
    claims_promoted: int = 0
    llm_calls: int = 0
    extraction_failures: int = 0
    mode: str = ExtractorMode.SHADOW.value
    events: list[ev.Event] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def build_evidence(
    page: tavily.ExtractedPage,
    hit: tavily.SearchHit | None,
    now: datetime,
) -> EvidenceDocument:
    """Assemble an evidence document, with acquisition and origin
    identities kept separate."""
    authority = classify_authority(page.url)
    return EvidenceDocument(
        url=page.url,
        title=page.title or (hit.title if hit else ""),
        acquisition_source=tavily.SOURCE_ID,   # who fetched it
        origin_domain=domain_of(page.url),      # who said it
        origin_authority=authority.value,
        retrieved_at=to_iso(now),
        content_hash=content_hash(page.content),
        published_at=page.published_at or (hit.published_at if hit else None),
        canonical=canonical_url(page.url),
        snippet=(hit.snippet if hit else page.content[:400]),
        content=page.content,
    )


def research(
    conn: sqlite3.Connection,
    home_team: str,
    away_team: str,
    plans: list[PlannedQuery],
    api_key: str,
    extractor: ClaimExtractor,
    now: datetime | None = None,
    round_number: int | None = None,
    max_extract: int = 5,
    mode: str = ExtractorMode.SHADOW.value,
    **request_kwargs,
) -> ResearchResult:
    """Run one bounded research pass for a fixture.

    `mode` defaults to **shadow**: claims are extracted, validated,
    recorded with provenance, and deliberately NOT promoted into
    reconciled availability state. Only `active` feeds the availability
    pipeline, and even then through the same deterministic reconciliation
    every other source uses.
    """
    now = now or utcnow()
    result = ResearchResult(mode=mode)

    if constrained(tavily.QUOTA):
        result.notes.append(
            "Tavily budget low — routine research skipped to preserve "
            "deadline investigations (§25)"
        )
        return result

    hits: dict[str, tavily.SearchHit] = {}
    for plan in plans:
        try:
            found = tavily.search(
                plan.query, api_key,
                include_domains=list(plan.include_domains) or None,
                days=plan.days, **request_kwargs,
            )
        except QuotaExceededError as exc:
            result.notes.append(f"search stopped: {exc}")
            break
        result.searches += 1
        for hit in found:
            hits.setdefault(hit.url, hit)

    if not hits:
        result.notes.append("no results")
        return result

    urls = list(hits)[:max_extract]
    try:
        pages = tavily.extract(urls, api_key, **request_kwargs)
        result.extractions += 1
    except QuotaExceededError as exc:
        result.notes.append(f"extraction skipped: {exc}")
        return result

    documents: list[EvidenceDocument] = []
    for page in pages:
        document = build_evidence(page, hits.get(page.url), now)
        # Unchanged content costs no LLM call (§4.12).
        if store.seen_before(conn, document.url, document.content_hash):
            result.documents_skipped += 1
            continue
        documents.append(document)

    clustering.assign_clusters(documents)

    for document in documents:
        stored, was_new = store.record_evidence(conn, document)
        if was_new:
            result.documents_new += 1

        raw_claims = extractor.extract_claims(document)
        result.llm_calls += 1
        outcome = validate(raw_claims, document)

        promote = mode == ExtractorMode.ACTIVE.value
        run_id = _record_provenance(
            conn, extractor, mode, stored.id,
            len(outcome.accepted), len(outcome.rejected), promote,
        )

        failure = getattr(extractor, "last_failure", None)
        if failure is not None:
            result.extraction_failures += 1
            result.events.append(
                ev.make_event(
                    ev.EventType.KNOWLEDGE_EXTRACTION_FAILED,
                    ev.Severity.INFORMATIONAL,
                    f"Claim extraction failed ({failure.kind}) for "
                    f"{document.origin_domain}. Evidence retained; no "
                    "candidate claims. Forecasting is unaffected.",
                    round_number=round_number,
                    home_team=home_team,
                    away_team=away_team,
                    detail={
                        "failure_kind": failure.kind,
                        "detail": failure.detail[:300],
                        "source_url": document.url,
                    },
                    now=now,
                )
            )

        for claim in outcome.all_claims:
            store.record_claim(
                conn, claim, home_team, away_team, round_number, to_iso(now),
                promoted=promote and claim in outcome.accepted,
                extraction_run_id=run_id,
            )
        result.claims_accepted += len(outcome.accepted)
        result.claims_rejected += len(outcome.rejected)

        for claim in outcome.rejected:
            result.events.append(
                ev.make_event(
                    ev.EventType.CLAIM_REJECTED,
                    ev.Severity.INFORMATIONAL,
                    f"Rejected claim from {document.origin_domain}: "
                    f"{claim.rejection_reason}",
                    round_number=round_number,
                    home_team=home_team,
                    away_team=away_team,
                    detail={"source_url": claim.source_url},
                    now=now,
                )
            )

        for claim in outcome.accepted:
            result.events.append(
                ev.make_event(
                    ev.EventType.CLAIM_DISCOVERED,
                    ev.Severity.INFORMATIONAL,
                    f"{claim.player_name or claim.team}: "
                    f"{claim.availability or claim.expected_role} "
                    f"({claim.certainty}) via {document.origin_domain}",
                    round_number=round_number,
                    home_team=home_team,
                    away_team=away_team,
                    detail={
                        "acquisition_source": document.acquisition_source,
                        "origin_domain": document.origin_domain,
                        "origin_authority": document.origin_authority,
                        "published_at": document.published_at,
                        "event_at": claim.event_at,
                        "observed_at": to_iso(now),
                        "evidence": claim.supporting_evidence,
                        "story_cluster_id": document.story_cluster_id,
                        "quantitative_impact": "NONE DIRECTLY",
                    },
                    now=now,
                )
            )
            if mode == ExtractorMode.ACTIVE.value:
                result.claims_promoted += 1
                result.events.extend(
                    _feed_availability_pipeline(
                        conn, claim, document, home_team, away_team,
                        round_number, now,
                    )
                )

    if mode != ExtractorMode.ACTIVE.value and result.claims_accepted:
        result.notes.append(
            f"shadow mode: {result.claims_accepted} validated claim(s) "
            "recorded but NOT promoted into availability state"
        )
    return result


def _record_provenance(
    conn: sqlite3.Connection,
    extractor,
    mode: str,
    evidence_id: int | None,
    accepted: int,
    rejected: int,
    promoted: bool,
) -> int | None:
    """Persist extractor provenance when the extractor supplies it.

    Extractors without provenance (the null and fake ones) simply record
    nothing rather than fabricating a run.
    """
    provenance = getattr(extractor, "last_provenance", None)
    if provenance is None:
        return None
    return store.record_extraction_run(
        conn, provenance, mode, evidence_id, accepted, rejected, promoted
    )


def _feed_availability_pipeline(
    conn: sqlite3.Connection,
    claim: CandidateClaim,
    document: EvidenceDocument,
    home_team: str,
    away_team: str,
    round_number: int | None,
    now: datetime,
) -> list[ev.Event]:
    """Hand an accepted claim to the existing availability pipeline.

    Reliability comes from the ORIGIN authority, not from Tavily. The
    claim's own certainty is preserved unchanged — an official source
    making a hedged statement stays hedged.
    """
    if claim.claim_type != ClaimType.AVAILABILITY.value:
        return []
    event_type = WEB_AVAILABILITY_MAP.get(claim.availability or "")
    if event_type is None or not claim.player_name:
        return []

    tier = AUTHORITY_TO_TIER[OriginAuthority(document.origin_authority)]
    outcome = availability_ingest.ingest_availability(
        conn,
        home_team,
        away_team,
        claim.team or home_team,
        claim.player_name,
        event_type,
        ClaimStatus(claim.certainty or ClaimStatus.REPORTED.value),
        source_id=document.origin_domain,   # origin, not "tavily"
        source_tier=tier,
        source_timestamp=now,
        round_number=round_number,
        now=now,
        source_player_id=claim.player_external_id,
    )
    return outcome.events


def investigate_movement(
    conn: sqlite3.Connection,
    home_team: str,
    away_team: str,
    movement_description: str,
    api_key: str,
    extractor: ClaimExtractor,
    now: datetime | None = None,
    round_number: int | None = None,
    **kwargs,
) -> ResearchResult:
    """Targeted investigation triggered by an unexplained market move."""
    from .queries import plan_investigation

    now = now or utcnow()
    plans = plan_investigation(home_team) + plan_investigation(away_team)
    result = research(
        conn, home_team, away_team, plans, api_key, extractor,
        now=now, round_number=round_number, **kwargs,
    )

    if result.claims_accepted:
        result.events.append(
            ev.make_event(
                ev.EventType.INTELLIGENCE_EXPLAINS_MARKET_MOVE,
                ev.Severity.IMPORTANT,
                f"{movement_description} — {result.claims_accepted} claim(s) "
                "discovered that may be related. Plausible explanation only; "
                "causation cannot be established.",
                round_number=round_number,
                home_team=home_team,
                away_team=away_team,
                detail={
                    "relationship": "PLAUSIBLE",
                    "claims_found": result.claims_accepted,
                    "quantitative_impact": "NONE DIRECTLY",
                },
                now=now,
            )
        )
    return result
