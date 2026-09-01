"""Cautious fixed-squad estimates when no current player feed is permitted.

This is *not* ``BASELINE_FPL_001`` and it never produces a transfer
recommendation.  It is a transparent bridge for a manager-provided current
fifteen: where a player has an unambiguous historical identity we use their
stored historical rates; otherwise we use a cohort-level positional prior and
make the uncertainty conspicuous.  Current injuries, current minutes, FPL
form, price changes and opponent-strength adjustments are intentionally not
claimed by this small fallback.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .lineup import recommend_lineup
from .lineup_state import LineupPlayer, LineupState
from .optimiser import OptimiserCandidate
from .players import Position
from .projection import HistoricalProfile, historical_profile


MODEL_VERSION = "MANUAL_SQUAD_PRIOR_001"

# These aliases are an explicit interpretation of the manager-visible FPL
# names, not a fuzzy name match.  A new/ambiguous player is deliberately left
# unmapped and receives the position prior instead of risking a wrong profile.
MANUAL_NAME_ALIASES = {
    "B. Fernandes": "Bruno Fernandes",
    "Guéhi": "Marc Guéhi",
    "Konsa": "Ezri Konsa",
    "Anderson": "Elliot Anderson",
    "Mbeumo": "Bryan Mbeumo",
    "Wilson": "Harry Wilson",
    "Haaland": "Erling Haaland",
    "Calvert-Lewin": "Dominic Calvert-Lewin",
    "Palmer": "Alex Palmer",
    "Davis": "Leif Davis",
    "Gibbs-White": "Morgan Gibbs-White",
    "Scarlett": "Dane Scarlett",
}

# A simple home/away modifier is all that can be supported by the visible
# fixture string without a current team-strength source.  It is intentionally
# small; this estimate must not pretend to know the opponent's 2026/27 level.
HOME_FACTOR = 1.05
AWAY_FACTOR = 0.95

# An empirical clean-sheet base is necessary because the historical free feed
# does not hold a per-player clean-sheet field.  This is a broad league prior,
# not a claim about the player's current club or opponent.
BASE_CLEAN_SHEET_PROBABILITY = 0.25


@dataclass(frozen=True)
class _Rates:
    p_appearance: float
    p_60_plus: float
    goal_rate: float
    assist_rate: float
    saves_rate: float
    sample_fixtures: int


@dataclass(frozen=True)
class PlayerEstimate:
    player_id: int
    name: str
    expected_points: float
    p_appearance: float
    evidence: str
    uncertainty: str
    historical_fixtures: int


def _fixture_factor(fixture: str) -> float:
    return HOME_FACTOR if fixture.strip().upper().endswith("(H)") else AWAY_FACTOR


def _profile_rates(profile: HistoricalProfile) -> _Rates | None:
    if profile.team_fixtures_sampled == 0:
        return None
    return _Rates(
        p_appearance=profile.p_appearance,
        p_60_plus=profile.p_60_plus,
        goal_rate=profile.total_goals / profile.team_fixtures_sampled,
        assist_rate=profile.total_assists / profile.team_fixtures_sampled,
        saves_rate=profile.saves_per_team_fixture,
        sample_fixtures=profile.team_fixtures_sampled,
    )


def _player_rates(conn: sqlite3.Connection, player_id: int) -> _Rates | None:
    """Pool a known player's stored PL seasons without pretending they are current.

    ``team`` is read at fixture time, so a historical transfer cannot silently
    recast old performances as belonging to the player's current club.
    """
    rows = conn.execute(
        "SELECT DISTINCT season, team FROM fpl_player_match_stats "
        "WHERE player_id = ? AND team IS NOT NULL ORDER BY season, team",
        (player_id,),
    ).fetchall()
    profiles = [
        _profile_rates(historical_profile(conn, player_id, int(row["season"]), row["team"]))
        for row in rows
    ]
    usable = [profile for profile in profiles if profile is not None]
    if not usable:
        return None
    fixtures = sum(profile.sample_fixtures for profile in usable)
    if fixtures == 0:
        return None
    return _Rates(
        p_appearance=sum(profile.p_appearance * profile.sample_fixtures for profile in usable) / fixtures,
        p_60_plus=sum(profile.p_60_plus * profile.sample_fixtures for profile in usable) / fixtures,
        goal_rate=sum(profile.goal_rate * profile.sample_fixtures for profile in usable) / fixtures,
        assist_rate=sum(profile.assist_rate * profile.sample_fixtures for profile in usable) / fixtures,
        saves_rate=sum(profile.saves_rate * profile.sample_fixtures for profile in usable) / fixtures,
        sample_fixtures=fixtures,
    )


def _position_prior(conn: sqlite3.Connection, position: str) -> _Rates:
    """Return a cohort prior from the final historical season, with a safe
    fixed fallback for an empty test/new database."""
    rows = conn.execute(
        "SELECT player_id, team FROM fpl_player_seasons "
        "WHERE season = 2024 AND position = ? ORDER BY player_id",
        (position,),
    ).fetchall()
    profiles = [_profile_rates(historical_profile(conn, int(row["player_id"]), 2024, row["team"])) for row in rows]
    usable = [profile for profile in profiles if profile is not None]
    if usable:
        count = len(usable)
        return _Rates(
            p_appearance=sum(profile.p_appearance for profile in usable) / count,
            p_60_plus=sum(profile.p_60_plus for profile in usable) / count,
            goal_rate=sum(profile.goal_rate for profile in usable) / count,
            assist_rate=sum(profile.assist_rate for profile in usable) / count,
            saves_rate=sum(profile.saves_rate for profile in usable) / count,
            sample_fixtures=0,
        )
    # This is only reached in a new/empty local database.  It keeps the
    # fixed-squad solver usable in tests, but remains VERY_HIGH uncertainty.
    return _Rates(0.55, 0.40, 0.05 if position == "FWD" else 0.03, 0.03, 0.75 if position == "GK" else 0.0, 0)


def _expected_points(rates: _Rates, player: LineupPlayer) -> float:
    position = player.position
    factor = _fixture_factor(player.fixture)
    appearance = rates.p_60_plus * 2 + max(0.0, rates.p_appearance - rates.p_60_plus)
    goal_points = {"GK": 10, "DEF": 6, "MID": 5, "FWD": 4}[position]
    clean_points = {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0}[position]
    return factor * (
        appearance
        + rates.goal_rate * goal_points
        + rates.assist_rate * 3
        + (BASE_CLEAN_SHEET_PROBABILITY * rates.p_60_plus * clean_points)
        + (rates.saves_rate / 3 if position == Position.GK.value else 0.0)
    )


def estimate_lineup(conn: sqlite3.Connection, state: LineupState) -> dict:
    """Estimate and choose only from the manager's current fifteen.

    The returned selection is intentionally transient: it is recomputed from
    the immutable manual snapshot and never written as a frozen recommendation
    or transfer plan.
    """
    players: list[PlayerEstimate] = []
    candidates: list[OptimiserCandidate] = []
    for number, player in enumerate(state.players, start=1):
        canonical = MANUAL_NAME_ALIASES.get(player.name)
        row = None if canonical is None else conn.execute(
            "SELECT id FROM fpl_players WHERE canonical_name = ? COLLATE NOCASE", (canonical,)
        ).fetchone()
        rates = _player_rates(conn, int(row["id"])) if row is not None else None
        if rates is None:
            rates = _position_prior(conn, player.position)
            evidence = "position prior"
            uncertainty = "VERY HIGH"
            history = 0
        else:
            evidence = f"historical profile ({rates.sample_fixtures} team fixtures)"
            uncertainty = "MODERATE" if rates.sample_fixtures >= 60 else "HIGH"
            history = rates.sample_fixtures
        expected = _expected_points(rates, player)
        players.append(PlayerEstimate(number, player.name, expected, rates.p_appearance, evidence, uncertainty, history))
        candidates.append(OptimiserCandidate(number, player.position, player.club, player.current_price, expected, rates.p_appearance))

    selection = recommend_lineup(candidates)
    names = {estimate.player_id: estimate.name for estimate in players}
    by_id = {estimate.player_id: estimate for estimate in players}
    prior_players = [
        estimate.name for estimate in players if estimate.evidence == "position prior"
    ]
    starter_reasons = [
        {
            "name": names[player_id],
            "reason": (
                f"Selected in the legal XI on {by_id[player_id].expected_points:.2f} "
                "provisional estimated points."
            ),
        }
        for player_id in sorted(selection.starter_ids, key=lambda player_id: names[player_id])
    ]
    bench_reasons = [
        {
            "name": names[player_id],
            "reason": (
                f"Bench {index}: lower provisional value after the XI and "
                "appearance-contingency check."
            ),
        }
        for index, player_id in enumerate(selection.bench_order, start=1)
    ]
    captain = by_id[selection.captain_id]
    vice = by_id[selection.vice_captain_id]
    downside = [
        {
            "title": "Not a live player forecast",
            "detail": (
                "Current form, injuries, predicted lineups, opponent strength and "
                "FPL bonus/DefCon are absent from this estimate."
            ),
        },
        {
            "title": "No market-wide transfer claim",
            "detail": "Only the existing fifteen are considered; the named shortlist remains separate.",
        },
        {
            "title": "Captain risk",
            "detail": (
                f"{captain.name} leads {vice.name} by only "
                f"{captain.expected_points - vice.expected_points:.2f} provisional points; "
                "that margin is not calibrated."
            ),
        },
    ]
    if prior_players:
        downside.append(
            {
                "title": "Position-prior players",
                "detail": (
                    f"{', '.join(prior_players)} lack a usable stored Premier League "
                    "profile and are ranked with VERY HIGH uncertainty."
                ),
            }
        )
    return {
        "model_version": MODEL_VERSION,
        "scope": "CURRENT SQUAD ONLY",
        "message": (
            "Provisional historical-rate estimate. It uses no current player-form, "
            "injury, minutes, opponent-strength or full-market data; uncertainty is "
            "therefore deliberately wide."
        ),
        "players": [
            {
                "name": estimate.name,
                "expected_points": round(estimate.expected_points, 2),
                "p_appearance": round(estimate.p_appearance, 3),
                "evidence": estimate.evidence,
                "uncertainty": estimate.uncertainty,
                "historical_fixtures": estimate.historical_fixtures,
            }
            for estimate in sorted(players, key=lambda estimate: (-estimate.expected_points, estimate.name))
        ],
        "starters": [names[player_id] for player_id in sorted(selection.starter_ids)],
        "bench": [names[player_id] for player_id in selection.bench_order],
        "captain": names[selection.captain_id],
        "vice_captain": names[selection.vice_captain_id],
        "reasoning": {
            "starters": starter_reasons,
            "bench": bench_reasons,
            "captain": (
                f"{captain.name} has the highest provisional estimate among the legal XI "
                f"({captain.expected_points:.2f})."
            ),
            "vice_captain": (
                f"{vice.name} is the next best captaincy contingency in this provisional XI "
                f"({vice.expected_points:.2f})."
            ),
        },
        "downside": downside,
    }
