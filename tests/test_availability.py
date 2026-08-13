"""Increment 3 acceptance tests."""

from datetime import datetime, timedelta, timezone

import pytest

from bym import db, events as ev, movement
from bym.availability import attribution, completeness, impact, ingest, store
from bym.availability.lineups import compare_lineups
from bym.availability.model import (
    AvailabilityEvent,
    ClaimStatus,
    EventType,
    Importance,
    Lineup,
    LineupKind,
    Reliability,
)
from bym.availability.players import PlayerRef
from bym.sources.consensus import Consensus

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
SQUAD = [
    PlayerRef("p1", "Cole Palmer"),
    PlayerRef("p2", "Nicolas Jackson"),
    PlayerRef("p3", "Enzo Fernandez"),
]


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "bym.db") as c:
        yield c


def ingest_one(conn, player="Cole Palmer", etype=EventType.INJURY,
               status=ClaimStatus.REPORTED, tier="ESTABLISHED_JOURNALIST",
               source="journo", minutes=0, importance=Importance.UNKNOWN):
    return ingest.ingest_availability(
        conn, "Chelsea", "Newcastle", "Chelsea", player, etype, status,
        source, tier, NOW + timedelta(minutes=minutes), squad=SQUAD,
        round_number=1, importance=importance, now=NOW + timedelta(minutes=minutes),
    )


# ---- persistence, versioning, idempotency -------------------------------


def test_events_persist_as_versioned_records(conn):
    ingest_one(conn, status=ClaimStatus.REPORTED, minutes=0)
    ingest_one(conn, etype=EventType.DOUBT, status=ClaimStatus.LIKELY,
               source="club", tier="OFFICIAL_CLUB", minutes=60)
    ingest_one(conn, etype=EventType.CONFIRMED_STARTER, status=ClaimStatus.CONFIRMED,
               source="xi", tier="OFFICIAL_CLUB", minutes=120)
    history = store.history_for_player(conn, "Chelsea", "Newcastle", "Cole Palmer")
    assert len(history) == 3  # evidence chain, nothing overwritten


def test_repeated_identical_source_event_is_idempotent(conn):
    first = ingest_one(conn)
    second = ingest_one(conn)
    assert first.was_new and not second.was_new
    assert len(store.history_for_player(conn, "Chelsea", "Newcastle", "Cole Palmer")) == 1


def test_correction_creates_history_rather_than_mutation(conn):
    original = ingest_one(conn).event
    correction = AvailabilityEvent(
        home_team="Chelsea", away_team="Newcastle", team="Chelsea",
        player_name="Cole Palmer", player_id="p1",
        event_type=EventType.RETURN.value, claim_status=ClaimStatus.CONFIRMED.value,
        source_id="club", source_reliability=Reliability.HIGH.value,
        source_timestamp=db.to_iso(NOW + timedelta(hours=2)),
        observed_at=db.to_iso(NOW + timedelta(hours=2)),
    )
    store.supersede(conn, correction, original.id)
    history = store.history_for_player(conn, "Chelsea", "Newcastle", "Cole Palmer")
    assert len(history) == 2
    assert history[1].supersedes_event_id == original.id
    assert history[0].event_type == EventType.INJURY.value  # original intact


def test_retraction_preserves_the_original(conn):
    original = ingest_one(conn).event
    retraction = AvailabilityEvent(
        home_team="Chelsea", away_team="Newcastle", team="Chelsea",
        player_name="Cole Palmer", player_id="p1",
        event_type=EventType.RETURN.value, claim_status=ClaimStatus.CONFIRMED.value,
        source_id="journo", source_reliability=Reliability.MEDIUM.value,
        source_timestamp=db.to_iso(NOW + timedelta(hours=1)),
        observed_at=db.to_iso(NOW + timedelta(hours=1)),
        note="retracts earlier injury report",
    )
    store.retract(conn, original.id, retraction)
    assert len(store.history_for_player(conn, "Chelsea", "Newcastle", "Cole Palmer")) == 2
    active = store.events_for_fixture(conn, "Chelsea", "Newcastle")
    assert all(e.id != original.id for e in active)  # excluded from current view


# ---- source reliability vs claim certainty ------------------------------


