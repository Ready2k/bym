"""football-data.org adapter — offline, against the real payload shape."""

import pytest

from super6.scoring import FixtureStatus
from super6.sources import football_data_org as fdo
from super6.sources import registry as reg
from super6.sources.teams import resolve

# Shape taken from a real live response (2026-08-08).
PAYLOAD = {
    "matches": [
        {
            "id": 537000,
            "utcDate": "2026-08-21T19:00:00Z",
            "status": "FINISHED",
            "matchday": 1,
            "homeTeam": {"id": 57, "name": "Arsenal FC", "shortName": "Arsenal",
                         "tla": "ARS", "crest": "x"},
            "awayTeam": {"id": 1076, "name": "Coventry City FC",
                         "shortName": "Coventry", "tla": "COV", "crest": "x"},
            "score": {"fullTime": {"home": 2, "away": 0}},
        },
        {
            "id": 537001,
            "utcDate": "2026-08-22T14:00:00Z",
            "status": "POSTPONED",
            "matchday": 1,
            "homeTeam": {"id": 66, "name": "Manchester United FC"},
            "awayTeam": {"id": 62, "name": "Everton FC"},
            "score": {"fullTime": {"home": None, "away": None}},
        },
        {
            "id": 537002,
            "utcDate": "2026-08-22T16:30:00Z",
            "status": "SCHEDULED",
            "matchday": 1,
            "homeTeam": {"id": 61, "name": "Chelsea FC"},
            "awayTeam": {"id": 67, "name": "Newcastle United FC"},
            "score": {"fullTime": {"home": None, "away": None}},
        },
    ]
}


def test_parse_real_payload_shape():
    matches = fdo.parse_matches(PAYLOAD)
    assert len(matches) == 3
    assert matches[0].home_team == "Arsenal FC"
    assert matches[0].home_goals == 2
    assert matches[0].matchday == 1


def test_status_mapping_to_fixture_status():
    matches = fdo.parse_matches(PAYLOAD)
    assert matches[0].status == FixtureStatus.COMPLETED.value
    assert matches[1].status == FixtureStatus.POSTPONED.value
    assert matches[2].status == FixtureStatus.SCHEDULED.value


def test_void_detection_covers_every_non_result_case():
    """§2.2: postponed, abandoned, cancelled and awarded all mean there
    was no real 90-minute result."""
    for raw, expect_void in [
        ("POSTPONED", True), ("SUSPENDED", True), ("CANCELLED", True),
        ("AWARDED", True), ("FINISHED", False), ("SCHEDULED", False),
        ("IN_PLAY", False),
    ]:
        payload = {"matches": [{
            "id": 1, "status": raw, "utcDate": "2026-08-21T19:00:00Z",
            "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"},
            "score": {"fullTime": {"home": None, "away": None}},
        }]}
        assert fdo.parse_matches(payload)[0].is_void is expect_void, raw


def test_unknown_status_defaults_to_scheduled():
    payload = {"matches": [{
        "id": 1, "status": "SOME_NEW_STATUS",
        "utcDate": "2026-08-21T19:00:00Z",
        "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"},
        "score": {"fullTime": {"home": None, "away": None}},
    }]}
    assert fdo.parse_matches(payload)[0].status == FixtureStatus.SCHEDULED.value


def test_results_for_round_maps_and_marks_void():
    matches = fdo.parse_matches(PAYLOAD)
    fixtures = [
        ("Arsenal", "Coventry"),
        ("Man United", "Everton"),
        ("Chelsea", "Newcastle"),
    ]
    results = fdo.results_for_round(matches, fixtures, resolve)
    assert results[0] == (2, 0)      # completed
    assert results[1] is None        # VOID, not a zero-point miss
    assert 2 not in results          # not yet played -> absent, not scored


def test_half_finished_round_scores_nothing_for_unplayed_fixtures():
    """An unplayed fixture must be absent rather than counted as 0-0."""
    matches = fdo.parse_matches(PAYLOAD)
    results = fdo.results_for_round(
        matches, [("Chelsea", "Newcastle")], resolve
    )
    assert results == {}


def test_unknown_division_rejected():
    with pytest.raises(ValueError, match="No competition code"):
        fdo.fetch_matches("E2", 2026, "key")


