"""Command-line interface for the Phase 1A prediction engine.

    super6 fetch    --seasons 2019 2025 --data data/
    super6 predict  --round round.json --seasons 2021 2025 --data data/
    super6 backtest --test-season 2024 --seasons 2018 2024 --data data/
    super6 tune-xi  --validation-season 2024 --seasons 2018 2024 --data data/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import DEFAULT_DATA, DEFAULT_MODEL


def _season_range(args) -> list[int]:
    start, end = args.seasons
    return list(range(start, end + 1))


def cmd_fetch(args) -> int:
    from .data.football_data import fetch_season

    for year in _season_range(args):
        for div in args.divisions:
            path = fetch_season(year, div, Path(args.data), force=args.force)
            print(f"  {year}/{str(year + 1)[2:]} {div}: {path}")
    return 0


def cmd_predict(args) -> int:
    from .config import DEFAULT_MODEL
    from .data.football_data import load_matches, team_divisions
    from .model import dixon_coles, priors
    from .recommend import format_report, recommend_round
    from .round import load_round

    rnd = load_round(Path(args.round))
    matches = load_matches(Path(args.data), _season_range(args), tuple(args.divisions))

    known = set(matches["home_team"]) | set(matches["away_team"])
    for f in rnd.fixtures:
        for team in (f.home, f.away):
            if team not in known:
                print(
                    f"WARNING: '{team}' not in training data — it will use "
                    "league-average strength (§29.4). Check the spelling "
                    "matches Football-Data.co.uk names.",
                    file=sys.stderr,
                )

    # Per-division fits (validated ablation win — see VALIDATION.md), with
    # a pooled fallback for cross-division or unknown-team fixtures.
    divisions = team_divisions(matches)

    # §29.4 priors for teams newly arrived in their division this season,
    # estimated from earlier seasons only.
    arrival_priors: dict[str, dict[str, priors.PriorSpec]] = {}
    if DEFAULT_MODEL.use_promoted_priors:
        current_season = int(matches["season"].max())
        estimated = priors.estimate(matches[matches["season"] < current_season])
        for div in sorted(matches["division"].unique()):
            specs = {}
            for team, direction in priors.arrivals(
                matches, div, current_season
            ).items():
                spec = estimated.lookup(div, direction)
                if spec is not None:
                    specs[team] = spec
            arrival_priors[div] = specs

    pooled = dixon_coles.fit(matches, xi=args.xi)
    per_div: dict[str, dixon_coles.DixonColesParams] = {}

    def router(home: str, away: str) -> dixon_coles.DixonColesParams:
        div_h, div_a = divisions.get(home), divisions.get(away)
        if div_h is None or div_h != div_a:
            return pooled
        if div_h not in per_div:
            per_div[div_h] = dixon_coles.fit(
                matches[matches["division"] == div_h],
                xi=args.xi,
                param_priors=arrival_priors.get(div_h) or None,
                season_decay=DEFAULT_MODEL.season_decay,
                reference_season=int(matches["season"].max()),
            )
        return per_div[div_h]

    print(format_report(recommend_round(router, rnd)))
    return 0


def cmd_backtest(args) -> int:
    from .backtest.walk_forward import compare
    from .data.football_data import load_matches

    matches = load_matches(Path(args.data), _season_range(args), tuple(args.divisions))
    table = compare(matches, args.test_season, xi=args.xi)
    print(table.to_string(index=False))
    return 0


def cmd_tune_xi(args) -> int:
    from .backtest.walk_forward import tune_xi
    from .data.football_data import load_matches

    matches = load_matches(Path(args.data), _season_range(args), tuple(args.divisions))
    table = tune_xi(matches, args.validation_season)
    print(table.to_string(index=False))
    best = table.loc[table["logloss"].idxmin()]
    print(f"\nBest out-of-sample log loss: xi={best['xi']}")
    print(
        "Read the whole curve, not just the winner: if several xi perform "
        "identically, prefer the stable region (§29.2). Selecting xi here "
        "makes this season validation data — do not quote its scores as "
        "unbiased performance afterwards (see VALIDATION.md)."
    )
    return 0


def cmd_final_test(args) -> int:
    """Single-shot evaluation of the frozen model on the holdout season."""
    from .backtest.metrics import evaluate
    from .backtest.walk_forward import run_favourite_baseline, run_frozen_model
    from .data.football_data import load_matches

    if not args.i_understand_this_is_single_use:
        print(
            "REFUSED. The holdout season is single-use (VALIDATION.md).\n"
            "Running it and then tuning against the result destroys the one\n"
            "unbiased estimate this project has. Re-run with\n"
            "--i-understand-this-is-single-use once the model is frozen.",
            file=sys.stderr,
        )
        return 2

    matches = load_matches(Path(args.data), _season_range(args), tuple(args.divisions))
    test_mask = matches["season"] == args.test_season

    rows = []
    for label, closing in [("MARKET-AVAILABLE", False), ("MARKET-ORACLE", True)]:
        baseline = run_favourite_baseline(matches, test_mask, closing=closing)
        if baseline is not None:
            rows.append(baseline.metrics.as_row(f"{label} fav +2-1/1-1/1-2"))

    frozen = run_frozen_model(matches, args.test_season)
    rows.append(frozen.metrics.as_row("PHASE1_MODEL_V1 (frozen)"))

    import pandas as pd

    print(pd.DataFrame(rows).to_string(index=False))
    print(
        "\nThis number is now spent. Record it in results/ and do not tune "
        "against it (VALIDATION.md)."
    )
    return 0


def cmd_tune_market(args) -> int:
    from .backtest.walk_forward import tune_market
    from .data.football_data import load_matches

    matches = load_matches(Path(args.data), _season_range(args), tuple(args.divisions))
    table = tune_market(matches, args.validation_season)
    print(table.to_string(index=False))
    best = table.loc[table["logloss"].idxmin()]
    print(f"\nBest out-of-sample log loss: weight={best['weight']}")
    print(
        "Read the whole curve (§28): weight=0 is pure model, weight=1 is "
        "pure market result probabilities with the model's score shape. "
        "Selecting here makes this season validation data (VALIDATION.md)."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="super6")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--data", default="data", help="cache directory for CSVs")
        p.add_argument(
            "--seasons",
            nargs=2,
            type=int,
            metavar=("START", "END"),
            required=True,
            help="season start-years, inclusive (e.g. 2019 2025)",
        )
        p.add_argument(
            "--divisions", nargs="+", default=list(DEFAULT_DATA.divisions)
        )

    p = sub.add_parser("fetch", help="download Football-Data.co.uk seasons")
    common(p)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("predict", help="six picks + Golden Goal for a round (§20)")
    common(p)
    p.add_argument("--round", required=True, help="round JSON file (§9.1)")
    p.add_argument("--xi", type=float, default=DEFAULT_MODEL.xi)
    p.set_defaults(func=cmd_predict)

    p = sub.add_parser("backtest", help="walk-forward ablation table (§16/§28)")
    common(p)
    p.add_argument("--test-season", type=int, required=True)
    p.add_argument("--xi", type=float, default=DEFAULT_MODEL.xi)
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("tune-xi", help="select ξ by walk-forward validation (§29.2)")
    common(p)
    p.add_argument("--validation-season", type=int, required=True)
    p.set_defaults(func=cmd_tune_xi)

    p = sub.add_parser(
        "tune-market", help="select §32 market blend weight by validation"
    )
    common(p)
    p.add_argument("--validation-season", type=int, required=True)
    p.set_defaults(func=cmd_tune_market)

    p = sub.add_parser(
        "final-test", help="single-shot frozen-model test on the holdout season"
    )
    common(p)
    p.add_argument("--test-season", type=int, required=True)
    p.add_argument(
        "--i-understand-this-is-single-use", action="store_true",
        help="required acknowledgement that the holdout is spent by this run",
    )
    p.set_defaults(func=cmd_final_test)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
