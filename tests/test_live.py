"""Live integration tests — skipped unless BYM_LIVE_TESTS=1.

An ordinary `pytest` run must make no network calls whatsoever. These
exist so live verification is repeatable, not so it happens by accident.
"""

import os

import pytest

LIVE = os.environ.get("BYM_LIVE_TESTS") == "1"

pytestmark = pytest.mark.skipif(
    not LIVE, reason="live tests require BYM_LIVE_TESTS=1"
)


def test_ollama_reachable():
    from bym.knowledge.config import from_env
    from bym.knowledge.ollama import check_health

    config = from_env()
    health = check_health(config)
    assert health.reachable, health.detail


def test_ollama_model_installed():
    from bym.knowledge.config import from_env
    from bym.knowledge.ollama import check_health

    config = from_env()
    health = check_health(config)
    assert health.model_available, health.detail


def test_ollama_structured_extraction():
    from datetime import datetime, timezone

    from bym.knowledge.config import ExtractorConfig, from_env
    from bym.knowledge.models import EvidenceDocument, content_hash
    from bym.knowledge.ollama import OllamaClaimExtractor

    config = from_env()
    if config.kind != "ollama":
        pytest.skip("CLAIM_EXTRACTOR is not 'ollama'")

    text = (
        "Chelsea head coach confirmed on Friday that Cole Palmer will be "
        "assessed tomorrow after a knock in training."
    )
    evidence = EvidenceDocument(
        url="https://example.test/live",
        title="Live extraction test",
        acquisition_source="test",
        origin_domain="example.test",
        origin_authority="OTHER_MEDIA",
        retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        content_hash=content_hash(text),
        content=text,
    )
    extractor = OllamaClaimExtractor(config)
    claims = extractor.extract_claims(evidence)
    assert extractor.last_failure is None, extractor.last_failure
    # The model may legitimately find nothing; what must hold is that
    # anything it does emit already passed schema validation.
    for claim in claims:
        assert claim.quantitative_impact == "NONE DIRECTLY"


@pytest.mark.parametrize(
    "source_id,env_var",
    [
        ("the_odds_api", "ODDS_API_KEY"),
        ("api_football", "API_FOOTBALL_KEY"),
        ("tavily", "TAVILY_API_KEY"),
    ],
)
def test_source_live_verification(source_id, env_var):
    from bym import verify
    from bym.sources.registry import load_registry

    if load_registry().get(source_id, None) is None:
        pytest.skip(f"{source_id} not registered")
    if load_registry()[source_id].usage_status != "APPROVED":
        pytest.skip(f"{source_id} is not APPROVED — approve it first")
    if not os.environ.get(env_var):
        pytest.skip(f"{env_var} not set")

    results = {r.source_id: r for r in verify.verify_all()}
    assert results[source_id].status != verify.FAILED, results[source_id].report()
