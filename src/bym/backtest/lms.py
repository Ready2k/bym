"""Leak-free historical evaluation for Last Man Standing.

Football-Data does not publish an official ``matchweek`` field, so this
module reconstructs *fixture slates* from the schedule.  A slate ends when
the next dated batch repeats a club already in it, or after a multi-day gap.
That is faithful to the information the LMS model needs (the clubs playing
before the next deadline), while making the unavoidable reconstruction
explicit rather than calling these official competition gameweeks.

The evaluation has two deliberately separate questions:

* ``weekly`` asks whether the team selected for the current slate wins.
  Each slate is a fresh entrant, so this measures forecast quality without a
  six-season survivor sample being dominated by the first loss.
* ``plan`` asks whether all picks in a short, one-club-once horizon survive.
  It compares the exact assignment planner with its legal greedy equivalent.

Every fit uses matches strictly before the slate's first date.  No team news
or post-deadline odds are reconstructed.  The optional market row uses
Football-Data's earlier 1X2 prices, the same Friday-price *proxy* used by the
Super 6 backtest; it is not a claim about a particular LMS deadline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from ..config import DEFAULT_LMS, DEFAULT_MODEL, LMSConfig, ModelConfig
from ..lms import Gameweek, GameweekFixture, plan_horizon, recommend_gameweek
from ..model import dixon_coles, market, priors


MIN_TRAINING_MATCHES = 200
DEFAULT_SLATE_GAP_DAYS = 3


@dataclass(frozen=True)
class LMSMetrics:
    """Calibration and realised survival for one binary LMS decision arm."""

    decisions: int
    wins: int
    win_rate: float
    mean_predicted_win: float
    calibration_gap: float
    brier: float
    log_loss: float

    def as_row(self, label: str) -> dict[str, object]:
        return {
            "arm": label,
            "decisions": self.decisions,
            "wins": self.wins,
            "win%": round(self.win_rate * 100, 2),
            "mean p(win)%": round(self.mean_predicted_win * 100, 2),
            "calibration pp": round(self.calibration_gap * 100, 2),
            "brier": round(self.brier, 4),
            "logloss": round(self.log_loss, 4),
        }


@dataclass(frozen=True)
class LMSBacktestResult:
    """Rows and summaries for the weekly and horizon-replay questions."""

    weekly_model: pd.DataFrame
    weekly_market: pd.DataFrame
    planned_horizon: pd.DataFrame
    greedy_horizon: pd.DataFrame
    slate_count: int
    skipped_slates: int
    horizon: int

    def summary(self) -> pd.DataFrame:
        rows = [
            metrics(self.weekly_model).as_row("MODEL weekly (fresh entry)"),
            metrics(self.planned_horizon).as_row(
                f"MODEL plan ({self.horizon}-slate survival)"
            ),
            metrics(self.greedy_horizon).as_row(
                f"LEGAL greedy ({self.horizon}-slate survival)"
            ),
        ]
        if not self.weekly_market.empty:
            rows.insert(
                1,
                metrics(self.weekly_market).as_row(
                    "MARKET-AVAILABLE weekly (price proxy)"
                ),
            )
        return pd.DataFrame(rows)


@dataclass(frozen=True)
class LMSReplaySlate:
    """One leak-free historical LMS decision for the interactive replay UI.

    Results are included solely so the local UI can reveal the already
    completed slate after a player has committed a choice.  The model fit
    remains restricted to matches before this slate's first kick-off.
    """

    season: int
    slate: int
    total_slates: int
    date: pd.Timestamp
    fixtures: tuple[dict[str, object], ...]
    candidates: tuple[dict[str, object], ...]
    model_pick: str | None
    market_pick: str | None
    suggested_pick: str | None
    suggested_won: bool | None
    suggested_path: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "season": self.season,
            "game": self.slate,
            "slate": self.slate,
            "total_games": self.total_slates,
            "total_slates": self.total_slates,
            "date": self.date.isoformat(),
            "fixtures": list(self.fixtures),
            "candidates": list(self.candidates),
            "model_pick": self.model_pick,
            "market_pick": self.market_pick,
            "suggested_pick": self.suggested_pick,
            "suggested_won": self.suggested_won,
            "suggested_path": list(self.suggested_path),
            "slate_note": (
                "Games are reconstructed from fixture dates and club repeats; "
                "Football-Data does not supply official matchweek labels."
            ),
        }


def metrics(rows: pd.DataFrame) -> LMSMetrics:
    """Evaluate ``p_win`` against a binary ``won`` column."""
    if rows.empty:
        nan = float("nan")
        return LMSMetrics(0, 0, nan, nan, nan, nan, nan)
    predicted = rows["p_win"].to_numpy(dtype=float)
    actual = rows["won"].to_numpy(dtype=float)
    return LMSMetrics(
        decisions=len(rows),
        wins=int(actual.sum()),
        win_rate=float(actual.mean()),
        mean_predicted_win=float(predicted.mean()),
        calibration_gap=float(actual.mean() - predicted.mean()),
        brier=float(((predicted - actual) ** 2).mean()),
        log_loss=float(
            -np.log(np.where(actual == 1.0, predicted, 1.0 - predicted).clip(1e-12, 1.0)).mean()
        ),
    )


def infer_fixture_slates(
    matches: pd.DataFrame, max_gap_days: int = DEFAULT_SLATE_GAP_DAYS
) -> list[pd.DataFrame]:
    """Reconstruct chronological, non-overlapping fixture slates.

    A data source that carries a genuine matchweek identifier should replace
    this adapter.  Football-Data has dates but no such identifier, and simply
    grouping by calendar week would merge a weekend round with a midweek
    round.  The club-repeat rule is the strongest schedule signal available.
    """
    if max_gap_days < 0:
        raise ValueError("max_gap_days must be non-negative")
    ordered = matches.sort_values(["date", "home_team", "away_team"])
    slates: list[pd.DataFrame] = []
    parts: list[pd.DataFrame] = []
    clubs: set[str] = set()
    last_date: pd.Timestamp | None = None

    def flush() -> None:
        nonlocal parts, clubs, last_date
        if parts:
            slates.append(pd.concat(parts).sort_values(["date", "home_team", "away_team"]))
        parts, clubs, last_date = [], set(), None

    for date, same_day in ordered.groupby("date", sort=True):
        day_clubs = set(same_day["home_team"]) | set(same_day["away_team"])
        gap = (date - last_date).days if last_date is not None else 0
        if parts and (gap > max_gap_days or clubs.intersection(day_clubs)):
            flush()
        parts.append(same_day)
        clubs.update(day_clubs)
        last_date = date
    flush()
    return slates


def _gameweek(slate: pd.DataFrame, number: int) -> Gameweek:
    return Gameweek(
        number=number,
        fixtures=tuple(
            GameweekFixture(str(row.home_team), str(row.away_team))
            for row in slate.itertuples()
        ),
        deadline_at=slate["date"].min().to_pydatetime(),
    )


def _fit_params(
    train: pd.DataFrame,
    current_season_schedule: pd.DataFrame,
    current_season: int,
    xi: float,
    cfg: ModelConfig,
):
    """Fit the exact per-division E0 model the LMS path consumes."""
    param_priors = None
    if cfg.use_promoted_priors:
        estimated = priors.estimate(train[train["season"] < current_season])
        specs = {}
        # The fixture list is public before the season starts; it supplies
        # membership only, never a result or a price.  Without it, promoted
        # clubs would vanish from `arrivals` until after their first match.
        membership = pd.concat([train, current_season_schedule], ignore_index=True)
        for team, direction in priors.arrivals(membership, "E0", current_season).items():
            spec = estimated.lookup("E0", direction)
            if spec is not None:
                specs[team] = spec
        param_priors = specs or None
    return dixon_coles.fit(
        train,
        xi=xi,
        param_priors=param_priors,
        season_decay=cfg.season_decay,
        reference_season=current_season,
    )


def _won(slate: pd.DataFrame, team: str) -> bool:
    fixture = slate[(slate["home_team"] == team) | (slate["away_team"] == team)]
    if len(fixture) != 1:
        raise ValueError(f"could not find exactly one fixture for {team}")
    row = fixture.iloc[0]
    return bool(
        (row.home_team == team and row.home_goals > row.away_goals)
        or (row.away_team == team and row.away_goals > row.home_goals)
    )


def _market_pick(slate: pd.DataFrame) -> tuple[str, float] | None:
    """Best available 1X2-priced winning team; draws are not LMS picks."""
    candidates: list[tuple[str, float]] = []
    for row in slate.dropna(subset=["odds_h", "odds_d", "odds_a"]).itertuples():
        p_home, _p_draw, p_away = market.implied_result_probs(
            float(row.odds_h), float(row.odds_d), float(row.odds_a)
        )
        candidates.extend(((str(row.home_team), p_home), (str(row.away_team), p_away)))
    return max(candidates, key=lambda item: (item[1], item[0])) if candidates else None


def _decision_row(
    slate: pd.DataFrame,
    season: int,
    slate_number: int,
    team: str,
    p_win: float,
) -> dict[str, object]:
    return {
        "season": season,
        "slate": slate_number,
        "date": slate["date"].min(),
        "team": team,
        "p_win": p_win,
        "won": _won(slate, team),
    }


def replay_slate(
    matches: pd.DataFrame,
    season: int,
    slate_number: int,
    *,
    used_clubs: Sequence[str] = (),
    suggested_used_clubs: Sequence[str] = (),
    xi: float = DEFAULT_MODEL.xi,
    cfg: ModelConfig = DEFAULT_MODEL,
    lms_cfg: LMSConfig = DEFAULT_LMS,
    min_training_matches: int = MIN_TRAINING_MATCHES,
    max_slate_gap_days: int = DEFAULT_SLATE_GAP_DAYS,
) -> LMSReplaySlate:
    """Build one historical LMS slate for a player-controlled replay.

    This is intentionally a single-slate service rather than an invocation
    of :func:`run`: the browser owns the player's used-club list and advances
    only after it has revealed the result.  That makes a replay a genuine
    pick-and-reveal experience, while keeping every forecast leakage-free.
    """
    if slate_number < 1:
        raise ValueError("game must be at least 1")
    if set(matches["division"].unique()) != {"E0"}:
        raise ValueError("LMS replay requires Premier League (E0) matches only")

    season_matches = matches[matches["season"] == season]
    if season_matches.empty:
        raise ValueError(f"no Premier League data is available for {season}/{str(season + 1)[2:]}")
    slates = infer_fixture_slates(season_matches, max_slate_gap_days)
    if slate_number > len(slates):
        raise ValueError(f"game must be between 1 and {len(slates)} for this season")

    slate = slates[slate_number - 1]
    start = slate["date"].min()
    train = matches[matches["date"] < start]
    if len(train) < min_training_matches:
        raise ValueError(
            "not enough earlier match data to build a leakage-free forecast for this game"
        )
    params = _fit_params(train, season_matches, season, xi, cfg)
    gameweeks = [_gameweek(part, number + 1) for number, part in enumerate(slates)]
    candidates = recommend_gameweek(
        params,
        gameweeks[slate_number - 1],
        used_clubs=used_clubs,
        cfg=cfg,
        lms_cfg=lms_cfg,
    )
    candidate_rows = tuple(
        {
            "team": candidate.team,
            "opponent": candidate.opponent,
            "is_home": candidate.is_home,
            "p_win": candidate.p_win,
            "p_draw": candidate.p_draw,
            "p_loss": candidate.p_loss,
            "confidence": candidate.confidence,
            "won": _won(slate, candidate.team),
        }
        for candidate in candidates
    )
    market_choice = _market_pick(slate)
    # The comparator gets its own one-club-once history. It is therefore a
    # real alternative route, not a recommendation distorted by the clubs
    # the player has already spent. The horizon is deliberately short: the
    # current fixture schedule is known, but a long historical reconstruction
    # would overstate how far-ahead it could have planned in practice.
    suggestion = plan_horizon(
        params,
        gameweeks[slate_number - 1:slate_number - 1 + lms_cfg.horizon_gameweeks],
        used_clubs=suggested_used_clubs,
        cfg=cfg,
        lms_cfg=lms_cfg,
    )
    suggested_pick = suggestion.this_week.candidate.team if suggestion.this_week else None
    return LMSReplaySlate(
        season=season,
        slate=slate_number,
        total_slates=len(slates),
        date=start,
        fixtures=tuple(
            {
                "home": str(row.home_team),
                "away": str(row.away_team),
                "score": f"{int(row.home_goals)}–{int(row.away_goals)}",
            }
            for row in slate.itertuples()
        ),
        candidates=candidate_rows,
        model_pick=candidate_rows[0]["team"] if candidate_rows else None,
        market_pick=market_choice[0] if market_choice is not None else None,
        suggested_pick=suggested_pick,
        suggested_won=_won(slate, suggested_pick) if suggested_pick else None,
        suggested_path=tuple(
            {
                "game": pick.gameweek,
                "team": pick.candidate.team,
                "opponent": pick.candidate.opponent,
                "is_home": pick.candidate.is_home,
                "p_win": pick.candidate.p_win,
            }
            for pick in suggestion.picks
        ),
    )


def run(
    matches: pd.DataFrame,
    test_seasons: Iterable[int],
    *,
    horizon: int = DEFAULT_LMS.horizon_gameweeks,
    xi: float = DEFAULT_MODEL.xi,
    cfg: ModelConfig = DEFAULT_MODEL,
    lms_cfg: LMSConfig = DEFAULT_LMS,
    refit_every_days: int | None = None,
    min_training_matches: int = MIN_TRAINING_MATCHES,
    max_slate_gap_days: int = DEFAULT_SLATE_GAP_DAYS,
) -> LMSBacktestResult:
    """Replay historical Premier League fixture slates without result leakage.

    ``matches`` must be the E0 corpus.  Future fixtures are supplied to the
    horizon planner because the Premier League publishes a schedule in
    advance, but the reconstructed schedule includes later postponements;
    hence the result reports that caveat rather than presenting the horizon
    arm as a perfectly live reproduction.
    """
    if horizon < 1:
        raise ValueError("horizon must be at least 1")
    if refit_every_days is None:
        refit_every_days = cfg.refit_every_days
    if refit_every_days < 1:
        raise ValueError("refit_every_days must be at least 1")
    divisions = set(matches["division"].unique())
    if divisions != {"E0"}:
        raise ValueError("LMS backtest requires Premier League (E0) matches only")

    weekly_rows: list[dict[str, object]] = []
    market_rows: list[dict[str, object]] = []
    planned_rows: list[dict[str, object]] = []
    greedy_rows: list[dict[str, object]] = []
    slate_count = skipped = 0

    for season in sorted(set(int(value) for value in test_seasons)):
        season_slates = infer_fixture_slates(
            matches[matches["season"] == season], max_slate_gap_days
        )
        gameweeks = [_gameweek(slate, number + 1) for number, slate in enumerate(season_slates)]
        params = None
        last_fit_at: pd.Timestamp | None = None

        for index, slate in enumerate(season_slates):
            slate_count += 1
            start = slate["date"].min()
            train = matches[matches["date"] < start]
            if len(train) < min_training_matches:
                skipped += 1
                continue
            if params is None or (start - last_fit_at).days >= refit_every_days:
                params = _fit_params(train, matches[matches["season"] == season], season, xi, cfg)
                last_fit_at = start

            weekly = recommend_gameweek(params, gameweeks[index], cfg=cfg, lms_cfg=lms_cfg)
            if weekly:
                top = weekly[0]
                weekly_rows.append(
                    _decision_row(slate, season, index + 1, top.team, top.p_win)
                )
            market_choice = _market_pick(slate)
            if market_choice is not None:
                team, p_win = market_choice
                market_rows.append(_decision_row(slate, season, index + 1, team, p_win))

            window = gameweeks[index:index + horizon]
            actual = season_slates[index:index + horizon]
            if len(window) < horizon:
                continue
            plan = plan_horizon(params, window, cfg=cfg, lms_cfg=lms_cfg)
            if len(plan.picks) == horizon:
                survived = all(_won(actual[offset], pick.candidate.team) for offset, pick in enumerate(plan.picks))
                planned_rows.append(
                    {
                        "season": season,
                        "slate": index + 1,
                        "date": start,
                        "team": plan.this_week.candidate.team,
                        "p_win": plan.survival_p,
                        "won": survived,
                    }
                )
            if len(plan.greedy_picks) == horizon:
                greedy_survived = all(
                    _won(actual[offset], pick.candidate.team)
                    for offset, pick in enumerate(plan.greedy_picks)
                )
                greedy_rows.append(
                    {
                        "season": season,
                        "slate": index + 1,
                        "date": start,
                        "team": plan.greedy_picks[0].candidate.team,
                        "p_win": plan.greedy_survival_p,
                        "won": greedy_survived,
                    }
                )

    return LMSBacktestResult(
        weekly_model=pd.DataFrame(weekly_rows),
        weekly_market=pd.DataFrame(market_rows),
        planned_horizon=pd.DataFrame(planned_rows),
        greedy_horizon=pd.DataFrame(greedy_rows),
        slate_count=slate_count,
        skipped_slates=skipped,
        horizon=horizon,
    )
