"""Increment 5 step 1: joining recorded claims to what actually happened."""

from __future__ import annotations

import pytest

from super6 import claim_outcomes as co
from super6 import db, prediction_history
from super6.knowledge import store
from super6.knowledge.models import CandidateClaim, ClaimType, EvidenceDocument


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "s.db") as connection:
        yield connection


def evidence(conn) -> int:
    document, _was_new = store.record_evidence(conn, EvidenceDocument(
        url="https://example.test/a", title="t", acquisition_source="tavily",
        origin_domain="example.test", origin_authority="TIER_2",
        retrieved_at="2026-08-14T10:00:00+00:00", content_hash="h",
    ))
    return int(document.id)


def claim(conn, home, away, *, team, round_number=1, availability="CONFIRMED_ABSENT",
          verdict="ACCEPTED", player="A Player"):
    return store.record_claim(
        conn,
        CandidateClaim(
            claim_type=ClaimType.AVAILABILITY.value,
            player_name=player, team=team, availability=availability,
            certainty="CONFIRMED", evidence_id=evidence(conn), verdict=verdict,
        ),
        home, away, round_number=round_number,
        observed_at="2026-08-14T10:00:00+00:00",
    )


def forecast(path, season=2026, round_number=1, fixtures=None):
    fixtures = fixtures or [
        {"home_team": "Arsenal", "away_team": "Everton", "result": "HOME", "score": "1-0"},
        {"home_team": "Chelsea", "away_team": "Newcastle", "result": "HOME", "score": "2-1"},
    ]
    prediction_history.record_snapshot(season, round_number, fixtures, path=path)


# ---------------------------------------------------------------------------
# the join


def test_a_claim_is_paired_with_the_result_of_its_own_fixture(conn, tmp_path):
    path = tmp_path / "history.json"
    forecast(path)
    claim(conn, "Arsenal", "Everton", team="Arsenal")
    results = {(2026, 1, 0): (3, 1), (2026, 1, 1): (0, 0)}

    outcomes = co.claim_outcomes(conn, results, prediction_path=path)
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert (outcome.season, outcome.round_number, outcome.fixture_index) == (2026, 1, 0)
    assert outcome.team_was_home is True
    assert (outcome.team_goals, outcome.opponent_goals) == (3, 1)
    assert outcome.is_confirmed_absence is True


def test_goals_are_attributed_to_the_claimed_team_not_the_home_team(conn, tmp_path):
    path = tmp_path / "history.json"
    forecast(path)
    claim(conn, "Arsenal", "Everton", team="Everton")
    outcomes = co.claim_outcomes(conn, {(2026, 1, 0): (3, 1)}, prediction_path=path)
    assert outcomes[0].team_was_home is False
    assert (outcomes[0].team_goals, outcomes[0].opponent_goals) == (1, 3)


def test_the_season_is_recovered_from_the_forecast_since_claims_lack_one(conn, tmp_path):
    path = tmp_path / "history.json"
    forecast(path, season=2027, round_number=4)
    claim(conn, "Arsenal", "Everton", team="Arsenal", round_number=4)
    outcomes = co.claim_outcomes(conn, {(2027, 4, 0): (2, 0)}, prediction_path=path)
    assert outcomes[0].season == 2027


def test_a_fixture_repeated_across_seasons_is_dropped_rather_than_guessed(conn, tmp_path):
    """Attaching team news to the wrong match would quietly poison exactly
    the regression this data exists to support."""
    path = tmp_path / "history.json"
    forecast(path, season=2026, round_number=1)
    forecast(path, season=2027, round_number=1)
    claim(conn, "Arsenal", "Everton", team="Arsenal")
    results = {(2026, 1, 0): (3, 1), (2027, 1, 0): (0, 2)}
    assert co.claim_outcomes(conn, results, prediction_path=path) == ()


