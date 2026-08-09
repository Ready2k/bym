"""What a real Super 6 round actually looks like (SPEC.md §13.2).

`season_simulation.sample_synthetic_round` stands in for the rounds Sky
hasn't published yet by drawing six fixtures uniformly from the historical
corpus — any pairing, any era. §13.2 wants that draw to match *observed*
Super 6 characteristics instead: league mix, favourite-strength
distribution, home/away balance, fixture competitiveness, expected-goals
distribution.

The blocker was never the sampler. It was that "observed characteristics"
had nothing to be observed from — there is no record of what Sky's rounds
look like beyond the rounds themselves, and nothing measured them. This
module is that measurement. It does **not** change how synthetic rounds
are drawn; adopting a matched sampler needs an ablation against the
current uniform draw (§28), and that needs a season's worth of rounds to
have any power at all.

Deliberately derived, never stored. Every input is already persisted:
`prediction_history` holds each round's fixtures and the market snapshot
the forecast consumed (§19), and the corpus holds division membership. A
parallel store would be one more thing to keep in sync, and would start
empty — deriving means every round already recorded is characterised
retroactively, and every future round is characterised for free.

One characteristic is computed rather than recalled. Expected goals is
not in the snapshot (`FixtureForecast` carries probabilities, not λ), so
callers pass a function that computes it from the current fit. That is
the right basis anyway: `sample_synthetic_round` also forecasts its draws
with current parameters, so target and sampler are measured on the same
footing. Rounds characterised without one report `None` rather than a
guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import prediction_history

# §13.2's "fixture competitiveness". The gap between the most and second
# most likely results: 0.0 is a coin flip between two outcomes, large is a
# foregone conclusion. Expressed as a gap rather than a "competitiveness
# score" so the direction is unambiguous in the raw number.
ExpectedGoalsFn = Callable[[str, str], float | None]


@dataclass(frozen=True)
class FixtureCharacteristics:
    home_team: str
    away_team: str
    division: str | None  # None: neither team appears in the corpus
    favourite_strength: float | None  # P(most likely result), from the market
    favourite_at_home: bool | None
    competitiveness: float | None  # P(most likely) - P(second most likely)
    expected_goals: float | None  # λ_home + λ_away under the current fit


@dataclass(frozen=True)
class RoundCharacteristics:
    season: int
    round_number: int
    fixtures: tuple[FixtureCharacteristics, ...]
    division_mix: tuple[tuple[str, int], ...]  # (division, count), sorted
    home_favourite_share: float | None
    mean_favourite_strength: float | None
    mean_competitiveness: float | None
    mean_expected_goals: float | None


def _market_results(fixture: Mapping) -> tuple[float, float, float] | None:
    """(home, draw, away) implied probabilities, when odds were stored."""
    market = fixture.get("market_snapshot") or {}
    values = (
        market.get("home_probability"),
        market.get("draw_probability"),
        market.get("away_probability"),
    )
    if any(value is None for value in values):
        return None
    return values


def characterise_fixture(
    fixture: Mapping,
    *,
    divisions: Mapping[str, str] | None = None,
    expected_goals: ExpectedGoalsFn | None = None,
) -> FixtureCharacteristics:
    """Measure one fixture from its stored forecast snapshot.

    Favourite strength and competitiveness come from the market rather
    than from our own forecast: §13.2 is describing what kind of fixture
    Sky picked, which is a property of the fixture, not of how well we
    happened to model it. Any of these can be `None` — a fixture forecast
    without odds, or a team absent from the corpus, is reported as unknown
    rather than filled in.
    """
    home = str(fixture.get("home_team") or "")
    away = str(fixture.get("away_team") or "")
    divisions = divisions or {}
    # A fixture's division is the home team's, falling back to the away
    # team's — Super 6 never mixes divisions within a fixture, so the two
    # agree whenever both are known.
    division = divisions.get(home) or divisions.get(away)

    probabilities = _market_results(fixture)
    strength = runner_up = None
    favourite_at_home = None
    if probabilities is not None:
        home_p, _draw_p, away_p = probabilities
        ordered = sorted(probabilities, reverse=True)
        strength, runner_up = ordered[0], ordered[1]
        # A draw being the single most likely result means neither team is
        # favoured, which is not the same as the home team being favoured.
        favourite_at_home = home_p > away_p if home_p != away_p else None

    goals = expected_goals(home, away) if expected_goals is not None else None
    return FixtureCharacteristics(
        home_team=home,
        away_team=away,
        division=division,
        favourite_strength=round(strength, 4) if strength is not None else None,
        favourite_at_home=favourite_at_home,
        competitiveness=(
            round(strength - runner_up, 4) if strength is not None else None
        ),
        expected_goals=round(goals, 4) if goals is not None else None,
    )


def _mean(values: Sequence[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return round(sum(known) / len(known), 4) if known else None


def characterise_round(
    season: int,
    round_number: int,
    fixtures: Sequence[Mapping],
    *,
    divisions: Mapping[str, str] | None = None,
    expected_goals: ExpectedGoalsFn | None = None,
) -> RoundCharacteristics:
    """Measure one round's six fixtures, plus the round-level shape."""
    measured = tuple(
        characterise_fixture(fixture, divisions=divisions, expected_goals=expected_goals)
        for fixture in fixtures
    )
    mix: dict[str, int] = {}
    for fixture in measured:
        mix[fixture.division or "UNKNOWN"] = mix.get(fixture.division or "UNKNOWN", 0) + 1
    sided = [
        fixture.favourite_at_home
        for fixture in measured
        if fixture.favourite_at_home is not None
    ]
    return RoundCharacteristics(
        season=season,
        round_number=round_number,
        fixtures=measured,
        division_mix=tuple(sorted(mix.items())),
        home_favourite_share=(
            round(sum(sided) / len(sided), 4) if sided else None
        ),
        mean_favourite_strength=_mean([f.favourite_strength for f in measured]),
        mean_competitiveness=_mean([f.competitiveness for f in measured]),
        mean_expected_goals=_mean([f.expected_goals for f in measured]),
    )


