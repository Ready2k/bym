"""API key loading (SPEC.md §22 — never write secrets into the repo).

Keys are read from environment variables first, then from a gitignored
`secrets/` directory holding one key per file. The directory form exists
because exporting four variables in every shell is the kind of friction
that ends with someone pasting a key into a tracked file.

Nothing here ever logs, prints or persists a key value. Callers get the
string; everything user-facing gets `describe_available()`, which reports
only whether a key is present.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_SECRETS_DIR = Path("secrets")

# env var -> candidate filenames in secrets/, most specific first.
# Several spellings are accepted because a key that silently isn't found
# is far more annoying than a slightly permissive lookup.
KEY_SOURCES: dict[str, tuple[str, ...]] = {
    "ODDS_API_KEY": ("theoddsapi", "the_odds_api", "odds_api", "oddsapi"),
    "API_FOOTBALL_KEY": ("apifootball", "api_football", "api-football"),
    "TAVILY_API_KEY": ("tavily", "tally", "tavilly"),
}


def _read_key_file(path: Path) -> str | None:
    try:
        value = path.read_text().strip()
    except OSError:
        return None
    if not value:
        return None
    # Tolerate `NAME=value` as well as a bare key.
    if "=" in value and "\n" not in value:
        _, _, tail = value.partition("=")
        value = tail.strip() or value
    return value or None


def get_key(
    env_var: str, secrets_dir: Path = DEFAULT_SECRETS_DIR
) -> str | None:
    """Resolve one API key. Environment wins over the secrets directory."""
    from_env = os.environ.get(env_var)
    if from_env:
        return from_env.strip()

    for candidate in KEY_SOURCES.get(env_var, ()):
        for name in (candidate, f"{candidate}.txt", f"{candidate}.key"):
            value = _read_key_file(secrets_dir / name)
            if value:
                return value
    return None


def load_all(secrets_dir: Path = DEFAULT_SECRETS_DIR) -> dict[str, str | None]:
    return {var: get_key(var, secrets_dir) for var in KEY_SOURCES}


def describe_available(
    secrets_dir: Path = DEFAULT_SECRETS_DIR,
) -> dict[str, bool]:
    """Presence only — never the values."""
    return {var: value is not None for var, value in load_all(secrets_dir).items()}


def apply_to_environment(secrets_dir: Path = DEFAULT_SECRETS_DIR) -> list[str]:
    """Populate os.environ from the secrets directory for keys not already
    set, so existing env-var-based code paths work unchanged. Returns the
    variable names that were filled (names only, never values)."""
    filled = []
    for var, value in load_all(secrets_dir).items():
        if value and not os.environ.get(var):
            os.environ[var] = value
            filled.append(var)
    return filled