def test_reliability_and_claim_status_are_separate(conn):
    """A HIGH-reliability journalist saying 'expected to miss out' is
    LIKELY, not CONFIRMED."""
    result = ingest_one(conn, status=ClaimStatus.LIKELY, tier="ESTABLISHED_JOURNALIST")
    assert result.event.claim_status == "LIKELY"
    assert result.event.source_reliability == "MEDIUM"

    official = ingest_one(conn, status=ClaimStatus.LIKELY, tier="OFFICIAL_CLUB",
                          source="club", minutes=5)
    assert official.event.source_reliability == "HIGH"
    assert official.event.claim_status == "LIKELY"  # authority did not upgrade it


# ---- unresolved players -------------------------------------------------


def test_unresolved_player_emits_event_and_stores_no_identity(conn):
    result = ingest.ingest_availability(
        conn, "Chelsea", "Newcastle", "Chelsea", "J. Williams",
        EventType.DOUBT, ClaimStatus.REPORTED, "journo",
        "ESTABLISHED_JOURNALIST", NOW, squad=SQUAD, now=NOW,
    )
    assert result.event.player_id is None
    assert result.event.resolution == "PLAYER_UNRESOLVED"
    assert any(e.event_type == "PLAYER_UNRESOLVED" for e in result.events)


# ---- lineups ------------------------------------------------------------


def lineup(kind, players, team="Chelsea", minutes=0, confidence=None):
    return Lineup(
        home_team="Chelsea", away_team="Newcastle", team=team, kind=kind.value,
        source_id="club", generated_at=db.to_iso(NOW + timedelta(minutes=minutes)),
        observed_at=db.to_iso(NOW + timedelta(minutes=minutes)),
        players=players, confidence=confidence, round_number=1,
    )


def test_expected_and_confirmed_are_distinct_objects(conn):
    exp = lineup(LineupKind.EXPECTED, ["Cole Palmer", "Nicolas Jackson"], confidence="MEDIUM")
    con = lineup(LineupKind.CONFIRMED, ["Cole Palmer", "Nicolas Jackson"], minutes=60)
    ingest.ingest_lineup(conn, exp, now=NOW)
    ingest.ingest_lineup(conn, con, now=NOW + timedelta(minutes=60))
    stored = store.lineups_for_fixture(conn, "Chelsea", "Newcastle")
    assert {l.kind for l in stored} == {"EXPECTED", "CONFIRMED"}


def test_confirmed_lineup_cannot_carry_confidence():
    with pytest.raises(ValueError, match="no confidence"):
        lineup(LineupKind.CONFIRMED, ["A"], confidence="HIGH")


def test_material_lineup_difference_detected(conn):
    ingest.ingest_lineup(
        conn, lineup(LineupKind.EXPECTED, ["Cole Palmer", "Nicolas Jackson", "Enzo Fernandez"],
                     confidence="MEDIUM"), now=NOW)
    emitted = ingest.ingest_lineup(
        conn, lineup(LineupKind.CONFIRMED, ["Nicolas Jackson", "Enzo Fernandez"], minutes=60),
        key_players={"Cole Palmer"}, now=NOW + timedelta(minutes=60))
    surprise = next(e for e in emitted if e.event_type == "LINEUP_SURPRISE")
    assert "Cole Palmer" in surprise.detail["expected_starters_absent"]
    assert surprise.detail["quantitative_impact"] == "NONE DIRECTLY"
    assert surprise.detail["action"] == "Review required"


def test_single_key_absence_is_material_even_if_only_one_change():
    exp = lineup(LineupKind.EXPECTED, ["Cole Palmer", "Nicolas Jackson"], confidence="HIGH")
    con = lineup(LineupKind.CONFIRMED, ["Nicolas Jackson"], minutes=60)
    diff = compare_lineups(exp, con, key_players={"Cole Palmer"})
    assert diff.material and diff.key_players_absent == ("Cole Palmer",)


def test_matching_lineup_is_not_a_surprise(conn):
    players = ["Cole Palmer", "Nicolas Jackson"]
    ingest.ingest_lineup(conn, lineup(LineupKind.EXPECTED, players, confidence="HIGH"), now=NOW)
    emitted = ingest.ingest_lineup(
        conn, lineup(LineupKind.CONFIRMED, players, minutes=60),
        now=NOW + timedelta(minutes=60))
    assert not any(e.event_type == "LINEUP_SURPRISE" for e in emitted)


# ---- the λ boundary -----------------------------------------------------