def observed_rounds(
    *,
    prediction_path: Path = prediction_history.DEFAULT_HISTORY_PATH,
    divisions: Mapping[str, str] | None = None,
    expected_goals: ExpectedGoalsFn | None = None,
) -> tuple[RoundCharacteristics, ...]:
    """Characterise every round with a stored forecast, oldest first.

    The latest snapshot per round is used: a round re-forecast closer to
    its deadline describes the same six fixtures, and the fixtures are
    what's being measured.
    """
    store = prediction_history.load_history(prediction_path)
    rounds = []
    for data in store.get("rounds", {}).values():
        snapshots = data.get("snapshots") or []
        if not snapshots:
            continue
        rounds.append(
            characterise_round(
                int(data["season"]),
                int(data["round_number"]),
                snapshots[-1]["fixtures"],
                divisions=divisions,
                expected_goals=expected_goals,
            )
        )
    return tuple(sorted(rounds, key=lambda item: (item.season, item.round_number)))


@dataclass(frozen=True)
class ObservedProfile:
    """The accumulating §13.2 target — what a stratified sampler would match.

    `rounds` is the honest headline: with one round observed this describes
    six fixtures, and any sampler matched to it would be fitting noise.
    """

    rounds: int
    fixtures: int
    division_mix: tuple[tuple[str, int], ...]
    home_favourite_share: float | None
    mean_favourite_strength: float | None
    favourite_strength_range: tuple[float, float] | None
    mean_competitiveness: float | None
    mean_expected_goals: float | None


def observed_profile(rounds: Sequence[RoundCharacteristics]) -> ObservedProfile:
    """Pool characterised rounds into the distribution a matched sampler
    would be asked to reproduce (§13.2 step 1).

    Pooling at *fixture* level, not by averaging round averages: the
    sampler draws fixtures, so the fixture is the unit, and a round with
    fewer priced fixtures should not count for as much as a complete one.
    """
    fixtures = [fixture for item in rounds for fixture in item.fixtures]
    mix: dict[str, int] = {}
    for fixture in fixtures:
        mix[fixture.division or "UNKNOWN"] = mix.get(fixture.division or "UNKNOWN", 0) + 1
    strengths = [f.favourite_strength for f in fixtures if f.favourite_strength is not None]
    sided = [f.favourite_at_home for f in fixtures if f.favourite_at_home is not None]
    return ObservedProfile(
        rounds=len(rounds),
        fixtures=len(fixtures),
        division_mix=tuple(sorted(mix.items())),
        home_favourite_share=round(sum(sided) / len(sided), 4) if sided else None,
        mean_favourite_strength=_mean(strengths),
        favourite_strength_range=(
            (round(min(strengths), 4), round(max(strengths), 4)) if strengths else None
        ),
        mean_competitiveness=_mean([f.competitiveness for f in fixtures]),
        mean_expected_goals=_mean([f.expected_goals for f in fixtures]),
    )


# Rounds of real observation before a stratified sampler has enough to
# match against. Not a threshold the code enforces — nothing here reads
# it — but a stated expectation, so "is there enough yet?" has a written
# answer rather than being re-argued each time someone looks.
ROUNDS_FOR_MATCHING = 20


def matching_readiness(profile: ObservedProfile) -> str:
    """A plain-language answer to "can §13.2 be attempted yet?"."""
    if profile.rounds >= ROUNDS_FOR_MATCHING:
        return (
            f"{profile.rounds} rounds observed ({profile.fixtures} fixtures) — "
            "enough to build a stratified sampler and ablate it against the "
            "current uniform draw (§28)."
        )
    return (
        f"{profile.rounds} of ~{ROUNDS_FOR_MATCHING} rounds observed "
        f"({profile.fixtures} fixtures). Characteristics are accumulating; a "
        "sampler matched to this few would fit noise, and an ablation against "
        "the uniform draw would have no power to detect the difference."
    )
