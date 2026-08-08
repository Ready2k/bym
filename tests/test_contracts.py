"""API read-model contracts — frontend-independent, no DB rows leaked."""

import json
from datetime import datetime, timezone

import pytest

from super6 import db
from super6.api import contracts, service
from super6.model import dixon_coles
from super6.recommend import recommend_round
from super6.round import Fixture, Round
from super6.sources.consensus import Consensus

from test_dixon_coles import synthetic_matches

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)

ROUND = Round(
    round_number=14,
    deadline_at=datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc),
    fixtures=(
        Fixture("Strong", "Weak"),
        Fixture("Weak", "Strong"),
        Fixture("Middling A", "Middling B"),
        Fixture("Middling B", "Middling A"),
        Fixture("Strong", "Middling A"),
        Fixture("Weak", "Middling B"),
    ),
)

# Database-internal names that must never surface in a contract.
DB_ONLY_FIELDS = {
    "id", "evidence_id", "extraction_run_id", "dedupe_key", "rowid",
    "supersedes_event_id", "retracted_by_id", "content_hash",
    "canonical", "external_id", "next_run_at", "attempt_count",
}


@pytest.fixture(scope="module")
def recommendation():
    params = dixon_coles.fit(synthetic_matches(), xi=0.0)
    return recommend_round(lambda h, a: params, ROUND)


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "s.db") as c:
        yield c


def all_keys(payload) -> set[str]:
    keys = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            keys.add(key)
            keys |= all_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            keys |= all_keys(item)
    return keys


def test_round_summary_contract(recommendation):
    summary = service.build_round_summary(ROUND, recommendation)
    assert summary.round_number == 14
    assert len(summary.fixtures) == 6
    assert summary.model_version == "PHASE1_MODEL_V1"
    assert summary.golden_goal is not None
    assert summary.schema_version == contracts.SCHEMA_VERSION


def test_contracts_are_json_serialisable(recommendation):
    payload = service.build_round_summary(ROUND, recommendation).to_dict()
    json.dumps(payload)  # must not raise


def test_no_database_fields_leak(recommendation, conn):
    market = service.build_market_state(None)
    availability = service.build_availability_state(conn, "Strong", "Weak", False)
    summary = service.build_round_summary(
        ROUND, recommendation,
        {("Strong", "Weak"): market},
        {("Strong", "Weak"): availability},
    )
    leaked = all_keys(summary.to_dict()) & DB_ONLY_FIELDS
    assert not leaked, f"database-internal fields leaked into the API: {leaked}"


def test_probability_and_confidence_stay_separate(recommendation):
    fixture = service.build_round_summary(ROUND, recommendation).fixtures[0]
    assert isinstance(fixture.result_probability, float)
    assert isinstance(fixture.score_probability, float)
    assert fixture.confidence in {"HIGH", "MEDIUM", "LOW"}
    # CONF is never a number
    with pytest.raises(ValueError):
        float(fixture.confidence)


def test_alternatives_marked_with_the_recommendation(recommendation):
    fixture = service.build_round_summary(ROUND, recommendation).fixtures[0]
    chosen = [a for a in fixture.alternatives if a.is_recommendation]
    assert len(chosen) == 1
    assert chosen[0].score == fixture.pick


def test_market_state_absent_is_explicit():
    market = service.build_market_state(None, reason_unavailable="all quotes stale")
    assert market.available is False
    assert market.reason_unavailable == "all quotes stale"
    assert market.home_probability is None   # never a fabricated default


def test_market_state_from_consensus():
    consensus = Consensus(
        "Strong", "Weak", "2026-08-15T12:00:00+00:00", 9,
        0.6, 0.25, 0.15, 0.02, 0, None,
    )
    market = service.build_market_state(consensus)
    assert market.available
    assert market.bookmaker_count == 9
    assert market.home_probability == 0.6


def test_availability_state_declares_no_quantitative_impact(conn):
    state = service.build_availability_state(conn, "Strong", "Weak", True)
    assert state.quantitative_impact == "NONE DIRECTLY"
    assert state.feature_completeness in {"HIGH", "MEDIUM", "LOW"}


def test_knowledge_events_carry_both_source_identities():
    from super6 import events as ev

    event = ev.make_event(
        ev.EventType.CLAIM_DISCOVERED,
        ev.Severity.INFORMATIONAL,
        "Palmer DOUBT",
        home_team="Chelsea", away_team="Newcastle",
        detail={
            "acquisition_source": "tavily",
            "origin_domain": "bbc.co.uk",
            "origin_authority": "TRUSTED",
            "published_at": "2026-08-15T11:00:00+00:00",
            "event_at": "2026-08-13T09:00:00+00:00",
        },
    )
    built = service.build_knowledge_events([event], mode="shadow")
    assert len(built) == 1
    assert built[0].acquisition_source == "tavily"
    assert built[0].origin_domain == "bbc.co.uk"
    assert built[0].promoted is False
    assert built[0].quantitative_impact == "NONE DIRECTLY"


def test_system_health_contract(conn, tmp_path):
    """Built from an isolated registry: coupling this to the real one
    made the test depend on which sources happen to be approved today."""
    from super6.sources.registry import load_registry, save_registry

    registry_path = tmp_path / "registry.json"
    save_registry(load_registry(registry_path), registry_path)
    registry = load_registry(registry_path)

    health = service.build_system_health(
        conn, registry=registry, schema_version=6
    )
    assert health.database_schema_version == 6
    assert {s.source_id for s in health.sources} >= {
        "the_odds_api", "api_football", "tavily"
    }
    # seeded sources start REVIEW_REQUIRED, so the warning path fires
    assert any("REVIEW_REQUIRED" in w for w in health.warnings)


def test_system_health_has_no_warnings_when_all_approved(conn, tmp_path):
    from super6.sources.registry import SourceRecord

    approved = {
        "the_odds_api": SourceRecord(
            "the_odds_api", "The Odds API", "API", "APPROVED"
        )
    }
    health = service.build_system_health(conn, registry=approved)
    assert health.warnings == []


def test_prospective_contract_carries_sample_warning():
    import pandas as pd

    frame = pd.DataFrame(
        [
            {"arm": "PHASE1_MODEL_V1", "fixtures": 6, "rounds": 1,
             "points": 8, "pts/round": 8.0, "exact": 1, "exact%": 16.67}
        ]
    )
    performance = service.build_prospective_performance(frame)
    assert performance.arms[0].arm == "PHASE1_MODEL_V1"
    assert "thin sample" in performance.sample_warning


def test_contracts_are_immutable(recommendation):
    """Read models must not be mutated by whatever consumes them."""
    summary = service.build_round_summary(ROUND, recommendation)
    with pytest.raises(Exception):
        summary.round_number = 99