def test_availability_alone_cannot_change_lambda():
    """The load-bearing guard: no availability event may alter goal rates
    while no validated impact model is enabled."""
    assert impact.QUANTITATIVE_IMPACT_MODEL_ENABLED is False
    assessment = impact.assess_impact()
    assert assessment.lambda_delta_home == 0.0
    assert assessment.lambda_delta_away == 0.0
    assert not assessment.quantitative
    assert not assessment.alters_forecast
    assert impact.apply_to_lambdas(1.82, 0.96, assessment) == (1.82, 0.96)


def test_forecast_path_has_no_availability_input():
    """There must be no argument through which team news reaches the model.

    Pinned as an exact set, not a subset: a new parameter has to be
    justified here deliberately rather than slipped in. The permitted ones
    are the fitted parameters, the two teams, the config, the market price,
    the per-fixture blend weight, and a provenance label — none of which
    can carry an injury, a suspension or a lineup.
    """
    import inspect

    from bym.recommend import recommend_fixture

    params = set(inspect.signature(recommend_fixture).parameters)
    assert params == {
        "params", "home", "away", "cfg", "odds", "blend_weight", "basis",
    }


def test_market_only_fallback_cannot_smuggle_news_into_the_forecast():
    """The fallback changes how much of the market is used, nothing else.

    `basis` is a label and must not alter any number; `blend_weight` may
    only move the result probabilities toward the market.
    """
    import pandas as pd

    from bym.config import FROZEN_PHASE1_V1
    from bym.model.dixon_coles import DixonColesParams
    from bym.recommend import recommend_fixture

    # Built directly rather than fitted: test_dixon_coles.synthetic_matches
    # advances a module-level RNG, so calling it here would silently change
    # what every later test in the session fits on.
    fitted = DixonColesParams(
        teams=("Strong", "Weak"), mu=0.15, gamma=0.3,
        alpha={"Strong": 0.4, "Weak": -0.4},
        beta={"Strong": -0.3, "Weak": 0.3},
        rho=-0.05, xi=0.0, reference_date=pd.Timestamp("2026-08-01"),
        n_matches=100, log_likelihood=-1.0,
    )
    odds = (2.0, 3.4, 3.6)
    labelled = recommend_fixture(
        fitted, "Strong", "Weak", FROZEN_PHASE1_V1, odds=odds, basis="ANYTHING"
    )
    plain = recommend_fixture(fitted, "Strong", "Weak", FROZEN_PHASE1_V1, odds=odds)
    assert labelled.pick == plain.pick
    assert labelled.ev == plain.ev
    assert labelled.p_result == plain.p_result

    # w=1.0 must reproduce the market's result probabilities exactly.
    from bym.model.market import implied_result_probs

    market_only = recommend_fixture(
        fitted, "Strong", "Weak", FROZEN_PHASE1_V1, odds=odds, blend_weight=1.0
    )
    mh, md, ma = implied_result_probs(*odds)
    assert market_only.matrix.p_home_win == pytest.approx(mh, abs=1e-9)
    assert market_only.matrix.p_draw == pytest.approx(md, abs=1e-9)
    assert market_only.matrix.p_away_win == pytest.approx(ma, abs=1e-9)
    # ...while the scoreline shape still comes from the model.
    assert market_only.raw_matrix.lambda_home == plain.raw_matrix.lambda_home


# ---- attribution --------------------------------------------------------


def consensus(at, h=0.287, d=0.262, a=0.451):
    return Consensus("Chelsea", "Newcastle", db.to_iso(at), 9, h, d, a, 0.01, 0, None)


def test_market_move_attributed_to_nearby_news(conn):
    ingest_one(conn, etype=EventType.CONFIRMED_ABSENT, status=ClaimStatus.CONFIRMED,
               tier="OFFICIAL_CLUB", source="club", minutes=6)
    report = movement.compare(consensus(NOW), 0.221, 0.262, 0.517)
    found = attribution.attribute(report, store.events_for_fixture(conn, "Chelsea", "Newcastle"))
    assert len(found) == 1
    assert found[0].relationship == "PLAUSIBLE"
    assert found[0].temporal_distance_minutes == pytest.approx(6.0)
    assert "causality is not established" in found[0].describe()


def test_distant_news_is_not_attributed(conn):
    ingest_one(conn, etype=EventType.CONFIRMED_ABSENT, status=ClaimStatus.CONFIRMED,
               tier="OFFICIAL_CLUB", source="club", minutes=600)
    report = movement.compare(consensus(NOW), 0.221, 0.262, 0.517)
    assert attribution.attribute(report, store.events_for_fixture(conn, "Chelsea", "Newcastle")) == []


