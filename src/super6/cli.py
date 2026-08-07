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
    if getattr(args, "fetch_odds", False):
        rnd, _ = _acquire_odds(rnd, args)
        print()
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


def cmd_sources(args) -> int:
    from .sources.registry import (
        SourceNotApprovedError,
        approve,
        load_registry,
        save_registry,
    )

    path = Path(args.registry)
    registry = load_registry(path)

    if args.sources_action == "approve":
        try:
            record = approve(args.source_id, path=path)
        except (KeyError, SourceNotApprovedError) as exc:
            print(f"{exc}", file=sys.stderr)
            return 1
        print(f"{record.source_id} -> APPROVED (terms checked {record.terms_checked_at})")
        return 0

    if not path.exists():
        save_registry(registry, path)
        print(f"Initialised registry at {path}\n")

    for source_id in sorted(registry):
        r = registry[source_id]
        print(f"{r.source_name}  [{r.source_id}]")
        print(f"  method   {r.acquisition_method}")
        print(f"  status   {r.usage_status}")
        if r.refresh_limit:
            print(f"  limit    {r.refresh_limit}")
        if r.terms_url:
            print(f"  terms    {r.terms_url}  (checked {r.terms_checked_at or 'never'})")
        if r.notes:
            print(f"  note     {r.notes}")
        print()
    return 0


def cmd_quota(args) -> int:
    from .sources.odds_api import (
        FREE_TIER_MONTHLY_CREDITS,
        credits_used_this_month,
        load_quota,
    )

    used = credits_used_this_month(Path(args.quota))
    print(f"The Odds API credits this month: {used}/{FREE_TIER_MONTHLY_CREDITS}")
    quota = load_quota(Path(args.quota))
    for month in sorted(quota):
        entry = quota[month]
        reported = entry.get("api_reported_remaining")
        extra = f", API reports {reported} remaining" if reported is not None else ""
        print(f"  {month}: {entry.get('used', 0)} used{extra}")
    return 0


def _acquire_odds(rnd, args):
    """Fetch live consensus odds for a round, returning the updated round."""
    import os

    from .sources.pipeline import acquire_round_odds, apply_to_round
    from .sources.registry import SourceNotApprovedError

    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print(
            "ODDS_API_KEY is not set. Export your The Odds API key to fetch "
            "live odds, or omit --fetch-odds to use odds from the round file.",
            file=sys.stderr,
        )
        return rnd, None

    try:
        acquired = acquire_round_odds(rnd, api_key)
    except SourceNotApprovedError as exc:
        print(f"{exc}", file=sys.stderr)
        return rnd, None

    print(
        f"Odds acquired {acquired.fetched_at}: {acquired.covered}/6 fixtures "
        f"covered, {acquired.credits_spent} credit(s) spent"
    )
    for f in acquired.fixtures:
        if f.consensus is None:
            print(f"  ! {f.home} v {f.away}: {f.reason} — using model only", file=sys.stderr)
        else:
            c = f.consensus
            print(
                f"  {f.home} v {f.away}: {c.p_home:.1%}/{c.p_draw:.1%}/{c.p_away:.1%} "
                f"({c.bookmaker_count} books, dispersion {c.dispersion:.3f})"
            )
    return apply_to_round(rnd, acquired), acquired


def _fit_router(matches, xi, cfg):
    """Per-division fits + §29.4 priors, with a pooled fallback. Shared by
    predict and record so both score a round identically."""
    from .data.football_data import team_divisions
    from .model import dixon_coles, priors

    divisions = team_divisions(matches)
    current_season = int(matches["season"].max())
    arrival_priors: dict[str, dict] = {}
    if cfg.use_promoted_priors:
        estimated = priors.estimate(matches[matches["season"] < current_season])
        for div in sorted(matches["division"].unique()):
            specs = {}
            for team, direction in priors.arrivals(matches, div, current_season).items():
                spec = estimated.lookup(div, direction)
                if spec is not None:
                    specs[team] = spec
            arrival_priors[div] = specs

    pooled = dixon_coles.fit(matches, xi=xi)
    per_div: dict = {}

    def router(home: str, away: str):
        div_h, div_a = divisions.get(home), divisions.get(away)
        if div_h is None or div_h != div_a:
            return pooled
        if div_h not in per_div:
            per_div[div_h] = dixon_coles.fit(
                matches[matches["division"] == div_h],
                xi=xi,
                param_priors=arrival_priors.get(div_h) or None,
                season_decay=cfg.season_decay,
                reference_season=current_season,
            )
        return per_div[div_h]

    return router


