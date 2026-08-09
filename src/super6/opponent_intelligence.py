"""Opponent behavioural modelling relative to our own forecasts (SPEC.md §11).

`league.opponent_profiles` deliberately stays pick-only, with no reference
to our own forecasts — its module docstring keeps "what a rival tends to
pick" cleanly separate from "what we predict". This module is the other
half of §11, and it only ever *reads* both records after the fact; nothing
here feeds back into `λ`, `P(score)` or any live forecast (§22).

Four things a pure pick tally cannot answer on its own:

1. Whether a rival's picks lean toward or away from our model's favoured
   outcome ("favourite bias" / "upset frequency") — needs the forecast
   snapshot stored for the same fixture (`prediction_history`).
2. Whether that lean attaches to *team identity* rather than to fixture
   position — §11's "favours big clubs / underrates promoted teams".
   Reputation is taken from the bookmaker odds already persisted with
   each forecast, never from the model's own fitted team strength, so
   the separation above survives it (`team_bias_profiles`).
3. How much weight a thin history deserves before it dominates a
   prediction about that rival — §11's continuous "opponent influence
   weight", the same empirical-Bayes shrinkage shape as §29.4's
   promoted-team priors (`model/priors.py`), applied to competitor
   behaviour instead of team strength. No round-14 cliff.
4. A predictive distribution over what a specific rival is likely to pick
   next — built the same way §32.3 blends market and model probabilities
   (`model/market.py`'s result-constrained reweighting), applied to two
   pick "buckets" (matches our favourite / does not) instead of three
   match results.

Everything here is a documented heuristic baseline, not a fitted or
backtested model — there is no opponent-prediction corpus yet (§11: this
starts accumulating from Round 1 and "does not block Phase 3"). Treat
every number as visibly checkable once real picks and results exist to
check it against, not as a finished model. §28's ablation discipline
applies here exactly as it does to the forecasting core: nothing here has
earned a more complex replacement yet because there is nothing to test
one against.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from . import prediction_history
from .league import (
    OpponentProfile,
    OpponentRound,
    ResultKey,
    Scoreline,
    league_standings,
    opponent_profiles,
)

DEFAULT_HALF_WEIGHT_AT = 20  # picks at which influence weight reaches 0.5


def opponent_influence_weight(
    sample_size: int, *, half_weight_at: int = DEFAULT_HALF_WEIGHT_AT
) -> float:
    """§11: a continuous 0..1 weight — never a hard cutoff.

    ``sample_size / (sample_size + half_weight_at)``: zero evidence gives
    weight 0 (pure league-average prior), rising smoothly toward 1 as picks
    accumulate, crossing 0.5 at ``half_weight_at`` picks.

    §11 also names posterior_uncertainty and predictive_validation as
    inputs alongside sample size. Both need an opponent-prediction track
    record to compute honestly — was a shrunk prediction actually closer
    than the unshrunk one, checked out of sample — which does not exist
    yet with a handful of imported rounds. Sample size alone is
    implemented now; the other two terms are a natural extension once
    there is something to validate them against, not a hidden constant
    standing in for them.
    """
    if sample_size < 0:
        raise ValueError("sample_size cannot be negative")
    if half_weight_at <= 0:
        raise ValueError("half_weight_at must be positive")
    return sample_size / (sample_size + half_weight_at)


def _result_of(score: Scoreline) -> str:
    home, away = score
    if home > away:
        return "HOME"
    if home < away:
        return "AWAY"
    return "DRAW"


def result_of(home_goals: int, away_goals: int) -> str:
    """Public HOME/AWAY/DRAW label for a scoreline — the word-based
    convention this module and `season_simulation.py` share, as opposed to
    `scoring.result_of`'s H/D/A letters used by the scoring core."""
    return _result_of((home_goals, away_goals))


@dataclass(frozen=True)
class JoinedPick:
    """One recorded opponent pick, matched to the forecast fixture it was made against."""

    participant: str
    season: int
    round_number: int
    fixture_index: int
    home_team: str | None
    away_team: str | None
    pick: Scoreline
    model_result: str | None  # HOME/DRAW/AWAY — our own recommended pick's result
    # The market state the forecast actually consumed (§19's odds-snapshot
    # linkage). Both None for a fixture forecast without odds, and for any
    # snapshot written before `market_snapshot` was persisted at all.
    market_home_probability: float | None = None
    market_away_probability: float | None = None


