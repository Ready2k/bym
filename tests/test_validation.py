from pathlib import Path

import pytest

from bym.validation import (
    HoldoutAlreadySpentError,
    assert_holdout_unspent,
    final_test_result_path,
)


def test_final_test_result_path_matches_the_recorded_filename():
    """results/final_test_2025_26.md is the real, already-written record —
    the filename convention this function must reproduce exactly, or the
    spent-holdout check below silently fails to see it."""
    assert final_test_result_path(2025, Path("results")).name == "final_test_2025_26.md"


def test_assert_holdout_unspent_passes_when_no_record_exists(tmp_path):
    assert_holdout_unspent(2030, tmp_path)  # does not raise


def test_assert_holdout_unspent_refuses_when_a_record_already_exists(tmp_path):
    """§ the exact shape of the 2026-08-08 breach: a spent holdout, re-run.
    No acknowledgement flag or confirmation phrase may talk this past an
    existing record."""
    (tmp_path / "final_test_2025_26.md").write_text("spent")
    with pytest.raises(HoldoutAlreadySpentError, match="already exists"):
        assert_holdout_unspent(2025, tmp_path)
