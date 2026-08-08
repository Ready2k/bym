"""End-to-end engine run and verification-harness guards."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from super6 import db, engine, verify
from super6.knowledge.config import ExtractorConfig
from super6.model import dixon_coles
from super6.round import Fixture, Round
from super6.sources import registry as reg

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


@pytest.fixture(scope="module")
def router():
    params = dixon_coles.fit(synthetic_matches(), xi=0.0)
    return lambda home, away: params


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "s.db") as c:
        yield c


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "registry.json"
    reg.save_registry(reg.load_registry(path), path)
    return path


# ---- engine run ---------------------------------------------------------


def test_engine_runs_without_any_external_credentials(conn, router):
    """The engine must produce a forecast with no live sources at all."""
    result = engine.run_engine(conn, ROUND, router, now=NOW)
    assert len(result.round_summary.fixtures) == 6
    assert result.round_summary.expected_round_score > 0
    assert result.knowledge_layer["enabled"] is False


def test_engine_reports_every_fixture_modellable_when_no_corpus_is_supplied(conn, router):
    """Silence about the corpus must not be read as a warning."""
    result = engine.run_engine(conn, ROUND, router, now=NOW)
    assert all(f.modellability.modellable for f in result.round_summary.fixtures)


def test_engine_flags_fixtures_whose_teams_are_outside_the_corpus(conn, router):
    result = engine.run_engine(
        conn, ROUND, router, now=NOW,
        known_teams={"Strong", "Weak"}, corpus="E0",
    )
    verdicts = {
        (f.home_team, f.away_team): f.modellability
        for f in result.round_summary.fixtures
    }
    assert verdicts[("Strong", "Weak")].modellable is True
    unknown = verdicts[("Middling A", "Middling B")]
    assert unknown.modellable is False
    assert unknown.unknown_teams == ["Middling A", "Middling B"]
    assert unknown.corpus == "E0"

    report = engine.format_engine_report(result)
    assert "NOT A TRUSTWORTHY FORECAST" in report
    assert "Middling A v Middling B" in report


def test_engine_does_not_leak_known_teams_into_the_knowledge_worker(
    conn, router, monkeypatch
):
    """`known_teams` is a forecast concern. `run_engine` forwards its spare
    kwargs to the research pass as HTTP request options, so a corpus set
    arriving there would end up on an outbound request."""
    from super6.knowledge import worker as knowledge_worker

    seen = []

    def fake_research(*args, **kwargs):
        seen.append(kwargs)
        return knowledge_worker.ResearchResult()

    monkeypatch.setattr(knowledge_worker, "research", fake_research)
    engine.run_engine(
        conn, ROUND, router, now=NOW,
        tavily_api_key="key", extractor=object(), run_knowledge=True,
        known_teams={"Strong"}, corpus="E0", timeout=5,
    )
    assert seen, "the research pass did not run"
    for kwargs in seen:
        assert "known_teams" not in kwargs
        assert "corpus" not in kwargs
        assert kwargs["timeout"] == 5


def test_engine_report_separates_four_layers(conn, router):
    report = engine.format_engine_report(
        engine.run_engine(conn, ROUND, router, now=NOW)
    )
    for heading in [
        "=== FORECAST INPUT ===",
        "=== MARKET INTELLIGENCE ===",
        "=== STRUCTURED TEAM INTELLIGENCE ===",
        "=== KNOWLEDGE INTELLIGENCE ===",
    ]:
        assert heading in report


def test_engine_report_states_no_quantitative_impact(conn, router):
    report = engine.format_engine_report(
        engine.run_engine(conn, ROUND, router, now=NOW)
    )
    assert "quantitative impact: NONE DIRECTLY" in report
    assert "PHASE1_MODEL_V1's frozen w=0.5" in report


def test_availability_layer_declares_no_impact(conn, router):
    result = engine.run_engine(conn, ROUND, router, now=NOW)
    assert result.availability_layer["quantitative_impact"] == "NONE DIRECTLY"


def test_knowledge_defaults_to_shadow(conn, router):
    result = engine.run_engine(
        conn, ROUND, router, now=NOW,
        extractor_config=ExtractorConfig(kind="ollama"),
    )
    assert result.knowledge_layer["mode"] == "shadow"
    assert result.knowledge_layer["claims_promoted"] == 0


def test_engine_degrades_when_market_acquisition_fails(conn, router, monkeypatch):
    from super6.sources import pipeline

    def boom(*a, **k):
        raise RuntimeError("odds API down")

    monkeypatch.setattr(pipeline, "acquire_round_odds", boom)
    result = engine.run_engine(conn, ROUND, router, odds_api_key="key", now=NOW)
    assert len(result.round_summary.fixtures) == 6   # forecast still produced
    assert any("unavailable" in n for n in result.notes)


def test_engine_uses_the_frozen_config(conn, router):
    """The engine must not drift from PHASE1_MODEL_V1."""
    from super6.config import FROZEN_PHASE1_V1

    assert FROZEN_PHASE1_V1.xi == 0.003
    assert FROZEN_PHASE1_V1.market_blend_weight == 0.5
    result = engine.run_engine(conn, ROUND, router, now=NOW)
    assert result.round_summary.model_version == "PHASE1_MODEL_V1"


# ---- verification harness ----------------------------------------------


def test_verification_cannot_bypass_approval(registry):
    """A source that is not APPROVED is reported blocked, not queried."""
    for verifier, key in [
        (verify.verify_odds_api, "ODDS_API_KEY"),
        (verify.verify_api_football, "API_FOOTBALL_KEY"),
        (verify.verify_tavily, "TAVILY_API_KEY"),
    ]:
        result = (
            verifier("fake-key", registry_path=registry)
            if verifier is not verify.verify_api_football
            else verifier("fake-key", 2026, registry)
        )
        assert result.status == verify.BLOCKED
        assert any(c.status == verify.BLOCKED for c in result.checks)


def test_verification_does_not_approve_anything(registry):
    verify.verify_odds_api("fake-key", registry_path=registry)
    assert reg.load_registry(registry)["the_odds_api"].usage_status == (
        "REVIEW_REQUIRED"
    )


def test_sky_remains_permanently_prohibited(registry):
    assert reg.load_registry(registry)["sky_super6"].usage_status == "PROHIBITED"
    with pytest.raises(reg.SourceNotApprovedError, match="cannot be promoted"):
        reg.approve("sky_super6", path=registry)
    with pytest.raises(reg.SourceNotApprovedError):
        reg.assert_approved("sky_super6", registry)


def test_missing_credentials_are_skipped_not_failed(registry):
    reg.approve("the_odds_api", path=registry)
    result = verify.verify_odds_api(None, registry_path=registry)
    assert result.status != verify.FAILED
    assert any(c.status == verify.SKIPPED for c in result.checks)


def test_verification_reports_are_readable(registry):
    report = verify.verify_odds_api("k", registry_path=registry).report()
    assert "the_odds_api" in report
    assert "BLOCK" in report


def test_engine_populates_market_divergence_from_the_unblended_model(conn, router):
    """§33's divergence must compare the model's own view with the market.

    The recommendation's matrix has already had the market folded into it
    at w=0.5, so measuring against that would partly compare the market
    with itself and understate the disagreement.
    """
    from super6.round import Fixture, Round

    priced = Round(
        round_number=14,
        deadline_at=ROUND.deadline_at,
        fixtures=tuple(
            Fixture(f.home, f.away, odds=(2.0, 3.4, 3.6)) for f in ROUND.fixtures
        ),
    )
    result = engine.run_engine(conn, priced, router, now=NOW)
    states = [f.market for f in result.round_summary.fixtures]
    assert all(s is not None and s.available for s in states)
    assert all(s.model_vs_market is not None for s in states)
    assert all(s.market_divergence in {"LOW", "MEDIUM", "HIGH"} for s in states)

    # Same comparison against the blended matrix would be a smaller number.
    from super6 import divergence as divergence_mod

    fixture = result.recommendation.fixtures[0]
    blended = divergence_mod.assess(fixture.matrix, *_implied(), 0.0, 1)
    raw = divergence_mod.assess(fixture.model_only_matrix, *_implied(), 0.0, 1)
    assert raw.model_vs_market > blended.model_vs_market


def _implied():
    from super6.model.market import implied_result_probs

    return implied_result_probs(2.0, 3.4, 3.6)


def test_engine_market_state_degrades_when_no_snapshot_history_exists(conn, router, tmp_path):
    """A missing movement reference is a missing reference, not a failure."""
    result = engine.run_engine(
        conn, ROUND, router, now=NOW,
        consensus_snapshot_path=tmp_path / "does_not_exist.csv",
    )
    assert len(result.round_summary.fixtures) == 6
    for fixture in result.round_summary.fixtures:
        # No odds on ROUND, so no consensus and therefore no movement.
        assert fixture.market is not None
        assert fixture.market.last_movement is None


def test_engine_reports_market_movement_against_the_previous_poll(conn, router, tmp_path):
    """movement.py and divergence.py were fully built and tested but never
    reached the engine, so six MarketState fields were permanently empty in
    the read model. This pins the wiring, not the movement maths."""
    import pandas as pd

    from super6.round import Fixture, Round

    home, away = "Strong", "Weak"
    snapshot = tmp_path / "consensus.csv"
    # An earlier poll that had the home side much shorter than they are now.
    pd.DataFrame([{
        "calculated_at": "2026-08-15T09:00:00+00:00",
        "event_id": "e1", "home_team": home, "away_team": away,
        "bookmaker_count": 6, "consensus_h": 0.70, "consensus_d": 0.18,
        "consensus_a": 0.12, "dispersion": 0.01,
        "stale_excluded": 0, "oldest_quote": None,
    }]).to_csv(snapshot, index=False)

    priced = Round(
        round_number=14,
        deadline_at=ROUND.deadline_at,
        fixtures=(Fixture(home, away, odds=(2.5, 3.3, 2.9)),),
    )
    result = engine.run_engine(
        conn, priced, router, now=NOW, consensus_snapshot_path=snapshot,
    )
    market = result.round_summary.fixtures[0].market
    assert market.available
    assert market.last_movement is not None, "movement never reached the read model"
    assert market.materiality in {"NOISE", "MATERIAL", "MAJOR"}
    assert market.market_agreement in {"HIGH", "MEDIUM", "LOW"}
    assert market.movement_strength_pp is not None and market.movement_strength_pp > 0

    report = engine.format_engine_report(result)
    assert "moved:" in report
    assert "model vs market" in report
