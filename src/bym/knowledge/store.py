"""Knowledge evidence and claim persistence (Increment 4A/4B).

Retention policy is `derived_claims_and_source_metadata_only`: URL,
title, byline, timestamps, content hash and a **minimal audit snippet** —
never a full copy of a copyrighted article. The snippet exists so a claim
can be audited later, not so the system accumulates a news archive.
"""

from __future__ import annotations

import sqlite3

from ..db import to_iso, utcnow
from .models import CandidateClaim, EvidenceDocument

EVIDENCE_COLUMNS = (
    "acquisition_source", "origin_domain", "origin_authority", "url",
    "canonical", "title", "byline", "published_at", "retrieved_at",
    "content_hash", "story_cluster_id", "snippet",
)

CLAIM_COLUMNS = (
    "evidence_id", "round_number", "home_team", "away_team", "team",
    "player_name", "player_external_id", "claim_type", "availability",
    "certainty", "expected_role", "event_at", "observed_at",
    "supporting_evidence", "source_url", "expires_at", "verdict",
    "rejection_reason", "story_cluster_id", "promoted", "extraction_run_id",
)

# Audit snippets are capped: enough to justify a claim, not an archive.
MAX_SNIPPET_CHARS = 400


def seen_before(conn: sqlite3.Connection, url: str, content_hash: str) -> bool:
    """Whether this exact content has already been processed — the guard
    that stops unchanged pages causing repeat LLM work (§4.12)."""
    row = conn.execute(
        "SELECT 1 FROM evidence_documents WHERE url = ? AND content_hash = ?",
        (url, content_hash),
    ).fetchone()
    return row is not None


def record_evidence(
    conn: sqlite3.Connection, document: EvidenceDocument
) -> tuple[EvidenceDocument, bool]:
    """Store evidence metadata. Returns (document, was_new)."""
    existing = conn.execute(
        "SELECT id FROM evidence_documents WHERE url = ? AND content_hash = ?",
        (document.url, document.content_hash),
    ).fetchone()
    if existing is not None:
        document.id = existing["id"]
        return document, False

    document.snippet = (document.snippet or "")[:MAX_SNIPPET_CHARS]
    cursor = conn.execute(
        f"INSERT INTO evidence_documents ({', '.join(EVIDENCE_COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in EVIDENCE_COLUMNS)})",
        tuple(getattr(document, c) for c in EVIDENCE_COLUMNS),
    )
    document.id = int(cursor.lastrowid)
    return document, True


def record_claim(
    conn: sqlite3.Connection,
    claim: CandidateClaim,
    home_team: str,
    away_team: str,
    round_number: int | None = None,
    observed_at: str | None = None,
    promoted: bool = False,
    extraction_run_id: int | None = None,
) -> CandidateClaim:
    values = {
        "promoted": int(promoted),
        "extraction_run_id": extraction_run_id,
        "evidence_id": claim.evidence_id,
        "round_number": round_number,
        "home_team": home_team,
        "away_team": away_team,
        "team": claim.team,
        "player_name": claim.player_name,
        "player_external_id": claim.player_external_id,
        "claim_type": claim.claim_type,
        "availability": claim.availability,
        "certainty": claim.certainty,
        "expected_role": claim.expected_role,
        "event_at": claim.event_at,
        "observed_at": observed_at or to_iso(utcnow()),
        "supporting_evidence": claim.supporting_evidence[:MAX_SNIPPET_CHARS],
        "source_url": claim.source_url,
        "expires_at": claim.expires_at,
        "verdict": claim.verdict,
        "rejection_reason": claim.rejection_reason,
        "story_cluster_id": claim.story_cluster_id,
    }
    cursor = conn.execute(
        f"INSERT INTO candidate_claims ({', '.join(CLAIM_COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in CLAIM_COLUMNS)})",
        tuple(values[c] for c in CLAIM_COLUMNS),
    )
    claim.id = int(cursor.lastrowid)
    return claim


def claims_for_fixture(
    conn: sqlite3.Connection, home: str, away: str, verdict: str | None = None
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM candidate_claims WHERE home_team = ? AND away_team = ?"
    params: tuple = (home, away)
    if verdict:
        sql += " AND verdict = ?"
        params += (verdict,)
    return conn.execute(sql + " ORDER BY observed_at", params).fetchall()


RUN_COLUMNS = (
    "provider", "model", "model_digest", "host", "prompt_version",
    "extractor_version", "mode", "requested_at", "completed_at",
    "evidence_hash", "evidence_id", "status", "error", "claims_emitted",
    "claims_accepted", "claims_rejected", "promoted",
)


def record_extraction_run(
    conn: sqlite3.Connection,
    provenance,
    mode: str,
    evidence_id: int | None,
    claims_accepted: int,
    claims_rejected: int,
    promoted: bool,
) -> int:
    """Persist how a set of claims was produced (§5 provenance).

    Stores host rather than base URL, and no article content — a run is
    reproducible from this row plus the evidence hash, without the record
    becoming either a secret store or a news archive.
    """
    values = {
        "provider": provenance.provider,
        "model": provenance.model,
        "model_digest": provenance.model_digest,
        "host": provenance.host,
        "prompt_version": provenance.prompt_version,
        "extractor_version": provenance.extractor_version,
        "mode": mode,
        "requested_at": provenance.requested_at,
        "completed_at": provenance.completed_at,
        "evidence_hash": provenance.evidence_hash,
        "evidence_id": evidence_id,
        "status": provenance.status,
        "error": provenance.error,
        "claims_emitted": provenance.claims_emitted,
        "claims_accepted": claims_accepted,
        "claims_rejected": claims_rejected,
        "promoted": int(promoted),
    }
    cursor = conn.execute(
        f"INSERT INTO extraction_runs ({', '.join(RUN_COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in RUN_COLUMNS)})",
        tuple(values[c] for c in RUN_COLUMNS),
    )
    return int(cursor.lastrowid)


def extraction_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM extraction_runs ORDER BY requested_at"
    ).fetchall()


def evidence_by_id(conn: sqlite3.Connection, evidence_id: int):
    return conn.execute(
        "SELECT * FROM evidence_documents WHERE id = ?", (evidence_id,)
    ).fetchone()