def join_picks_with_forecasts(
    history: Sequence[OpponentRound],
    *,
    prediction_path: Path = prediction_history.DEFAULT_HISTORY_PATH,
) -> tuple[JoinedPick, ...]:
    """Match every recorded pick to the forecast fixture it was made against.

    Only picks whose (season, round_number) has a stored forecast snapshot
    can be joined — an imported round for which no local forecast was ever
    generated silently contributes nothing here rather than guessing at
    who the favourite was.
    """
    cache: dict[tuple[int, int], list[dict] | None] = {}
    joined: list[JoinedPick] = []
    for record in history:
        key = (record.season, record.round_number)
        if key not in cache:
            snapshots = prediction_history.round_snapshots(
                record.season, record.round_number, prediction_path
            )
            cache[key] = snapshots[-1]["fixtures"] if snapshots else None
        fixtures = cache[key]
        if not fixtures:
            continue
        by_index = {fixture["fixture_index"]: fixture for fixture in fixtures}
        for index, pick in enumerate(record.picks):
            fixture = by_index.get(index)
            if fixture is None:
                continue
            market = fixture.get("market_snapshot") or {}
            joined.append(
                JoinedPick(
                    participant=record.participant,
                    season=record.season,
                    round_number=record.round_number,
                    fixture_index=index,
                    home_team=fixture.get("home_team"),
                    away_team=fixture.get("away_team"),
                    pick=pick,
                    model_result=fixture.get("result"),
                    market_home_probability=market.get("home_probability"),
                    market_away_probability=market.get("away_probability"),
                )
            )
    return tuple(joined)


@dataclass(frozen=True)
class FavouriteBiasProfile:
    participant: str
    sample_size: int
    favourite_rate: float | None  # None: no joinable evidence yet
    upset_rate: float | None
    influence_weight: float


def favourite_bias_profiles(
    joined: Sequence[JoinedPick],
) -> tuple[FavouriteBiasProfile, ...]:
    """§11 "favourite bias": how often a rival's pick shares our model's favoured result.

    Stated plainly, this is an approximation: `model_result` is our own
    *recommended* pick's result, which is usually but not always the
    model's single highest-probability outcome — a strategic differential
    (§12) can occasionally diverge from it. Good enough to describe a
    tendency; not precise enough to treat as ground truth about the "true"
    favourite for that fixture.
    """
    grouped: dict[str, list[JoinedPick]] = {}
    for item in joined:
        grouped.setdefault(item.participant, []).append(item)
    profiles: list[FavouriteBiasProfile] = []
    for participant, picks in grouped.items():
        with_result = [pick for pick in picks if pick.model_result is not None]
        n = len(with_result)
        if n == 0:
            profiles.append(
                FavouriteBiasProfile(participant, 0, None, None, opponent_influence_weight(0))
            )
            continue
        matches = sum(1 for pick in with_result if _result_of(pick.pick) == pick.model_result)
        rate = round(matches / n, 4)
        profiles.append(
            FavouriteBiasProfile(
                participant=participant,
                sample_size=n,
                favourite_rate=rate,
                upset_rate=round(1 - rate, 4),
                influence_weight=opponent_influence_weight(n),
            )
        )
    return tuple(sorted(profiles, key=lambda profile: profile.participant.casefold()))


def league_average_favourite_rate(joined: Sequence[JoinedPick]) -> float | None:
    """Pooled favourite-match rate across every participant — the shrinkage target."""
    with_result = [pick for pick in joined if pick.model_result is not None]
    if not with_result:
        return None
    matches = sum(1 for pick in with_result if _result_of(pick.pick) == pick.model_result)
    return round(matches / len(with_result), 4)


# ---------------------------------------------------------------------------
# §11 team-specific bias: favours "big" clubs, underrates promoted teams.
#
# `favourite_bias_profiles` above is fixture-relative — it only knows
# whether a pick matched *this fixture's* favourite. §11 also wants the
# team-identity version: does a rival back Man City because it is Man
# City, over and above what the fixture warranted?
#
# That needs a club-reputation signal, and the choice of signal is a
# design decision, not a detail. The fitted model's own α would be the
# most direct one and is deliberately *not* used: this module and
# `league.py` are model-independent by design (see their docstrings), and
# reaching into the forecasting core for a "big club" proxy would end
# that separation for a convenience. Instead reputation comes from the
# bookmaker odds already persisted alongside each forecast — externally
# sourced, so the separation holds, and available with no new data source.

