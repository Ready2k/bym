"""Source Acquisition Policy registry (SPEC.md §18.1, §19, §22).

Every data source is registered with a `usage_status`, and **no worker may
query a source that is not APPROVED** (§22). That rule is enforced here in
code rather than left to discipline, because the failure mode §18.1 warns
about — "a worker script six months from now" quietly querying something
whose terms were never checked — is exactly the kind of thing that looks
harmless in a diff.

Promotion REVIEW_REQUIRED -> APPROVED is a deliberate human act
(`bym sources approve`), never automatic, and is refused outright for
PROHIBITED sources.

The four statuses say different things about *who owes what*:

    APPROVED         cleared; workers may query it
    REVIEW_REQUIRED  awaiting your terms review — a task
    EVALUATED        assessed and not adopted — a record, nothing pending
    PROHIBITED       cannot be used, cannot be promoted

Only REVIEW_REQUIRED is a call to action, and only that one raises a
System Health warning.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path

DEFAULT_REGISTRY_PATH = Path("config/source_registry.json")


class UsageStatus(str, Enum):
    APPROVED = "APPROVED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    # Assessed and deliberately not adopted. Distinct from REVIEW_REQUIRED,
    # which means "awaiting your review, and will be used once approved" —
    # a source in that state is a task. EVALUATED is a *record*: nothing
    # queries it, nothing is waiting on you, and it exists so the decision
    # is not re-derived. Without this, parking a source raised a System
    # Health warning demanding action nobody intended to take.
    EVALUATED = "EVALUATED"
    PROHIBITED = "PROHIBITED"


class AcquisitionMethod(str, Enum):
    API = "API"
    RSS = "RSS"
    DATASET = "DATASET"
    SEARCH = "SEARCH"
    MANUAL = "MANUAL"


class SourceNotApprovedError(RuntimeError):
    """Raised when code attempts to query a non-APPROVED source."""


@dataclass
class SourceRecord:
    source_id: str
    source_name: str
    acquisition_method: str
    usage_status: str
    terms_url: str = ""
    terms_checked_at: str = ""
    refresh_limit: str = ""
    retention_policy: str = ""
    attribution_required: bool = False
    purpose: list[str] = field(default_factory=list)
    notes: str = ""


def _seed() -> list[SourceRecord]:
    """Sources known at Phase 2 Increment 1."""
    return [
        SourceRecord(
            source_id="football_data_co_uk",
            source_name="Football-Data.co.uk",
            acquisition_method=AcquisitionMethod.DATASET.value,
            usage_status=UsageStatus.APPROVED.value,
            terms_url="https://www.football-data.co.uk/",
            terms_checked_at="2026-08-07",
            refresh_limit="weekly CSV download per division",
            retention_policy="persist raw CSV + parsed fixtures",
            purpose=["historical_results", "historical_odds", "backtest_corpus"],
            notes="Free downloadable historical results/odds. Training and "
            "backtesting corpus for Phase 1.",
        ),
        SourceRecord(
            source_id="the_odds_api",
            source_name="The Odds API",
            acquisition_method=AcquisitionMethod.API.value,
            usage_status=UsageStatus.REVIEW_REQUIRED.value,
            terms_url="https://the-odds-api.com/terms-and-conditions.html",
            terms_checked_at="2026-08-07",
            refresh_limit="500 credits/month (free tier); 1 credit per "
            "markets x regions per request",
            retention_policy="persist_internal_odds_snapshots",
            attribution_required=False,
            purpose=["pre_match_1x2", "market_consensus", "market_movement"],
            notes="UK region, h2h market, Premier League + Championship. "
            "Terms permit use in analytical tools including commercial "
            "applications, but prohibit reselling or repackaging the raw "
            "market feed as a standalone data product — this system is a "
            "permitted analytical use. Promote to APPROVED only once an "
            "account exists and the terms have been accepted.",
        ),
        SourceRecord(
            source_id="api_football",
            source_name="API-Football",
            acquisition_method=AcquisitionMethod.API.value,
            usage_status=UsageStatus.REVIEW_REQUIRED.value,
            terms_url="https://www.api-football.com/terms",
            terms_checked_at="2026-08-07",
            refresh_limit="free tier: 100 requests/day, 10 requests/minute",
            retention_policy="persist_internal_structured_events",
            attribution_required=False,
            purpose=[
                "fixture_mapping",
                "confirmed_lineups",
                "injuries",
                "suspensions",
                "squads",
                "player_resolution",
            ],
            notes="Structured availability source for Phase 2 Increment 3B. "
            "Terms permit using the data to build applications and "
            "fantasy-football projects but prohibit reselling the feed "
            "directly — this private analytical system is a permitted "
            "use. Provides confirmed XIs (typically 20-40 min before "
            "kickoff) and injuries (Missing Fixture vs Questionable, "
            "refreshed roughly every four hours). Does NOT provide "
            "predicted/expected lineups; that gap is deliberately left "
            "to manual entry or the later knowledge agent, since "
            "Sportmonks' expected-lineups product is far outside the "
            "GBP10 budget (§25). "
            "FREE-TIER LIMITS, established by live probing 2026-08-08: "
            "current seasons are paywalled (2025+ rejected, 'try from "
            "2022 to 2024'), and the batch `ids` parameter is paid-only. "
            "BUT single-fixture /injuries and /fixtures/lineups DO return "
            "full data for 2022-2024 with stable player ids. That makes "
            "the free tier useless for live 2026/27 operation but "
            "sufficient to measure, at zero cost, whether availability "
            "data improves forecasts at all before anyone pays for a "
            "live feed (§25's cost-per-Brier-improvement test).",
        ),
        SourceRecord(
            source_id="tavily",
            source_name="Tavily",
            acquisition_method=AcquisitionMethod.SEARCH.value,
            usage_status=UsageStatus.REVIEW_REQUIRED.value,
            terms_url="https://www.tavily.com/terms",
            terms_checked_at="2026-08-07",
            refresh_limit="free Researcher tier: 1000 credits/month; basic "
            "search 1 credit, advanced 2, extract 1 per 5 pages",
            retention_policy="derived_claims_and_source_metadata_only",
            attribution_required=False,
            purpose=[
                "news_discovery",
                "official_statement_discovery",
                "press_conference_discovery",
                "expected_lineup_research",
                "market_move_investigation",
            ],
            notes="Retrieval layer for the knowledge agent (Increment 4). "
            "Search and Extract endpoints ONLY — Tavily's Answer/Research "
            "layer is deliberately unused, because feeding an "
            "AI-generated research answer into another model turns "
            "provenance into interpretive dance. Tavily is the "
            "*acquisition* source; the authority behind any football "
            "claim is the ORIGIN domain, stored separately. Retention is "
            "derived claims plus source metadata and a minimal audit "
            "snippet — never full copies of articles.",
        ),
        SourceRecord(
            source_id="football_data_org",
            source_name="football-data.org",
            acquisition_method=AcquisitionMethod.API.value,
            usage_status=UsageStatus.REVIEW_REQUIRED.value,
            terms_url="https://www.football-data.org/terms",
            terms_checked_at="2026-08-07",
            refresh_limit="free tier: 10 requests/minute",
            retention_policy="persist fixtures/results/status",
            purpose=["fixtures", "results", "match_status", "void_detection"],
            notes="Scope established by live probing 2026-08-08. HAS "
            "current-season fixtures (380 PL + Championship for 2026/27), "
            "results, match status, referees and venue. LACKS lineups "
            "(team objects carry crest/id/name/shortName/tla only) and "
            "injuries, so it does NOT replace API-Football for "
            "availability. Odds are a paid add-on (~EUR15/month) that "
            "would breach the GBP10 target (§25) and are not used. Its "
            "real value is the fixture/result/status feed the system "
            "otherwise lacks entirely: §2.2 VOID handling had no source, "
            "and ledger scoring was manual JSON entry.",
        ),
        SourceRecord(
            source_id="big_balls_sports_data",
            source_name="Big Balls Sports Data",
            acquisition_method=AcquisitionMethod.API.value,
            usage_status=UsageStatus.EVALUATED.value,
            terms_url="https://bigballsdata.com/docs",
            terms_checked_at="",  # capability probed, terms NOT yet reviewed
            refresh_limit="free tier: 1000 requests/day (2000 with a GitHub account)",
            retention_policy="none — nothing queries this source",
            purpose=[],
            notes="Assessed 2026-08-10 as a candidate xG source (§7) and "
            "not adopted. NOTHING QUERIES THIS SOURCE: there is no adapter, "
            "purpose is empty, and no validation or cross-checking runs "
            "against it. A key is held in secrets/ only so the option stays "
            "open; this entry exists so its presence is explained rather "
            "than rediscovered. Probed with a live key: /v1/leagues returns 8 "
            "competitions, England = EPL only, so NO Championship (59% of "
            "the corpus). Its advertised match-level xG does not appear in "
            "any payload — 0 of 30 finished EPL matches had any team_stats, "
            "and the one match in 40 that did carried 28 fields (shots, "
            "shots on target, possession, passing, cards) with no xG in "
            "either the stats or the match object. Lineups return "
            "available=false. Fixture horizon ended 2026-05-24, so it has "
            "no 2026/27 season yet. Terms have NOT been reviewed: do that "
            "first if this is ever promoted. Genuine but modest value as a "
            "second opinion on results/scores alongside football_data_org.",
        ),
        SourceRecord(
            source_id="sky_super6",
            source_name="Sky Sports Super 6",
            acquisition_method=AcquisitionMethod.MANUAL.value,
            usage_status=UsageStatus.PROHIBITED.value,
            terms_url="https://www.skysports.com/terms-and-conditions",
            terms_checked_at="2026-08-07",
            purpose=[],
            notes="PERMANENTLY PROHIBITED as an automated source. Sky's "
            "terms prohibit automated and unauthorised manual data "
            "extraction. The agent is external to Sky by architecture "
            "(§3, §18.1): round fixtures and deadlines are entered by "
            "the user by hand (§9.1). Cannot be promoted.",
        ),
    ]


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> dict[str, SourceRecord]:
    """Load the registry, merging in any seeded sources the stored file
    does not yet know about.

    The merge matters: a source added in a later release would otherwise
    be invisible to an existing installation, and `assert_approved` would
    reject it as unregistered with no way to fix that but hand-editing
    JSON. New entries arrive at their seeded status (REVIEW_REQUIRED for
    anything needing an account), and **stored statuses always win** — a
    release must never silently re-approve, or un-approve, a source the
    user has already decided about.
    """
    seeded = {r.source_id: r for r in _seed()}
    if not path.exists():
        return seeded
    stored = {
        r["source_id"]: SourceRecord(**r) for r in json.loads(path.read_text())
    }
    for source_id, record in seeded.items():
        stored.setdefault(source_id, record)
    return stored


def save_registry(
    registry: dict[str, SourceRecord], path: Path = DEFAULT_REGISTRY_PATH
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = [asdict(registry[k]) for k in sorted(registry)]
    path.write_text(json.dumps(ordered, indent=2) + "\n")


def assert_approved(
    source_id: str, path: Path = DEFAULT_REGISTRY_PATH
) -> SourceRecord:
    """Gate every outbound query on registry status (§18.1, §22).

    Raises SourceNotApprovedError unless the source is registered as
    APPROVED. An unregistered source is treated as not approved — the
    default is refusal, not permission.
    """
    registry = load_registry(path)
    record = registry.get(source_id)
    if record is None:
        raise SourceNotApprovedError(
            f"Source '{source_id}' is not in the registry. Register it with "
            "its terms and usage_status before any code queries it (§18.1)."
        )
    if record.usage_status == UsageStatus.PROHIBITED.value:
        raise SourceNotApprovedError(
            f"Source '{source_id}' is PROHIBITED and must never be queried. "
            f"{record.notes}"
        )
    if record.usage_status != UsageStatus.APPROVED.value:
        raise SourceNotApprovedError(
            f"Source '{source_id}' is {record.usage_status}, not APPROVED. "
            f"Review its terms ({record.terms_url}) and promote it with "
            f"`bym sources approve {source_id}` before querying it."
        )
    return record


def approve(
    source_id: str, checked_at: str | None = None, path: Path = DEFAULT_REGISTRY_PATH
) -> SourceRecord:
    """Promote a source to APPROVED. Refuses PROHIBITED sources outright."""
    registry = load_registry(path)
    record = registry.get(source_id)
    if record is None:
        raise KeyError(f"Unknown source '{source_id}'")
    if record.usage_status == UsageStatus.PROHIBITED.value:
        raise SourceNotApprovedError(
            f"'{source_id}' is PROHIBITED by architecture and cannot be "
            f"promoted. {record.notes}"
        )
    record.usage_status = UsageStatus.APPROVED.value
    record.terms_checked_at = checked_at or date.today().isoformat()
    registry[source_id] = record
    save_registry(registry, path)
    return record
