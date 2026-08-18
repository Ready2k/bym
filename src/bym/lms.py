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

Strategy: the highest P(win) is not the pick
--------------------------------------------
Ranking this gameweek's clubs by P(win) answers "which club is most
likely to win on Saturday", which is not the question the format asks. A
club is a one-use resource across the whole competition, so spending the
best one now is only right if nothing better is coming. `plan_horizon`
therefore solves the actual problem — assign one distinct club to each of
the next few gameweeks so that the probability of surviving *all* of them
is maximised — and `recommend_gameweek`'s ranking becomes what it should
be, the menu the plan chooses from rather than the recommendation itself.

`field_equity` handles the second gap. Surviving is not winning: an
entrant who survives alongside the entire field has gained nothing, so
where the field's picks are known, equity in the pot (survive while
others do not) ranks differently from raw survival. Both are offered
separately rather than fused into one number, because the weighting
between them depends on pot size and rules this module does not know.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment

from .config import DEFAULT_LMS, DEFAULT_MODEL, LMSConfig, ModelConfig
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
    # Optional: club -> share of the field picking it, where the
    # competition publishes its entrants' picks. Feeds `field_equity`;
    # absent everywhere else, since nothing else needs to know what the
    # rest of the field is doing.
    popularity: dict[str, float] | None = None


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
    popularity = raw.get("popularity")
    if popularity:
        unknown = sorted(k for k in popularity if k.lower() not in seen)
        if unknown:
            # A club that isn't playing cannot hold any of the field's
            # picks, so this is a typo, and a silent 0% share would
            # quietly distort every equity number computed from it.
            raise ValueError(
                f"popularity names club(s) not playing this gameweek: "
                f"{', '.join(unknown)}"
            )
        if any(float(v) < 0 for v in popularity.values()):
            raise ValueError("popularity shares cannot be negative")
        popularity = {k: float(v) for k, v in popularity.items()}
    return Gameweek(
        number=int(raw["gameweek"]),
        fixtures=fixtures,
        deadline_at=deadline,
        popularity=popularity or None,
    )


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


def _confidence(p_win: float, lms_cfg: LMSConfig) -> str:
    # LMS-specific banding (config.LMSConfig), NOT Super 6's §20 CONF v1.
    # Same quantity, but a threshold that means "worth two points off one
    # of six fixtures" is not the threshold that means "worth staking the
    # competition on" — see LMSConfig for why 0.58 would flatter the
    # coin-flips that get entrants knocked out.
    if p_win >= lms_cfg.conf_high_p_win:
        return "HIGH"
    if p_win < lms_cfg.conf_low_p_win:
        return "LOW"
    return "MEDIUM"


@dataclass(frozen=True)
class FixtureOutlook:
    """One fixture's 1X2 probabilities, model fitted and market blended.

    Split out from candidate ranking because the ranking is per entrant
    (each has their own used-club list) while this is not: the dashboard
    ranks the same gameweek for every entrant, and the horizon planner
    ranks the same gameweek repeatedly. Computing the score matrix once
    per fixture instead of once per fixture per entrant is the whole point
    of the type.
    """

    fixture: GameweekFixture
    p_home_win: float
    p_draw: float
    p_away_win: float

    # Populated by `fixture_outlooks()` from the same score matrix as the
    # 1X2 probabilities above — optional (default None) so existing
    # positional construction elsewhere (LMS tests) stays valid. FPL's
    # player projection (SPEC_FPL.md §6) is the reason these exist: goal/
    # clean-sheet components need team goal expectancy and clean-sheet
    # probability, and recomputing score_matrix() a second time per player
    # would violate the "once per gameweek" discipline this type exists to
    # enforce in the first place.
    lambda_home: float | None = None
    lambda_away: float | None = None
    p_home_clean_sheet: float | None = None
    p_away_clean_sheet: float | None = None


def fixture_outlooks(
    params: DixonColesParams | ParamRouter,
    gw: Gameweek,
    cfg: ModelConfig = DEFAULT_MODEL,
) -> list[FixtureOutlook]:
    """1X2 probabilities for every fixture in a gameweek.

    `params` is either one fitted parameter set or a (home, away) -> params
    router, same convention as `recommend.recommend_round`.
    """
    router: ParamRouter = params if callable(params) else (lambda h, a: params)
    outlooks: list[FixtureOutlook] = []
    for f in gw.fixtures:
        p = router(f.home, f.away)
        lam_h, lam_a = p.lambdas(f.home, f.away, cfg)
        matrix = score_matrix(lam_h, lam_a, p.rho, cfg)
        if f.odds is not None and cfg.market_blend_weight is not None:
            mh, md, ma = market_mod.implied_result_probs(*f.odds)
            matrix = market_mod.blend_matrix(matrix, mh, md, ma, cfg.market_blend_weight)
        outlooks.append(
            FixtureOutlook(
                f, matrix.p_home_win, matrix.p_draw, matrix.p_away_win,
                lambda_home=matrix.lambda_home,
                lambda_away=matrix.lambda_away,
                p_home_clean_sheet=float(matrix.probs[:, 0].sum()),
                p_away_clean_sheet=float(matrix.probs[0, :].sum()),
            )
        )
    return outlooks


