import pytest

from bym.sources import dataset_manifest as dm


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "fpl_dataset_manifest.json"
    dm.save_manifests(dm.load_manifests(p), p)
    return p


def test_seeded_manifest_entries_exist_for_each_fpl_5_3_source(path):
    """§15: every static/community dataset used in model selection must
    have a frozen manifest entry — one per §5.3 source."""
    manifests = dm.load_manifests(path)
    assert set(manifests) == {
        "vaastav_fpl_historical_v1",
        "understat_v1",
        "fpl_core_insights_v1",
        "openfpl_v1",
    }


def test_seeded_entries_carry_the_correct_usage_scope(path):
    """§15's usage_scope enum is DEVELOPMENT | RESEARCH_ONLY |
    BENCHMARK_ONLY; §5.3 assigns vaastav/understat/fpl_core_insights to
    RESEARCH_ONLY and openfpl to BENCHMARK_ONLY."""
    manifests = dm.load_manifests(path)
    assert manifests["vaastav_fpl_historical_v1"].usage_scope == "RESEARCH_ONLY"
    assert manifests["understat_v1"].usage_scope == "RESEARCH_ONLY"
    assert manifests["fpl_core_insights_v1"].usage_scope == "RESEARCH_ONLY"
    assert manifests["openfpl_v1"].usage_scope == "BENCHMARK_ONLY"


def test_seeded_entries_have_no_fabricated_retrieval_evidence(path):
    """Phase 0 registers intent and caveats, not ingested data — no
    dataset has actually been downloaded yet, so retrieved_at,
    version_or_commit and file_hashes must stay honestly empty rather than
    claim a retrieval that never happened."""
    for manifest in dm.load_manifests(path).values():
        assert manifest.retrieved_at == ""
        assert manifest.version_or_commit == ""
        assert manifest.file_hashes == {}


def test_vaastav_leakage_caveat_matches_the_5_3_and_12_2_rule_text(path):
    """§5.3/§12.2/§19: the xP/ep_this leakage rule must be present
    verbatim-ish on the manifest entry, not paraphrased into something
    weaker by a future editor."""
    caveats = dm.load_manifests(path)["vaastav_fpl_historical_v1"].known_leakage_caveats
    assert any("xP" in c and "ep_this" in c for c in caveats)
    assert any("same-Gameweek" in c for c in caveats)
    assert any("forbidden" in c for c in caveats)


def test_dataset_ids_reference_a_seeded_source_registry_entry(path):
    """A manifest entry whose source_id isn't registered would be
    unreconstructable provenance (§15) — every dataset here must point at
    one of the four §5.3 SourceRecords."""
    from bym.sources.registry import load_registry

    registry = load_registry(path.parent / "source_registry_unused.json")
    for manifest in dm.load_manifests(path).values():
        assert manifest.source_id in registry


def test_merge_never_overrides_a_stored_retrieval(tmp_path):
    """Mirrors registry.py's merge-safety property: once Phase 1A actually
    retrieves a dataset and fills in version_or_commit/file_hashes, a
    reseed on the next release must not wipe that out."""
    p = tmp_path / "fpl_dataset_manifest.json"
    manifests = dm.load_manifests(p)
    manifests["vaastav_fpl_historical_v1"].retrieved_at = "2026-09-01"
    manifests["vaastav_fpl_historical_v1"].version_or_commit = "abc123"
    manifests["vaastav_fpl_historical_v1"].file_hashes = {"players_raw.csv": "sha256:deadbeef"}
    dm.save_manifests(manifests, p)

    reloaded = dm.load_manifests(p)
    assert reloaded["vaastav_fpl_historical_v1"].retrieved_at == "2026-09-01"
    assert reloaded["vaastav_fpl_historical_v1"].version_or_commit == "abc123"
    assert reloaded["vaastav_fpl_historical_v1"].file_hashes == {
        "players_raw.csv": "sha256:deadbeef"
    }


def test_new_seeded_dataset_appears_in_an_existing_manifest_file(tmp_path):
    """A dataset added in a later release must become visible without
    hand-editing JSON, matching registry.py's same guarantee."""
    import json

    p = tmp_path / "fpl_dataset_manifest.json"
    partial = [
        m
        for m in json.loads(
            json.dumps([dm.asdict(v) for v in dm.load_manifests(p).values()])
        )
        if m["dataset_id"] != "openfpl_v1"
    ]
    p.write_text(json.dumps(partial))
    assert "openfpl_v1" in dm.load_manifests(p)
    assert dm.load_manifests(p)["openfpl_v1"].usage_scope == "BENCHMARK_ONLY"