def test_unexplained_move_stays_unexplained(conn):
    report = movement.compare(consensus(NOW), 0.221, 0.262, 0.517)
    assert attribution.best_explanation(report, []) is None


def test_other_fixtures_news_cannot_explain_this_move(conn):
    ingest.ingest_availability(
        conn, "Arsenal", "Everton", "Arsenal", "Cole Palmer", EventType.INJURY,
        ClaimStatus.CONFIRMED, "club", "OFFICIAL_CLUB", NOW, squad=SQUAD, now=NOW)
    report = movement.compare(consensus(NOW), 0.221, 0.262, 0.517)
    others = store.events_for_fixture(conn, "Arsenal", "Everton")
    assert attribution.attribute(report, others) == []


# ---- feature completeness ----------------------------------------------


def test_completeness_reflects_lineup_availability():
    both = [lineup(LineupKind.CONFIRMED, ["A"], minutes=1),
            lineup(LineupKind.CONFIRMED, ["B"], team="Newcastle", minutes=1)]
    good = completeness.assess("Chelsea", "Newcastle", both, has_market=True)
    assert good.state == "GOOD"
    assert completeness.completeness_factor(good) == "HIGH"

    partial = completeness.assess(
        "Chelsea", "Newcastle",
        [lineup(LineupKind.EXPECTED, ["A"], confidence="MEDIUM")], has_market=True)
    assert partial.state == "DEGRADED"
    assert completeness.completeness_factor(partial) == "MEDIUM"

    bare = completeness.assess("Chelsea", "Newcastle", [], has_market=False)
    assert bare.state == "LIMITED"
    assert completeness.completeness_factor(bare) == "LOW"


def test_stale_market_dominates():
    both = [lineup(LineupKind.CONFIRMED, ["A"], minutes=1),
            lineup(LineupKind.CONFIRMED, ["B"], team="Newcastle", minutes=1)]
    stale = completeness.assess("Chelsea", "Newcastle", both, has_market=True, stale_market=True)
    assert stale.state == "STALE"
    assert completeness.completeness_factor(stale) == "LOW"


def test_completeness_stays_categorical():
    """No 83.7 confidence units."""
    report = completeness.assess("Chelsea", "Newcastle", [], has_market=True)
    assert completeness.completeness_factor(report) in {"HIGH", "MEDIUM", "LOW"}


def test_read_model_carries_the_lineup_surprise_as_standing_fact(conn):
    """LINEUP_SURPRISE is raised as an event, but a fixture opened later
    would show nothing — the read model has to hold the fact too."""
    from bym.api import service

    expected = lineup(
        LineupKind.EXPECTED,
        ["Palmer", "Jackson", "Caicedo", "James", "Colwill"],
        confidence="MEDIUM",
    )
    confirmed = lineup(
        LineupKind.CONFIRMED,
        ["Palmer", "Mudryk", "Chalobah", "Disasi", "Badiashile"],
        minutes=60,
    )
    ingest.ingest_lineup(conn, expected, now=NOW)
    ingest.ingest_lineup(conn, confirmed, now=NOW + timedelta(minutes=60))

    state = service.build_availability_state(conn, "Chelsea", "Newcastle", has_market=True)
    assert state.lineup_surprise is not None
    assert "Chelsea" in state.lineup_surprise
    # Evidence never moves a frozen model, however surprising it is.
    assert state.quantitative_impact == "NONE DIRECTLY"


def test_no_lineup_surprise_when_the_confirmed_xi_matches(conn):
    from bym.api import service

    players = ["Palmer", "Jackson", "Caicedo", "James", "Colwill"]
    ingest.ingest_lineup(conn, lineup(LineupKind.EXPECTED, players, confidence="HIGH"), now=NOW)
    ingest.ingest_lineup(
        conn, lineup(LineupKind.CONFIRMED, players, minutes=60),
        now=NOW + timedelta(minutes=60),
    )
    state = service.build_availability_state(conn, "Chelsea", "Newcastle", has_market=True)
    assert state.lineup_surprise is None


def test_no_lineup_surprise_without_an_expected_lineup_to_compare(conn):
    """A confirmed XI alone is not a surprise — there is nothing to differ from."""
    from bym.api import service

    ingest.ingest_lineup(
        conn, lineup(LineupKind.CONFIRMED, ["Palmer", "Jackson"], minutes=60), now=NOW
    )
    state = service.build_availability_state(conn, "Chelsea", "Newcastle", has_market=True)
    assert state.lineup_surprise is None
