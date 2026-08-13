"""Key resolution (§22), and the CLI wiring that makes it reachable.

The loader existed and was correct, but nothing called it: every command
read os.environ directly, so a key sitting in secrets/ produced
"ODDS_API_KEY is not set". These tests hold that wiring in place.
"""

import os

import pytest

from bym import cli, secrets

KEYS = ("ODDS_API_KEY", "API_FOOTBALL_KEY", "TAVILY_API_KEY",
        "FOOTBALL_DATA_ORG_KEY")


@pytest.fixture
def clean_env():
    """Start with no keys set, and restore the real environment afterwards.

    monkeypatch.delenv cannot do this alone: apply_to_environment writes to
    os.environ directly, and a variable that was unset to begin with is not
    tracked, so those writes would leak into every later test.
    """
    saved = {var: os.environ.pop(var, None) for var in KEYS}
    yield
    for var, value in saved.items():
        if value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = value


@pytest.fixture
def secrets_dir(tmp_path):
    directory = tmp_path / "secrets"
    directory.mkdir()
    (directory / "theoddsapi").write_text("odds-key\n")
    (directory / "apifootball").write_text("football-key")
    return directory


def test_environment_wins_over_the_secrets_directory(
    clean_env, secrets_dir, monkeypatch
):
    monkeypatch.setenv("ODDS_API_KEY", "from-env")
    assert secrets.get_key("ODDS_API_KEY", secrets_dir) == "from-env"


def test_key_file_is_read_and_stripped(clean_env, secrets_dir):
    assert secrets.get_key("ODDS_API_KEY", secrets_dir) == "odds-key"


def test_name_equals_value_form_is_tolerated(clean_env, tmp_path):
    directory = tmp_path / "secrets"
    directory.mkdir()
    (directory / "tavily").write_text("TAVILY_API_KEY=abc123")
    assert secrets.get_key("TAVILY_API_KEY", directory) == "abc123"


def test_missing_key_resolves_to_none(clean_env, secrets_dir):
    assert secrets.get_key("FOOTBALL_DATA_ORG_KEY", secrets_dir) is None
    assert secrets.describe_available(secrets_dir) == {
        "ODDS_API_KEY": True,
        "API_FOOTBALL_KEY": True,
        "TAVILY_API_KEY": False,
        "FOOTBALL_DATA_ORG_KEY": False,
    }


def test_apply_fills_only_unset_variables(clean_env, secrets_dir, monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "already-exported")
    filled = secrets.apply_to_environment(secrets_dir)

    assert filled == ["API_FOOTBALL_KEY"]
    assert os.environ["ODDS_API_KEY"] == "already-exported"
    assert os.environ["API_FOOTBALL_KEY"] == "football-key"


def test_main_populates_the_environment_from_secrets(
    clean_env, secrets_dir, tmp_path, monkeypatch, capsys
):
    """The commands that need keys read os.environ, so main() must fill it
    before dispatching — `jobs` is used here only as an inert subcommand."""
    monkeypatch.chdir(tmp_path)

    assert cli.main(["jobs", "--db", str(tmp_path / "scheduler.db")]) == 0

    assert os.environ["ODDS_API_KEY"] == "odds-key"
    assert os.environ["API_FOOTBALL_KEY"] == "football-key"
    assert "FOOTBALL_DATA_ORG_KEY" not in os.environ


def test_main_does_not_print_key_values(
    clean_env, secrets_dir, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    cli.main(["jobs", "--db", str(tmp_path / "scheduler.db")])

    output = capsys.readouterr()
    assert "odds-key" not in output.out + output.err
