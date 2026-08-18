"""FPL research dataset manifest (SPEC_FPL.md §15, §12.2, §19).

Every static/community dataset used for FPL model selection or benchmarking
must have a frozen manifest entry recording what it is, what season range it
covers, and any known timing/leakage caveats — §12.2: "the source review
must happen before using a dataset to select a model component." A dataset
that later fails source-policy review cannot be rescued by having already
produced an attractive ablation result, which is why this manifest exists
independently of whatever ad-hoc notebook first touches the data.

This module only *registers intent and caveats* for Phase 0. None of these
datasets have actually been retrieved yet, so `retrieved_at`,
`version_or_commit` and `file_hashes` are honestly empty here — Phase 1A's
actual retrieval step is what fills them in, never this seed.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_MANIFEST_PATH = Path("config/fpl_dataset_manifest.json")

# Vaastav's historical `xP`/`ep_this` field is scraped after the Gameweek and
# may contain post-match information (§5.3, §12.2, §19). Kept as a named
# constant so the manifest seed and any future ingestion/regression test
# quote the identical rule text rather than two slightly different
# paraphrases drifting apart.
VAASTAV_XP_LEAKAGE_CAVEAT = (
    "historical `xP`/`ep_this` is forbidden as a same-Gameweek model "
    "feature unless an observation timestamp proves it was captured before "
    "that historical FPL deadline; otherwise drop it or use only a "
    "pre-registered lag such as the previous Gameweek"
)


@dataclass
class DatasetManifest:
    dataset_id: str
    source_id: str
    usage_scope: str  # DEVELOPMENT | RESEARCH_ONLY | BENCHMARK_ONLY
    season_range: str
    retrieved_at: str = ""  # empty until an actual retrieval has happened
    upstream_description: str = ""
    version_or_commit: str = ""  # empty until retrieval pins a real commit/version
    file_hashes: dict[str, str] = field(default_factory=dict)
    known_leakage_caveats: list[str] = field(default_factory=list)
    terms_checked_at: str = ""
    notes: str = ""


def _seed() -> list[DatasetManifest]:
    """Manifest entries known at FPL Phase 0 (§18) — one per §5.3 source.

    None of these datasets have been downloaded. This freezes the *intent*
    and *caveats* a future retrieval must respect; `version_or_commit` and
    `file_hashes` are filled in only when Phase 1A actually pulls the data,
    never fabricated ahead of that.
    """
    return [
        DatasetManifest(
            dataset_id="vaastav_fpl_historical_v1",
            source_id="vaastav_fpl_historical",
            usage_scope="RESEARCH_ONLY",
            season_range="not yet retrieved",
            retrieved_at="",
            upstream_description="vaastav/Fantasy-Premier-League: "
            "Gameweek-level FPL labels/features across seasons — minutes, "
            "starts, goals, assists, FPL points, bonus/BPS, prices, "
            "ownership and xG/xA; newer seasons add tackles, recoveries "
            "and combined clearances/blocks/interceptions where present "
            "(§5.3).",
            version_or_commit="",  # pinned only once Phase 1A retrieves it
            file_hashes={},
            known_leakage_caveats=[VAASTAV_XP_LEAKAGE_CAVEAT],
            terms_checked_at="2026-08-17",
            notes="Registered at Phase 0 to record intent and the "
            "leakage caveat ahead of any retrieval (§18). "
            "terms_checked_at reflects reading published documentation, "
            "not a live legal terms-of-service review — see the "
            "corresponding SourceRecord's notes.",
        ),
        DatasetManifest(
            dataset_id="understat_v1",
            source_id="understat",
            usage_scope="RESEARCH_ONLY",
            season_range="not yet retrieved",
            retrieved_at="",
            upstream_description="Understat match-level attacking-player "
            "features: xG, xA, shots, key passes, non-penalty xG and "
            "involvement/build-up measures (§5.3). Does not cover DefCon.",
            version_or_commit="",
            file_hashes={},
            known_leakage_caveats=[],
            terms_checked_at="2026-08-17",
            notes="Registered at Phase 0 to record intent ahead of any "
            "retrieval (§18). terms_checked_at reflects reading "
            "documentation/site conventions, not a live legal review.",
        ),
        DatasetManifest(
            dataset_id="fpl_core_insights_v1",
            source_id="fpl_core_insights",
            usage_scope="RESEARCH_ONLY",
            season_range="not yet retrieved",
            retrieved_at="",
            upstream_description="olbauday/FPL-Core-Insights per-match "
            "tables including tackles, interceptions, recoveries, blocks "
            "and clearances aligned to FPL identities — feasibility "
            "evidence for CBIT/CBIRT and 2026/27 DefCon only (§5.3, §5.4).",
            version_or_commit="",
            file_hashes={},
            known_leakage_caveats=[
                "match-data provenance is aggregated from multiple free "
                "sources rather than one clearly licensed authoritative "
                "feed; usable only for a DefCon feasibility study, not as "
                "a production dependency, until provenance and use rights "
                "are independently cleared (§5.3)",
            ],
            terms_checked_at="2026-08-17",
            notes="Registered at Phase 0 to record intent and scope ahead "
            "of any retrieval (§18). terms_checked_at reflects reading "
            "repository documentation, not a live legal review.",
        ),
        DatasetManifest(
            dataset_id="openfpl_v1",
            source_id="openfpl",
            usage_scope="BENCHMARK_ONLY",
            season_range="not yet retrieved",
            retrieved_at="",
            upstream_description="daniegr/OpenFPL benchmark implementation "
            "(Groos, arXiv:2508.09992): position-specific ensemble models "
            "trained on FPL + Understat data 2020/21-2023/24, with "
            "prospective evaluation reported on 2024/25 (§5.3, §12.2).",
            version_or_commit="",
            file_hashes={},
            known_leakage_caveats=[
                "independent benchmark implementation, not a data source; "
                "never a training label and its published performance is "
                "not evidence that bym itself is calibrated (§12.2)",
            ],
            terms_checked_at="2026-08-17",
            notes="Registered at Phase 0 to record intent and scope ahead "
            "of any retrieval (§18). terms_checked_at reflects reading the "
            "repository/paper, not a live legal review.",
        ),
    ]


def load_manifests(path: Path = DEFAULT_MANIFEST_PATH) -> dict[str, DatasetManifest]:
    """Load the manifest, merging in any seeded entries the stored file
    does not yet know about.

    Mirrors `registry.load_registry`'s merge-safety property: a dataset
    added in a later release becomes visible without hand-editing JSON, and
    **stored entries always win** — a release must never silently overwrite
    a manifest entry that has since been filled in with a real
    `retrieved_at`/`version_or_commit`/`file_hashes` from an actual
    retrieval.
    """
    seeded = {m.dataset_id: m for m in _seed()}
    if not path.exists():
        return seeded
    stored = {
        m["dataset_id"]: DatasetManifest(**m) for m in json.loads(path.read_text())
    }
    for dataset_id, manifest in seeded.items():
        stored.setdefault(dataset_id, manifest)
    return stored


def save_manifests(
    manifests: dict[str, DatasetManifest], path: Path = DEFAULT_MANIFEST_PATH
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = [asdict(manifests[k]) for k in sorted(manifests)]
    path.write_text(json.dumps(ordered, indent=2) + "\n")