def test_registry_gate(tmp_path):
    path = tmp_path / "registry.json"
    reg.save_registry(reg.load_registry(path), path)
    with pytest.raises(reg.SourceNotApprovedError):
        fdo.fetch_matches("E0", 2026, "key", registry_path=path)


def test_empty_payload():
    assert fdo.parse_matches({}) == []
    assert fdo.parse_matches({"matches": []}) == []


def test_current_matchday_is_the_next_scheduled_fixture():
    matches = fdo.parse_matches(PAYLOAD)
    assert fdo.current_matchday(matches) == 1


def test_current_matchday_skips_a_matchday_stuck_on_a_postponement():
    payload = {
        "matches": [
            {
                "id": 1, "status": "FINISHED", "matchday": 1,
                "utcDate": "2026-08-21T19:00:00Z",
                "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"},
                "score": {"fullTime": {"home": 1, "away": 0}},
            },
            {
                "id": 2, "status": "POSTPONED", "matchday": 1,
                "utcDate": "2026-08-22T14:00:00Z",
                "homeTeam": {"name": "C"}, "awayTeam": {"name": "D"},
                "score": {"fullTime": {"home": None, "away": None}},
            },
            {
                "id": 3, "status": "SCHEDULED", "matchday": 2,
                "utcDate": "2026-08-29T14:00:00Z",
                "homeTeam": {"name": "E"}, "awayTeam": {"name": "F"},
                "score": {"fullTime": {"home": None, "away": None}},
            },
        ]
    }
    matches = fdo.parse_matches(payload)
    assert fdo.current_matchday(matches) == 2


def test_current_matchday_none_when_nothing_scheduled():
    payload = {"matches": [{
        "id": 1, "status": "FINISHED", "matchday": 1,
        "utcDate": "2026-08-21T19:00:00Z",
        "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"},
        "score": {"fullTime": {"home": 1, "away": 0}},
    }]}
    matches = fdo.parse_matches(payload)
    assert fdo.current_matchday(matches) is None


def test_fixtures_for_matchday_filters_and_orders_by_kickoff():
    matches = fdo.parse_matches(PAYLOAD)
    day = fdo.fixtures_for_matchday(matches, 1)
    # 537001 is POSTPONED: it is not a fixture being played this matchday,
    # and its clubs must not be offered as Last Man Standing picks.
    assert [m.match_id for m in day] == ["537000", "537002"]


def test_fixtures_for_matchday_can_include_void_fixtures():
    matches = fdo.parse_matches(PAYLOAD)
    day = fdo.fixtures_for_matchday(matches, 1, include_void=True)
    assert [m.match_id for m in day] == ["537000", "537001", "537002"]


def test_upcoming_matchdays_are_ordered_by_kickoff_not_by_number():
    """A rearranged fixture can leave a lower matchday being played later;
    planning has to see them in the order they will be picked."""
    payload = {"matches": [
        {
            "id": 1, "status": "SCHEDULED", "matchday": 13,
            "utcDate": "2026-11-07T15:00:00Z",
            "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"},
            "score": {"fullTime": {"home": None, "away": None}},
        },
        {
            "id": 2, "status": "SCHEDULED", "matchday": 12,
            "utcDate": "2026-11-25T19:45:00Z",
            "homeTeam": {"name": "C"}, "awayTeam": {"name": "D"},
            "score": {"fullTime": {"home": None, "away": None}},
        },
        {
            "id": 3, "status": "SCHEDULED", "matchday": 14,
            "utcDate": "2026-11-14T15:00:00Z",
            "homeTeam": {"name": "E"}, "awayTeam": {"name": "F"},
            "score": {"fullTime": {"home": None, "away": None}},
        },
    ]}
    matches = fdo.parse_matches(payload)
    assert fdo.upcoming_matchdays(matches, 3) == [13, 14, 12]
    assert fdo.upcoming_matchdays(matches, 2) == [13, 14]
    assert fdo.current_matchday(matches) == 13


def test_upcoming_matchdays_empty_when_nothing_scheduled():
    payload = {"matches": [{
        "id": 1, "status": "FINISHED", "matchday": 1,
        "utcDate": "2026-08-21T19:00:00Z",
        "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"},
        "score": {"fullTime": {"home": 1, "away": 0}},
    }]}
    assert fdo.upcoming_matchdays(fdo.parse_matches(payload), 4) == []
