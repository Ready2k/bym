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

import pytest

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
        ["node", "--check", str(ROOT / "src" / "super6" / "web_assets" / "app.js")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
