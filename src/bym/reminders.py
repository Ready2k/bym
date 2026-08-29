"""Deadline reminders (SPEC.md §9, §54).

The single largest scoring risk in Super 6 is not a poor forecast, it is a
round entered late or not at all: a missed deadline scores zero, which no
amount of model accuracy recovers. The `deadline_reminder` preference has
existed since the first UI state, with nothing behind it.

Design constraints this module answers:

* **Reminders stop once the picks are in.** They exist to prompt an
  action, so they are silent from the moment the user marks the round as
  entered — nagging someone who already acted trains them to ignore it.
* **Each threshold fires once.** The activity log is append-only and the
  UI reads it on every load, so without a per-threshold guard a reminder
  would be re-emitted on every page view.
* **Nothing is inferred about delivery.** This produces events; whether
  they reach an inbox is the notification layer's decision, filtered by
  the user's own settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from . import events as ev

# Chosen to match how people actually behave rather than to be evenly
# spaced: a day ahead is "plan it", six hours is "do it today", one hour
# is "do it now". A fourth reminder in between adds noise, not urgency.
THRESHOLDS: tuple[timedelta, ...] = (
    timedelta(hours=24),
    timedelta(hours=6),
    timedelta(hours=1),
)

# States meaning the user has already acted; nothing left to remind about.
SETTLED_STATES = frozenset({"MARKED_AS_SUBMITTED", "ROUND_CLOSED"})


@dataclass(frozen=True)
class Reminder:
    threshold_hours: int
    remaining: timedelta

    @property
    def label(self) -> str:
        hours = self.threshold_hours
        return "1 hour" if hours == 1 else f"{hours} hours"


def _already_sent(existing: list[ev.Event], round_number: int) -> set[int]:
    sent = set()
    for event in existing:
        if event.event_type != ev.EventType.DEADLINE_APPROACHING.value:
            continue
        if event.round_number != round_number:
            continue
        hours = (event.detail or {}).get("threshold_hours")
        if isinstance(hours, int):
            sent.add(hours)
    return sent


def due_reminders(
    deadline: datetime,
    now: datetime,
    submission_state: str,
    existing: list[ev.Event],
    round_number: int,
) -> list[Reminder]:
    """Thresholds crossed but not yet announced for this round.

    Only the most urgent crossed threshold is returned. If a user opens
    the app two hours before a deadline having never looked, they need to
    be told they have two hours — not handed the 24-hour and 6-hour
    notices they slept through as well.
    """
    if submission_state in SETTLED_STATES:
        return []
    remaining = deadline - now
    if remaining <= timedelta(0):
        return []
    crossed = [t for t in THRESHOLDS if remaining <= t]
    if not crossed:
        return []
    tightest = int(min(crossed).total_seconds() // 3600)

    # Announcing a tighter threshold retires every looser one: having been
    # told "6 hours left", being told "24 hours left" an hour later would
    # be both redundant and wrong. So the guard is the most urgent notice
    # already sent, not whether this exact threshold was.
    sent = _already_sent(existing, round_number)
    if sent and min(sent) <= tightest:
        return []
    return [Reminder(threshold_hours=tightest, remaining=remaining)]


def describe(reminder: Reminder, submission_state: str) -> str:
    hours, minutes = divmod(int(reminder.remaining.total_seconds() // 60), 60)
    left = f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
    acted = submission_state == "CHANGED_AFTER_SUBMISSION"
    return (
        f"Under {reminder.label} to the Super 6 deadline — {left} left. "
        + (
            "Your recorded picks no longer match the recommendation; review before it locks."
            if acted
            else "Your picks are not yet marked as entered. This app cannot enter them for you."
        )
    )


def build_events(
    deadline: datetime,
    now: datetime,
    submission_state: str,
    existing: list[ev.Event],
    round_number: int,
) -> list[ev.Event]:
    """Reminder events for any threshold newly crossed."""
    return [
        ev.make_event(
            ev.EventType.DEADLINE_APPROACHING,
            # Escalates: a day out is a note, an hour out is the last call.
            ev.Severity.CRITICAL if reminder.threshold_hours <= 1 else ev.Severity.IMPORTANT,
            describe(reminder, submission_state),
            round_number=round_number,
            detail={
                "threshold_hours": reminder.threshold_hours,
                "minutes_remaining": int(reminder.remaining.total_seconds() // 60),
                "submission_state": submission_state,
            },
            now=now,
        )
        for reminder in due_reminders(
            deadline, now, submission_state, existing, round_number
        )
    ]


def final_summary_due(
    deadline: datetime,
    now: datetime,
    submission_state: str,
    existing: list[ev.Event],
    round_number: int,
) -> bool:
    """Whether it is time to email the full six-pick recommendation.

    Fires once, at the same tightest threshold (currently 1 hour) that
    already means "do it now" for a plain reminder — by then lineups have
    usually landed, so the picks are as final as they will get. Reuses
    `THRESHOLDS[-1]` rather than a separate constant so the two notions of
    "last call" cannot drift apart. Silent once the round is settled: a
    submitted user does not need to be handed picks again.
    """
    if submission_state in SETTLED_STATES:
        return False
    remaining = deadline - now
    if not (timedelta(0) < remaining <= THRESHOLDS[-1]):
        return False
    for event in existing:
        if (
            event.event_type == ev.EventType.FINAL_SCORES_SENT.value
            and event.round_number == round_number
        ):
            return False
    return True
