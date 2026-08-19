from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bym import db
from bym.fpl import harvest
from bym.sources import api_football
from bym.sources import quota as quota_mod
from bym.sources import registry as reg
from bym.sources.api_football import ApiFootballFixture, PlayerMatchStatsRecord
from bym.sources.quota import QuotaExceededError, RateLimitError

SEASON = 2022
COMPETITION = "E0"
NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "bym.db") as c:
        yield c


@pytest.fixture
def registry_path(tmp_path):
    path = tmp_path / "registry.json"
    reg.save_registry(reg.load_registry(path), path)
    reg.approve("api_football", path=path)
    return path


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.headers = {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _player_stats_payload():
    return {
        "response": [
            {
                "team": {"id": 1, "name": "TeamA"},
                "players": [
                    {
                        "player": {"id": 909, "name": "Cole Palmer"},
                        "statistics": [
                            {
                                "games": {"minutes": 90, "position": "M"},
                                "shots": {"total": 3, "on": 2},
                                "goals": {"total": 1, "assists": 1, "saves": None},
                                "tackles": {"total": 1, "blocks": 0, "interceptions": 0},
                                "cards": {"yellow": 0, "red": 0},
                                "penalty": {"scored": 0, "missed": 0, "saved": None},
                            }
                        ],
                    }
                ],
            }
        ],
    }


def mock_success_real_quota(monkeypatch):
    """Mocks only the HTTP dispatch, not `fetch_player_match_stats` —
    `_request`'s real registry gate and quota check/record still run,
    against whatever `quota_path`/`registry_path` the caller supplies.
    Needed for tests that assert on real quota progression, as opposed
    to the simpler `mock_success` (below) which bypasses quota
    bookkeeping entirely and is fine for tests that don't care."""
    monkeypatch.setattr(
        api_football.requests, "get",
        lambda *a, **k: _FakeResponse(_player_stats_payload()),
    )


def fx(fixture_id, home="Chelsea", away="Everton", kickoff="2022-08-06T14:00:00+00:00"):
    return ApiFootballFixture(
        fixture_id=fixture_id, home_team=home, away_team=away,
        kickoff_at=kickoff, league_id=39, season=SEASON,
    )


def palmer_record(**overrides):
    defaults = dict(
        fixture_id="fx1", team="Chelsea", provider_player_id="909",
        player_name="Cole Palmer", position_code="M", minutes=90, goals=1,
        assists=1, shots=3, saves=None, yellow_cards=0, red_cards=0,
        tackles=1, interceptions=0, blocks=0, penalties_scored=0,
        penalties_missed=0, penalties_saved=None,
    )
    defaults.update(overrides)
    return PlayerMatchStatsRecord(**defaults)


def test_seed_harvest_targets_creates_pending_rows(conn):
    added = harvest.seed_harvest_targets(conn, [fx("1"), fx("2")], COMPETITION)
    assert added == 2
    counts = harvest.harvest_status_counts(conn, SEASON, COMPETITION)
    assert counts == {"PENDING": 2}


def test_seed_harvest_targets_is_idempotent_and_never_resets_status(conn, monkeypatch):
    harvest.seed_harvest_targets(conn, [fx("1")], COMPETITION)
    monkeypatch.setattr(
        api_football, "fetch_player_match_stats",
        lambda fixture_id, api_key, **kw: [palmer_record()],
    )
    harvest.harvest_next_batch(conn, SEASON, COMPETITION, "fake-key", now=NOW)
    assert harvest.harvest_status_counts(conn, SEASON, COMPETITION) == {"FETCHED": 1}

    # Re-seeding the same fixture must not reset it back to PENDING.
    added = harvest.seed_harvest_targets(conn, [fx("1")], COMPETITION)
    assert added == 0
    assert harvest.harvest_status_counts(conn, SEASON, COMPETITION) == {"FETCHED": 1}


def test_harvest_next_batch_marks_a_successful_fixture_fetched(conn, monkeypatch):
    harvest.seed_harvest_targets(conn, [fx("1")], COMPETITION)
    monkeypatch.setattr(
        api_football, "fetch_player_match_stats",
        lambda fixture_id, api_key, **kw: [palmer_record()],
    )
    result = harvest.harvest_next_batch(conn, SEASON, COMPETITION, "fake-key", now=NOW)
    assert result.fetched == 1
    assert result.attempted == 1
    assert result.stopped_reason is None
    row = conn.execute("SELECT * FROM fpl_fixture_harvest_state").fetchone()
    assert row["player_stats_status"] == "FETCHED"
    assert row["row_count"] == 1
    assert row["content_hash"] is not None


def test_harvest_next_batch_marks_an_empty_response_as_no_data_not_fetched(conn, monkeypatch):
    harvest.seed_harvest_targets(conn, [fx("1")], COMPETITION)
    monkeypatch.setattr(
        api_football, "fetch_player_match_stats", lambda fixture_id, api_key, **kw: []
    )
    result = harvest.harvest_next_batch(conn, SEASON, COMPETITION, "fake-key", now=NOW)
    assert result.no_data == 1
    assert result.fetched == 0
    row = conn.execute("SELECT * FROM fpl_fixture_harvest_state").fetchone()
    assert row["player_stats_status"] == "NO_DATA"
    assert row["row_count"] == 0


def test_harvest_next_batch_never_refetches_a_fetched_fixture(conn, monkeypatch):
    harvest.seed_harvest_targets(conn, [fx("1")], COMPETITION)
    call_count = 0

    def fetch(fixture_id, api_key, **kw):
        nonlocal call_count
        call_count += 1
        return [palmer_record()]

    monkeypatch.setattr(api_football, "fetch_player_match_stats", fetch)
    harvest.harvest_next_batch(conn, SEASON, COMPETITION, "fake-key", now=NOW)
    harvest.harvest_next_batch(conn, SEASON, COMPETITION, "fake-key", now=NOW)
    assert call_count == 1  # the second batch found nothing PENDING left


def test_harvest_next_batch_stops_on_quota_exhaustion_without_failing_the_fixture(conn, monkeypatch):
    harvest.seed_harvest_targets(conn, [fx("1"), fx("2")], COMPETITION)

    def fetch(fixture_id, api_key, **kw):
        raise QuotaExceededError("quota spent")

    monkeypatch.setattr(api_football, "fetch_player_match_stats", fetch)
    result = harvest.harvest_next_batch(conn, SEASON, COMPETITION, "fake-key", now=NOW)
    assert result.attempted == 0
    assert result.stopped_reason == "quota spent"
    counts = harvest.harvest_status_counts(conn, SEASON, COMPETITION)
    assert counts == {"PENDING": 2}  # untouched, resumable tomorrow


def test_harvest_next_batch_retries_a_transient_failure_then_gives_up(conn, monkeypatch):
    harvest.seed_harvest_targets(conn, [fx("1")], COMPETITION)

    def fetch(fixture_id, api_key, **kw):
        raise RuntimeError("network blip")

    monkeypatch.setattr(api_football, "fetch_player_match_stats", fetch)
    for _ in range(harvest.MAX_ATTEMPTS - 1):
        result = harvest.harvest_next_batch(conn, SEASON, COMPETITION, "fake-key", now=NOW)
        assert result.retryable_failures == 1
        row = conn.execute("SELECT * FROM fpl_fixture_harvest_state").fetchone()
        assert row["player_stats_status"] == "RETRYABLE_FAILURE"

    # One more attempt reaches MAX_ATTEMPTS -> FINAL_FAILURE.
    result = harvest.harvest_next_batch(conn, SEASON, COMPETITION, "fake-key", now=NOW)
    assert result.final_failures == 1
    row = conn.execute("SELECT * FROM fpl_fixture_harvest_state").fetchone()
    assert row["player_stats_status"] == "FINAL_FAILURE"
    assert row["attempt_count"] == harvest.MAX_ATTEMPTS

    # A FINAL_FAILURE fixture is never picked up again.
    result = harvest.harvest_next_batch(conn, SEASON, COMPETITION, "fake-key", now=NOW)
    assert result.attempted == 0


def test_harvest_next_batch_paces_through_a_rate_limit_rather_than_stopping(conn, monkeypatch):
    """A per-minute RateLimitError is a pacing signal, not a budget
    exhaustion — the batch must wait it out and still fetch the fixture,
    not abandon the rest of the day's work over a one-minute cap."""
    harvest.seed_harvest_targets(conn, [fx("1")], COMPETITION)
    calls = {"n": 0}

    def fetch(fixture_id, api_key, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimitError("10/minute limit reached")
        return [palmer_record()]

    monkeypatch.setattr(api_football, "fetch_player_match_stats", fetch)
    slept = []
    monkeypatch.setattr(harvest.time, "sleep", lambda s: slept.append(s))

    result = harvest.harvest_next_batch(conn, SEASON, COMPETITION, "fake-key", now=NOW)
    assert result.fetched == 1
    assert result.stopped_reason is None
    assert len(slept) == 2  # paced through 2 rate-limit hits before success


def test_harvest_next_batch_gives_up_after_persistent_rate_limiting(conn, monkeypatch):
    harvest.seed_harvest_targets(conn, [fx("1"), fx("2")], COMPETITION)
    monkeypatch.setattr(
        api_football, "fetch_player_match_stats",
        lambda fixture_id, api_key, **kw: (_ for _ in ()).throw(RateLimitError("stuck")),
    )
    monkeypatch.setattr(harvest.time, "sleep", lambda s: None)

    result = harvest.harvest_next_batch(conn, SEASON, COMPETITION, "fake-key", now=NOW)
    assert result.attempted == 0
    assert result.stopped_reason == "stuck"
    # Neither fixture was marked failed — a rate limit is not a fixture-
    # level failure, and both stay resumable.
    assert harvest.harvest_status_counts(conn, SEASON, COMPETITION) == {"PENDING": 2}


def test_harvest_next_batch_respects_max_fixtures(conn, monkeypatch):
    harvest.seed_harvest_targets(conn, [fx("1"), fx("2"), fx("3")], COMPETITION)
    monkeypatch.setattr(
        api_football, "fetch_player_match_stats",
        lambda fixture_id, api_key, **kw: [palmer_record(fixture_id=fixture_id)],
    )
    result = harvest.harvest_next_batch(conn, SEASON, COMPETITION, "fake-key", max_fixtures=2, now=NOW)
    assert result.attempted == 2
    counts = harvest.harvest_status_counts(conn, SEASON, COMPETITION)
    assert counts == {"FETCHED": 2, "PENDING": 1}


# ---- daily orchestration ---------------------------------------------


def mock_success(monkeypatch):
    monkeypatch.setattr(
        api_football, "fetch_player_match_stats",
        lambda fixture_id, api_key, **kw: [palmer_record(fixture_id=fixture_id)],
    )


def test_is_season_complete_true_once_nothing_pending_or_retryable(conn, monkeypatch):
    harvest.seed_harvest_targets(conn, [fx("1")], COMPETITION)
    assert not harvest.is_season_complete(conn, SEASON, COMPETITION)
    mock_success(monkeypatch)
    harvest.harvest_next_batch(conn, SEASON, COMPETITION, "fake-key", now=NOW)
    assert harvest.is_season_complete(conn, SEASON, COMPETITION)


def test_harvest_complete_requires_every_season_done(conn, monkeypatch):
    harvest.seed_harvest_targets(conn, [fx("1")], "E0")
    mock_success(monkeypatch)
    harvest.harvest_next_batch(conn, 2022, "E0", "fake-key", now=NOW)
    assert not harvest.harvest_complete(conn, seasons=(2022, 2023), competition="E0")

    harvest.seed_harvest_targets(
        conn, [ApiFootballFixture("2", "A", "B", "2023-08-06T14:00:00+00:00", 39, 2023)], "E0"
    )
    harvest.harvest_next_batch(conn, 2023, "E0", "fake-key", now=NOW)
    assert harvest.harvest_complete(conn, seasons=(2022, 2023), competition="E0")


def test_run_daily_harvest_caps_total_requests_at_the_daily_cap(conn, monkeypatch, tmp_path):
    quota_path = tmp_path / "quota.json"
    quota_mod.record(api_football.QUOTA, 80, None, quota_path, NOW)  # already used today
    harvest.seed_harvest_targets(
        conn, [fx(str(i)) for i in range(10)], COMPETITION,
    )
    mock_success(monkeypatch)
    result = harvest.run_daily_harvest(
        conn, "fake-key", daily_cap=85, seasons=(SEASON,), competition=COMPETITION,
        now=NOW, quota_path=quota_path,
    )
    assert result.season_results[SEASON].attempted == 5  # 85 - 80 already used
    assert not result.all_seasons_complete


def test_run_daily_harvest_skips_a_season_already_complete(conn, monkeypatch, tmp_path):
    quota_path = tmp_path / "quota.json"
    mock_success(monkeypatch)
    harvest.seed_harvest_targets(conn, [fx("1")], COMPETITION)
    harvest.harvest_next_batch(conn, 2022, COMPETITION, "fake-key", now=NOW, quota_path=quota_path)
    assert harvest.is_season_complete(conn, 2022, COMPETITION)

    harvest.seed_harvest_targets(
        conn, [ApiFootballFixture("2", "A", "B", "2023-08-06T14:00:00+00:00", 39, 2023)], COMPETITION,
    )
    result = harvest.run_daily_harvest(
        conn, "fake-key", daily_cap=85, seasons=(2022, 2023), competition=COMPETITION,
        now=NOW, quota_path=quota_path,
    )
    assert 2022 not in result.season_results  # already complete, skipped entirely
    assert result.season_results[2023].attempted == 1
    assert result.all_seasons_complete


def test_run_daily_harvest_finishes_2022_before_starting_2023(conn, monkeypatch, tmp_path, registry_path):
    # Real quota tracking must fire here (not the fetch_player_match_stats-
    # level mock) — the whole point of this test is that hitting the
    # DAILY CAP mid-run stops 2023 from being touched at all, which only
    # a genuinely advancing `used()` count can demonstrate.
    quota_path = tmp_path / "quota.json"
    mock_success_real_quota(monkeypatch)
    harvest.seed_harvest_targets(conn, [fx(str(i)) for i in range(3)], COMPETITION)
    harvest.seed_harvest_targets(
        conn,
        [ApiFootballFixture(str(100 + i), "A", "B", "2023-08-06T14:00:00+00:00", 39, 2023)
         for i in range(3)],
        COMPETITION,
    )
    # Exactly enough budget for 2022/23's 3 fixtures and nothing else.
    result = harvest.run_daily_harvest(
        conn, "fake-key", daily_cap=3, seasons=(2022, 2023), competition=COMPETITION,
        now=NOW, quota_path=quota_path, registry_path=registry_path,
    )
    assert result.season_results[2022].attempted == 3
    assert 2023 not in result.season_results
    assert not result.all_seasons_complete
    assert result.requests_used_today_after == 3


def test_run_daily_harvest_spills_leftover_budget_into_the_next_season(conn, monkeypatch, tmp_path):
    quota_path = tmp_path / "quota.json"
    mock_success(monkeypatch)
    harvest.seed_harvest_targets(conn, [fx(str(i)) for i in range(2)], COMPETITION)
    harvest.seed_harvest_targets(
        conn,
        [ApiFootballFixture(str(100 + i), "A", "B", "2023-08-06T14:00:00+00:00", 39, 2023)
         for i in range(3)],
        COMPETITION,
    )
    result = harvest.run_daily_harvest(
        conn, "fake-key", daily_cap=10, seasons=(2022, 2023), competition=COMPETITION,
        now=NOW, quota_path=quota_path,
    )
    assert result.season_results[2022].attempted == 2  # finishes 2022 (only 2 existed)
    assert result.season_results[2023].attempted == 3  # then spends leftover budget on 2023
    assert result.all_seasons_complete


def test_harvest_next_batch_actually_clears_a_real_per_minute_limit_when_now_is_not_pinned(
    conn, monkeypatch, tmp_path, registry_path,
):
    """Regression for a real production bug: `harvest_next_batch` used to
    resolve `now` ONCE and reuse that single frozen instant for every
    fixture in the batch, including ones retried after a RateLimitError.
    That made every successful request record an IDENTICAL
    `recent_calls` timestamp, so the 11th request tripped the 10/minute
    cap and — because `now` never advanced — `sources.quota`'s 60-second
    window could never be seen to clear no matter how many real seconds
    `time.sleep` actually spent waiting. This drives the REAL quota
    machinery (not a request-count mock) with `now=None` — exactly how
    `scripts/fpl_daily_harvest.py` calls it — and a fake clock that
    genuinely advances across sleeps, proving the batch pushes past the
    cap instead of permanently stopping at exactly 10."""
    quota_path = tmp_path / "quota.json"
    mock_success_real_quota(monkeypatch)
    harvest.seed_harvest_targets(conn, [fx(str(i)) for i in range(15)], COMPETITION)

    clock = {"t": NOW}
    monkeypatch.setattr(harvest, "utcnow", lambda: clock["t"])
    monkeypatch.setattr(
        harvest.time, "sleep",
        lambda seconds: clock.__setitem__("t", clock["t"] + timedelta(seconds=seconds)),
    )

    result = harvest.harvest_next_batch(
        conn, SEASON, COMPETITION, "fake-key", now=None,
        quota_path=quota_path, registry_path=registry_path,
    )
    assert result.fetched == 15
    assert result.stopped_reason is None


def test_run_daily_harvest_runs_no_model_evaluation():
    """This module cannot accidentally score BASELINE_FPL_001 on
    completion — it never imports anything that could (no walk-forward
    harness, no metrics, no decision-quality module), so there is no
    call it could make even by a future one-line mistake going unnoticed
    in review."""
    source = Path(harvest.__file__).read_text()
    for forbidden in ("fpl_walk_forward", "fpl_metrics", "fpl_decision_quality"):
        assert forbidden not in source


def test_current_season_matches_the_lms_fd_season_convention():
    # Same month>=7 rollover as web._lms_fd_season: June still belongs to
    # the season that started the previous July/August.
    assert harvest.current_season(datetime(2026, 6, 30, tzinfo=timezone.utc)) == 2025
    # July starts the new season.
    assert harvest.current_season(datetime(2026, 7, 1, tzinfo=timezone.utc)) == 2026
    # A date shortly after New Year still belongs to the season that
    # started the previous year, not a fresh one.
    assert harvest.current_season(datetime(2027, 1, 15, tzinfo=timezone.utc)) == 2026


def test_harvest_seasons_never_reaches_the_providers_2025_cutoff():
    # api_football's free tier rejects 2025+ outright (registry notes) —
    # this is the guard against ever re-adding that mistake by widening
    # HARVEST_SEASONS past what the provider will actually serve.
    assert max(harvest.HARVEST_SEASONS) <= harvest.PROVIDER_MAX_FETCHABLE_SEASON
    assert harvest.PROVIDER_MAX_FETCHABLE_SEASON < 2025


def test_ensure_season_seeded_seeds_an_empty_season_from_one_request(conn, monkeypatch):
    fixtures = [fx(str(i)) for i in range(3)]
    monkeypatch.setattr(
        api_football, "fetch_fixtures",
        lambda competition, season, api_key, **kw: fixtures,
    )
    spent = harvest.ensure_season_seeded(conn, SEASON, COMPETITION, "fake-key", now=NOW)
    assert spent == 1
    assert harvest.harvest_status_counts(conn, SEASON, COMPETITION) == {"PENDING": 3}


def test_ensure_season_seeded_is_a_no_op_once_the_season_already_has_rows(conn, monkeypatch):
    harvest.seed_harvest_targets(conn, [fx("1")], COMPETITION)

    def fail_if_called(*a, **k):
        raise AssertionError("fetch_fixtures should not be called for an already-seeded season")

    monkeypatch.setattr(api_football, "fetch_fixtures", fail_if_called)
    spent = harvest.ensure_season_seeded(conn, SEASON, COMPETITION, "fake-key", now=NOW)
    assert spent == 0


def test_run_daily_harvest_auto_seeds_a_newly_added_season_then_harvests_it(conn, monkeypatch, tmp_path):
    """A season added to HARVEST_SEASONS (e.g. 2024/25 joining 2022/23 and
    2023/24) starts with zero rows in fpl_fixture_harvest_state — the
    daily driver must seed it itself rather than sitting idle forever
    waiting for a manual seeding step that was only ever run once."""
    quota_path = tmp_path / "quota.json"
    fixtures = [fx(str(i)) for i in range(2)]
    monkeypatch.setattr(
        api_football, "fetch_fixtures",
        lambda competition, season, api_key, **kw: fixtures,
    )
    mock_success(monkeypatch)
    result = harvest.run_daily_harvest(
        conn, "fake-key", daily_cap=85, seasons=(SEASON,), competition=COMPETITION,
        now=NOW, quota_path=quota_path,
    )
    assert harvest.harvest_status_counts(conn, SEASON, COMPETITION) == {"FETCHED": 2}
    assert result.season_results[SEASON].attempted == 2
    assert result.all_seasons_complete