BIG_CLUB_STRENGTH = 0.10  # venue-adjusted implied win probability above average


@dataclass(frozen=True)
class TeamReputation:
    """How strongly the *market* rates a team, pooled over its appearances."""

    team: str
    appearances: int
    # Mean implied win probability minus the mean for that venue, so a
    # team is not rated "big" merely for having been drawn at home a lot:
    # 0.0 is an average team, +0.10 is ten percentage points above one.
    market_strength: float


def team_market_reputations(
    joined: Sequence[JoinedPick], *, min_appearances: int = 1
) -> tuple[TeamReputation, ...]:
    """Rank teams by market-implied strength, from stored odds snapshots.

    Home advantage is removed by centring each appearance on the mean
    implied win probability *for that venue* across the same corpus,
    rather than pooling home and away probabilities as if they were
    comparable. This is a market proxy for reputation, not a measurement
    of it: it says what bookmakers priced, which is what a rival plausibly
    reacts to, and it can only see teams that have appeared in a round we
    forecast and stored odds for.
    """
    if min_appearances < 1:
        raise ValueError("min_appearances must be positive")
    # One entry per *fixture*, not per pick: `joined` repeats every fixture
    # once per participant who picked it, which would otherwise weight a
    # team by how many rivals happened to be imported that round.
    fixtures: dict[tuple[int, int, int], JoinedPick] = {}
    for item in joined:
        if _market_pair(item) is None:
            continue
        fixtures[(item.season, item.round_number, item.fixture_index)] = item
    if not fixtures:
        return ()

    appearances = list(fixtures.values())
    average_home = sum(item.market_home_probability for item in appearances) / len(appearances)
    average_away = sum(item.market_away_probability for item in appearances) / len(appearances)

    deltas: dict[str, list[float]] = {}
    for item in appearances:
        deltas.setdefault(item.home_team, []).append(item.market_home_probability - average_home)
        deltas.setdefault(item.away_team, []).append(item.market_away_probability - average_away)
    return tuple(
        sorted(
            (
                TeamReputation(team, len(values), round(sum(values) / len(values), 4))
                for team, values in deltas.items()
                if len(values) >= min_appearances
            ),
            key=lambda reputation: (-reputation.market_strength, reputation.team.casefold()),
        )
    )


def _market_pair(item: JoinedPick) -> tuple[float, float] | None:
    """This fixture's (home, away) implied win probabilities, when it has
    both teams named and odds actually stored against it."""
    if item.home_team is None or item.away_team is None:
        return None
    home, away = item.market_home_probability, item.market_away_probability
    if home is None or away is None:
        return None
    return home, away


@dataclass(frozen=True)
class CategoryBias:
    """A rival's picks for one class of team, against what the market priced."""

    picks: int
    backed_rate: float | None  # how often they picked that team to win
    market_rate: float | None  # how often the market said it would
    bias: float | None  # backed - market; positive => they back it more than priced
    influence_weight: float


NO_CATEGORY_EVIDENCE = CategoryBias(0, None, None, None, opponent_influence_weight(0))


@dataclass(frozen=True)
class TeamBiasProfile:
    participant: str
    big_clubs: CategoryBias
    promoted: CategoryBias