def rank_candidates(
    outlooks: Sequence[FixtureOutlook],
    used_clubs: Iterable[str] = (),
    lms_cfg: LMSConfig = DEFAULT_LMS,
) -> list[PickCandidate]:
    """Eligible clubs from already-computed outlooks, best P(win) first.

    A club in `used_clubs` (matched case-insensitively) is excluded
    entirely — the one-club-once rule — rather than merely flagged, so the
    returned list is always a legal set of picks.
    """
    used_lower = {c.lower() for c in used_clubs}
    candidates: list[PickCandidate] = []
    for o in outlooks:
        f = o.fixture
        if f.home.lower() not in used_lower:
            candidates.append(
                PickCandidate(
                    f.home, f.away, True, o.p_home_win, o.p_draw, o.p_away_win,
                    _confidence(o.p_home_win, lms_cfg),
                )
            )
        if f.away.lower() not in used_lower:
            candidates.append(
                PickCandidate(
                    f.away, f.home, False, o.p_away_win, o.p_draw, o.p_home_win,
                    _confidence(o.p_away_win, lms_cfg),
                )
            )
    # Ties broken by club name so the ordering is stable across runs —
    # a recommendation that reshuffles between identical inputs reads as
    # the model changing its mind when it has not.
    candidates.sort(key=lambda c: (-c.p_win, c.team))
    return candidates


