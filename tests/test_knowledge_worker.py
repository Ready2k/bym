"""Increment 4A/4D — retrieval, provenance, pipeline handoff, triggers."""

from datetime import datetime, timezone

import pytest

from bym import db, events as ev
from bym.availability import store as avail_store
from bym.knowledge import queries, store, worker
from bym.knowledge.models import (
    CandidateClaim,
    ClaimType,
    EvidenceDocument,
    classify_authority,
)
from bym.sources import registry as reg
from bym.sources import tavily

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)

ARTICLE = (
    "Chelsea head coach spoke on Friday. Cole Palmer will be assessed "
    "tomorrow after a knock sustained in training this week."
)


class FakeExtractor:
    """Deterministic stand-in — tests must not require an API bill to
    discover pytest."""

    def __init__(self, claims=None):
        self.claims = claims
        self.calls = 0

    def extract_claims(self, evidence: EvidenceDocument):
        self.calls += 1
        if self.claims is not None:
            return list(self.claims)
        return [
            CandidateClaim(
                claim_type=ClaimType.AVAILABILITY.value,
                player_name="Cole Palmer",
                team="Chelsea",
                availability="DOUBT",
                certainty="REPORTED",
                supporting_evidence="Cole Palmer will be assessed tomorrow",
            )
        ]


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "s.db") as c:
        yield c


