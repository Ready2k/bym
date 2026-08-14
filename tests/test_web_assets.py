"""Run the browser-UI tests as part of the normal suite.

The UI carries real decision logic — which forecast basis a fixture has,
whether monitoring may be claimed, whether hostile text can escape its
slot — and until now none of it was covered. A separate `node --test`
command would be forgotten, so it hangs off `pytest` like everything else.

No dependency is added: Node ships its own test runner, and the UI keeps
its no-build-chain property.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
import re

import pytest

from bym.data.football_data import load_matches
from bym.sources.teams import ALIASES

ROOT = Path(__file__).resolve().parent.parent
JS_TESTS = ROOT / "tests" / "js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_web_asset_javascript_suite():
    result = subprocess.run(
        ["node", "--test", "*.test.mjs"],
        cwd=JS_TESTS, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        pytest.fail(
            "JavaScript UI tests failed:\n"
            + result.stdout[-4000:]
            + ("\n--- stderr ---\n" + result.stderr[-2000:] if result.stderr else "")
        )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_app_js_parses():
    """A syntax error in app.js is a blank page, and nothing else catches it."""
    result = subprocess.run(
        ["node", "--check", str(ROOT / "src" / "bym" / "web_assets" / "app.js")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_every_current_english_league_club_has_a_badge():
    """Cup Match must not silently fall back to initials for an E0–E3 club."""
    source = (ROOT / "src" / "bym" / "web_assets" / "app.js").read_text()
    badge_block = source.split("const TEAM_BADGES = {", 1)[1].split("\n};", 1)[0]
    badge_names = set(
        re.findall(
            r'^  "([^"]+)": "https://assets\.football-logos\.cc/',
            badge_block,
            re.MULTILINE,
        )
    )
    matches = load_matches(ROOT / "data", [2025], ("E0", "E1", "E2", "E3"), fetch=False)
    current_clubs = set(matches.home_team) | set(matches.away_team)

    assert len(current_clubs) == 92
    assert current_clubs <= badge_names


def test_every_cup_name_alias_has_a_matching_badge():
    """A safe Cup Match alias must not turn its crest into initials."""
    source = (ROOT / "src" / "bym" / "web_assets" / "app.js").read_text()
    badge_block = source.split("const TEAM_BADGES = {", 1)[1].split("\n};", 1)[0]
    alias_block = source.split("const TEAM_BADGE_ALIASES = {", 1)[1].split("\n};", 1)[0]
    badge_names = set(re.findall(r'^  "([^"]+)": ', badge_block, re.MULTILINE))
    badge_aliases = dict(re.findall(r'"([^"]+)": "([^"]+)"', alias_block))

    for canonical, aliases in ALIASES.items():
        for alias in aliases:
            assert alias in badge_names or badge_aliases.get(alias) == canonical
