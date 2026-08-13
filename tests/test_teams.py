import pytest

from bym.sources import teams

KNOWN = {"Man City", "Man United", "Arsenal", "Tottenham", "Nott'm Forest",
         "Wolves", "Sheffield United", "Sheffield Weds", "Bournemouth"}


def test_exact_match():
    assert teams.resolve("Arsenal", KNOWN) == "Arsenal"


def test_alias_from_odds_api_full_names():
    assert teams.resolve("Manchester City", KNOWN) == "Man City"
    assert teams.resolve("Manchester United", KNOWN) == "Man United"
    assert teams.resolve("Tottenham Hotspur", KNOWN) == "Tottenham"
    assert teams.resolve("Nottingham Forest", KNOWN) == "Nott'm Forest"
    assert teams.resolve("Wolverhampton Wanderers", KNOWN) == "Wolves"


def test_fc_suffix_stripped():
    assert teams.resolve("Arsenal FC", KNOWN) == "Arsenal"
    assert teams.resolve("AFC Bournemouth", KNOWN) == "Bournemouth"


def test_does_not_confuse_similar_clubs():
    """Sheffield United and Sheffield Wednesday must never collapse
    together — near-miss matching is dangerous with these names."""
    assert teams.resolve("Sheffield Wednesday", KNOWN) == "Sheffield Weds"
    assert teams.resolve("Sheffield Utd", KNOWN) == "Sheffield United"


def test_unknown_returns_none_rather_than_guessing():
    assert teams.resolve("Real Madrid", KNOWN) is None
    assert teams.resolve("", KNOWN) is None


def test_alias_table_has_no_collisions():
    """A duplicate alias silently routes a club to the wrong canonical
    name and drops its odds — the index build must refuse to load."""
    teams._alias_index()  # raises AliasCollisionError if a duplicate exists


def test_collision_is_detected(monkeypatch):
    monkeypatch.setattr(
        teams,
        "ALIASES",
        {"Nott'm Forest": ("Nottingham Forest",), "Nottingham": ("Nottingham Forest",)},
    )
    with pytest.raises(teams.AliasCollisionError, match="claimed by both"):
        teams._alias_index()


def test_match_event_requires_both_sides():
    assert teams.match_event("Manchester City", "Arsenal", KNOWN) == ("Man City", "Arsenal")
    assert teams.match_event("Manchester City", "Real Madrid", KNOWN) is None
