import pytest

from bym import db
from bym.fpl import corpus_report, player_store
from bym.fpl.player_match_stats import PlayerMatchStats, record_match_stats
from bym.fpl.players import FplPlayerSeason, Position

SEASON = 2022


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "bym.db") as c:
        yield c


def _make_player(conn, name, position, external_ids=None):
    player, _ = player_store.get_or_create_player(conn, name)
    player_store.record_player_season(
        conn,
        FplPlayerSeason(
            player_id=player.id, season=SEASON, team="Chelsea", position=position,
            external_ids=external_ids or {},
        ),
    )
    return player


def _stat(player_id, fixture_id, **overrides):
    defaults = dict(
        player_id=player_id, fixture_id=fixture_id, season=SEASON,
        source_id="api_football", source_record_version=f"fixture:{fixture_id}",
        observed_at="2026-08-17T00:00:00+00:00", minutes=90, goals=0, assists=0,
    )
    defaults.update(overrides)
    return PlayerMatchStats(**defaults)


def test_field_completeness_reports_100_percent_for_not_null_fields(conn):
    p = _make_player(conn, "A", Position.MID.value, {"api_football": "1"})
    record_match_stats(conn, _stat(p.id, "fx1", minutes=90, goals=1, assists=0))
    report = corpus_report.field_completeness(conn, SEASON)
    assert report.minutes_pct == 100.0
    assert report.goals_pct == 100.0
    assert report.assists_pct == 100.0


def test_field_completeness_measures_nullable_field_coverage(conn):
    p1 = _make_player(conn, "A", Position.DEF.value, {"api_football": "1"})
    p2 = _make_player(conn, "B", Position.DEF.value, {"api_football": "2"})
    record_match_stats(conn, _stat(p1.id, "fx1", tackles=2, interceptions=1, shots=1))
    record_match_stats(conn, _stat(p2.id, "fx1", tackles=None, interceptions=None, shots=None))
    report = corpus_report.field_completeness(conn, SEASON)
    assert report.tackles_pct == pytest.approx(50.0)
    assert report.interceptions_pct == pytest.approx(50.0)
    assert report.shots_pct == pytest.approx(50.0)


def test_field_completeness_saves_only_measured_over_gk_rows(conn):
    gk = _make_player(conn, "Keeper", Position.GK.value, {"api_football": "1"})
    outfield = _make_player(conn, "Outfielder", Position.MID.value, {"api_football": "2"})
    record_match_stats(conn, _stat(gk.id, "fx1", saves=3))
    record_match_stats(conn, _stat(outfield.id, "fx1", saves=None))  # expected, not a gap
    report = corpus_report.field_completeness(conn, SEASON)
    assert report.n_gk_rows == 1
    assert report.saves_pct_of_gk_rows == 100.0  # the outfield None must not drag this down


def test_field_completeness_computes_player_id_resolution_rate(conn):
    resolved = _make_player(conn, "A", Position.MID.value, {"api_football": "1"})
    unresolved = _make_player(conn, "B", Position.MID.value, {})  # no provider id
    record_match_stats(conn, _stat(resolved.id, "fx1"))
    record_match_stats(conn, _stat(unresolved.id, "fx1"))
    report = corpus_report.field_completeness(conn, SEASON)
    assert report.player_id_resolved_pct == pytest.approx(50.0)


def test_field_completeness_handles_an_empty_season(conn):
    report = corpus_report.field_completeness(conn, SEASON)
    assert report.n_rows == 0
    import math
    assert math.isnan(report.minutes_pct)


def test_gate_check_fails_on_zero_rows():
    report = corpus_report.SeasonFieldCompleteness(
        season=SEASON, n_rows=0, n_players=0, n_gk_rows=0,
        minutes_pct=float("nan"), starts_pct=float("nan"), goals_pct=float("nan"),
        assists_pct=float("nan"), saves_pct_of_gk_rows=float("nan"),
        shots_pct=float("nan"), tackles_pct=float("nan"), interceptions_pct=float("nan"),
        player_id_resolved_pct=float("nan"),
    )
    result = corpus_report.gate_check([report])
    assert not result.passed
    assert "no rows harvested" in result.reasons[0]


def test_gate_check_fails_on_poor_identity_resolution():
    report = corpus_report.SeasonFieldCompleteness(
        season=SEASON, n_rows=100, n_players=50, n_gk_rows=5,
        minutes_pct=100.0, starts_pct=80.0, goals_pct=100.0, assists_pct=100.0,
        saves_pct_of_gk_rows=100.0, shots_pct=90.0, tackles_pct=90.0,
        interceptions_pct=90.0, player_id_resolved_pct=60.0,  # below threshold
    )
    result = corpus_report.gate_check([report])
    assert not result.passed
    assert any("player_id_resolved" in r for r in result.reasons)


def test_gate_check_does_not_fail_on_poor_shots_tackles_coverage():
    """§18: BASELINE_FPL_001 does not read shots/tackles/interceptions at
    all — however poor their coverage, it cannot fail the gate."""
    report = corpus_report.SeasonFieldCompleteness(
        season=SEASON, n_rows=100, n_players=50, n_gk_rows=5,
        minutes_pct=100.0, starts_pct=80.0, goals_pct=100.0, assists_pct=100.0,
        saves_pct_of_gk_rows=100.0, shots_pct=0.0, tackles_pct=0.0,
        interceptions_pct=0.0, player_id_resolved_pct=100.0,
    )
    result = corpus_report.gate_check([report])
    assert result.passed


def test_gate_check_passes_with_healthy_coverage():
    report = corpus_report.SeasonFieldCompleteness(
        season=SEASON, n_rows=100, n_players=50, n_gk_rows=5,
        minutes_pct=100.0, starts_pct=80.0, goals_pct=100.0, assists_pct=100.0,
        saves_pct_of_gk_rows=100.0, shots_pct=95.0, tackles_pct=95.0,
        interceptions_pct=95.0, player_id_resolved_pct=99.0,
    )
    result = corpus_report.gate_check([report])
    assert result.passed
    assert result.reasons == []


def test_render_markdown_includes_both_seasons_and_the_verdict():
    reports = [
        corpus_report.SeasonFieldCompleteness(
            season=2022, n_rows=100, n_players=50, n_gk_rows=5,
            minutes_pct=100.0, starts_pct=80.0, goals_pct=100.0, assists_pct=100.0,
            saves_pct_of_gk_rows=100.0, shots_pct=95.0, tackles_pct=95.0,
            interceptions_pct=95.0, player_id_resolved_pct=99.0,
        ),
        corpus_report.SeasonFieldCompleteness(
            season=2023, n_rows=100, n_players=50, n_gk_rows=5,
            minutes_pct=100.0, starts_pct=80.0, goals_pct=100.0, assists_pct=100.0,
            saves_pct_of_gk_rows=100.0, shots_pct=95.0, tackles_pct=95.0,
            interceptions_pct=95.0, player_id_resolved_pct=99.0,
        ),
    ]
    gate = corpus_report.gate_check(reports)
    markdown = corpus_report.render_markdown(reports, gate)
    assert "2022/23" in markdown
    assert "2023/24" in markdown
    assert "PASSED" in markdown
    assert "player ID resolved" in markdown