def cmd_record(args) -> int:
    """Record prospective picks for a round, before the deadline."""
    import pandas as pd

    from .config import FROZEN_PHASE1_V1
    from .data.football_data import load_matches
    from .prospective import (
        FROZEN_ARM,
        MARKET_ARM,
        SUBMITTED_ARM,
        load_ledger,
        market_pick,
        record_picks,
        save_ledger,
    )
    from .recommend import recommend_round
    from .round import load_round

    rnd = load_round(Path(args.round))
    if getattr(args, "fetch_odds", False):
        rnd, _ = _acquire_odds(rnd, args)
    ledger_path = Path(args.ledger)
    ledger = load_ledger(ledger_path)
    fixtures = [(f.home, f.away) for f in rnd.fixtures]

    matches = load_matches(Path(args.data), _season_range(args), tuple(args.divisions))
    router = _fit_router(matches, FROZEN_PHASE1_V1.xi, FROZEN_PHASE1_V1)
    rec = recommend_round(router, rnd, FROZEN_PHASE1_V1)

    ledger = record_picks(
        ledger, args.season, rnd.round_number, fixtures, FROZEN_ARM,
        [(f.pick_home, f.pick_away) for f in rec.fixtures],
    )
    print(f"Recorded {FROZEN_ARM}: " + " ".join(f.pick for f in rec.fixtures))

    if all(f.odds is not None for f in rnd.fixtures):
        picks = [market_pick(f.odds) for f in rnd.fixtures]
        ledger = record_picks(
            ledger, args.season, rnd.round_number, fixtures, MARKET_ARM, picks
        )
        print(f"Recorded {MARKET_ARM}: " + " ".join(f"{h}-{a}" for h, a in picks))
    else:
        print(
            f"Skipped {MARKET_ARM}: not every fixture has odds. Add "
            '"odds": [H, D, A] to each fixture to track the benchmark.',
            file=sys.stderr,
        )

    if args.submitted:
        picks = [tuple(int(x) for x in p.split("-")) for p in args.submitted]
        if len(picks) != len(fixtures):
            print("--submitted needs one score per fixture", file=sys.stderr)
            return 1
        ledger = record_picks(
            ledger, args.season, rnd.round_number, fixtures, SUBMITTED_ARM, picks
        )
        print(f"Recorded {SUBMITTED_ARM}: " + " ".join(args.submitted))
    else:
        print(
            f"No {SUBMITTED_ARM} picks given. Re-run with --submitted once you "
            "have actually entered your picks in Super 6.",
            file=sys.stderr,
        )

    save_ledger(ledger, ledger_path)
    print(f"\nLedger: {ledger_path}")
    return 0


def cmd_score(args) -> int:
    """Fill in a round's actual results and score every recorded arm."""
    import json

    import pandas as pd

    from .prospective import load_ledger, save_ledger, score_round, standings

    ledger_path = Path(args.ledger)
    ledger = load_ledger(ledger_path)
    raw = json.loads(Path(args.results).read_text())
    results = {
        int(k): (None if v is None else (int(v[0]), int(v[1])))
        for k, v in raw.items()
    }
    ledger = score_round(ledger, args.season, args.round, results)
    save_ledger(ledger, ledger_path)

    rnd_rows = ledger[(ledger["season"] == args.season) & (ledger["round"] == args.round)]
    print(f"ROUND {args.round} SCORED\n")
    for arm, g in rnd_rows.groupby("arm"):
        scored = g[g["status"] == "OK"]
        total = int(scored["points"].sum()) if not scored.empty else 0
        voids = int((g["status"] == "VOID").sum())
        note = f"  ({voids} void, excluded)" if voids else ""
        print(f"  {arm:18s} {total:3d} pts{note}")
    print("\nRUNNING TOTALS\n")
    print(standings(ledger).to_string(index=False))
    return 0


def cmd_ledger(args) -> int:
    from .prospective import load_ledger, standings

    ledger = load_ledger(Path(args.ledger))
    if ledger.empty:
        print("No prospective picks recorded yet. Use `super6 record`.")
        return 0
    print(standings(ledger).to_string(index=False))
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
    p.add_argument(
        "--fetch-odds", action="store_true",
        help="fetch live market consensus (needs ODDS_API_KEY and an "
             "APPROVED the_odds_api registry entry)",
    )
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
        "record", help="record prospective picks for a round (before deadline)"
    )
    common(p)
    p.add_argument("--round", required=True, help="round JSON file (§9.1)")
    p.add_argument("--season", type=int, default=2026)
    p.add_argument("--ledger", default="results/prospective_ledger.csv")
    p.add_argument(
        "--submitted", nargs="+", metavar="H-A",
        help="six scores you actually entered in Super 6, e.g. 2-0 1-0 ...",
    )
    p.add_argument(
        "--fetch-odds", action="store_true",
        help="fetch live market consensus before recording picks",
    )
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("sources", help="source registry (§18.1)")
    p.add_argument(
        "sources_action", nargs="?", default="list", choices=["list", "approve"]
    )
    p.add_argument("source_id", nargs="?")
    p.add_argument("--registry", default="config/source_registry.json")
    p.set_defaults(func=cmd_sources)

    p = sub.add_parser("quota", help="The Odds API credit usage this month")
    p.add_argument("--quota", default="config/odds_api_quota.json")
    p.set_defaults(func=cmd_quota)

    p = sub.add_parser("score", help="score a recorded round once results are known")
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--season", type=int, default=2026)
    p.add_argument("--ledger", default="results/prospective_ledger.csv")
    p.add_argument(
        "--results", required=True,
        help='JSON: {"0": [2,0], "1": [1,1], "2": null}  (null = VOID)',
    )
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("ledger", help="running prospective comparison across arms")
    p.add_argument("--ledger", default="results/prospective_ledger.csv")
    p.set_defaults(func=cmd_ledger)

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
