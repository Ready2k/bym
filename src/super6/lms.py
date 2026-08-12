"""Last Man Standing recommender and survival tracker.

Adapts the Super 6 forecasting core (`model/dixon_coles.py`,
`model/matrix.py`) to a different competition format:

  - Each round, an entrant picks exactly one club.
  - The entrant survives only if that club WINS — a draw eliminates just
    like a loss, there is no partial credit.
  - A club may be picked at most once per entrant for the whole
    competition.

Nothing about the model is duplicated: P(win) for a candidate club is
just `p_home_win` / `p_away_win` off the same fitted score matrix that
Super 6 uses for its exact-score forecast. What's new here is LMS-specific
bookkeeping — excluding already-used clubs, ranking by win probability,
and tracking one entrant's survival round over round — none of which
exists anywhere else in this codebase.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import DEFAULT_MODEL, ModelConfig
from .model import market as market_mod
from .model.dixon_coles import DixonColesParams
from .model.matrix import score_matrix

ParamRouter = Callable[[str, str], DixonColesParams]

DEFAULT_STATE_PATH = Path("data/lms_state.json")


# ---------------------------------------------------------------------------
# Gameweek input — deliberately not Super 6's `Round`: a Last Man Standing
# gameweek is however many fixtures the Premier League actually plays that
# week, not a fixed six.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GameweekFixture:
    home: str
    away: str
    odds: tuple[float, float, float] | None = None  # (H, D, A) decimal odds


@dataclass(frozen=True)
class Gameweek:
    number: int
    fixtures: tuple[GameweekFixture, ...]
    deadline_at: datetime | None = None


def load_gameweek(path: Path) -> Gameweek:
    return gameweek_from_dict(json.loads(Path(path).read_text()))


def gameweek_from_dict(raw: dict) -> Gameweek:
    def parse_fixture(f: dict) -> GameweekFixture:
        odds = f.get("odds")
        if odds is not None:
            if len(odds) != 3 or any(o <= 1.0 for o in odds):
                raise ValueError(
                    f"{f['home']} v {f['away']}: odds must be three decimal "
                    f"odds > 1.0 as [home, draw, away], got {odds}"
                )
            odds = (float(odds[0]), float(odds[1]), float(odds[2]))
        return GameweekFixture(f["home"], f["away"], odds)

    fixtures = tuple(parse_fixture(f) for f in raw["fixtures"])
    if not fixtures:
        raise ValueError("A gameweek needs at least one fixture")
    seen: set[str] = set()
    for f in fixtures:
        for team in (f.home, f.away):
            key = team.lower()
            if key in seen:
                raise ValueError(f"{team} appears in two fixtures the same gameweek")
            seen.add(key)
    deadline = raw.get("deadline_at")
    if deadline:
        deadline = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
    return Gameweek(number=int(raw["gameweek"]), fixtures=fixtures, deadline_at=deadline)


# ---------------------------------------------------------------------------
# Recommendation — rank every still-available club by P(win)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PickCandidate:
    team: str
    opponent: str
    is_home: bool
    p_win: float
    p_draw: float
    p_loss: float
    confidence: str

    @property
    def fixture_label(self) -> str:
        return f"{self.team} {'v' if self.is_home else '@'} {self.opponent}"


def _confidence(p_win: float, cfg: ModelConfig) -> str:
    # Reuses Super 6's RESULT confidence banding (§20 CONF v1) — it is
    # exactly the same quantity (a result probability) read for a
    # different purpose.
    if p_win >= cfg.conf_high_result_p:
        return "HIGH"
    if p_win < cfg.conf_low_result_p:
        return "LOW"
    return "MEDIUM"


def recommend_gameweek(
    params: DixonColesParams | ParamRouter,
    gw: Gameweek,
    used_clubs: set[str] | frozenset[str] = frozenset(),
    cfg: ModelConfig = DEFAULT_MODEL,
) -> list[PickCandidate]:
    """Every club still eligible this gameweek, ranked by P(win) descending.

    `params` is either one fitted parameter set or a (home, away) -> params
    router, same convention as `recommend.recommend_round`. A club already
    in `used_clubs` (matched case-insensitively) is excluded entirely — the
    one-club-once rule — rather than merely flagged, so the returned list
    is always a legal set of picks.
    """
    router: ParamRouter = params if callable(params) else (lambda h, a: params)
    used_lower = {c.lower() for c in used_clubs}
    candidates: list[PickCandidate] = []
    for f in gw.fixtures:
        p = router(f.home, f.away)
        lam_h, lam_a = p.lambdas(f.home, f.away, cfg)
        matrix = score_matrix(lam_h, lam_a, p.rho, cfg)
        if f.odds is not None and cfg.market_blend_weight is not None:
            mh, md, ma = market_mod.implied_result_probs(*f.odds)
            matrix = market_mod.blend_matrix(matrix, mh, md, ma, cfg.market_blend_weight)
        p_h, p_d, p_a = matrix.p_home_win, matrix.p_draw, matrix.p_away_win
        if f.home.lower() not in used_lower:
            candidates.append(
                PickCandidate(f.home, f.away, True, p_h, p_d, p_a, _confidence(p_h, cfg))
            )
        if f.away.lower() not in used_lower:
            candidates.append(
                PickCandidate(f.away, f.home, False, p_a, p_d, p_h, _confidence(p_a, cfg))
            )
    candidates.sort(key=lambda c: c.p_win, reverse=True)
    return candidates


def format_recommendations(
    gw: Gameweek,
    candidates: list[PickCandidate],
    used_clubs: set[str] | frozenset[str] = frozenset(),
    top_n: int = 10,
) -> str:
    lines = [f"LAST MAN STANDING — GAMEWEEK {gw.number}", ""]
    if used_clubs:
        lines.append(f"Already used ({len(used_clubs)}): {', '.join(sorted(used_clubs))}")
        lines.append("")
    if not candidates:
        lines.append(
            "No eligible clubs this gameweek — every fixture involves a "
            "club you've already used."
        )
        return "\n".join(lines)
    lines.append(
        f"{'TEAM':20s} {'FIXTURE':28s} {'P(WIN)':>7s} {'P(DRAW)':>8s} "
        f"{'P(LOSS)':>8s}  CONF"
    )
    for c in candidates[:top_n]:
        lines.append(
            f"{c.team[:20]:20s} {c.fixture_label[:28]:28s} {c.p_win:>6.1%} "
            f"{c.p_draw:>7.1%} {c.p_loss:>7.1%}  {c.confidence}"
        )
    best = candidates[0]
    lines += [
        "",
        f"Recommended pick: {best.team} ({best.fixture_label}) — "
        f"{best.p_win:.1%} to win",
    ]
    if best.confidence != "HIGH":
        lines.append(
            "  (no HIGH-confidence pick available this gameweek — check "
            "the alternatives above before committing a strong future club)"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Survival state — one entrant's used clubs and round-by-round history
# ---------------------------------------------------------------------------


@dataclass
class LMSState:
    entrant: str
    alive: bool = True
    used_clubs: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)


def load_states(path: Path = DEFAULT_STATE_PATH) -> dict[str, LMSState]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {k: LMSState(**v) for k, v in raw.items()}


def save_states(states: dict[str, LMSState], path: Path = DEFAULT_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: vars(v) for k, v in states.items()}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def get_state(entrant: str, path: Path = DEFAULT_STATE_PATH) -> LMSState:
    return load_states(path).get(entrant, LMSState(entrant=entrant))


def record_pick(
    state: LMSState, gameweek: int, team: str, opponent: str, is_home: bool
) -> LMSState:
    """Book a pick against the entrant's used-club list.

    Refuses a club already used (the format's core constraint) and refuses
    a second pick for a gameweek already recorded — both are mistakes in
    the caller, not legal state transitions.
    """
    if not state.alive:
        raise ValueError(f"{state.entrant} is already eliminated — no further picks")
    if team.lower() in {c.lower() for c in state.used_clubs}:
        raise ValueError(f"{team} has already been picked by {state.entrant}")
    if any(h["gameweek"] == gameweek for h in state.history):
        raise ValueError(
            f"{state.entrant} already has a pick recorded for gameweek {gameweek}"
        )
    state.used_clubs.append(team)
    state.history.append(
        {
            "gameweek": gameweek,
            "team": team,
            "opponent": opponent,
            "is_home": is_home,
            "result": "PENDING",
            "source": None,
        }
    )
    return state


VALID_RESULTS = {"WIN", "DRAW", "LOSS"}


def record_result(
    state: LMSState, gameweek: int, result: str, source: str = "MANUAL"
) -> LMSState:
    """Settle a gameweek's pick. Anything but WIN eliminates (§ rules note
    at module top — a draw is not a survival, same as a loss).

    `source` is kept alongside the result rather than discarded: "AUTO"
    (fetched from a results feed) versus "MANUAL" (typed in by whoever
    runs the competition) is exactly the distinction a multi-entrant game
    needs visible, since a self-reported survival is a conflict of
    interest an automatic feed doesn't have. See `auto_settle` below.
    """
    result = result.upper()
    if result not in VALID_RESULTS:
        raise ValueError(f"result must be one of {sorted(VALID_RESULTS)}, got {result!r}")
    entry = next((h for h in state.history if h["gameweek"] == gameweek), None)
    if entry is None:
        raise ValueError(f"no pick recorded for {state.entrant} in gameweek {gameweek}")
    entry["result"] = result
    entry["source"] = source
    if result != "WIN":
        state.alive = False
    return state


def result_for_pick(is_home: bool, home_goals: int, away_goals: int) -> str:
    """WIN/DRAW/LOSS for the picked side, given the full-time scoreline."""
    if home_goals == away_goals:
        return "DRAW"
    home_won = home_goals > away_goals
    return "WIN" if (home_won == is_home) else "LOSS"


def auto_settle(
    states: dict[str, LMSState],
    gw: Gameweek,
    results: dict[int, tuple[int, int] | None],
) -> list[tuple[str, int, str]]:
    """Settle every PENDING pick this gameweek that now has a fetched result.

    `results` is fixture-index -> (home_goals, away_goals), or None for a
    void fixture — the same shape
    `sources.football_data_org.results_for_round` returns, keyed against
    `gw.fixtures` in the same order. A void or still-unresulted fixture is
    left PENDING rather than guessed at. Returns (entrant, gameweek,
    result) for everything this call settled.
    """
    fixture_index = {(f.home, f.away): i for i, f in enumerate(gw.fixtures)}
    settled: list[tuple[str, int, str]] = []
    for name, state in states.items():
        entry = next(
            (
                h for h in state.history
                if h["gameweek"] == gw.number and h["result"] == "PENDING"
            ),
            None,
        )
        if entry is None:
            continue
        pair = (
            (entry["team"], entry["opponent"])
            if entry["is_home"]
            else (entry["opponent"], entry["team"])
        )
        index = fixture_index.get(pair)
        if index is None:
            continue
        outcome = results.get(index)
        if outcome is None:
            continue
        result = result_for_pick(entry["is_home"], outcome[0], outcome[1])
        record_result(state, gw.number, result, source="AUTO")
        settled.append((name, gw.number, result))
    return settled


def format_status(state: LMSState) -> str:
    status = "ALIVE" if state.alive else "ELIMINATED"
    lines = [
        f"{state.entrant}: {status}  ({len(state.used_clubs)} club(s) used)",
        "",
    ]
    if not state.history:
        lines.append("  no picks recorded yet")
        return "\n".join(lines)
    for h in sorted(state.history, key=lambda h: h["gameweek"]):
        venue = "v" if h["is_home"] else "@"
        source = f" ({h['source'].lower()})" if h.get("source") else ""
        lines.append(
            f"  GW{h['gameweek']:>2d}  {h['team']} {venue} {h['opponent']:<20s} "
            f"{h['result']}{source}"
        )
    return "\n".join(lines)
