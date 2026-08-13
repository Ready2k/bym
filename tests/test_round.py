import json

import pytest

from bym.round import load_round, round_from_dict

VALID = {
    "round_number": 14,
    "deadline_at": "2026-10-18T14:00:00",
    "fixtures": [
        {"home": "Arsenal", "away": "Everton"},
        {"home": "Chelsea", "away": "Newcastle"},
        {"home": "Fulham", "away": "Brentford"},
        {"home": "Leeds", "away": "Tottenham"},
        {"home": "Man City", "away": "Bournemouth"},
        {"home": "West Ham", "away": "Aston Villa"},
    ],
}


def write(tmp_path, payload):
    p = tmp_path / "round.json"
    p.write_text(json.dumps(payload))
    return p


def test_loads_valid_round(tmp_path):
    rnd = load_round(write(tmp_path, VALID))
    assert rnd.round_number == 14
    assert len(rnd.fixtures) == 6
    assert rnd.source == "MANUAL"
    assert str(rnd.deadline_at.tzinfo) == "Europe/London"


def test_rejects_wrong_fixture_count(tmp_path):
    bad = dict(VALID, fixtures=VALID["fixtures"][:5])
    with pytest.raises(ValueError, match="exactly 6"):
        load_round(write(tmp_path, bad))


def test_rejects_duplicate_fixture(tmp_path):
    bad = dict(VALID, fixtures=VALID["fixtures"][:5] + [VALID["fixtures"][0]])
    with pytest.raises(ValueError, match="Duplicate"):
        load_round(write(tmp_path, bad))


def test_optional_odds_parsed(tmp_path):
    payload = dict(VALID)
    payload["fixtures"] = [dict(f) for f in VALID["fixtures"]]
    payload["fixtures"][0]["odds"] = [1.5, 4.2, 7.0]
    rnd = load_round(write(tmp_path, payload))
    assert rnd.fixtures[0].odds == (1.5, 4.2, 7.0)
    assert rnd.fixtures[1].odds is None


def test_invalid_odds_rejected(tmp_path):
    payload = dict(VALID)
    payload["fixtures"] = [dict(f) for f in VALID["fixtures"]]
    payload["fixtures"][0]["odds"] = [1.5, 4.2]  # missing away odds
    with pytest.raises(ValueError, match="odds"):
        load_round(write(tmp_path, payload))
    payload["fixtures"][0]["odds"] = [0.9, 4.2, 7.0]  # impossible price
    with pytest.raises(ValueError, match="odds"):
        load_round(write(tmp_path, payload))


# --- Club-name resolution ----------------------------------------------
#
# A round file holds whatever the user typed. Sky writes "Manchester
# City"; the training corpus uses football-data.co.uk's "Man City". An
# unresolved name is not cosmetic — the fixture falls back to a
# league-average stand-in, which flipped Man City v Bournemouth from a
# 2-0 home win to a 0-2 home defeat while reporting MEDIUM confidence.

CORPUS = ("Man City", "Nott'm Forest", "Leeds", "Bournemouth", "Wolves", "Everton")


def _round_named(home, away):
    payload = dict(VALID)
    payload["fixtures"] = [dict(f) for f in VALID["fixtures"]]
    payload["fixtures"][0] = {"home": home, "away": away}
    return round_from_dict(payload)


def test_sky_long_names_resolve_to_corpus_names():
    from bym.round import resolve_round_teams

    rnd, renamed = resolve_round_teams(
        _round_named("Manchester City", "Nottingham Forest"), CORPUS
    )
    assert rnd.fixtures[0].home == "Man City"
    assert rnd.fixtures[0].away == "Nott'm Forest"
    assert ("Manchester City", "Man City") in renamed
    assert ("Nottingham Forest", "Nott'm Forest") in renamed


def test_a_name_already_canonical_is_left_alone():
    from bym.round import resolve_round_teams

    rnd, renamed = resolve_round_teams(_round_named("Man City", "Leeds"), CORPUS)
    assert rnd.fixtures[0].home == "Man City"
    assert renamed == []


def test_a_genuinely_unknown_team_is_kept_verbatim():
    """Resolution must not invent a match. An unknown club stays as typed
    so the caller's own unknown-team warning still fires."""
    from bym.round import resolve_round_teams

    rnd, renamed = resolve_round_teams(_round_named("Real Madrid", "Leeds"), CORPUS)
    assert rnd.fixtures[0].home == "Real Madrid"
    assert renamed == []


def test_predict_and_engine_resolve_through_the_same_helper():
    """These two commands disagreeing about one round file is the bug this
    helper exists to prevent, so neither may grow its own copy."""
    import inspect

    from bym import cli, engine

    assert "resolve_round_teams" in inspect.getsource(cli.cmd_predict)
    assert "resolve_round_teams" in inspect.getsource(engine.run_engine)
