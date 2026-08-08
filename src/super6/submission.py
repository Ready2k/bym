"""Submission state (SPEC.md §46).

The system cannot know that predictions were entered into Sky — it is
external to Sky by architecture (§3) — so submission state is something
the user tells it, and the system's job is to notice when its own
recommendation has since moved away from what was entered.

```text
NOT_READY                 forecasts still settling
READY                     stable enough to enter
CHANGED_SINCE_REVIEW      moved since the user last looked
MARKED_AS_SUBMITTED       user confirmed they entered these picks
CHANGED_AFTER_SUBMISSION  critical: entered picks no longer recommended
ROUND_CLOSED              deadline passed
```

`CHANGED_AFTER_SUBMISSION` is the state the whole mechanism exists for.
Nothing here ever submits anything, and no wording implies it did.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from .db import from_iso, to_iso, utcnow

DEFAULT_STATE_PATH = Path("data/submissions.json")


class SubmissionState(str, Enum):
    NOT_READY = "NOT_READY"
    READY = "READY"
    CHANGED_SINCE_REVIEW = "CHANGED_SINCE_REVIEW"
    MARKED_AS_SUBMITTED = "MARKED_AS_SUBMITTED"
    CHANGED_AFTER_SUBMISSION = "CHANGED_AFTER_SUBMISSION"
    ROUND_CLOSED = "ROUND_CLOSED"


@dataclass
class SubmissionRecord:
    season: int
    round_number: int
    state: str = SubmissionState.NOT_READY.value
    submitted_picks: list[str] = field(default_factory=list)
    submitted_golden_goal: int | None = None
    submitted_at: str | None = None
    reviewed_picks: list[str] = field(default_factory=list)
    reviewed_at: str | None = None
    drift: list[dict] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.season}:{self.round_number}"


def load_states(path: Path = DEFAULT_STATE_PATH) -> dict[str, SubmissionRecord]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {k: SubmissionRecord(**v) for k, v in raw.items()}


def save_states(
    states: dict[str, SubmissionRecord], path: Path = DEFAULT_STATE_PATH
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: vars(v) for k, v in states.items()}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def get(
    season: int, round_number: int, path: Path = DEFAULT_STATE_PATH
) -> SubmissionRecord:
    states = load_states(path)
    return states.get(
        f"{season}:{round_number}", SubmissionRecord(season, round_number)
    )


def mark_reviewed(
    season: int,
    round_number: int,
    picks: list[str],
    now: datetime | None = None,
    path: Path = DEFAULT_STATE_PATH,
) -> SubmissionRecord:
    """Record what the user last saw, so later drift is detectable."""
    now = now or utcnow()
    states = load_states(path)
    record = states.get(
        f"{season}:{round_number}", SubmissionRecord(season, round_number)
    )
    record.reviewed_picks = list(picks)
    record.reviewed_at = to_iso(now)
    if record.state in {
        SubmissionState.NOT_READY.value,
        SubmissionState.CHANGED_SINCE_REVIEW.value,
    }:
        record.state = SubmissionState.READY.value
    states[record.key] = record
    save_states(states, path)
    return record


def mark_submitted(
    season: int,
    round_number: int,
    picks: list[str],
    golden_goal: int | None = None,
    now: datetime | None = None,
    path: Path = DEFAULT_STATE_PATH,
) -> SubmissionRecord:
    """Record that the user manually entered these picks into Super 6."""
    now = now or utcnow()
    states = load_states(path)
    record = states.get(
        f"{season}:{round_number}", SubmissionRecord(season, round_number)
    )
    record.submitted_picks = list(picks)
    record.submitted_golden_goal = golden_goal
    record.submitted_at = to_iso(now)
    record.state = SubmissionState.MARKED_AS_SUBMITTED.value
    record.drift = []
    states[record.key] = record
    save_states(states, path)
    return record


def reconcile(
    season: int,
    round_number: int,
    current_picks: list[str],
    deadline: datetime,
    now: datetime | None = None,
    path: Path = DEFAULT_STATE_PATH,
) -> SubmissionRecord:
    """Compare the current recommendation against what was reviewed or
    submitted, and move the state accordingly."""
    now = now or utcnow()
    states = load_states(path)
    record = states.get(
        f"{season}:{round_number}", SubmissionRecord(season, round_number)
    )

    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=now.tzinfo)
    if now >= deadline:
        record.state = SubmissionState.ROUND_CLOSED.value
        states[record.key] = record
        save_states(states, path)
        return record

    if record.submitted_picks:
        drift = _diff(record.submitted_picks, current_picks)
        record.drift = drift
        record.state = (
            SubmissionState.CHANGED_AFTER_SUBMISSION.value
            if drift
            else SubmissionState.MARKED_AS_SUBMITTED.value
        )
    elif record.reviewed_picks:
        drift = _diff(record.reviewed_picks, current_picks)
        record.drift = drift
        record.state = (
            SubmissionState.CHANGED_SINCE_REVIEW.value
            if drift
            else SubmissionState.READY.value
        )
    states[record.key] = record
    save_states(states, path)
    return record


def _diff(before: list[str], after: list[str]) -> list[dict]:
    return [
        {"index": i, "was": b, "now": a}
        for i, (b, a) in enumerate(zip(before, after))
        if b != a
    ]


def describe(record: SubmissionRecord) -> str:
    """User-facing wording. Never implies the system submitted anything."""
    if record.state == SubmissionState.CHANGED_AFTER_SUBMISSION.value:
        lines = [
            "PREDICTIONS CHANGED AFTER SUBMISSION",
            f"You marked your Super 6 picks as entered at {record.submitted_at}.",
            "",
        ]
        for change in record.drift:
            lines.append(
                f"  Fixture {change['index'] + 1}: entered {change['was']}, "
                f"now recommended {change['now']}"
            )
        lines.append("")
        lines.append("Remember to update these predictions in Super 6 yourself.")
        return "\n".join(lines)
    if record.state == SubmissionState.CHANGED_SINCE_REVIEW.value:
        return (
            f"{len(record.drift)} recommendation(s) changed since you last "
            "reviewed this round."
        )
    if record.state == SubmissionState.MARKED_AS_SUBMITTED.value:
        return "Your entered picks still match the current recommendation."
    if record.state == SubmissionState.ROUND_CLOSED.value:
        return "The deadline has passed; this round is closed."
    if record.state == SubmissionState.READY.value:
        return "Recommendations are stable and ready to enter in Super 6."
    return "Forecasts are still settling."