def team_bias_profiles(
    joined: Sequence[JoinedPick],
    *,
    reputations: Sequence[TeamReputation] | None = None,
    promoted_teams: Sequence[str] = (),
    big_club_strength: float = BIG_CLUB_STRENGTH,
) -> tuple[TeamBiasProfile, ...]:
    """§11: does a rival over-back big clubs, or under-back promoted teams?

    Each category is self-baselined per fixture — a rival's rate of
    backing the team is compared against the mean probability the market
    gave that same team winning those same fixtures. So `bias` is not "how
    often they back Man City" (which would mostly measure how good Man
    City is) but "how much more often than the odds warranted". Positive
    big-club bias is §11's "favours big clubs"; *negative* promoted bias
    is its "underrates promoted teams".

    A fixture only counts toward a category when exactly one of its two
    teams is in it — a big-club-versus-big-club fixture has no unambiguous
    subject, and silently picking one would invent a direction the pick
    never expressed.

    Like everything else in this module, a documented heuristic baseline:
    the counts here will be tiny for a long time, which is what
    `influence_weight` is for. Read a bias of +0.30 over four picks as
    "nothing yet", not as a finding.
    """
    if reputations is None:
        reputations = team_market_reputations(joined)
    big_clubs = {
        reputation.team
        for reputation in reputations
        if reputation.market_strength >= big_club_strength
    }
    promoted = set(promoted_teams)

    grouped: dict[str, list[JoinedPick]] = {}
    for item in joined:
        grouped.setdefault(item.participant, []).append(item)

    profiles = [
        TeamBiasProfile(
            participant=participant,
            big_clubs=_category_bias(picks, big_clubs),
            promoted=_category_bias(picks, promoted),
        )
        for participant, picks in grouped.items()
    ]
    return tuple(sorted(profiles, key=lambda profile: profile.participant.casefold()))


def _category_bias(picks: Sequence[JoinedPick], group: set[str]) -> CategoryBias:
    samples: list[tuple[float, bool]] = []
    for item in picks:
        market = _market_pair(item)
        if market is None:
            continue
        home_in, away_in = item.home_team in group, item.away_team in group
        if home_in == away_in:  # neither side, or both — no single subject
            continue
        side = "HOME" if home_in else "AWAY"
        samples.append((market[0] if home_in else market[1], _result_of(item.pick) == side))
    if not samples:
        return NO_CATEGORY_EVIDENCE
    backed = sum(1 for _, was_backed in samples if was_backed) / len(samples)
    priced = sum(probability for probability, _ in samples) / len(samples)
    return CategoryBias(
        picks=len(samples),
        backed_rate=round(backed, 4),
        market_rate=round(priced, 4),
        bias=round(backed - priced, 4),
        influence_weight=opponent_influence_weight(len(samples)),
    )


def league_average_profile(history: Sequence[OpponentRound]) -> OpponentProfile | None:
    """Pool every recorded pick, across every participant, into one profile.

    This is the "league-wide average opponent behaviour" that §11 says a
    thin individual history should shrink toward. Relabelling every record
    under one synthetic participant and reusing `league.opponent_profiles`
    keeps the pooled statistic defined identically to the per-rival one,
    rather than maintaining a second calculation that could drift from it.
    """
    if not history:
        return None
    pooled = tuple(
        OpponentRound(
            participant="__LEAGUE_AVERAGE__",
            season=record.season,
            round_number=record.round_number,
            picks=record.picks,
            recorded_at=record.recorded_at,
        )
        for record in history
    )
    profiles = opponent_profiles(pooled)
    return profiles[0] if profiles else None


def predict_pick_distribution(
    profile: OpponentProfile,
    favourite: FavouriteBiasProfile,
    league_average: OpponentProfile,
    average_favourite_rate: float | None,
    fixture_favoured_result: str | None,
) -> dict[str, float]:
    """Predict a rival's likely-pick distribution for a fixture (§11).

    Two blends, deliberately mirroring §32.3's result-constrained
    reweighting (`model/market.py`) rather than inventing a new shape:

    1. Shrink the rival's own scoreline frequencies toward the league-wide
       average by `influence_weight` — a thin history contributes almost
       nothing of its own shape yet, a deep one dominates.
    2. If the fixture has a model-favoured result, redistribute mass
       between "scorelines matching that result" and "scorelines that
       don't", using the rival's own (also shrunk) favourite rate, while
       preserving each side's internal shape from step 1 — the same
       ``q(h,a) = p(h,a) × [q_R / p_R]`` idea, applied to two buckets
       instead of three results.

    Only scorelines either profile has actual evidence for get a
    probability; this can legitimately return ``{}`` for a brand-new
    fixture against a brand-new rival, which is the honest answer, not a
    bug to paper over with an invented uniform prior.
    """
    weight = favourite.influence_weight
    own = dict(profile.score_frequency)
    average = dict(league_average.score_frequency)
    scores = set(own) | set(average)
    blended = {
        score: weight * own.get(score, 0.0) + (1 - weight) * average.get(score, 0.0)
        for score in scores
    }
    total = sum(blended.values())
    if total <= 0:
        return {}
    blended = {score: value / total for score, value in blended.items()}

    if (
        fixture_favoured_result is None
        or favourite.favourite_rate is None
        or average_favourite_rate is None
    ):
        return {score: round(value, 4) for score, value in blended.items()}

    def parse(score: str) -> Scoreline:
        home, away = score.split("-", 1)
        return int(home), int(away)

    matching = {
        score: value for score, value in blended.items() if _result_of(parse(score)) == fixture_favoured_result
    }
    other = {score: value for score, value in blended.items() if score not in matching}
    p_match, p_other = sum(matching.values()), sum(other.values())
    if p_match <= 0 or p_other <= 0:
        # Every scoreline with evidence sits on one side of the favourite
        # split — there is nothing to redistribute toward or away from.
        return {score: round(value, 4) for score, value in blended.items()}

    own_rate = weight * favourite.favourite_rate + (1 - weight) * average_favourite_rate
    scale_match = own_rate / p_match
    scale_other = (1 - own_rate) / p_other
    result = {score: round(value * scale_match, 4) for score, value in matching.items()}
    result.update({score: round(value * scale_other, 4) for score, value in other.items()})
    return result


