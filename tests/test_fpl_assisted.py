from __future__ import annotations

import json

import pytest

from bym.fpl import assisted


def test_assisted_actions_require_exact_confirmation_and_retain_no_session_data(tmp_path):
    path = tmp_path / "fpl_assisted_actions.json"
    action = assisted.prepare(
        path, action_type="transfer", summary="Wilson → M. Sangaré", deadline="2026-09-04T18:30"
    )

    assert action.status == assisted.PREPARED
    stored = json.loads(path.read_text())
    assert set(stored[0]) == {"id", "action_type", "summary", "deadline", "status", "created_at", "confirmed_at", "completed_at"}
    with pytest.raises(assisted.AssistedActionError, match="type exactly"):
        assisted.confirm_for_browser(path, action_id=action.id, confirmation="yes")

    confirmed = assisted.confirm_for_browser(
        path, action_id=action.id, confirmation=assisted.CONFIRMATION_PHRASE
    )
    assert confirmed.status == assisted.CONFIRMED_FOR_BROWSER
    completed = assisted.complete_by_manager(path, action_id=action.id)
    assert completed.status == assisted.COMPLETED_BY_MANAGER


def test_assisted_actions_cannot_be_marked_complete_before_browser_confirmation(tmp_path):
    path = tmp_path / "fpl_assisted_actions.json"
    action = assisted.prepare(path, action_type="CAPTAIN", summary="Captain Haaland", deadline=None)

    with pytest.raises(assisted.AssistedActionError, match="browser-confirmed"):
        assisted.complete_by_manager(path, action_id=action.id)
