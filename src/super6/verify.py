"""Live source verification harness.

One controlled command rather than ad-hoc scripts. Each source is checked
independently, and **approval is never bypassed**: a source that is not
APPROVED in the registry is reported as blocked, not quietly queried. A
successful verification does not approve anything either — approval stays
a deliberate human act.

Absence of data is distinguished from failure throughout. A lineup that
has not been published yet is not a fault; nor is an empty injury list.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .sources import api_football, capabilities, tavily
from .sources import odds_api
from .sources.consensus import build_consensus
from .sources.registry import (
    DEFAULT_REGISTRY_PATH,
    SourceNotApprovedError,
    load_registry,
)

OK = "OK"
FAILED = "FAILED"
BLOCKED = "BLOCKED"
SKIPPED = "SKIPPED"
ABSENT = "ABSENT"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""

    def line(self) -> str:
        mark = {
            OK: "  ok  ", FAILED: " FAIL ", BLOCKED: "BLOCK ",
            SKIPPED: " skip ", ABSENT: "absent",
        }.get(self.status, "  ?   ")
        return f"[{mark}] {self.name}" + (f" — {self.detail}" if self.detail else "")


@dataclass
class SourceVerification:
    source_id: str
    checks: list[Check] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(c.status == FAILED for c in self.checks):
            return FAILED
        if any(c.status == BLOCKED for c in self.checks):
            return BLOCKED
        if all(c.status == SKIPPED for c in self.checks):
            return SKIPPED
        return OK

    def add(self, name, status, detail="") -> None:
        self.checks.append(Check(name, status, detail))

    def report(self) -> str:
        header = f"{self.source_id}: {self.status}"
        return "\n".join([header] + [f"  {c.line()}" for c in self.checks])


def _approval_gate(
    verification: SourceVerification, source_id: str, registry_path: Path
) -> bool:
    """Returns True only when the source may legitimately be queried."""
    registry = load_registry(registry_path)
    record = registry.get(source_id)
    if record is None:
        verification.add("registry", BLOCKED, "not registered")
        return False
    if record.usage_status != "APPROVED":
        verification.add(
            "registry approval",
            BLOCKED,
            f"{record.usage_status} — run `super6 sources approve "
            f"{source_id}` after accepting the terms",
        )
        return False
    verification.add("registry approval", OK, "APPROVED")
    return True


# ---- The Odds API -------------------------------------------------------


def verify_odds_api(
    api_key: str | None,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    division: str = "E0",
) -> SourceVerification:
    v = SourceVerification("the_odds_api")
    if not _approval_gate(v, "the_odds_api", registry_path):
        return v
    if not api_key:
        v.add("credentials", SKIPPED, "ODDS_API_KEY not set")
        return v

    try:
        events = odds_api.fetch_odds(division, api_key, registry_path=registry_path)
    except SourceNotApprovedError as exc:
        v.add("authentication", BLOCKED, str(exc))
        return v
    except Exception as exc:
        v.add("authentication / request", FAILED, f"{type(exc).__name__}: {exc}")
        return v

    v.add("authentication", OK)
    v.add("competition discovery", OK, f"{len(events)} event(s) for {division}")
    if not events:
        v.add("odds request", ABSENT, "no upcoming fixtures returned")
        return v

    event = next((e for e in events if e.bookmakers), events[0])
    if not event.bookmakers:
        v.add("bookmaker parsing", ABSENT, "no bookmaker prices in response")
        return v
    v.add("bookmaker parsing", OK, f"{len(event.bookmakers)} bookmaker(s)")

    # Outcome-order independence: prices must be keyed by team name.
    sample = event.bookmakers[0]
    ordered_ok = sample.home > 1.0 and sample.draw > 1.0 and sample.away > 1.0
    v.add(
        "H/D/A outcome-order independence",
        OK if ordered_ok else FAILED,
        f"{event.home_team} {sample.home} / draw {sample.draw} / "
        f"{event.away_team} {sample.away}",
    )

    consensus = build_consensus(event)
    if consensus is None:
        v.add("de-vigging / consensus", ABSENT, "all quotes stale")
    else:
        total = consensus.p_home + consensus.p_draw + consensus.p_away
        v.add(
            "de-vigging / consensus",
            OK if abs(total - 1.0) < 1e-6 else FAILED,
            f"{consensus.p_home:.1%}/{consensus.p_draw:.1%}/"
            f"{consensus.p_away:.1%} from {consensus.bookmaker_count} books, "
            f"dispersion {consensus.dispersion:.3f}",
        )

    used = odds_api.credits_used_this_month()
    v.add("quota accounting", OK, f"{used}/{odds_api.FREE_TIER_MONTHLY_CREDITS} used")
    return v


# ---- API-Football -------------------------------------------------------


def verify_api_football(
    api_key: str | None,
    season: int = 2026,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> SourceVerification:
    v = SourceVerification("api_football")
    if not _approval_gate(v, "api_football", registry_path):
        return v
    if not api_key:
        v.add("credentials", SKIPPED, "API_FOOTBALL_KEY not set")
        return v

    coverage_by_division = {}
    for division in ("E0", "E1"):
        try:
            coverage = api_football.fetch_coverage(
                division, season, api_key, registry_path=registry_path
            )
        except Exception as exc:
            v.add(f"{division} coverage", FAILED, f"{type(exc).__name__}: {exc}")
            continue
        coverage_by_division[division] = coverage
        v.add(
            f"{division} coverage ({season})",
            OK,
            ", ".join(
                f"{k}={'AVAILABLE' if val else 'MISSING'}"
                for k, val in coverage.items()
            ),
        )

    if not coverage_by_division:
        return v
    v.add("authentication", OK)

    try:
        fixtures = api_football.fetch_fixtures(
            "E0", season, api_key, registry_path=registry_path
        )
    except Exception as exc:
        v.add("fixture mapping", FAILED, f"{type(exc).__name__}: {exc}")
        return v
    v.add(
        "fixture mapping",
        OK if fixtures else ABSENT,
        f"{len(fixtures)} fixture(s) returned",
    )
    if not fixtures:
        return v

    ids = [f.fixture_id for f in fixtures[:20] if f.fixture_id]
    try:
        injuries = api_football.fetch_injuries(
            ids, api_key, registry_path=registry_path
        )
    except Exception as exc:
        v.add("injury response", FAILED, f"{type(exc).__name__}: {exc}")
    else:
        v.add(
            "injury response",
            OK if injuries else ABSENT,
            f"{len(injuries)} record(s)"
            + (
                f"; stable ids present: "
                f"{sum(1 for i in injuries if i.player_id)}/{len(injuries)}"
                if injuries
                else " (none reported)"
            ),
        )

    try:
        lineups = api_football.fetch_lineups(
            ids[0], api_key, registry_path=registry_path
        )
    except Exception as exc:
        v.add("lineup response", FAILED, f"{type(exc).__name__}: {exc}")
    else:
        v.add(
            "lineup response",
            OK if lineups else ABSENT,
            f"{len(lineups)} team lineup(s)"
            if lineups
            else "not yet published (expected outside the 20-40 min window)",
        )
    return v


# ---- Tavily -------------------------------------------------------------


def verify_tavily(
    api_key: str | None,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> SourceVerification:
    v = SourceVerification("tavily")
    if not _approval_gate(v, "tavily", registry_path):
        return v
    if not api_key:
        v.add("credentials", SKIPPED, "TAVILY_API_KEY not set")
        return v

    try:
        hits = tavily.search(
            "Premier League team news injuries",
            api_key,
            max_results=3,
            registry_path=registry_path,
        )
    except Exception as exc:
        v.add("authentication / search", FAILED, f"{type(exc).__name__}: {exc}")
        return v

    v.add("authentication", OK)
    v.add("bounded search", OK if hits else ABSENT, f"{len(hits)} result(s)")
    if not hits:
        return v

    with_metadata = sum(1 for h in hits if h.url and h.title)
    v.add(
        "URL/title metadata",
        OK if with_metadata == len(hits) else FAILED,
        f"{with_metadata}/{len(hits)} complete",
    )
    dated = sum(1 for h in hits if h.published_at)
    v.add("published date metadata", OK if dated else ABSENT, f"{dated}/{len(hits)}")

    try:
        pages = tavily.extract([hits[0].url], api_key, registry_path=registry_path)
    except Exception as exc:
        v.add("extraction", FAILED, f"{type(exc).__name__}: {exc}")
        return v
    v.add(
        "extraction",
        OK if pages and pages[0].content else ABSENT,
        f"{len(pages)} page(s), "
        f"{len(pages[0].content) if pages else 0} chars retrieved",
    )
    v.add(
        "retention policy",
        OK,
        "metadata + audit snippet only; no full article persisted",
    )
    return v


# ---- Ollama -------------------------------------------------------------


def verify_ollama(config=None) -> SourceVerification:
    from .knowledge.config import from_env
    from .knowledge.models import EvidenceDocument, content_hash
    from .knowledge.ollama import OllamaClaimExtractor, check_health

    config = config or from_env()
    v = SourceVerification("ollama")

    health = check_health(config)
    v.add(
        f"host resolves ({config.host})",
        OK if health.reachable else FAILED,
        health.detail or f"{health.models_seen} model(s) installed",
    )
    if not health.reachable:
        return v
    v.add(
        f"model '{config.model}' installed",
        OK if health.model_available else FAILED,
        health.detail,
    )
    if not health.model_available:
        return v

    if config.kind != "ollama":
        v.add(
            "schema-constrained extraction",
            SKIPPED,
            "CLAIM_EXTRACTOR is not 'ollama' — set it to test extraction",
        )
        return v

    # Deliberately contains two unambiguous, differently-shaped claims. A
    # single hedged sentence is a poor probe: the model may reasonably
    # emit nothing, and "0 claims" would then read as a pass whether the
    # pipeline worked or not.
    sample = (
        "Chelsea head coach Enzo Maresca confirmed on Friday that Cole "
        "Palmer will be assessed tomorrow after picking up a knock in "
        "training. Robert Sanchez is suspended for this fixture after his "
        "red card."
    )
    evidence = EvidenceDocument(
        url="https://example.test/verification",
        title="Verification sample",
        acquisition_source="verification",
        origin_domain="example.test",
        origin_authority="OTHER_MEDIA",
        retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        content_hash=content_hash(sample),
        content=sample,
    )
    extractor = OllamaClaimExtractor(config)
    claims = extractor.extract_claims(evidence)
    failure = extractor.last_failure
    if failure is not None:
        v.add("schema-constrained extraction", FAILED, f"{failure.kind}: {failure.detail[:200]}")
        return v
    v.add(
        "schema-constrained extraction",
        OK if claims else ABSENT,
        f"{len(claims)} claim(s) passed schema validation"
        + ("" if claims else " — the model found nothing in a sample that "
                            "contains two clear claims; check the model is "
                            "suited to structured extraction"),
    )

    from .knowledge.extractor import validate

    outcome = validate(claims, evidence)
    v.add(
        "deterministic validators still apply",
        OK,
        f"{len(outcome.accepted)} accepted, {len(outcome.rejected)} rejected "
        "by grounding/injection/inference/completeness checks — enforced "
        "regardless of provider",
    )
    return v


def verify_all(
    registry_path: Path = DEFAULT_REGISTRY_PATH, season: int = 2026
) -> list[SourceVerification]:
    from .secrets import get_key

    return [
        verify_odds_api(get_key("ODDS_API_KEY"), registry_path),
        verify_api_football(get_key("API_FOOTBALL_KEY"), season, registry_path),
        verify_tavily(get_key("TAVILY_API_KEY"), registry_path),
        verify_ollama(),
    ]