def recommend_gameweek(
    params: DixonColesParams | ParamRouter,
    gw: Gameweek,
    used_clubs: set[str] | frozenset[str] = frozenset(),
    cfg: ModelConfig = DEFAULT_MODEL,
    lms_cfg: LMSConfig = DEFAULT_LMS,
) -> list[PickCandidate]:
    """Every club still eligible this gameweek, ranked by P(win) descending.

    This is the menu, not the recommendation: the best club for *this*
    gameweek is frequently the wrong club to spend, because it is also the
    best club for a gameweek to come. See `plan_horizon`.
    """
    return rank_candidates(fixture_outlooks(params, gw, cfg), used_clubs, lms_cfg)


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
        f"Strongest club this gameweek: {best.team} ({best.fixture_label}) — "
        f"{best.p_win:.1%} to win",
        "  This ranking ignores future gameweeks. Run `lms-plan` before "
        "committing — the strongest club now is often one you need later.",
    ]
    if best.confidence != "HIGH":
        lines.append(
            f"  Nothing above {DEFAULT_LMS.conf_high_p_win:.0%} is available "
            "this gameweek, so every pick here is a real risk of elimination."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Horizon planning — the strategic core (see module docstring)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedPick:
    gameweek: int
    weeks_ahead: int
    candidate: PickCandidate
    # p_win after the future-uncertainty shrink; equal to candidate.p_win
    # for the gameweek being picked now (weeks_ahead == 0).
    planning_p_win: float


@dataclass(frozen=True)
class HorizonPlan:
    """One club per gameweek, chosen jointly rather than one week at a time.

    `survival_p` is the honest headline: the probability of surviving
    every gameweek in the plan, using the model's unshrunk probabilities.
    `greedy_*` is the same figure for the take-the-favourite-every-week
    policy, kept so the difference between the two is visible rather than
    asserted.
    """

    picks: tuple[PlannedPick, ...]
    survival_p: float
    greedy_picks: tuple[PlannedPick, ...]
    greedy_survival_p: float
    # Gameweeks in the requested horizon with no legal pick left (every
    # club playing has already been used). Not padded over: an entrant who
    # reaches one of these is eliminated by the fixture list, and that is
    # worth seeing early.
    unplannable: tuple[int, ...] = ()

    @property
    def this_week(self) -> PlannedPick | None:
        return self.picks[0] if self.picks else None

    @property
    def survival_gain(self) -> float:
        """Extra probability of surviving the whole horizon, versus greedy."""
        return self.survival_p - self.greedy_survival_p


def _shrunk(p: float, mean_p: float, weeks_ahead: int, lms_cfg: LMSConfig) -> float:
    """Pull a future gameweek's win probability toward that gameweek's mean.

    A fixture k weeks out is forecast from today's squad strengths; the
    further out it is, the more time there is for that to stop being true.
    Shrinking toward the gameweek's own mean rather than toward a fixed
    constant keeps the correction free of any assumed base rate — it only
    says "distant edges are smaller than they look", never how big a win
    probability ought to be.
    """
    if weeks_ahead <= 0 or lms_cfg.future_shrink_per_week <= 0:
        return p
    keep = (1.0 - lms_cfg.future_shrink_per_week) ** weeks_ahead
    return mean_p + (p - mean_p) * keep


def _planning_table(
    outlooks: Sequence[Sequence[FixtureOutlook]],
    used_clubs: Iterable[str],
    lms_cfg: LMSConfig,
) -> tuple[list[list[PickCandidate]], list[dict[str, float]], list[str]]:
    """Per-gameweek candidates, their planning probabilities, and club keys."""
    per_week: list[list[PickCandidate]] = []
    planning: list[dict[str, float]] = []
    keys: dict[str, None] = {}
    for k, week_outlooks in enumerate(outlooks):
        candidates = rank_candidates(week_outlooks, used_clubs, lms_cfg)
        per_week.append(candidates)
        mean_p = (
            sum(c.p_win for c in candidates) / len(candidates) if candidates else 0.0
        )
        planning.append(
            {c.team.lower(): _shrunk(c.p_win, mean_p, k, lms_cfg) for c in candidates}
        )
        for c in candidates:
            keys.setdefault(c.team.lower(), None)
    return per_week, planning, list(keys)


def plan_horizon(
    params: DixonColesParams | ParamRouter,
    gameweeks: Sequence[Gameweek],
    used_clubs: Iterable[str] = (),
    cfg: ModelConfig = DEFAULT_MODEL,
    lms_cfg: LMSConfig = DEFAULT_LMS,
    outlooks: Sequence[Sequence[FixtureOutlook]] | None = None,
) -> HorizonPlan:
    """Assign one distinct club to each gameweek, maximising joint survival.

    Surviving every gameweek in the horizon has probability ∏ p_win over
    the chosen picks, so maximising it is the same as minimising
    ∑ −log p_win — a rectangular assignment problem (gameweeks × clubs)
    with the one-club-once rule expressed exactly as the constraint that
    no column is used twice. `scipy.optimize.linear_sum_assignment` solves
    it optimally in polynomial time; there is no need to approximate, and
    a greedy week-by-week choice is not merely an approximation but a
    systematically worse policy, because it spends clubs with no regard
    for what they would have been worth later.

    `gameweeks` must be in chronological order, the first being the one to
    pick now. Probabilities for later gameweeks are shrunk toward that
    gameweek's mean before planning (see `_shrunk`); the reported
    `survival_p` uses the unshrunk model probabilities, so the plan is
    chosen cautiously but scored honestly.

    `outlooks` accepts probabilities already computed for these gameweeks
    (`fixture_outlooks` per gameweek, in the same order). Only the
    used-club exclusion differs between entrants, so a caller planning for
    several of them — the dashboard — computes the score matrices once
    rather than once per entrant.
    """
    gameweeks = list(gameweeks)
    if not gameweeks:
        return HorizonPlan((), 0.0, (), 0.0, ())
    if outlooks is None:
        outlooks = [fixture_outlooks(params, gw, cfg) for gw in gameweeks]

    per_week, planning, club_keys = _planning_table(outlooks, used_clubs, lms_cfg)
    by_key = [{c.team.lower(): c for c in week} for week in per_week]

    # −log p, with unavailable (club, gameweek) pairs infeasible. Clamped
    # away from zero so a 0% club is merely a terrible assignment rather
    # than an infinity that makes every plan containing it equally bad.
    floor = 1e-9
    cost = np.full((len(gameweeks), len(club_keys)), np.inf)
    for row, week in enumerate(planning):
        for col, key in enumerate(club_keys):
            p = week.get(key)
            if p is not None:
                cost[row, col] = -math.log(max(p, floor))

    rows, cols = _assign(cost)
    picks = tuple(
        PlannedPick(
            gameweek=gameweeks[r].number,
            weeks_ahead=r,
            candidate=by_key[r][club_keys[c]],
            planning_p_win=planning[r][club_keys[c]],
        )
        for r, c in sorted(zip(rows, cols))
    )
    planned_weeks = {p.weeks_ahead for p in picks}
    unplannable = tuple(
        gw.number for i, gw in enumerate(gameweeks) if i not in planned_weeks
    )

    greedy = _greedy_picks(gameweeks, per_week, planning)
    return HorizonPlan(
        picks=picks,
        survival_p=_survival(picks),
        greedy_picks=greedy,
        # Greedy running out of legal clubs before the horizon the plan
        # reaches is not a shorter horizon, it is elimination: it survives
        # those gameweeks with probability zero. Scoring it over the weeks
        # it managed to fill would reward it for spending clubs it should
        # have saved, which is the exact failure being measured.
        greedy_survival_p=_survival(greedy) if len(greedy) == len(picks) else 0.0,
        unplannable=unplannable,
    )


def _assign(cost: np.ndarray) -> tuple[Sequence[int], Sequence[int]]:
    """Optimal assignment over the longest fillable prefix of gameweeks.

    Late in a competition there may be no legal club left for some future
    gameweek. Two ways that shows up, both handled here rather than
    surfacing as a broken plan: `linear_sum_assignment` raises when the
    infeasible entries leave no complete assignment, and on a matrix with
    fewer clubs than gameweeks it quietly matches only as many rows as
    there are columns.

    Shortening from the END is what makes the result meaningful: a
    gameweek with no legal pick eliminates the entrant, so planning past
    one is planning for a competition already lost. The prefix is
    re-solved rather than trimmed, so the clubs go to the gameweeks that
    are actually going to be played.
    """
    for depth in range(cost.shape[0], 0, -1):
        try:
            rows, cols = linear_sum_assignment(cost[:depth])
        except ValueError:
            continue
        if len(rows) == depth:  # every gameweek in the prefix got a club
            return rows, cols
    return (), ()


def _greedy_picks(
    gameweeks: Sequence[Gameweek],
    per_week: Sequence[Sequence[PickCandidate]],
    planning: Sequence[dict[str, float]],
) -> tuple[PlannedPick, ...]:
    """The take-the-favourite-every-week policy, for comparison only.

    Stops at the first gameweek where it has no legal club left, because
    that is where the policy's entrant goes out — it does not get to skip
    a week and carry on.
    """
    spent: set[str] = set()
    picks: list[PlannedPick] = []
    for k, week in enumerate(per_week):
        best = next((c for c in week if c.team.lower() not in spent), None)
        if best is None:
            break
        spent.add(best.team.lower())
        picks.append(
            PlannedPick(gameweeks[k].number, k, best, planning[k][best.team.lower()])
        )
    return tuple(picks)


def _survival(picks: Sequence[PlannedPick]) -> float:
    """P(surviving every gameweek in a plan), on unshrunk probabilities."""
    if not picks:
        return 0.0
    p = 1.0
    for pick in picks:
        p *= pick.candidate.p_win
    return p


def format_plan(plan: HorizonPlan, used_clubs: Iterable[str] = ()) -> str:
    used = sorted(used_clubs)
    lines = ["LAST MAN STANDING — HORIZON PLAN", ""]
    if used:
        lines += [f"Already used ({len(used)}): {', '.join(used)}", ""]
    if not plan.picks:
        lines.append(
            "No legal pick available — every club in the horizon's fixtures "
            "has already been used."
        )
        return "\n".join(lines)
    lines.append(
        f"{'GW':>4s}  {'PICK':20s} {'FIXTURE':28s} {'P(WIN)':>7s} "
        f"{'PLAN':>6s}  CONF"
    )
    for p in plan.picks:
        c = p.candidate
        lines.append(
            f"{p.gameweek:>4d}  {c.team[:20]:20s} {c.fixture_label[:28]:28s} "
            f"{c.p_win:>6.1%} {p.planning_p_win:>5.1%}  {c.confidence}"
        )
    now = plan.this_week
    lines += [
        "",
        f"Pick now (GW{now.gameweek}): {now.candidate.team} — "
        f"{now.candidate.p_win:.1%} to win",
        f"Surviving all {len(plan.picks)} gameweek(s) on this plan: "
        f"{plan.survival_p:.1%}",
    ]
    greedy_now = plan.greedy_picks[0] if plan.greedy_picks else None
    ran_out = len(plan.greedy_picks) < len(plan.picks)
    if greedy_now and greedy_now.candidate.team != now.candidate.team:
        lines.append(
            f"Taking the favourite ({greedy_now.candidate.team}, "
            f"{greedy_now.candidate.p_win:.1%}) instead: "
            f"{plan.greedy_survival_p:.1%} — "
            f"{plan.survival_gain * 100:+.1f}pp for planning ahead. "
            f"{now.candidate.team} is chosen because "
            f"{greedy_now.candidate.team} is worth more in a later gameweek."
        )
        if ran_out:
            lines.append(
                f"  (0.0% because taking the favourite every week leaves no "
                f"legal club for GW{plan.picks[len(plan.greedy_picks)].gameweek}.)"
            )
    elif plan.survival_gain > 0:
        lines.append(
            f"Same club as the week-by-week favourite, but the plan reorders "
            f"later gameweeks: {plan.survival_gain * 100:+.1f}pp over the horizon."
        )
    else:
        lines.append(
            "The week-by-week favourite is also the planned pick — nothing "
            "in the horizon is worth saving it for."
        )
    if plan.unplannable:
        lines.append(
            "No legal pick for GW"
            + ", GW".join(str(n) for n in plan.unplannable)
            + " — every club playing is already used. Surviving that far "
            "means being eliminated by the fixture list."
        )
    lines.append(
        "\nThe plan is advisory for future gameweeks and re-solved every "
        "week; only the GW"
        f"{now.gameweek} pick has to be committed now."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pot equity — surviving is not the same as winning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldEquity:
    candidate: PickCandidate
    field_share: float       # fraction of the field on this club
    expected_survivors: float  # fraction of the field still alive if it wins
    equity: float            # P(win) / expected survivors — relative pot share


def field_equity(
    candidates: Sequence[PickCandidate],
    popularity: dict[str, float],
) -> list[FieldEquity]:
    """Rank picks by share of the pot rather than by survival alone.

    In a pool, an entrant who survives alongside everyone else has gained
    nothing; the pot is only won by outlasting the field. Picking club c
    is therefore worth, to a first approximation,

        equity(c) = P(c wins) / E[fraction of the field alive | c wins]

    which is exactly the expected share of the pot if the competition
    settled after this gameweek. Two consequences fall straight out and
    are the reason this exists: a heavily-backed favourite is worth less
    than its win probability suggests, and a rival who picked c's OPPONENT
    is eliminated with certainty when c wins — that correlation is real,
    and is the one case where the independence assumption below would be
    plainly wrong, so it is handled exactly.

    `popularity` maps club name to the fraction of the field picking it
    (percentages are accepted and converted). Any mass not accounted for
    is assumed to survive at the mean win probability of the candidate
    list. Everything beyond the picked fixture is treated as independent,
    which is what independent football matches are.

    Deliberately NOT folded into `plan_horizon`: pot equity is a one-week
    quantity about other entrants, joint survival is a multi-week quantity
    about fixtures, and the exchange rate between them depends on pot size
    and rollover rules this module has no knowledge of.
    """
    if not candidates:
        return []
    pop = {k.lower(): float(v) for k, v in popularity.items() if float(v) > 0}
    if any(v > 1.0 for v in pop.values()):  # given as percentages
        pop = {k: v / 100.0 for k, v in pop.items()}
    p_by_club = {c.team.lower(): c.p_win for c in candidates}
    mean_p = sum(p_by_club.values()) / len(p_by_club)
    unaccounted = max(0.0, 1.0 - sum(pop.values()))

    out: list[FieldEquity] = []
    for c in candidates:
        key, opp = c.team.lower(), c.opponent.lower()
        # Given c wins: everyone on c survives, everyone on c's opponent
        # is out, everyone else survives at their own club's P(win).
        survivors = pop.get(key, 0.0) + unaccounted * mean_p
        for other, share in pop.items():
            if other in (key, opp):
                continue
            survivors += share * p_by_club.get(other, mean_p)
        survivors = max(survivors, 1e-9)
        out.append(FieldEquity(c, pop.get(key, 0.0), survivors, c.p_win / survivors))
    out.sort(key=lambda e: (-e.equity, e.candidate.team))
    return out


def format_field_equity(equities: Sequence[FieldEquity], top_n: int = 10) -> str:
    lines = [
        "LAST MAN STANDING — POT EQUITY (field-adjusted)",
        "",
        f"{'TEAM':20s} {'FIXTURE':28s} {'P(WIN)':>7s} {'FIELD':>6s} "
        f"{'EQUITY':>7s}",
    ]
    for e in equities[:top_n]:
        c = e.candidate
        lines.append(
            f"{c.team[:20]:20s} {c.fixture_label[:28]:28s} {c.p_win:>6.1%} "
            f"{e.field_share:>5.1%} {e.equity:>7.2f}"
        )
    if equities:
        best = equities[0]
        lines += [
            "",
            f"Highest pot equity: {best.candidate.team} "
            f"({best.candidate.p_win:.1%} to win, {best.field_share:.0%} of "
            f"the field on it).",
            "Equity is relative (expected share of the pot ÷ the field's) and "
            "means nothing except against the other rows here.",
            "It also covers this gameweek only. A long shot can rank high "
            "purely because beating a heavily-backed opponent would wipe out "
            "the field — read it next to P(WIN), which is still the chance "
            "you are in the competition next week at all.",
        ]
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


_STATE_FIELDS = ("entrant", "alive", "used_clubs", "history")


def load_states(path: Path = DEFAULT_STATE_PATH) -> dict[str, LMSState]:
    """Read every entrant's state.

    Unrecognised keys are dropped rather than raising: this file is
    hand-editable by design (it is the fallback when a result cannot be
    fetched), and a whole competition's history should not become
    unreadable because one stray key was typed into it, or because it was
    written by a later version of this module.
    """
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {
        k: LMSState(**{f: v[f] for f in _STATE_FIELDS if f in v})
        for k, v in raw.items()
    }


def save_states(states: dict[str, LMSState], path: Path = DEFAULT_STATE_PATH) -> None:
    """Write every entrant's state atomically.

    Via a temporary file and `os.replace` because this one file is the
    entire competition — every entrant's used clubs and pick history, with
    no other copy. A crash or a full disk partway through a plain write
    would leave truncated JSON that `load_states` cannot parse, and there
    would be nothing to restore it from.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: vars(v) for k, v in states.items()}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def get_state(entrant: str, path: Path = DEFAULT_STATE_PATH) -> LMSState:
    return load_states(path).get(entrant, LMSState(entrant=entrant))


@dataclass(frozen=True)
class LineageStep:
    """One gameweek of an entrant's path, beside the plan's pick for it.

    `p_win` is None where it cannot be known rather than zero or omitted:
    a booked week whose fixtures have rolled out of the horizon can still
    name the club that was taken, and saying "unknown" is the honest
    rendering. `plan_team` is likewise None once the week is behind the
    fixture list — the plan for a week nobody can replay is not
    reconstructible, and inventing one would make the comparison lie in
    the direction that flatters the tool.
    """

    gameweek: int
    status: str  # BOOKED | PLANNED
    team: str | None
    p_win: float | None
    plan_team: str | None
    plan_p_win: float | None
    # The graph is a decision surface, not just a probability trace. Keep
    # enough fixture context for the UI to explain each future point without
    # trying to reverse-engineer an opponent from a club name.
    opponent: str | None = None
    is_home: bool | None = None
    result: str | None = None

    @property
    def delta(self) -> float | None:
        """Points of win probability given up against the plan's pick."""
        if self.p_win is None or self.plan_p_win is None:
            return None
        return self.p_win - self.plan_p_win


def pick_outlook(
    params: DixonColesParams | ParamRouter,
    team: str,
    opponent: str,
    is_home: bool,
    cfg: ModelConfig = DEFAULT_MODEL,
) -> float:
    """P(this club wins) for one booked pick, from the club's own side.

    Recomputed from the fixture rather than stored at pick time, so the
    lineage always reflects the current model rather than a probability
    frozen against whatever fit was loaded that week.
    """
    fixture = (
        GameweekFixture(team, opponent) if is_home else GameweekFixture(opponent, team)
    )
    outlook = fixture_outlooks(params, Gameweek(0, (fixture,)), cfg)[0]
    return outlook.p_home_win if is_home else outlook.p_away_win


def lineage(
    params: DixonColesParams | ParamRouter,
    state: LMSState,
    gameweeks: Sequence[Gameweek],
    plan: HorizonPlan,
    reference: HorizonPlan | None = None,
    cfg: ModelConfig = DEFAULT_MODEL,
) -> list[LineageStep]:
    """An entrant's booked picks and remaining plan, as one ordered path.

    `plan` covers the weeks still open to this entrant; `reference` is the
    plan an entrant with nothing used would have been given over the whole
    horizon, which is what "the recommendation" means for a week already
    booked. Weeks appear in gameweek order regardless of which list they
    came from, because the point of the view is the path, not the split.
    """
    by_number = {gw.number: gw for gw in gameweeks}
    reference_by_gw = {p.gameweek: p for p in (reference.picks if reference else ())}
    planned_by_gw = {p.gameweek: p for p in plan.picks}

    steps: list[LineageStep] = []
    for entry in state.history:
        number = int(entry["gameweek"])
        p_win = None
        if number in by_number or entry.get("opponent"):
            try:
                p_win = pick_outlook(
                    params, entry["team"], entry["opponent"], bool(entry["is_home"]), cfg
                )
            except (KeyError, TypeError, ValueError):
                p_win = None
        ref = reference_by_gw.get(number)
        steps.append(
            LineageStep(
                gameweek=number,
                status="BOOKED",
                team=entry.get("team"),
                p_win=p_win,
                plan_team=ref.candidate.team if ref else None,
                plan_p_win=ref.candidate.p_win if ref else None,
                opponent=entry.get("opponent"),
                is_home=bool(entry.get("is_home")),
                result=entry.get("result"),
            )
        )

    booked = {s.gameweek for s in steps}
    for pick in plan.picks:
        if pick.gameweek in booked:
            continue
        ref = reference_by_gw.get(pick.gameweek)
        steps.append(
            LineageStep(
                gameweek=pick.gameweek,
                status="PLANNED",
                team=pick.candidate.team,
                p_win=pick.candidate.p_win,
                plan_team=ref.candidate.team if ref else pick.candidate.team,
                plan_p_win=ref.candidate.p_win if ref else pick.candidate.p_win,
                opponent=pick.candidate.opponent,
                is_home=pick.candidate.is_home,
            )
        )
    steps.sort(key=lambda s: s.gameweek)
    return steps


def cumulative_survival(values: Sequence[float | None]) -> list[float | None]:
    """Running ∏ p, stopping at the first unknown.

    Survival is a product, so one missing week makes every later figure
    unknowable rather than merely less precise — carrying on past it would
    quietly report the product of the weeks that happened to be known.
    """
    out: list[float | None] = []
    running = 1.0
    for value in values:
        if value is None or running is None:
            out.append(None)
            running = None
            continue
        running *= value
        out.append(running)
    return out


def open_gameweek_indices(
    gameweeks: Sequence[Gameweek], state: LMSState | None
) -> list[int]:
    """Positions in `gameweeks` this entrant has not yet committed a pick for.

    The used-club list alone is not enough to place an entrant in the
    horizon. Someone who has booked GW1 has spent a club *and* spent the
    week: planning still from GW1 tells them to pick a club for a deadline
    that has passed, and — worse — solves the remaining weeks as though
    the club it suggests for GW1 were unavailable later, which understates
    every week after it.

    Indices rather than gameweeks, so a caller holding score matrices
    computed per gameweek (the dashboard does) can slice them in step
    instead of recomputing.
    """
    if state is None:
        return list(range(len(gameweeks)))
    committed = {int(entry["gameweek"]) for entry in state.history if "gameweek" in entry}
    return [i for i, gw in enumerate(gameweeks) if gw.number not in committed]


def record_pick(
    state: LMSState,
    gameweek: int,
    team: str,
    opponent: str,
    is_home: bool,
    *,
    picked_p_win: float | None = None,
    suggested_team: str | None = None,
    suggested_p_win: float | None = None,
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
    entry = {
        "gameweek": gameweek,
        "team": team,
        "opponent": opponent,
        "is_home": is_home,
        "result": "PENDING",
        "source": None,
    }
    if picked_p_win is not None:
        entry["picked_p_win"] = picked_p_win
    if suggested_team is not None:
        entry["suggested_team"] = suggested_team
    if suggested_p_win is not None:
        entry["suggested_p_win"] = suggested_p_win
    state.history.append(entry)
    return state


VALID_RESULTS = {"WIN", "DRAW", "LOSS"}
ELIMINATING_RESULTS = {"DRAW", "LOSS"}


def alive_from_history(history: Sequence[dict]) -> bool:
    """Survival derived from the settled picks, not tracked alongside them.

    `alive` has to be recomputed rather than latched off, because results
    get corrected: a pick settled LOSS by a mistyped `lms-result`, or by a
    feed reporting an abandoned match, is later fixed to WIN. Latching
    would leave that entrant permanently eliminated with a full set of
    wins on record and no way back short of editing the JSON by hand.
    """
    return not any(h.get("result") in ELIMINATING_RESULTS for h in history)


class PickLocked(ValueError):
    """The gameweek has started, so its pick can no longer be changed."""


def pick_is_locked(
    gameweek: Gameweek | None, entry: dict | None, now: datetime | None = None
) -> bool:
    """Has this gameweek's pick stopped being changeable?

    Last Man Standing lets an entrant change their club right up until the
    first match of the round kicks off, so a booked pick is provisional,
    not final. Two things end that: the round starting, and the pick being
    settled.

    A gameweek with no known kickoff is treated as still open. The
    alternative — locking what cannot be timed — would make a manually
    written gameweek file permanently unchangeable, which is worse than
    trusting the entrant with a rule the competition already trusts them
    with.
    """
    if entry is not None and entry.get("result") not in (None, "PENDING"):
        return True
    if gameweek is None or gameweek.deadline_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    deadline = gameweek.deadline_at
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return now >= deadline


def change_pick(
    state: LMSState,
    gameweek: int,
    team: str,
    opponent: str,
    is_home: bool,
    gameweek_fixtures: Gameweek | None = None,
    now: datetime | None = None,
    *,
    picked_p_win: float | None = None,
    suggested_team: str | None = None,
    suggested_p_win: float | None = None,
) -> LMSState:
    """Replace a gameweek's pending pick, releasing the club it used.

    `record_pick` refuses a second pick for a gameweek, which is right for
    a *new* booking and wrong as a statement about the format: changing
    before kickoff is a legal move, not a caller error. Without this the
    only way back from a mis-click was editing the state file by hand —
    and doing that correctly means remembering to pull the old club out of
    `used_clubs`, which is exactly the step that gets forgotten.
    """
    entry = next((h for h in state.history if h["gameweek"] == gameweek), None)
    if entry is None:
        raise ValueError(f"no pick recorded for {state.entrant} in gameweek {gameweek}")
    if pick_is_locked(gameweek_fixtures, entry, now):
        raise PickLocked(
            f"gameweek {gameweek} has already started — {entry['team']} is final"
        )
    previous = entry["team"]
    if team.lower() == previous.lower():
        return state
    # Checked against the OTHER weeks' clubs: the one being replaced is
    # about to be released, so it must not block its own replacement.
    spent = {c.lower() for c in state.used_clubs} - {previous.lower()}
    if team.lower() in spent:
        raise ValueError(f"{team} has already been picked by {state.entrant}")

    state.used_clubs = [c for c in state.used_clubs if c.lower() != previous.lower()]
    state.used_clubs.append(team)
    entry.update(team=team, opponent=opponent, is_home=is_home,
                 result="PENDING", source=None)
    for key, value in {
        "picked_p_win": picked_p_win,
        "suggested_team": suggested_team,
        "suggested_p_win": suggested_p_win,
    }.items():
        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
    state.alive = alive_from_history(state.history)
    return state


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
    state.alive = alive_from_history(state.history)
    return state


def result_for_pick(is_home: bool, home_goals: int, away_goals: int) -> str:
    """WIN/DRAW/LOSS for the picked side, given the full-time scoreline."""
    if home_goals == away_goals:
        return "DRAW"
    home_won = home_goals > away_goals
    return "WIN" if (home_won == is_home) else "LOSS"


def pending_gameweeks(states: dict[str, LMSState]) -> dict[int, Gameweek]:
    """Every gameweek holding an unsettled pick, with the fixtures those
    picks are on.

    Built from the picks themselves rather than from a fixture list,
    because settlement must not depend on which gameweek is *current*. A
    pick on the Monday night game is still PENDING when the feed has
    already moved the current matchday on to next week; keying settlement
    off the current gameweek would leave it PENDING forever and show an
    entrant as alive who may well be out.
    """
    out: dict[int, dict[tuple[str, str], GameweekFixture]] = {}
    for state in states.values():
        for h in state.history:
            if h.get("result") != "PENDING":
                continue
            home, away = (
                (h["team"], h["opponent"])
                if h["is_home"]
                else (h["opponent"], h["team"])
            )
            fixtures = out.setdefault(int(h["gameweek"]), {})
            fixtures.setdefault(
                (home.strip().lower(), away.strip().lower()),
                GameweekFixture(home, away),
            )
    return {
        number: Gameweek(number=number, fixtures=tuple(fixtures.values()))
        for number, fixtures in sorted(out.items())
    }


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
    # Keyed case- and whitespace-insensitively: a pick typed in by hand
    # ("man city") has to settle against a fixture list fetched from a
    # feed ("Man City"), and the failure mode of an exact match here is
    # silent — the pick just stays PENDING with no indication why.
    def key(home: str, away: str) -> tuple[str, str]:
        return (home.strip().lower(), away.strip().lower())

    fixture_index = {key(f.home, f.away): i for i, f in enumerate(gw.fixtures)}
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
            key(entry["team"], entry["opponent"])
            if entry["is_home"]
            else key(entry["opponent"], entry["team"])
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
