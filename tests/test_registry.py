import pytest

from bym.sources import registry as reg


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "registry.json"
    reg.save_registry(reg.load_registry(p), p)
    return p


def test_seeded_statuses(path):
    r = reg.load_registry(path)
    assert r["football_data_co_uk"].usage_status == "APPROVED"
    assert r["the_odds_api"].usage_status == "REVIEW_REQUIRED"
    assert r["sky_super6"].usage_status == "PROHIBITED"


def test_review_required_is_refused(path):
    """§22: no worker may query a source that is not APPROVED."""
    with pytest.raises(reg.SourceNotApprovedError, match="REVIEW_REQUIRED"):
        reg.assert_approved("the_odds_api", path)


def test_prohibited_is_refused(path):
    with pytest.raises(reg.SourceNotApprovedError, match="PROHIBITED"):
        reg.assert_approved("sky_super6", path)


def test_unregistered_source_refused(path):
    """Default is refusal, not permission."""
    with pytest.raises(reg.SourceNotApprovedError, match="not in the registry"):
        reg.assert_approved("some_scraper_someone_found", path)


def test_approved_source_passes(path):
    record = reg.assert_approved("football_data_co_uk", path)
    assert record.source_id == "football_data_co_uk"


def test_approve_promotes_and_persists(path):
    reg.approve("the_odds_api", checked_at="2026-08-07", path=path)
    assert reg.assert_approved("the_odds_api", path).usage_status == "APPROVED"
    # survives a reload
    assert reg.load_registry(path)["the_odds_api"].terms_checked_at == "2026-08-07"


def test_new_seeded_source_appears_in_an_existing_registry(tmp_path):
    """A source added in a later release must become visible without
    hand-editing JSON, or assert_approved rejects it as unregistered
    forever."""
    import json

    p = tmp_path / "registry.json"
    partial = [
        r for r in json.loads(json.dumps(
            [reg.asdict(v) for v in reg.load_registry(p).values()]
        )) if r["source_id"] != "api_football"
    ]
    p.write_text(json.dumps(partial))
    assert "api_football" in reg.load_registry(p)
    assert reg.load_registry(p)["api_football"].usage_status == "REVIEW_REQUIRED"


def test_merge_never_overrides_a_stored_decision(tmp_path):
    """Approving a source must survive a release that reseeds it."""
    p = tmp_path / "registry.json"
    reg.save_registry(reg.load_registry(p), p)
    reg.approve("the_odds_api", path=p)
    assert reg.load_registry(p)["the_odds_api"].usage_status == "APPROVED"


def test_prohibited_cannot_be_promoted(path):
    """Sky's exclusion is architectural (§3) — not a toggle."""
    with pytest.raises(reg.SourceNotApprovedError, match="cannot be promoted"):
        reg.approve("sky_super6", path=path)
    assert reg.load_registry(path)["sky_super6"].usage_status == "PROHIBITED"


def test_an_evaluated_but_unadopted_source_is_registered_and_still_gated(tmp_path):
    """A key sits in secrets/ for Big Balls Sports Data as a cross-reference.
    The registry entry exists so its presence is explained rather than
    rediscovered — but §18.1 must still refuse to let anything query it."""
    from bym.sources.registry import (
        SourceNotApprovedError, UsageStatus, assert_approved, load_registry,
    )

    registry = load_registry(tmp_path / "absent.json")  # seeded defaults
    record = registry["big_balls_sports_data"]
    # EVALUATED, not REVIEW_REQUIRED: the latter means "awaiting your terms
    # review" — a task — and raised a System Health warning demanding action
    # nobody intended to take. This is a record, and nothing is pending.
    assert record.usage_status == UsageStatus.EVALUATED.value
    assert record.usage_status != UsageStatus.REVIEW_REQUIRED.value
    assert record.purpose == [], "nothing consumes it, so it claims no purpose"
    assert record.terms_checked_at == "", "terms were never reviewed — do not imply they were"

    with pytest.raises(SourceNotApprovedError):
        assert_approved("big_balls_sports_data", tmp_path / "absent.json")


def test_the_evaluation_findings_survive_in_the_registry(tmp_path):
    """The point of the entry is that nobody probes this source a fourth
    time, so the findings have to be in it, not only in a results doc."""
    from bym.sources.registry import load_registry

    notes = load_registry(tmp_path / "absent.json")["big_balls_sports_data"].notes
    for fact in ("Championship", "xG", "2026-08-10"):
        assert fact in notes, fact


# --- SPEC_FPL.md §5.3/§15 research and benchmark sources -----------------


def test_fpl_research_sources_are_seeded_review_required_not_approved(path):
    """§5.3: vaastav and Understat are candidates needing their own terms
    review — dataset convenience never promotes a source to APPROVED."""
    r = reg.load_registry(path)
    assert r["vaastav_fpl_historical"].usage_status == "REVIEW_REQUIRED"
    assert r["understat"].usage_status == "REVIEW_REQUIRED"


def test_fpl_evaluated_sources_carry_their_declared_usage_scope(path):
    """§5.3/§15: fpl_core_insights is a DefCon feasibility study only;
    openfpl is a benchmark, never a training label."""
    r = reg.load_registry(path)
    assert r["fpl_core_insights"].usage_status == "EVALUATED"
    assert r["fpl_core_insights"].usage_scope == "RESEARCH_ONLY"
    assert r["openfpl"].usage_status == "EVALUATED"
    assert r["openfpl"].usage_scope == "BENCHMARK_ONLY"


def test_vaastav_xp_leakage_caveat_is_recorded_on_the_source(path):
    """§5.3/§12.2/§19: the xP/ep_this leakage rule must be visible on the
    SourceRecord itself, not only in the spec, so it survives a review that
    never re-reads SPEC_FPL.md."""
    notes = reg.load_registry(path)["vaastav_fpl_historical"].notes
    assert "xP" in notes
    assert "ep_this" in notes
    assert "same-Gameweek" in notes


@pytest.mark.parametrize(
    "source_id",
    ["vaastav_fpl_historical", "understat", "fpl_core_insights", "openfpl"],
)
def test_no_fpl_research_or_benchmark_source_can_be_queried_in_production(
    source_id, path
):
    """§19: EVALUATED / RESEARCH_ONLY / BENCHMARK_ONLY evidence cannot
    silently become a production dependency — assert_approved must still
    refuse every one of them, same gate as any other unapproved source."""
    with pytest.raises(reg.SourceNotApprovedError):
        reg.assert_approved(source_id, path)


@pytest.mark.parametrize(
    "source_id", ["vaastav_fpl_historical", "understat", "fpl_core_insights", "openfpl"]
)
def test_fpl_research_source_terms_checked_at_is_todays_phase0_review_date(
    source_id, path
):
    """These were seeded during the 2026-08-17 Phase 0 review, but the
    notes must say this reflects reading documentation, not a completed
    legal terms-of-service review (§5.3, §13.2)."""
    record = reg.load_registry(path)[source_id]
    assert record.terms_checked_at == "2026-08-17"
    assert "legal" in record.notes.lower()