def test_a_claim_with_no_recorded_result_contributes_nothing_yet(conn, tmp_path):
    path = tmp_path / "history.json"
    forecast(path)
    claim(conn, "Arsenal", "Everton", team="Arsenal")
    assert co.claim_outcomes(conn, {}, prediction_path=path) == ()
    assert co.claim_outcomes(conn, {(2026, 1, 0): None}, prediction_path=path) == ()


def test_a_claim_about_a_never_forecast_fixture_contributes_nothing(conn, tmp_path):
    path = tmp_path / "history.json"
    forecast(path)
    claim(conn, "Fulham", "Brentford", team="Fulham")
    assert co.claim_outcomes(conn, {(2026, 1, 0): (1, 0)}, prediction_path=path) == ()


def test_rejected_claims_are_excluded_by_default_but_reachable(conn, tmp_path):
    path = tmp_path / "history.json"
    forecast(path)
    claim(conn, "Arsenal", "Everton", team="Arsenal", verdict="REJECTED")
    results = {(2026, 1, 0): (3, 1)}
    assert co.claim_outcomes(conn, results, prediction_path=path) == ()
    assert len(co.claim_outcomes(conn, results, prediction_path=path, verdict=None)) == 1


def test_a_claim_naming_no_team_is_kept_but_attributed_to_neither_side(conn, tmp_path):
    path = tmp_path / "history.json"
    forecast(path)
    claim(conn, "Arsenal", "Everton", team=None)
    outcome = co.claim_outcomes(conn, {(2026, 1, 0): (3, 1)}, prediction_path=path)[0]
    assert outcome.team_was_home is None
    assert outcome.team_goals is None


# ---------------------------------------------------------------------------
# the accumulating evidence


def test_evidence_separates_pairs_from_attributed_from_absences(conn, tmp_path):
    path = tmp_path / "history.json"
    forecast(path)
    claim(conn, "Arsenal", "Everton", team="Arsenal", availability="CONFIRMED_ABSENT")
    claim(conn, "Chelsea", "Newcastle", team="Chelsea", availability="DOUBT")
    claim(conn, "Arsenal", "Everton", team=None, availability="CONFIRMED_ABSENT")
    results = {(2026, 1, 0): (3, 1), (2026, 1, 1): (2, 2)}

    evidence_summary = co.impact_evidence(
        co.claim_outcomes(conn, results, prediction_path=path)
    )
    assert evidence_summary.pairs == 3
    assert evidence_summary.attributed == 2  # the team-less claim has no side
    assert evidence_summary.confirmed_absences == 1
    assert evidence_summary.rounds == 1
    assert evidence_summary.teams == 2
    assert evidence_summary.mean_goals_with_absence == 3.0  # Arsenal scored 3
    assert evidence_summary.mean_goals_without_absence == 2.0  # Chelsea scored 2


def test_evidence_is_empty_rather_than_fabricated_with_nothing_paired():
    summary = co.impact_evidence(())
    assert summary.pairs == 0 and summary.confirmed_absences == 0
    assert summary.mean_goals_with_absence is None


def test_readiness_says_plainly_that_there_is_not_enough_to_fit():
    message = co.fitting_readiness(co.impact_evidence(()))
    assert f"0 of ~{co.ABSENCES_FOR_FITTING} confirmed absences" in message
    assert "nobody could defend" in message


def test_readiness_still_demands_backtesting_once_there_is_enough():
    plenty = co.ImpactEvidence(
        pairs=500, attributed=500, confirmed_absences=co.ABSENCES_FOR_FITTING,
        rounds=40, teams=24, mean_goals_with_absence=1.2,
        mean_goals_without_absence=1.5,
    )
    message = co.fitting_readiness(plenty)
    assert "enough to attempt a fit" in message
    assert "§28" in message  # never "go ahead and use it"


def test_this_module_never_becomes_the_impact_model():
    """§22/§28: pairing data must not quietly turn into adjusting forecasts."""
    from super6.availability import impact

    assert impact.QUANTITATIVE_IMPACT_MODEL_ENABLED is False
    source = __import__("inspect").getsource(co)
    for forbidden in ("lambda_delta", "apply_to_lambdas", "assess_impact"):
        assert forbidden not in source