@pytest.fixture
def approved(tmp_path):
    path = tmp_path / "registry.json"
    reg.save_registry(reg.load_registry(path), path)
    reg.approve("tavily", path=path)
    return path


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Stub Tavily search/extract with one BBC article."""
    hit = tavily.SearchHit(
        url="https://bbc.co.uk/sport/football/1",
        title="Chelsea team news",
        snippet="Palmer assessed",
        published_at="2026-08-15T11:00:00+00:00",
    )
    page = tavily.ExtractedPage(
        url="https://bbc.co.uk/sport/football/1",
        content=ARTICLE,
        title="Chelsea team news",
        published_at="2026-08-15T11:00:00+00:00",
    )
    monkeypatch.setattr(tavily, "search", lambda q, k, **kw: [hit])
    monkeypatch.setattr(tavily, "extract", lambda urls, k, **kw: [page])
    monkeypatch.setattr(worker, "constrained", lambda *a, **k: False)
    return hit, page


PLANS = [queries.PlannedQuery(query="Chelsea team news", priority="TRUSTED")]


# ---- registry gating ----------------------------------------------------


def test_tavily_registered_as_review_required(tmp_path):
    path = tmp_path / "registry.json"
    reg.save_registry(reg.load_registry(path), path)
    assert reg.load_registry(path)["tavily"].usage_status == "REVIEW_REQUIRED"
    with pytest.raises(reg.SourceNotApprovedError):
        tavily.search("anything", "key", registry_path=path)


def test_extract_also_gated(tmp_path):
    path = tmp_path / "registry.json"
    reg.save_registry(reg.load_registry(path), path)
    with pytest.raises(reg.SourceNotApprovedError):
        tavily.extract(["https://example.com"], "key", registry_path=path)


# ---- two source identities ---------------------------------------------


def test_acquisition_and_origin_stored_separately(conn, wired):
    extractor = FakeExtractor()
    worker.research(conn, "Chelsea", "Newcastle", PLANS, "key", extractor, now=NOW)
    row = conn.execute("SELECT * FROM evidence_documents").fetchone()
    assert row["acquisition_source"] == "tavily"
    assert row["origin_domain"] == "bbc.co.uk"
    assert row["origin_authority"] == "TRUSTED"


def test_authority_derives_from_origin_not_retriever():
    assert classify_authority("https://chelseafc.com/news/1").value == "OFFICIAL"
    assert classify_authority("https://bbc.co.uk/sport/1").value == "TRUSTED"
    assert classify_authority("https://x.com/someone/status/1").value == "SOCIAL"
    # A respectable search API finding nonsense does not make it credible.
    assert classify_authority("https://random-transfer-rumours.biz/x").value == (
        "OTHER_MEDIA"
    )


def test_reliability_passed_to_pipeline_comes_from_origin(conn, wired):
    worker.research(
        conn, "Chelsea", "Newcastle", PLANS, "key", FakeExtractor(), now=NOW,
        mode="active",
    )
    events = avail_store.events_for_fixture(conn, "Chelsea", "Newcastle")
    assert events, "claim should have reached the availability pipeline"
    assert events[0].source_id == "bbc.co.uk"   # not "tavily"
    assert events[0].source_reliability == "HIGH"


# ---- pipeline handoff and the model boundary ---------------------------


def test_accepted_claim_enters_availability_pipeline_in_active_mode(conn, wired):
    result = worker.research(
        conn, "Chelsea", "Newcastle", PLANS, "key", FakeExtractor(), now=NOW,
        mode="active",
    )
    assert result.claims_accepted == 1
    assert result.claims_promoted == 1
    events = avail_store.events_for_fixture(conn, "Chelsea", "Newcastle")
    assert events[0].event_type == "DOUBT"
    assert events[0].claim_status == "REPORTED"


def test_shadow_is_the_default_mode(conn, wired):
    """Turning the LLM on must be deliberate twice over: an extractor has
    to be configured, and then promoted out of shadow."""
    result = worker.research(
        conn, "Chelsea", "Newcastle", PLANS, "key", FakeExtractor(), now=NOW
    )
    assert result.mode == "shadow"
    assert result.claims_accepted == 1     # validated...
    assert result.claims_promoted == 0     # ...but not promoted
    assert avail_store.events_for_fixture(conn, "Chelsea", "Newcastle") == []
    assert any("NOT promoted" in n for n in result.notes)


def test_shadow_claims_are_recorded_but_flagged_unpromoted(conn, wired):
    worker.research(conn, "Chelsea", "Newcastle", PLANS, "key", FakeExtractor(), now=NOW)
    row = conn.execute(
        "SELECT * FROM candidate_claims WHERE verdict = 'ACCEPTED'"
    ).fetchone()
    assert row is not None            # the claim IS stored for inspection
    assert row["promoted"] == 0       # but explicitly not promoted


def test_no_claim_can_reach_the_model(conn, wired):
    """The knowledge worker must not import or touch the forecast layer."""
    import inspect

    source = inspect.getsource(worker)
    for token in ("lambda_home", "score_matrix", "recommend_fixture",
                  "market_blend_weight", "p_score"):
        assert token not in source


def test_events_declare_no_quantitative_impact(conn, wired):
    result = worker.research(
        conn, "Chelsea", "Newcastle", PLANS, "key", FakeExtractor(), now=NOW
    )
    discovered = [e for e in result.events if e.event_type == "CLAIM_DISCOVERED"]
    assert discovered
    assert all(e.detail["quantitative_impact"] == "NONE DIRECTLY" for e in discovered)


# ---- three clocks -------------------------------------------------------


def test_three_clocks_remain_distinct(conn, wired):
    extractor = FakeExtractor(
        [
            CandidateClaim(
                claim_type=ClaimType.AVAILABILITY.value,
                player_name="Cole Palmer",
                team="Chelsea",
                availability="DOUBT",
                certainty="REPORTED",
                event_at="2026-08-13T09:00:00+00:00",   # Wednesday presser
                supporting_evidence="Cole Palmer will be assessed tomorrow",
            )
        ]
    )
    worker.research(conn, "Chelsea", "Newcastle", PLANS, "key", extractor, now=NOW)
    row = conn.execute("SELECT * FROM candidate_claims").fetchone()
    evidence = conn.execute("SELECT * FROM evidence_documents").fetchone()
    assert row["event_at"] == "2026-08-13T09:00:00+00:00"        # when it happened
    assert evidence["published_at"] == "2026-08-15T11:00:00+00:00"  # when printed
    assert row["observed_at"] == "2026-08-15T12:00:00+00:00"      # when we saw it
    assert len({row["event_at"], evidence["published_at"], row["observed_at"]}) == 3


# ---- retention ----------------------------------------------------------


def test_only_metadata_and_snippet_persisted(conn, wired):
    worker.research(conn, "Chelsea", "Newcastle", PLANS, "key", FakeExtractor(), now=NOW)
    columns = {
        r[1] for r in conn.execute("PRAGMA table_info(evidence_documents)")
    }
    assert "content" not in columns  # no full article archive
    row = conn.execute("SELECT * FROM evidence_documents").fetchone()
    assert len(row["snippet"] or "") <= store.MAX_SNIPPET_CHARS


# ---- cost control -------------------------------------------------------


def test_unchanged_content_causes_no_repeat_llm_work(conn, wired):
    extractor = FakeExtractor()
    worker.research(conn, "Chelsea", "Newcastle", PLANS, "key", extractor, now=NOW)
    assert extractor.calls == 1
    second = worker.research(
        conn, "Chelsea", "Newcastle", PLANS, "key", extractor, now=NOW
    )
    assert extractor.calls == 1          # not called again
    assert second.documents_skipped == 1
    assert second.llm_calls == 0


def test_low_budget_skips_routine_research(conn, monkeypatch, wired):
    monkeypatch.setattr(worker, "constrained", lambda *a, **k: True)
    extractor = FakeExtractor()
    result = worker.research(
        conn, "Chelsea", "Newcastle", PLANS, "key", extractor, now=NOW
    )
    assert result.searches == 0
    assert extractor.calls == 0
    assert any("budget low" in n for n in result.notes)


def test_credit_costs():
    assert tavily.search_cost("basic") == 1
    assert tavily.search_cost("advanced") == 2
    assert tavily.extract_cost(5) == 1
    assert tavily.extract_cost(6) == 2
    assert tavily.extract_cost(0) == 0


def test_extract_url_limit(approved):
    with pytest.raises(ValueError, match="at most 20"):
        tavily.extract(
            [f"https://e.com/{i}" for i in range(21)], "key",
            registry_path=approved,
        )


# ---- query planning -----------------------------------------------------


def test_queries_are_bounded_templates_not_freeform():
    plans = queries.plan_team_queries("Chelsea", official_domain="chelseafc.com")
    assert plans[0].priority == "OFFICIAL"
    assert plans[0].include_domains == ("chelseafc.com",)
    assert all("Chelsea" in p.query for p in plans)


def test_investigation_uses_a_narrow_time_window():
    """A move twenty minutes ago is not explained by last Tuesday."""
    plans = queries.plan_investigation("Chelsea")
    assert all(p.days == 1 for p in plans)
    assert all(p.reason == "market-movement investigation" for p in plans)


# ---- market-triggered investigation ------------------------------------


def test_movement_investigation_emits_plausible_not_causal(conn, wired):
    result = worker.investigate_movement(
        conn, "Chelsea", "Newcastle",
        "Newcastle win 22.1% -> 28.7% (+6.6pp)", "key", FakeExtractor(), now=NOW,
    )
    explain = next(
        e for e in result.events
        if e.event_type == "INTELLIGENCE_EXPLAINS_MARKET_MOVE"
    )
    assert explain.detail["relationship"] == "PLAUSIBLE"
    assert "causation cannot be established" in explain.summary
    assert "caused" not in explain.summary.replace("causation", "")


def test_no_results_produces_no_claims(conn, monkeypatch):
    monkeypatch.setattr(tavily, "search", lambda q, k, **kw: [])
    monkeypatch.setattr(worker, "constrained", lambda *a, **k: False)
    extractor = FakeExtractor()
    result = worker.research(
        conn, "Chelsea", "Newcastle", PLANS, "key", extractor, now=NOW
    )
    assert result.claims_accepted == 0
    assert extractor.calls == 0
    assert "no results" in result.notes


def test_rejected_claims_are_recorded_with_reasons(conn, wired):
    extractor = FakeExtractor(
        [
            CandidateClaim(
                claim_type=ClaimType.AVAILABILITY.value,
                player_name="Cole Palmer",
                availability="CONFIRMED_ABSENT",
                certainty="CONFIRMED",
                supporting_evidence="Palmer has been ruled out for the season",
            )
        ]
    )
    result = worker.research(
        conn, "Chelsea", "Newcastle", PLANS, "key", extractor, now=NOW
    )
    assert result.claims_rejected == 1
    row = conn.execute(
        "SELECT * FROM candidate_claims WHERE verdict = 'REJECTED'"
    ).fetchone()
    assert "does not appear in the source" in row["rejection_reason"]
    # and nothing reached the availability pipeline
    assert avail_store.events_for_fixture(conn, "Chelsea", "Newcastle") == []


def test_default_extractor_emits_nothing(conn, wired):
    from bym.knowledge.extractor import NullExtractor

    result = worker.research(
        conn, "Chelsea", "Newcastle", PLANS, "key", NullExtractor(), now=NOW
    )
    assert result.claims_accepted == 0
