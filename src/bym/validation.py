"""Validation-protocol enforcement (docs/VALIDATION.md).

A holdout season has always been single-use by discipline — a printed
warning and a CLI flag you had to remember to pass — never by a
technical check. That gap is not hypothetical: on 2026-08-08 the 2025/26
holdout, already spent the day before, was re-run and grid-searched
anyway. `--i-understand-this-is-single-use` did not stop it, because it
is only a typed acknowledgement, not a check against anything.

`results/final_test_<season>_<season+1>.md` is the record that a season's
holdout has been evaluated — written by hand after the one legitimate
run, and per VALIDATION.md's "Frozen historical results", never
overwritten. Its presence *is* the fact that matters here, so
`assert_holdout_unspent` checks for it directly rather than trying to
parse the prose in VALIDATION.md. There is deliberately no override
parameter: if a result genuinely needs to be looked at again, delete or
rename that file first — a visible, deliberate act, not a flag left
unread.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_RESULTS_DIR = Path("results")


class HoldoutAlreadySpentError(RuntimeError):
    """Raised when a season's final-test result is already on record."""


def final_test_result_path(
    test_season: int, results_dir: Path = DEFAULT_RESULTS_DIR
) -> Path:
    return results_dir / f"final_test_{test_season}_{(test_season + 1) % 100:02d}.md"


def assert_holdout_unspent(
    test_season: int, results_dir: Path = DEFAULT_RESULTS_DIR
) -> None:
    """Refuse a season whose final-test result already exists.

    Raises unconditionally — there is no flag, phrase or caller that can
    talk this past an existing record. Read the existing file instead of
    regenerating it.
    """
    path = final_test_result_path(test_season, results_dir)
    if path.exists():
        raise HoldoutAlreadySpentError(
            f"{path} already exists — this holdout is spent (VALIDATION.md). "
            "Re-running it and comparing again is the exact protocol breach "
            "recorded there on 2026-08-08. Read the existing result instead "
            "of regenerating it; a new holdout requires a new season."
        )
