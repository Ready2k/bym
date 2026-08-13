"""Increment 2 acceptance tests.

Covers the listed cases: small move -> snapshot only; large move ->
reforecast; large move + same pick -> no change alert; large move +
different pick -> PREDICTION_CHANGED; stale bookmaker -> median stable,
dispersion up; all books move together -> consensus moves, dispersion
stays low; quota low -> cadence degrades; restart -> polling continues;
deadline passed -> polling stops.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bym import cadence, events, movement, scheduler, worker
from bym.model import dixon_coles
from bym.round import Fixture, Round
from bym.sources import pipeline
from bym.sources.consensus import build_consensus
from bym.sources.odds_api import BookmakerOdds, EventOdds

from test_dixon_coles import synthetic_matches

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
DEADLINE = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)

ROUND = Round(
    round_number=1,
    deadline_at=DEADLINE,
    fixtures=(Fixture("Strong", "Weak"),),
)

# A real round file must hold exactly six fixtures (§9.1). Only the first
# appears in the mocked feed; the rest legitimately report no market view.
ROUND_JSON = """{
  "round_number": 1,
  "deadline_at": "2026-08-15T14:00:00+00:00",
  "fixtures": [
    {"home": "Strong", "away": "Weak"},
    {"home": "Middling A", "away": "Middling B"},
    {"home": "Middling B", "away": "Middling A"},
    {"home": "Weak", "away": "Strong"},
    {"home": "Strong", "away": "Middling A"},
    {"home": "Weak", "away": "Middling B"}
  ]
}"""


def write_round(tmp_path: Path) -> Path:
    path = tmp_path / "round.json"
    path.write_text(ROUND_JSON)
    return path


@pytest.fixture(scope="module")
def params():
    return dixon_coles.fit(synthetic_matches(), xi=0.0)


@pytest.fixture
def router(params):
    return lambda home, away: params


def books(prices, minutes_old=5):
    ts = (NOW - timedelta(minutes=minutes_old)).isoformat().replace("+00:00", "Z")
    return [BookmakerOdds(f"book{i}", ts, h, d, a) for i, (h, d, a) in enumerate(prices)]


def feed(prices, ages=None):
    bookmakers = []
    for i, (h, d, a) in enumerate(prices):
        age = ages[i] if ages else 5
        ts = (NOW - timedelta(minutes=age)).isoformat().replace("+00:00", "Z")
        bookmakers.append(BookmakerOdds(f"book{i}", ts, h, d, a))
    return [
        EventOdds("e1", "soccer_epl", "2026-08-15T14:00:00Z", "Strong", "Weak", bookmakers)
    ]


def run_poll(monkeypatch, tmp_path, router, prices, ages=None, previous=None, now=NOW):
    monkeypatch.setattr(
        pipeline.odds_api, "fetch_odds", lambda division, key, **kw: feed(prices, ages)
    )
    monkeypatch.setattr(worker, "budget_constrained", lambda *a, **k: False)
    tmp_path.mkdir(parents=True, exist_ok=True)
    return worker.poll_round(
        ROUND,
        "fake-key",
        router,
        now=now,
        previous_picks=previous,
        consensus_path=tmp_path / "consensus.csv",
        odds_snapshot_path=tmp_path / "odds.csv",
        divisions=("E0",),
    )


def moved_forecast(monkeypatch, workdir, router):
    """TIGHT then MOVED in a clean directory; returns the recalculated
    forecast event so a test can set up a matching previous pick."""
    run_poll(monkeypatch, workdir, router, TIGHT)
    outcome = run_poll(
        monkeypatch, workdir, router, MOVED, now=NOW + timedelta(minutes=30)
    )
    return next(e for e in outcome.events if e.event_type == "FORECAST_RECALCULATED")


TIGHT = [(1.80, 3.50, 4.50), (1.82, 3.45, 4.40), (1.78, 3.55, 4.60)]
MOVED = [(1.40, 4.50, 7.00), (1.42, 4.40, 6.90), (1.38, 4.60, 7.10)]
NUDGED = [(1.79, 3.52, 4.52), (1.81, 3.47, 4.42), (1.77, 3.57, 4.62)]


def types(outcome):
    return [e.event_type for e in outcome.events]


def test_first_poll_snapshots_without_movement(monkeypatch, tmp_path, router):
    out = run_poll(monkeypatch, tmp_path, router, TIGHT)
    assert "MARKET_SNAPSHOT" in types(out)
    assert "MARKET_MOVEMENT" not in types(out)
    assert out.reforecast == 0  # no prior reference to move against


def test_small_move_is_snapshot_only(monkeypatch, tmp_path, router):
    run_poll(monkeypatch, tmp_path, router, TIGHT)
    out = run_poll(monkeypatch, tmp_path, router, NUDGED, now=NOW + timedelta(minutes=30))
    assert "MARKET_SNAPSHOT" in types(out)
    assert "MARKET_MOVEMENT" not in types(out)
    assert "FORECAST_RECALCULATED" not in types(out)
    assert out.reforecast == 0


def test_large_move_triggers_reforecast(monkeypatch, tmp_path, router):
    run_poll(monkeypatch, tmp_path, router, TIGHT)
    out = run_poll(monkeypatch, tmp_path, router, MOVED, now=NOW + timedelta(minutes=30))
    assert "MARKET_MAJOR_MOVEMENT" in types(out)
    assert "FORECAST_RECALCULATED" in types(out)
    assert out.reforecast == 1


def test_large_move_same_pick_emits_no_prediction_change(monkeypatch, tmp_path, router):
    rec = moved_forecast(monkeypatch, tmp_path / "discover", router)

    # Replay the same move in a clean directory, this time already holding
    # the pick it produces: the move is still MAJOR, but nothing changed.
    out_dir = tmp_path / "replay"
    run_poll(monkeypatch, out_dir, router, TIGHT)
    out = run_poll(
        monkeypatch, out_dir, router, MOVED,
        previous={("Strong", "Weak"): (rec.detail["pick"], rec.detail["confidence"])},
        now=NOW + timedelta(minutes=30),
    )
    assert "MARKET_MAJOR_MOVEMENT" in types(out)  # the move did happen
    assert "FORECAST_RECALCULATED" in types(out)  # and was acted on
    assert "PREDICTION_CHANGED" not in types(out)  # but the pick held
    assert "CONFIDENCE_CHANGED" not in types(out)


def test_large_move_different_pick_emits_prediction_changed(monkeypatch, tmp_path, router):
    run_poll(monkeypatch, tmp_path, router, TIGHT)
    out = run_poll(
        monkeypatch, tmp_path, router, MOVED,
        previous={("Strong", "Weak"): ("0-3", "LOW")},
        now=NOW + timedelta(minutes=30),
    )
    assert "PREDICTION_CHANGED" in types(out)
    changed = next(e for e in out.events if e.event_type == "PREDICTION_CHANGED")
    assert changed.detail["previous_pick"] == "0-3"
    assert changed.severity == "IMPORTANT"


def test_confidence_change_recorded_separately(monkeypatch, tmp_path, router):
    """§20/§68: probability and confidence are distinct dimensions."""
    rec = moved_forecast(monkeypatch, tmp_path / "discover", router)

    # Same pick, deliberately different confidence, so only the confidence
    # dimension has moved.
    other_conf = "LOW" if rec.detail["confidence"] != "LOW" else "HIGH"
    out_dir = tmp_path / "replay"
    run_poll(monkeypatch, out_dir, router, TIGHT)
    out = run_poll(
        monkeypatch, out_dir, router, MOVED,
        previous={("Strong", "Weak"): (rec.detail["pick"], other_conf)},
        now=NOW + timedelta(minutes=30),
    )
    assert "CONFIDENCE_CHANGED" in types(out)
    assert "PREDICTION_CHANGED" not in types(out)


def test_one_stale_book_keeps_median_stable(monkeypatch, tmp_path, router):
    """A single stale quote must not drag the consensus; it is excluded,
    and its absence shows up as a lower bookmaker count."""
    fresh = build_consensus(feed(TIGHT)[0], now=NOW)
    with_stale = build_consensus(
        feed(TIGHT + [(5.0, 4.0, 1.6)], ages=[5, 5, 5, 60 * 30])[0], now=NOW
    )
    assert with_stale.p_home == pytest.approx(fresh.p_home, abs=0.01)
    assert with_stale.stale_excluded == 1
    assert with_stale.bookmaker_count == fresh.bookmaker_count


def test_all_books_moving_together_keeps_dispersion_low(monkeypatch, tmp_path, router):
    before = build_consensus(feed(TIGHT)[0], now=NOW)
    after = build_consensus(feed(MOVED)[0], now=NOW)
    report = movement.compare(after, before.p_home, before.p_draw, before.p_away)
    assert report.materiality == "MAJOR"
    assert report.agreement == "HIGH"  # they all agree on the new price


def test_divergent_books_flagged_despite_same_consensus_move(monkeypatch, tmp_path, router):
    spread = [(1.40, 4.50, 7.00), (1.90, 3.40, 4.20), (1.38, 4.60, 7.10)]
    before = build_consensus(feed(TIGHT)[0], now=NOW)
    after = build_consensus(feed(spread)[0], now=NOW)
    report = movement.compare(after, before.p_home, before.p_draw, before.p_away)
    assert report.agreement in {"MEDIUM", "LOW"}


def test_quota_low_degrades_cadence(monkeypatch, tmp_path, router):
    monkeypatch.setattr(
        pipeline.odds_api, "fetch_odds", lambda division, key, **kw: feed(TIGHT)
    )
    monkeypatch.setattr(worker, "budget_constrained", lambda *a, **k: True)
    close_to_deadline = DEADLINE - timedelta(minutes=45)
    out = worker.poll_round(
        ROUND, "fake-key", router, now=close_to_deadline,
        consensus_path=tmp_path / "c.csv", odds_snapshot_path=tmp_path / "o.csv",
        divisions=("E0",),
    )
    assert out.next_run_at - close_to_deadline == timedelta(minutes=30)


def test_events_persist_to_log(tmp_path):
    log = tmp_path / "activity.jsonl"
    events.append(
        [
            events.make_event(
                events.EventType.MARKET_MOVEMENT,
                events.Severity.IMPORTANT,
                "Newcastle: 22.1% -> 28.7% (+6.6pp)",
                round_number=1,
                home_team="Chelsea",
                away_team="Newcastle",
                detail={"movement_strength_pp": 6.6, "market_agreement": "HIGH"},
            )
        ],
        log,
    )
    loaded = events.load(log)
    assert len(loaded) == 1
    assert loaded[0].fixture == "Chelsea v Newcastle"
    assert loaded[0].detail["market_agreement"] == "HIGH"
    assert "MARKET_MOVEMENT" in events.format_event(loaded[0])


# ---- scheduler integration ---------------------------------------------


def test_restart_continues_scheduled_polling(monkeypatch, tmp_path, router):
    db = tmp_path / "sched.db"
    monkeypatch.setattr(
        pipeline.odds_api, "fetch_odds", lambda division, key, **kw: feed(TIGHT)
    )
    monkeypatch.setattr(worker, "budget_constrained", lambda *a, **k: False)
    round_path = write_round(tmp_path)

    with scheduler.connect(db) as conn:
        scheduler.schedule_round(conn, 1, str(round_path), DEADLINE, first_run_at=NOW)
        scheduler.claim_due_jobs(conn, NOW)  # crash mid-run

    with scheduler.connect(db) as conn:  # restart
        outcomes = worker.run_due_jobs(
            conn, "fake-key", lambda: router, now=NOW,
            event_log=tmp_path / "events.jsonl",
            consensus_path=tmp_path / "c.csv",
            odds_snapshot_path=tmp_path / "o.csv",
            divisions=("E0",),
        )
        assert len(outcomes) == 1
        assert scheduler.get_job(conn, 1).status == "PENDING"  # rescheduled


def test_polling_stops_after_deadline(monkeypatch, tmp_path, router):
    db = tmp_path / "sched.db"
    round_path = write_round(tmp_path)
    with scheduler.connect(db) as conn:
        job = scheduler.schedule_round(
            conn, 1, str(round_path), DEADLINE, first_run_at=DEADLINE + timedelta(minutes=1)
        )
        outcomes = worker.run_due_jobs(
            conn, "fake-key", lambda: router, now=DEADLINE + timedelta(minutes=5),
            event_log=tmp_path / "events.jsonl",
        )
        assert outcomes == []
        assert scheduler.get_job(conn, job.id).status == "DONE"


def test_poll_failure_does_not_kill_the_loop(monkeypatch, tmp_path, router):
    db = tmp_path / "sched.db"
    round_path = write_round(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("API timeout")

    monkeypatch.setattr(pipeline.odds_api, "fetch_odds", boom)
    log = tmp_path / "events.jsonl"
    with scheduler.connect(db) as conn:
        job = scheduler.schedule_round(conn, 1, str(round_path), DEADLINE, first_run_at=NOW)
        worker.run_due_jobs(conn, "fake-key", lambda: router, now=NOW, event_log=log)
        reloaded = scheduler.get_job(conn, job.id)
        assert reloaded.status == "PENDING"  # retried, not abandoned
        assert "API timeout" in reloaded.last_error
    assert any(e.event_type == "POLL_FAILED" for e in events.load(log))


def test_a_sweep_is_skipped_rather_than_paid_for_when_the_model_is_silent(monkeypatch):
    """Every recorded extraction failure connected fine and then timed out,
    so the searches were paid for and the claims never arrived. The credits
    go before the model is ever asked to read anything."""
    import inspect

    from bym import worker

    source = inspect.getsource(worker._run_knowledge_job)
    assert "extractor_is_responsive" in source
    # And the skip must come before any research call spends credits.
    assert source.index("extractor_is_responsive") < source.index("knowledge_worker.research")


def test_an_extractor_with_no_ollama_config_is_treated_as_responsive():
    """The null extractor costs nothing and has nothing to probe."""
    from bym.worker import extractor_is_responsive

    assert extractor_is_responsive(None) is True
    assert extractor_is_responsive(object()) is True


UNRESOLVED_ROUND_JSON = ROUND_JSON.replace(
    '{"home": "Strong", "away": "Weak"}', '{"home": "Strong FC", "away": "weak"}'
)


def test_scheduled_polling_resolves_club_names(monkeypatch, tmp_path, router):
    """The scheduler stores a path, so the round file is re-read on every
    poll with names that have never been resolved. This loop runs
    unattended — an unresolved club would reforecast from a league-average
    stand-in indefinitely, with nobody there to read a warning."""
    seen: list[tuple[str, str]] = []

    real_poll = worker.poll_round

    def spy(rnd, *a, **kw):
        seen.extend((f.home, f.away) for f in rnd.fixtures)
        return real_poll(rnd, *a, **kw)

    monkeypatch.setattr(worker, "poll_round", spy)
    monkeypatch.setattr(
        pipeline.odds_api, "fetch_odds", lambda division, key, **kw: feed(TIGHT)
    )
    monkeypatch.setattr(worker, "budget_constrained", lambda *a, **k: False)

    round_path = tmp_path / "round.json"
    round_path.write_text(UNRESOLVED_ROUND_JSON)

    with scheduler.connect(tmp_path / "sched.db") as conn:
        scheduler.schedule_round(conn, 1, str(round_path), DEADLINE, first_run_at=NOW)
        worker.run_due_jobs(
            conn, "fake-key", lambda: router, now=NOW,
            event_log=tmp_path / "events.jsonl",
            consensus_path=tmp_path / "c.csv",
            odds_snapshot_path=tmp_path / "o.csv",
            divisions=("E0",),
            known_teams={"Strong", "Weak", "Middling A", "Middling B"},
        )

    assert seen, "poll_round was never reached"
    assert ("Strong", "Weak") == seen[0]
    assert "Strong FC" not in [team for pair in seen for team in pair]


def test_polling_without_known_teams_is_left_untouched(monkeypatch, tmp_path, router):
    """Callers that cannot supply a corpus still get the old behaviour
    rather than a crash — resolution is additive."""
    seen: list[tuple[str, str]] = []
    real_poll = worker.poll_round

    def spy(rnd, *a, **kw):
        seen.extend((f.home, f.away) for f in rnd.fixtures)
        return real_poll(rnd, *a, **kw)

    monkeypatch.setattr(worker, "poll_round", spy)
    monkeypatch.setattr(
        pipeline.odds_api, "fetch_odds", lambda division, key, **kw: feed(TIGHT)
    )
    monkeypatch.setattr(worker, "budget_constrained", lambda *a, **k: False)

    round_path = tmp_path / "round.json"
    round_path.write_text(UNRESOLVED_ROUND_JSON)

    with scheduler.connect(tmp_path / "sched.db") as conn:
        scheduler.schedule_round(conn, 1, str(round_path), DEADLINE, first_run_at=NOW)
        worker.run_due_jobs(
            conn, "fake-key", lambda: router, now=NOW,
            event_log=tmp_path / "events.jsonl",
            consensus_path=tmp_path / "c.csv",
            odds_snapshot_path=tmp_path / "o.csv",
            divisions=("E0",),
        )
    assert ("Strong FC", "weak") == seen[0]
