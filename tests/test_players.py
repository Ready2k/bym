from bym.availability.players import PlayerRef, resolve_player

SQUAD = [
    PlayerRef("p1", "Cole Palmer"),
    PlayerRef("p2", "Joe Williams"),
    PlayerRef("p3", "Jordan Williams"),
    PlayerRef("p4", "Nicolas Jackson", aliases=("Nico Jackson",)),
    PlayerRef("p5", "Enzo Fernandez", aliases=("Enzo Fernández",)),
]


def test_exact_match():
    r = resolve_player("Cole Palmer", SQUAD)
    assert r.resolved and r.player_id == "p1"


def test_alias_match():
    assert resolve_player("Nico Jackson", SQUAD).player_id == "p4"


def test_accent_normalised():
    assert resolve_player("Enzo Fernández", SQUAD).player_id == "p5"


def test_unique_surname_resolves():
    assert resolve_player("Palmer", SQUAD).player_id == "p1"


def test_ambiguous_surname_is_not_guessed():
    """Two Williams in the squad — the answer is AMBIGUOUS, not whichever
    scores highest on a similarity function."""
    r = resolve_player("Williams", SQUAD)
    assert not r.resolved
    assert r.status == "AMBIGUOUS"
    assert set(r.candidates) == {"Joe Williams", "Jordan Williams"}


def test_initial_plus_surname_still_ambiguous_when_both_match():
    """'J. Williams' matches both Joe and Jordan — must stay unresolved."""
    r = resolve_player("J. Williams", SQUAD)
    assert not r.resolved
    assert set(r.candidates) == {"Joe Williams", "Jordan Williams"}


def test_initial_disambiguates_when_unique():
    squad = [PlayerRef("a", "Joe Williams"), PlayerRef("b", "Marc Cucurella")]
    assert resolve_player("J. Williams", squad).player_id == "a"


def test_contradictory_initial_does_not_resolve_optimistically():
    """'X. Williams' matches no initial — surname alone must not win."""
    squad = [PlayerRef("a", "Joe Williams")]
    r = resolve_player("X. Williams", squad)
    assert not r.resolved
    assert r.candidates == ("Joe Williams",)


def test_unknown_player_unresolved():
    r = resolve_player("Lionel Messi", SQUAD)
    assert r.status == "PLAYER_UNRESOLVED"
    assert r.player_id is None


def test_empty_name_unresolved():
    assert not resolve_player("", SQUAD).resolved
    assert not resolve_player("   ", SQUAD).resolved


def test_empty_squad_unresolved():
    assert not resolve_player("Cole Palmer", []).resolved