@dataclass(frozen=True)
class ChaseDefendProfile:
    """§11 'tendency to chase when behind / defend when ahead'."""

    participant: str
    picks_trailing: int  # fixture picks made while trailing, pooled across rounds
    picks_leading: int  # fixture picks made while leading, pooled across rounds
    divergence_rate_when_trailing: float | None
    divergence_rate_when_leading: float | None
    chase_when_behind: float | None  # trailing rate - leading rate; positive => chases


def chase_defend_tendencies(
    history: Sequence[OpponentRound],
    joined: Sequence[JoinedPick],
    results: Mapping[ResultKey, Scoreline | None],
) -> tuple[ChaseDefendProfile, ...]:
    """For each round a participant picked, was their pick set bolder while trailing?

    "Trailing" and "leading" are computed from standings built only from
    rounds strictly before the one in question, scored against known
    results — never the round's own outcome — so this cannot leak the
    result it is trying to explain. A pick "diverges" when its result
    doesn't match our model's favoured result for that fixture; comparing
    the divergence rate while trailing against the rate while leading is a
    coarse first cut (rank 2 by a single point counts as "trailing" just
    like rank 6), documented rather than hidden, and good enough to be
    visibly wrong in an inspectable way once more rounds accumulate.
    """
    by_round: dict[tuple[int, int], list[JoinedPick]] = {}
    for item in joined:
        by_round.setdefault((item.season, item.round_number), []).append(item)

    round_keys = sorted({(record.season, record.round_number) for record in history})

    trailing: dict[str, list[bool]] = {}
    leading: dict[str, list[bool]] = {}

    for season, round_number in round_keys:
        earlier = [
            record
            for record in history
            if (record.season, record.round_number) < (season, round_number)
        ]
        if not earlier:
            continue
        standings = league_standings(earlier, results)
        if not standings:
            continue
        rank_by_participant = {row.participant: row.rank for row in standings}
        leader_rank = min(rank_by_participant.values())
        for item in by_round.get((season, round_number), ()):
            if item.model_result is None:
                continue
            rank = rank_by_participant.get(item.participant)
            if rank is None:
                continue
            diverged = _result_of(item.pick) != item.model_result
            bucket = trailing if rank > leader_rank else leading
            bucket.setdefault(item.participant, []).append(diverged)

    participants = set(trailing) | set(leading)
    profiles: list[ChaseDefendProfile] = []
    for participant in sorted(participants, key=str.casefold):
        trail = trailing.get(participant, [])
        lead = leading.get(participant, [])
        trail_rate = round(sum(trail) / len(trail), 4) if trail else None
        lead_rate = round(sum(lead) / len(lead), 4) if lead else None
        chase = (
            round(trail_rate - lead_rate, 4)
            if trail_rate is not None and lead_rate is not None
            else None
        )
        profiles.append(
            ChaseDefendProfile(
                participant=participant,
                picks_trailing=len(trail),
                picks_leading=len(lead),
                divergence_rate_when_trailing=trail_rate,
                divergence_rate_when_leading=lead_rate,
                chase_when_behind=chase,
            )
        )
    return tuple(profiles)
