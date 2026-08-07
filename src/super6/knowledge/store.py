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
    "rejection_reason", "story_cluster_id",
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
) -> CandidateClaim:
    values = {
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


def evidence_by_id(conn: sqlite3.Connection, evidence_id: int):
    return conn.execute(
        "SELECT * FROM evidence_documents WHERE id = ?", (evidence_id,)
    ).fetchone()
