"""Deadline-causality rules for FPL (docs/SPEC_FPL.md §12.1-§12.2, §19).

Phase 0 requires these as code, not prose, because Phase 1B's walk-forward
and deadline-causal tests check candidate implementations against them: a
player-stat update, a confirmed lineup or a historical dataset's
forward-looking field is either knowable before a given Gameweek's
decision deadline or it isn't, and getting that wrong silently invalidates
a backtest or a live recommendation the same way an unresolved club name
did for LMS (see round.py's resolve_round_teams).

Three separate rules live here, all "future information must not leak
backward":

    1. any observation strictly after a deadline is post-deadline evidence
       for that decision, never an input to it (§11.1, §19);
    2. a historical dataset's forward-looking fields (vaastav's `xP`,
       `ep_this`, `ep_next`) are lookahead risks unless their own
       observation timestamp proves they predate the Gameweek they'd be
       used for (§12.2);
    3. a permanent evaluation ledger entry may only be written once a
       Gameweek has reached FINAL status — PROVISIONAL results can still
       move during Opta's post-match BPS/DefCon review window (§4, §11.3,
       §12.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Fields carried by some historical FPL datasets (vaastav) that are
# forward-looking predictions computed by the FPL site itself, not
# observed match outcomes — see the vaastav_fpl_historical dataset
# manifest's known_leakage_caveats.
LOOKAHEAD_RISK_FIELDS = frozenset({"xP", "ep_this", "ep_next"})

# fpl_gameweek_state.status values, in their one-directional order (§4,
# §11.3). Only the terminal value permits a permanent evaluation write.
GAMEWEEK_STATUSES = ("UPCOMING", "LOCKED", "IN_PROGRESS", "PROVISIONAL", "FINAL")


class LookaheadViolation(RuntimeError):
    """Raised when post-deadline information would leak into a decision or
    its evaluation for the Gameweek it was made for."""


class GameweekNotFinalError(RuntimeError):
    """Raised when a permanent evaluation ledger write is attempted before
    a Gameweek has reached FINAL status."""


def is_causal(observed_at: datetime, deadline: datetime) -> bool:
    """True if `observed_at` was knowable evidence for a decision at `deadline`.

    Strictly before, not at-or-before: §2 treats information first
    published at the deadline instant itself as future information for
    that Gameweek, so an observation timestamped exactly at the deadline
    does not count as causal for it.
    """
    return observed_at < deadline


def assert_causal(observed_at: datetime, deadline: datetime, context: str) -> None:
    """Gate a single piece of evidence (a player-stat update, a confirmed
    lineup, a price snapshot) on §11.1/§19's deadline-causality rule."""
    if not is_causal(observed_at, deadline):
        raise LookaheadViolation(
            f"{context}: observed_at={observed_at.isoformat()} is not "
            f"before deadline={deadline.isoformat()} — this is "
            "post-deadline evidence for that Gameweek and cannot affect "
            "its recommendation or a walk-forward evaluation of it "
            "(§11.1, §19)."
        )


def assert_no_unproven_lookahead_field(
    field_name: str, observed_at: datetime | None, deadline: datetime
) -> None:
    """Refuse a historical dataset's forward-looking field (xP/ep_this/
    ep_next) as a same-Gameweek feature unless its own observation
    timestamp proves it predates that Gameweek's deadline (§12.2, §19).

    `observed_at=None` means the dataset carries no timestamp for this
    field — the ordinary case for a community CSV snapshot — and is
    refused outright rather than assumed safe, since an absent timestamp
    is not evidence the value was available in time.
    """
    if field_name not in LOOKAHEAD_RISK_FIELDS:
        return
    if observed_at is None:
        raise LookaheadViolation(
            f"'{field_name}' has no observation timestamp; it is a "
            "forward-looking field in historical FPL datasets and is "
            "prohibited as a same-Gameweek feature without proof it "
            "predates that Gameweek's deadline (§12.2). Drop it, or lag "
            "it to a prior Gameweek where it is genuinely historical."
        )
    assert_causal(observed_at, deadline, context=f"lookahead field '{field_name}'")


@dataclass(frozen=True)
class GameweekSettlement:
    """Mirrors fpl_gameweek_state.status (§4, §11.3) for callers that only
    need the one field this module gates on."""

    status: str


def assert_final(settlement: GameweekSettlement, context: str) -> None:
    """Gate a permanent evaluation ledger write on §4/§11.3/§12.1's rule.

    A PROVISIONAL Gameweek may be shown in the UI but is not ground truth:
    actual points, captain outcome, transfer gain, DefCon and bonus can
    all still move during Opta's post-match review window, right up to
    the official finalisation instant.
    """
    if settlement.status != "FINAL":
        raise GameweekNotFinalError(
            f"{context}: Gameweek status is {settlement.status}, not "
            "FINAL. A PROVISIONAL result is display-only; permanent "
            "evaluation ledger entries may only be written once official "
            "finalisation has passed (§4, §11.3, §12.1)."
        )
