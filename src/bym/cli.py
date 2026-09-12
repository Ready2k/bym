"""Command-line interface for the Phase 1A prediction engine.

    bym fetch    --seasons 2019 2025 --data data/
    bym predict  --round round.json --seasons 2021 2025 --data data/
    bym backtest --test-season 2024 --seasons 2018 2024 --data data/
    bym tune-xi  --validation-season 2024 --seasons 2018 2024 --data data/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import DEFAULT_DATA, DEFAULT_LMS, DEFAULT_MODEL, FROZEN_PHASE1_V1


def _season_range(args) -> list[int]:
    start, end = args.seasons
    return list(range(start, end + 1))


def cmd_fetch(args) -> int:
    from .data.football_data import SeasonUnavailable, fetch_season

    for year in _season_range(args):
        for div in args.divisions:
            label = f"{year}/{str(year + 1)[2:]} {div}"
            # A season the source has not published yet is reported and
            # stepped over: asking for a range that runs to the current
            # season should not abort the seasons behind it.
            try:
                path = fetch_season(year, div, Path(args.data), force=args.force)
            except SeasonUnavailable as exc:
                print(f"  {label}: not published yet ({exc})")
                continue
            print(f"  {label}: {path}")
    return 0


def cmd_predict(args) -> int:
    from .config import DEFAULT_MODEL
    from .data.football_data import load_matches, team_divisions
    from .model import dixon_coles, priors
    from .recommend import format_report, recommend_round
    from .round import load_round, resolve_round_teams

    rnd = load_round(Path(args.round))
    if getattr(args, "fetch_odds", False):
        rnd, _ = _acquire_odds(rnd, args)
        print()
    matches = load_matches(Path(args.data), _season_range(args), tuple(args.divisions))

    # Same resolution engine-run does, through the same helper: a round
    # file written with Sky's full club names must not produce a different
    # recommendation here than it does there.
    known = set(matches["home_team"]) | set(matches["away_team"])
    rnd, renamed = resolve_round_teams(rnd, known)
    for typed, canonical in renamed:
        print(f"note: read '{typed}' as '{canonical}'", file=sys.stderr)
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


def cmd_forecast(args) -> int:
    """Forecast a single ad-hoc fixture — a sanity check, not a Super 6 round.

    Deliberately separate from `predict`, which requires exactly six
    fixtures because that is what a Super 6 round is (§9.1).
    """
    from .config import FROZEN_PHASE1_V1
    from .data.football_data import load_matches
    from .model import golden_goal
    from .recommend import recommend_fixture

    divisions = (
        tuple(DEFAULT_DATA.extended_divisions)
        if args.extended
        else tuple(args.divisions)
    )
    matches = load_matches(Path(args.data), _season_range(args), divisions)
    known = set(matches["home_team"]) | set(matches["away_team"])

    unknown = [t for t in (args.home, args.away) if t not in known]
    router = _fit_router(matches, FROZEN_PHASE1_V1.xi, FROZEN_PHASE1_V1)
    params = router(args.home, args.away)

    odds = tuple(args.odds) if args.odds else None
    rec = recommend_fixture(
        params, args.home, args.away, FROZEN_PHASE1_V1, odds=odds
    )

    print(f"\n{args.home.upper()} v {args.away.upper()}\n")
    if args.extended:
        print(
            "  Corpus: EXTENDED (E0-E3 + National League).\n"
            "  This is NOT PHASE1_MODEL_V1 — that model was fitted and\n"
            "  holdout-tested on Premier League + Championship only. Use\n"
            "  this for cup-tie sanity checks, never for Super 6 picks.\n"
        )
    if unknown:
        print("  !! WARNING — NOT A TRUSTWORTHY FORECAST")
        for team in unknown:
            print(f"     '{team}' is not in the training corpus.")
        print(
            "     The model only knows the Premier League and Championship\n"
            "     (Football-Data.co.uk, §18). An unknown team is given the\n"
            "     league-average prior, so a lower-division side is treated\n"
            "     as an average team of the division it is compared against.\n"
            "     Expect it to be materially overrated.\n"
        )

    lam_h, lam_a = params.lambdas(args.home, args.away, FROZEN_PHASE1_V1)
    print(f"  Expected goals   {args.home} {lam_h:.2f} — {lam_a:.2f} {args.away}")
    print(f"  Pick             {rec.pick}")
    print(f"  P(result)        {rec.p_result:.1%}")
    print(f"  P(exact score)   {rec.p_score:.1%}")
    print(f"  Expected points  {rec.ev:.2f}")
    print(f"  Confidence       {rec.confidence}")
    if odds:
        print(f"  Market blended   yes (w={FROZEN_PHASE1_V1.market_blend_weight})")
    else:
        print("  Market blended   no odds supplied — pure statistical model")

    print("\n  Result probabilities")
    print(f"    home {rec.matrix.p_home_win:.1%}   "
          f"draw {rec.matrix.p_draw:.1%}   away {rec.matrix.p_away_win:.1%}")

    print("\n  Top alternatives by expected points")
    for score, probability, ev_value in rec.alternatives(6):
        marker = " <- pick" if score == rec.pick else ""
        print(f"    {score:>5s}  P {probability:>6.1%}   EV {ev_value:.2f}{marker}")

    gg = golden_goal.forecast([lam_h + lam_a])
    print(
        f"\n  Golden Goal      minute {gg.recommended_minute} "
        f"(Sky interval {golden_goal.sky_interval(gg.recommended_minute)})"
    )
    print("  (single fixture — a real round combines all six hazards)\n")
    return 0


def cmd_backtest(args) -> int:
    from .backtest.walk_forward import compare
    from .data.football_data import load_matches

    matches = load_matches(Path(args.data), _season_range(args), tuple(args.divisions))
    table = compare(matches, args.test_season, xi=args.xi)
    print(table.to_string(index=False))
    return 0


def cmd_fpl_evaluate(args) -> int:
    """Run the frozen FPL baseline against the completed historical corpus.

    The command is deliberately read-only with respect to the corpus: Phase
    1B evaluates a frozen baseline; it does not pull more data, retune a
    parameter, or write a recommendation.
    """
    from . import db
    from .backtest import fpl_evaluation
    from .data.football_data import load_matches

    matches = load_matches(
        Path(args.data), _season_range(args), tuple(args.divisions), fetch=False
    )
    with db.connect(Path(args.db)) as conn:
        try:
            corpus = fpl_evaluation.prepare_corpus(
                conn, matches, tuple(args.fpl_seasons)
            )
            result = fpl_evaluation.run(conn, matches, corpus)
        except fpl_evaluation.EvaluationCorpusError as exc:
            print(f"REFUSED. {exc}", file=sys.stderr)
            return 2

    report = fpl_evaluation.render_markdown(result)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report)
        print(f"Wrote {output}")
    else:
        print(report)
    return 0


def cmd_fpl_decision_evaluate(args) -> int:
    """Run the fixture-independent Phase 1B optimiser stability evidence.

    This deliberately needs no live or historical FPL market feed: it proves
    the decision layer's behaviour on a canonical adverse scenario, rather
    than inventing a historical transfer backtest from incomplete inputs.
    """
    from .backtest import fpl_decision_evaluation

    result = fpl_decision_evaluation.run(tuple(args.ks))
    report = fpl_decision_evaluation.render_markdown(result)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report)
        print(f"Wrote {output}")
    else:
        print(report)
    return 0


def cmd_fpl_import(args) -> int:
    """Persist one explicit, user-supplied FPL game-state snapshot.

    This is the prospective acquisition path for the FPL mode. It does not
    contact FPL, authenticate a user, or retain the raw import payload; the
    normalised minimum needed to reconstruct a later recommendation is what
    `fpl.game_state.store_import` persists.
    """
    import json

    from . import db
    from .fpl import game_state

    source = Path(args.input)
    try:
        payload = json.loads(source.read_text())
    except OSError as exc:
        print(f"REFUSED. Cannot read {source}: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"REFUSED. {source} is not valid JSON: {exc.msg}", file=sys.stderr)
        return 2

    with db.connect(Path(args.db)) as conn:
        try:
            state, squad = game_state.store_import(conn, payload, args.season)
        except game_state.ImportValidationError as exc:
            print(f"REFUSED. Invalid FPL state import: {exc}", file=sys.stderr)
            return 2

        universe = game_state.candidate_universe(state)

    print(
        f"Imported FPL state #{state.id}: season {squad.season}/{squad.season + 1}, "
        f"GW{squad.gameweek}, {len(squad.players)} squad players, "
        f"{len(universe)} priced candidates."
    )
    print(
        "The raw JSON was not retained. This immutable snapshot is ready for "
        "a future pre-deadline projection and optimiser run."
    )
    return 0


def cmd_fpl_plan(args) -> int:
    """Build a local 2–4 Gameweek Phase 2 plan from explicit input only."""
    import json

    from .fpl import phase2

    source = Path(args.input)
    try:
        data = phase2.parse_input(json.loads(source.read_text()))
        payload = phase2.as_payload(phase2.build_plan(data))
    except OSError as exc:
        print(f"REFUSED. Cannot read {source}: {exc}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, phase2.Phase2InputError) as exc:
        print(f"REFUSED. Invalid Phase 2 input: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(payload, indent=2) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered)
        print(f"Wrote {output}")
    else:
        print(rendered, end="")
    return 0


def cmd_fpl_relative(args) -> int:
    """Evaluate named FPL choices against pre-deadline rival scenarios."""
    import json

    from .fpl import phase3

    source = Path(args.input)
    try:
        data = phase3.parse_input(json.loads(source.read_text()))
        payload = phase3.build_plan(data)
    except OSError as exc:
        print(f"REFUSED. Cannot read {source}: {exc}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, phase3.Phase3InputError) as exc:
        print(f"REFUSED. Invalid Phase 3 input: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(payload, indent=2) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered)
        print(f"Wrote {output}")
    else:
        print(rendered, end="")
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


def _layer_credentials(web_config=None) -> dict:
    """Credentials and extractor for the availability and knowledge layers.

    Absent credentials are passed through as None rather than omitted, so
    each layer reports "skipped: no key" as a structured event instead of
    the loop silently doing less than it appears to.
    """
    import os

    from .knowledge.config import build_extractor, from_env

    # `bym tick` is the worker behind rounds configured in the browser, so
    # it must honour the extractor the browser saved.  Previously it read
    # environment variables only: a UI-enabled Ollama setup therefore paid
    # for Tavily retrieval but silently used the null extractor and produced
    # no intelligence events.
    if web_config is not None:
        from .web import web_extractor_config

        config = web_extractor_config(web_config)
    else:
        config = from_env()
    return {
        "availability_key": os.environ.get("API_FOOTBALL_KEY"),
        "tavily_key": os.environ.get("TAVILY_API_KEY"),
        "extractor": build_extractor(config),
        "extractor_mode": config.mode,
    }


def cmd_watch(args) -> int:
    """Schedule a round for automatic polling, and optionally run the loop."""
    import os
    import time

    from . import cadence, scheduler, worker
    from .config import FROZEN_PHASE1_V1
    from .data.football_data import load_matches
    from .round import load_round

    rnd = load_round(Path(args.round))
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("ODDS_API_KEY is not set.", file=sys.stderr)
        return 1

    matches = load_matches(Path(args.data), _season_range(args), tuple(args.divisions))
    router = _fit_router(matches, FROZEN_PHASE1_V1.xi, FROZEN_PHASE1_V1)
    known_teams = set(matches["home_team"]) | set(matches["away_team"])

    with scheduler.connect(Path(args.db)) as conn:
        jobs = scheduler.schedule_all_layers(
            conn, rnd.round_number, str(Path(args.round).resolve()),
            rnd.deadline_at, include_knowledge=args.with_knowledge,
        )
        print("Scheduled: " + ", ".join(j.job_type for j in jobs))
        interval = cadence.poll_interval(rnd.deadline_at)
        if interval is None:
            print("Deadline has passed — nothing to watch.")
            return 0
        print(
            f"Round {rnd.round_number} scheduled. Deadline {rnd.deadline_at}, "
            f"first poll now, cadence {interval}."
        )
        if not args.loop:
            print("Run with --loop to start polling, or invoke `bym tick` "
                  "from cron/systemd.")
            return 0

        print("Polling. Ctrl-C to stop; state is persisted, so restarting resumes.")
        try:
            while True:
                outcomes = worker.run_due_jobs(
                    conn, api_key, lambda: router,
                    known_teams=known_teams, **_layer_credentials()
                )
                for o in outcomes:
                    print(
                        f"[{o.round_number}] {o.fixtures_covered}/6 covered, "
                        f"{o.reforecast} reforecast, {len(o.events)} events, "
                        f"next {o.next_run_at}"
                    )
                if not scheduler.list_jobs(conn) or all(
                    j.status in {"DONE", "FAILED"} for j in scheduler.list_jobs(conn)
                ):
                    print("All jobs complete.")
                    return 0
                time.sleep(args.tick_seconds)
        except KeyboardInterrupt:
            print("\nStopped. Scheduled jobs remain persisted.")
            return 0


def cmd_tick(args) -> int:
    """Run one scheduler pass — for cron/systemd rather than --loop."""
    import os

    from . import scheduler, worker
    from .config import FROZEN_PHASE1_V1
    from .data.football_data import load_matches
    from .web import WebConfig

    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("ODDS_API_KEY is not set.", file=sys.stderr)
        return 1
    matches = load_matches(Path(args.data), _season_range(args), tuple(args.divisions))
    router = _fit_router(matches, FROZEN_PHASE1_V1.xi, FROZEN_PHASE1_V1)
    known_teams = set(matches["home_team"]) | set(matches["away_team"])
    # ui_state.json/the event log/submissions/prediction_history have no
    # `tick`-specific flags of their own — `bym serve` does not override
    # them either, so they stay at WebConfig's shared repo-root-relative
    # defaults. Only --db is threaded through, since a deployment can
    # legitimately point that elsewhere and refresh_and_notify must land
    # on the same database `bym serve` reads.
    web_config = WebConfig(database_path=Path(args.db))

    with scheduler.connect(Path(args.db)) as conn:
        outcomes = worker.run_due_jobs(
            conn, api_key, lambda: router,
            known_teams=known_teams, web_config=web_config,
            **_layer_credentials(web_config)
        )
    if not outcomes:
        print("No jobs due.")
    for o in outcomes:
        print(
            f"[round {o.round_number}] {o.fixtures_covered}/6 covered, "
            f"{o.reforecast} reforecast, next {o.next_run_at}"
        )
    return 0


def cmd_jobs(args) -> int:
    from . import scheduler

    with scheduler.connect(Path(args.db)) as conn:
        jobs = scheduler.list_jobs(conn)
    if not jobs:
        print("No scheduled jobs. Use `bym watch --round ...`.")
        return 0
    print(f"{'ID':>3} {'ROUND':>5} {'STATUS':<8} {'NEXT RUN':<26} ATTEMPTS")
    for j in jobs:
        print(
            f"{j.id:>3} {j.round_number or '-':>5} {j.status:<8} "
            f"{j.next_run_at:<26} {j.attempt_count}"
        )
        if j.last_error:
            print(f"      last error: {j.last_error}")
    return 0


def cmd_activity(args) -> int:
    from . import events

    log = events.load(Path(args.log))
    if not log:
        print("No activity recorded yet.")
        return 0
    if args.type:
        log = [e for e in log if e.event_type in args.type]
    if not args.all:
        # §53: routine no-change events are hidden by default.
        log = [e for e in log if e.event_type != "MARKET_SNAPSHOT"]
    for event in log[-args.limit :]:
        fixture = f"  {event.fixture}" if event.fixture else ""
        print(f"{event.occurred_at}  [{event.severity}] {event.event_type}{fixture}")
        if event.summary:
            print(f"    {event.summary}")
    print(f"\n{len(log)} event(s). Use --all to include routine snapshots.")
    return 0


def cmd_health(args) -> int:
    """Engine health, including Ollama reachability (§7)."""
    from . import db as db_mod
    from .api import service
    from .knowledge.config import from_env
    from .knowledge.ollama import check_health
    from .sources.registry import load_registry

    config = from_env()
    health = check_health(config)
    print(health.describe())
    print()

    with db_mod.connect(Path(args.db)) as conn:
        system = service.build_system_health(
            conn,
            registry=load_registry(Path(args.registry)),
            ollama_health=health,
            extractor_config=config,
            schema_version=db_mod.schema_version(conn),
        )
    print("Sources")
    for source in system.sources:
        quota = (
            f"  quota {source.quota_used}/{source.quota_limit} per {source.quota_period}"
            if source.quota_limit
            else ""
        )
        print(f"  {source.source_id:20s} {source.usage_status}{quota}")
    print(f"\nScheduler jobs pending: {system.scheduler_jobs_pending}")
    print(f"Database schema version: {system.database_schema_version}")
    print(f"Extractor: {system.extractor} (mode {system.extractor_mode})")
    for warning in system.warnings:
        print(f"  ! {warning}")
    return 0 if health.reachable else 1


def cmd_verify_live(args) -> int:
    """Live verification of each external source (§9)."""
    from .verify import FAILED, verify_all

    print("LIVE SOURCE VERIFICATION")
    print("Approval is never bypassed; a successful check approves nothing.\n")
    results = verify_all(Path(args.registry), season=args.season)
    for result in results:
        print(result.report())
        print()
    failures = [r for r in results if r.status == FAILED]
    print(
        f"{len(results) - len(failures)}/{len(results)} source(s) verified "
        "without failure"
    )
    return 1 if failures else 0


def cmd_engine_run(args) -> int:
    """Full engine pass for a manually configured round (§13)."""
    import os

    from . import db as db_mod
    from . import events
    from .config import FROZEN_PHASE1_V1
    from .data.football_data import load_matches
    from .engine import format_engine_report, run_engine
    from .knowledge.config import build_extractor, from_env
    from .round import load_round

    rnd = load_round(Path(args.round))
    matches = load_matches(Path(args.data), _season_range(args), tuple(args.divisions))
    router = _fit_router(matches, FROZEN_PHASE1_V1.xi, FROZEN_PHASE1_V1)

    config = from_env()
    extractor = build_extractor(config)

    with db_mod.connect(Path(args.db)) as conn:
        result = run_engine(
            conn,
            rnd,
            router,
            odds_api_key=os.environ.get("ODDS_API_KEY"),
            tavily_api_key=os.environ.get("TAVILY_API_KEY"),
            extractor=extractor,
            extractor_config=config,
            run_knowledge=args.with_knowledge,
            known_teams=set(matches["home_team"]) | set(matches["away_team"]),
            corpus="+".join(args.divisions),
        )
    # Otherwise a manual engine-run's availability/knowledge findings land
    # in the DB (and each fixture's Evidence panel) but never reach the
    # Activity tab, which reads only this log — see the live worker path
    # (worker.run_due_jobs) this mirrors.
    events.append(result.events, events.DEFAULT_EVENT_LOG)

    print(format_engine_report(result))
    if args.json:
        import json as json_mod

        Path(args.json).write_text(
            json_mod.dumps(result.round_summary.to_dict(), indent=2) + "\n"
        )
        print(f"\nRound summary contract written to {args.json}")
    return 0


def cmd_serve(args) -> int:
    """Run the local Phase 1 decision experience (UIUX.md §70)."""
    from .web import WebConfig, serve

    serve(
        WebConfig(
            round_path=Path(args.round),
            data_path=Path(args.data),
            database_path=Path(args.db),
            submissions_path=Path(args.submissions),
            registry_path=Path(args.registry),
            lms_gameweek_path=Path(args.lms_gameweek),
            lms_state_path=Path(args.lms_state),
            seasons=tuple(args.seasons),
            divisions=tuple(args.divisions),
        ),
        host=args.host,
        port=args.port,
    )
    return 0


def cmd_submission(args) -> int:
    """Inspect or update submission state (§46).

    Nothing here submits anything to Sky — the agent is external to Sky by
    architecture. `mark-entered` records that *you* entered the picks.
    """
    from . import submission as submission_mod

    if args.submission_action == "mark-entered":
        if not args.picks:
            print("--picks is required for mark-entered", file=sys.stderr)
            return 1
        record = submission_mod.mark_submitted(
            args.season, args.round, args.picks, args.golden_goal
        )
        print(f"Recorded as entered at {record.submitted_at}:")
        print("  " + " ".join(record.submitted_picks))
        if record.submitted_golden_goal is not None:
            print(f"  Golden Goal: {record.submitted_golden_goal}")
        print(
            "\nThis records what YOU entered in Super 6. The agent does not "
            "submit anything."
        )
        return 0

    if args.submission_action == "mark-reviewed":
        if not args.picks:
            print("--picks is required for mark-reviewed", file=sys.stderr)
            return 1
        record = submission_mod.mark_reviewed(args.season, args.round, args.picks)
        print(f"Review baseline recorded: {record.state}")
        return 0

    record = submission_mod.get(args.season, args.round)
    print(f"Round {args.round} ({args.season}): {record.state}")
    print(submission_mod.describe(record))
    if record.submitted_picks:
        print("\nEntered: " + " ".join(record.submitted_picks))
    return 0


def cmd_readiness(args) -> int:
    """How close the two data-blocked backlog items are to attemptable.

    Both §13.2's characteristic-matched sampler and §8's Evidence → λ model
    are blocked on data that only accrues by playing rounds, not on
    engineering time. This prints the two counters so "not yet" has a
    number attached rather than being re-argued from memory
    (docs/next_steps.md).
    """
    from . import claim_outcomes, db as db_mod, round_characteristics
    from .config import FROZEN_PHASE1_V1
    from .data.football_data import load_matches, team_divisions
    from .web import load_ui_state, recorded_results

    prediction_path = Path(args.prediction_history)

    divisions: dict[str, str] = {}
    expected_goals = None
    try:
        matches = load_matches(
            Path(args.data), list(range(args.seasons[0], args.seasons[1] + 1)),
            tuple(args.divisions), fetch=False,
        )
    except FileNotFoundError:
        print("No corpus found — division mix and expected goals unavailable.\n")
    else:
        divisions = team_divisions(matches)
        router = _fit_router(matches, FROZEN_PHASE1_V1.xi, FROZEN_PHASE1_V1)

        def expected_goals(home: str, away: str) -> float | None:
            try:
                lam_home, lam_away = router(home, away).lambdas(home, away, FROZEN_PHASE1_V1)
            except Exception:
                return None  # a team outside the corpus has no fitted λ
            return lam_home + lam_away

    rounds = round_characteristics.observed_rounds(
        prediction_path=prediction_path,
        divisions=divisions,
        expected_goals=expected_goals,
    )
    profile = round_characteristics.observed_profile(rounds)
    print("§13.2 ROUND OPPORTUNITY MODEL — characteristic matching\n")
    print(f"Rounds observed          {profile.rounds}")
    print(f"Fixtures observed        {profile.fixtures}")
    print(f"Division mix             {dict(profile.division_mix) or '—'}")
    print(f"Home-favourite share     {profile.home_favourite_share}")
    print(f"Mean favourite strength  {profile.mean_favourite_strength}")
    print(f"Favourite strength range {profile.favourite_strength_range}")
    print(f"Mean competitiveness     {profile.mean_competitiveness}")
    print(f"Mean expected goals      {profile.mean_expected_goals}")
    print(f"\n{round_characteristics.matching_readiness(profile)}\n")

    # A counter that reports zero because a file is missing looks exactly
    # like a counter that reports zero because nothing has happened yet —
    # and `db.connect` would helpfully *create* the database it can't find,
    # making the wrong answer look authoritative. Say so instead. This is
    # not hypothetical: the paths below are not `serve` flags, so a UI
    # started from the wrong directory keeps its state somewhere else
    # entirely (see web.missing_state_warnings).
    ui_state_path, db_path = Path(args.ui_state), Path(args.db)
    unresolved = [
        f"{label} not found at {path.resolve()}"
        for label, path in (
            ("UI state", ui_state_path),
            ("database", db_path),
            ("prediction history", prediction_path),
        )
        if not path.exists()
    ]
    if unresolved:
        for item in unresolved:
            print(f"  ! {item}", file=sys.stderr)
        print(
            "  ! The counters below are therefore incomplete, not merely "
            "empty.\n  !   Point --ui-state/--db/--prediction-history at the "
            "deployment you mean.\n",
            file=sys.stderr,
        )

    state = load_ui_state(ui_state_path)
    with db_mod.connect(db_path) as conn:
        outcomes = claim_outcomes.claim_outcomes(
            conn, recorded_results(state), prediction_path=prediction_path
        )
    evidence = claim_outcomes.impact_evidence(outcomes)
    print("§8 EVIDENCE → λ — player-impact model (Increment 5)\n")
    print(f"Claim/outcome pairs      {evidence.pairs}")
    print(f"Attributed to a team     {evidence.attributed}")
    print(f"Confirmed absences       {evidence.confirmed_absences}")
    print(f"Rounds covered           {evidence.rounds}")
    print(f"Teams covered            {evidence.teams}")
    print(f"Mean goals, absent       {evidence.mean_goals_with_absence}")
    print(f"Mean goals, otherwise    {evidence.mean_goals_without_absence}")
    print(f"\n{claim_outcomes.fitting_readiness(evidence)}")
    return 0


def cmd_shadow_report(args) -> int:
    """Shadow-mode extraction metrics (§12) — counts only, no AI score."""
    from . import db as db_mod
    from .knowledge import store as knowledge_store

    with db_mod.connect(Path(args.db)) as conn:
        runs = knowledge_store.extraction_runs(conn)
        rows = conn.execute(
            "SELECT verdict, rejection_reason, promoted FROM candidate_claims"
        ).fetchall()

    if not runs and not rows:
        print("No extraction runs recorded yet.")
        return 0

    accepted = sum(1 for r in rows if r["verdict"] == "ACCEPTED")
    rejected = sum(1 for r in rows if r["verdict"] == "REJECTED")
    promoted = sum(1 for r in rows if r["promoted"])
    print("SHADOW EXTRACTION REPORT\n")
    print(f"Extraction runs      {len(runs)}")
    print(f"Claims emitted       {sum(r['claims_emitted'] for r in runs)}")
    print(f"Claims accepted      {accepted}")
    print(f"Claims rejected      {rejected}")
    print(f"Claims promoted      {promoted}")
    failures = [r for r in runs if r["status"] == "FAILED"]
    print(f"Failed runs          {len(failures)}")

    reasons: dict[str, int] = {}
    for row in rows:
        if row["verdict"] == "REJECTED" and row["rejection_reason"]:
            key = row["rejection_reason"].split("(")[0].strip()[:60]
            reasons[key] = reasons.get(key, 0) + 1
    if reasons:
        print("\nRejection reasons")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"  {count:4d}  {reason}")

    if runs:
        print("\nProvenance")
        for run in runs[-5:]:
            print(
                f"  {run['requested_at']}  {run['provider']}/{run['model']} "
                f"@{run['host']}  prompt={run['prompt_version']} "
                f"mode={run['mode']} status={run['status']}"
            )
    print(
        "\nManual review of individual claims is expected. No automated "
        "quality score is produced on purpose."
    )
    return 0


def cmd_research(args) -> int:
    """AVAILABILITY_ORACLE_RESEARCH_V1 — sampling, harvest, prevalence.

    Research only: never touches production data or the forecast path.
    """
    from .research import harvest as harvest_mod
    from .research import prevalence, store as research_store
    from .secrets import get_key

    db_path = Path(args.research_db)

    if args.research_action == "status":
        with research_store.connect(db_path) as conn:
            size = research_store.sample_size(conn)
            if not size:
                print("No sample drawn yet. Run: bym research sample")
                return 0
            counts = research_store.progress(conn)
            total = sum(counts.values())
            done = sum(
                counts.get(s, 0) for s in research_store.TERMINAL_STATES
            )
            print(f"{STUDY_LABEL}\n")
            print(f"  sampled fixtures  {size}")
            print(f"  requests          {done}/{total} settled")
            for state, count in sorted(counts.items()):
                print(f"    {state:18s} {count}")
            for row in research_store.freezes(conn):
                print(
                    f"\n  FROZEN {row['frozen_at']}: {row['fixture_count']} "
                    f"fixtures, sample_sha {row['sample_sha']}"
                )
        return 0

    api_key = get_key("API_FOOTBALL_KEY")
    if not api_key and args.research_action in {"sample", "harvest"}:
        print("No API-Football key found.", file=sys.stderr)
        return 1

    if args.research_action == "sample":
        with research_store.connect(db_path) as conn:
            if research_store.sample_size(conn):
                print(
                    "Sample already drawn and frozen — the frame is "
                    "immutable by design."
                )
                return 0
            print("Building fixture universe (4 requests)...")
            universe = harvest_mod.build_universe(api_key)
            print(f"  universe: {len(universe)} fixtures")
            outcome = harvest_mod.create_sample(conn, universe)
            print(f"  sampled:  {outcome['created']} fixtures, seed {outcome['seed']}")
            print(f"  requests: {outcome['requests']} queued")
            for stratum, count in outcome["strata"].items():
                print(f"    {stratum}: {count}")
        return 0

    if args.research_action == "harvest":
        with research_store.connect(db_path) as conn:
            result = harvest_mod.harvest(
                conn, api_key, max_requests=args.max_requests,
                pace_seconds=args.pace_seconds,
            )
            print(result.describe())
            for note in result.notes:
                print(f"  ! {note}")
            written = harvest_mod.derive_facts(conn)
            print(f"derived facts for {written} fixture(s)")
            counts = research_store.progress(conn)
            settled = sum(
                counts.get(s, 0) for s in research_store.TERMINAL_STATES
            )
            print(f"progress: {settled}/{sum(counts.values())} requests settled")
            if settled < sum(counts.values()):
                print("Not complete. Re-run when quota allows; state is persisted.")
        return 0

    if args.research_action == "prevalence":
        with research_store.connect(db_path) as conn:
            counts = research_store.progress(conn)
            settled = sum(
                counts.get(s, 0) for s in research_store.TERMINAL_STATES
            )
            total = sum(counts.values())
            if total and settled < total:
                print(
                    f"REFUSED: harvest is {settled}/{total} complete.\n"
                    "The pre-registration says analyse the full sample once, "
                    "not peek as it accumulates. Finish the harvest first, or "
                    "pass --allow-partial if you accept that this is not the "
                    "pre-registered analysis.",
                    file=sys.stderr,
                )
                if not args.allow_partial:
                    return 2
            print(prevalence.compute(conn).describe())
        return 0

    if args.research_action == "freeze":
        with research_store.connect(db_path) as conn:
            record = research_store.freeze(conn, STUDY_LABEL, args.notes or "")
            print(f"FROZEN {record['study']} at {record['frozen_at']}")
            for key in ("seed", "fixture_count", "request_count",
                        "sample_sha", "payload_sha"):
                print(f"  {key}: {record[key]}")
        return 0
    return 0


STUDY_LABEL = "AVAILABILITY_ORACLE_RESEARCH_V1"


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
    from .sources import api_football, quota as quota_mod
    from .sources.odds_api import QUOTA as ODDS_QUOTA

    for spec in (ODDS_QUOTA, api_football.QUOTA):
        path = Path(args.quota) if args.quota else spec.path
        if args.quota and spec is api_football.QUOTA:
            path = spec.path  # --quota targets the odds file only
        used = quota_mod.used(spec, path)
        print(
            f"{spec.source_id}: {used}/{spec.limit} per {spec.period}"
            + (" — CONSTRAINED" if quota_mod.constrained(spec, path=path) else "")
        )
        state = quota_mod.load(path)
        for period in sorted(k for k in state if k != "recent_calls"):
            entry = state[period]
            reported = entry.get("api_reported_remaining")
            extra = f", API reports {reported} remaining" if reported is not None else ""
            print(f"    {period}: {entry.get('used', 0)} used{extra}")
    return 0


def cmd_coverage(args) -> int:
    """Detect and persist what API-Football covers for a season (§18.1)."""
    import os

    from . import db as db_mod
    from .availability.worker import detect_coverage
    from .sources.registry import SourceNotApprovedError

    api_key = os.environ.get("API_FOOTBALL_KEY")
    if not api_key:
        print("API_FOOTBALL_KEY is not set.", file=sys.stderr)
        return 1

    with db_mod.connect(Path(args.db)) as conn:
        for division in args.divisions:
            try:
                coverage = detect_coverage(conn, division, args.season, api_key)
            except SourceNotApprovedError as exc:
                print(f"{exc}", file=sys.stderr)
                return 1
            except KeyError:
                print(f"No league id for division {division}", file=sys.stderr)
                continue
            states = ", ".join(
                f"{k}={'AVAILABLE' if v else 'MISSING'}" for k, v in coverage.items()
            )
            print(f"{division} season {args.season}: {states}")
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
    from .round import load_round, resolve_round_teams

    rnd = load_round(Path(args.round))
    if getattr(args, "fetch_odds", False):
        rnd, _ = _acquire_odds(rnd, args)
    ledger_path = Path(args.ledger)
    ledger = load_ledger(ledger_path)

    matches = load_matches(Path(args.data), _season_range(args), tuple(args.divisions))

    # Before the fixture list is built, not after: the ledger stores the
    # club name alongside the pick, and it is the record the model is
    # later judged on. An unresolved name here writes a pick made for a
    # league-average stand-in into permanent evidence, under a name that
    # will not match the corpus at reconciliation.
    known = set(matches["home_team"]) | set(matches["away_team"])
    rnd, renamed = resolve_round_teams(rnd, known)
    for typed, canonical in renamed:
        print(f"note: read '{typed}' as '{canonical}'", file=sys.stderr)
    for f in rnd.fixtures:
        for team in (f.home, f.away):
            if team not in known:
                print(
                    f"WARNING: '{team}' not in training data — its pick comes "
                    "from league-average strength (§29.4) and is being "
                    "recorded as evidence. Check the spelling matches "
                    "Football-Data.co.uk names.",
                    file=sys.stderr,
                )
    fixtures = [(f.home, f.away) for f in rnd.fixtures]

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

    if args.results:
        raw = json.loads(Path(args.results).read_text())
        results = {
            int(k): (None if v is None else (int(v[0]), int(v[1])))
            for k, v in raw.items()
        }
    else:
        results = _fetch_results(args)
        if results is None:
            return 1
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


def _fetch_results(args):
    """Fetch a round's results from football-data.org (§2.2 aware)."""
    from .secrets import get_key
    from .sources import football_data_org as fdo
    from .sources.registry import SourceNotApprovedError
    from .sources.teams import resolve
    from .round import load_round

    if not args.round_file:
        print(
            "Provide --results with a JSON file, or --round-file so results "
            "can be fetched from football-data.org.",
            file=sys.stderr,
        )
        return None

    api_key = get_key("FOOTBALL_DATA_ORG_KEY")
    if not api_key:
        print("No football-data.org key found.", file=sys.stderr)
        return None

    rnd = load_round(Path(args.round_file))
    fixtures = [(f.home, f.away) for f in rnd.fixtures]
    try:
        matches = []
        for division in args.divisions:
            matches.extend(
                fdo.fetch_matches(division, args.fd_season, api_key)
            )
    except SourceNotApprovedError as exc:
        print(f"{exc}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"Result fetch failed: {exc}", file=sys.stderr)
        return None

    results = fdo.results_for_round(matches, fixtures, resolve)
    missing = [
        f"{h} v {a}" for i, (h, a) in enumerate(fixtures) if i not in results
    ]
    for index, value in sorted(results.items()):
        home, away = fixtures[index]
        label = "VOID" if value is None else f"{value[0]}-{value[1]}"
        print(f"  {home} v {away}: {label}")
    if missing:
        print(
            "  not yet resulted: " + ", ".join(missing),
            file=sys.stderr,
        )
    return results


def cmd_ledger(args) -> int:
    from .prospective import load_ledger, standings

    ledger = load_ledger(Path(args.ledger))
    if ledger.empty:
        print("No prospective picks recorded yet. Use `bym record`.")
        return 0
    print(standings(ledger).to_string(index=False))
    return 0


def cmd_final_test(args) -> int:
    """Single-shot evaluation of the frozen model on the holdout season."""
    from .backtest.walk_forward import run_final_test_rows
    from .data.football_data import load_matches
    from .validation import HoldoutAlreadySpentError, assert_holdout_unspent

    # Checked before the acknowledgement flag, and unconditionally: on
    # 2026-08-08 an already-spent holdout was re-run anyway because the
    # flag is only a typed acknowledgement, not a check against anything.
    # This refusal cannot be flagged past.
    try:
        assert_holdout_unspent(args.test_season)
    except HoldoutAlreadySpentError as exc:
        print(f"REFUSED. {exc}", file=sys.stderr)
        return 2

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
    rows = run_final_test_rows(matches, args.test_season)

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


def cmd_lms_recommend(args) -> int:
    """Last Man Standing: rank every eligible club by P(win) for one
    gameweek, reusing the same fitted Dixon-Coles router Super 6 uses.

    Fetches the current Premier League gameweek from football-data.org by
    default — pass --gameweek for a manually written file instead (no key
    configured, or a fixture needs correcting)."""
    from .config import FROZEN_PHASE1_V1
    from .data.football_data import load_matches
    from . import lms

    matches = load_matches(Path(args.data), _season_range(args), tuple(args.divisions))
    known = set(matches["home_team"]) | set(matches["away_team"])
    if args.gameweek:
        gw = lms.load_gameweek(Path(args.gameweek))
    else:
        try:
            gw = _fetch_lms_gameweek(known, args.fd_season)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Gameweek {gw.number} — {len(gw.fixtures)} fixture(s) fetched from football-data.org\n")
    for f in gw.fixtures:
        for team in (f.home, f.away):
            if team not in known:
                print(
                    f"WARNING: '{team}' not in training data — it will use "
                    "league-average strength. Check the spelling matches "
                    "Football-Data.co.uk names.",
                    file=sys.stderr,
                )

    router = _fit_router(matches, args.xi, FROZEN_PHASE1_V1)

    used: set[str] = set(args.used or [])
    if args.entrant:
        state = lms.get_state(args.entrant, Path(args.state))
        if not state.alive:
            print(f"{args.entrant} is already eliminated (see `lms-status`).")
            return 0
        used |= set(state.used_clubs)

    candidates = lms.recommend_gameweek(router, gw, used, FROZEN_PHASE1_V1)
    print(lms.format_recommendations(gw, candidates, used))
    if gw.popularity:
        print()
        print(lms.format_field_equity(lms.field_equity(candidates, gw.popularity)))
    return 0


def cmd_lms_plan(args) -> int:
    """Last Man Standing: plan the next several gameweeks at once.

    The command that actually answers the format's question. A club can be
    used once all season, so the pick that maximises survival *this*
    gameweek routinely spends a club that a later gameweek needs more.
    This assigns one club per gameweek over the horizon to maximise the
    probability of surviving all of them, and prints what the
    take-the-favourite policy would have cost."""
    from .config import FROZEN_PHASE1_V1
    from .data.football_data import load_matches
    from . import lms

    matches = load_matches(Path(args.data), _season_range(args), tuple(args.divisions))
    known = set(matches["home_team"]) | set(matches["away_team"])

    try:
        gameweeks = _fetch_lms_horizon(known, args.fd_season, args.horizon)
    except RuntimeError as exc:
        # Without future fixtures there is no horizon to plan over. Fall
        # back to the one gameweek to hand and say plainly that the plan
        # has degenerated to the greedy pick, rather than presenting a
        # one-week answer as if it were a plan.
        if not args.gameweek:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"{exc}\nPlanning over the single saved gameweek only — with no "
              "future fixtures this is just the week-by-week favourite.\n",
              file=sys.stderr)
        gameweeks = [lms.load_gameweek(Path(args.gameweek))]
    else:
        print(
            f"Planning GW{gameweeks[0].number}"
            + (f"-{gameweeks[-1].number}" if len(gameweeks) > 1 else "")
            + f" — {len(gameweeks)} gameweek(s) fetched from football-data.org\n"
        )

    unknown = sorted(
        {
            team
            for gw in gameweeks
            for f in gw.fixtures
            for team in (f.home, f.away)
            if team not in known
        }
    )
    for team in unknown:
        print(
            f"WARNING: '{team}' not in training data — it will use "
            "league-average strength.",
            file=sys.stderr,
        )

    router = _fit_router(matches, args.xi, FROZEN_PHASE1_V1)

    used: set[str] = set(args.used or [])
    if args.entrant:
        state = lms.get_state(args.entrant, Path(args.state))
        if not state.alive:
            print(f"{args.entrant} is already eliminated (see `lms-status`).")
            return 0
        used |= set(state.used_clubs)

        # A booked gameweek is spent, not just its club. Planning from it
        # would name a pick for a deadline already gone, and would solve
        # the weeks after it as though that club were still to be spent.
        open_idx = lms.open_gameweek_indices(gameweeks, state)
        skipped = [gw.number for i, gw in enumerate(gameweeks) if i not in set(open_idx)]
        if skipped:
            print(
                f"GW{', GW'.join(str(n) for n in skipped)} already booked by "
                f"{args.entrant} — planning from GW"
                f"{gameweeks[open_idx[0]].number if open_idx else '—'} onward.\n"
            )
        gameweeks = [gameweeks[i] for i in open_idx]
        if not gameweeks:
            print(
                f"{args.entrant} has a pick booked for every gameweek in the "
                "horizon. Nothing left to plan — widen it with --horizon, or "
                "wait for the next fixture list."
            )
            return 0

    plan = lms.plan_horizon(router, gameweeks, used, FROZEN_PHASE1_V1)
    print(lms.format_plan(plan, used))

    popularity = gameweeks[0].popularity
    if popularity:
        candidates = lms.rank_candidates(
            lms.fixture_outlooks(router, gameweeks[0], FROZEN_PHASE1_V1), used
        )
        print()
        print(lms.format_field_equity(lms.field_equity(candidates, popularity)))
    return 0


def cmd_lms_backtest(args) -> int:
    """Replay historical Premier League fixture slates for Last Man Standing."""
    from .backtest import lms as lms_backtest
    from .config import FROZEN_PHASE1_V1
    from .data.football_data import load_matches

    seasons = _season_range(args)
    test_seasons = args.test_seasons or seasons[1:]
    outside = sorted(set(test_seasons) - set(seasons))
    if outside:
        print(
            "--test-seasons must be inside --seasons: " + ", ".join(map(str, outside)),
            file=sys.stderr,
        )
        return 1
    if not test_seasons:
        print("need at least one test season after the first training season", file=sys.stderr)
        return 1

    matches = load_matches(Path(args.data), seasons, ("E0",))
    result = lms_backtest.run(
        matches,
        test_seasons,
        horizon=args.horizon,
        xi=args.xi,
        cfg=FROZEN_PHASE1_V1,
        refit_every_days=args.refit_days,
    )
    print(
        "LMS walk-forward backtest — Premier League fixture slates\n"
        f"Test seasons: {', '.join(map(str, sorted(test_seasons)))}\n"
        f"Slates reconstructed: {result.slate_count}; skipped before {lms_backtest.MIN_TRAINING_MATCHES} training matches: {result.skipped_slates}\n"
    )
    print(result.summary().to_string(index=False))
    print(
        "\nInterpretation:\n"
        "  • Weekly arms start fresh at each slate: this measures how often a recommendation wins.\n"
        "  • Horizon arms measure surviving every planned slate, with one club used only once.\n"
        "  • Fixture slates are inferred from dates and club repeats because Football-Data has no matchweek field.\n"
        "  • MARKET-AVAILABLE uses Football-Data's earlier price as a Friday-price proxy, not an exact LMS-deadline snapshot.\n"
        "  • No team news, lineups, or post-deadline odds are used."
    )
    if args.csv:
        path = Path(args.csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        result.summary().to_csv(path, index=False)
        print(f"\nSummary written to {path}")
    return 0


def cmd_lms_pick(args) -> int:
    """Book a Last Man Standing pick against an entrant's used-club list."""
    from . import lms

    path = Path(args.state)
    states = lms.load_states(path)
    state = states.get(args.entrant, lms.LMSState(entrant=args.entrant))
    existing = next(
        (h for h in state.history if h["gameweek"] == args.gameweek), None
    )
    try:
        if existing:
            # Changing, not duplicating. Locked once the round kicks off,
            # which needs the fixture list — fetched here rather than
            # assumed open, since "no fixtures" must not silently become
            # "no deadline".
            week = None
            try:
                known = {args.team, args.opponent}
                week = next(
                    (
                        w
                        for w in _fetch_lms_horizon(known, args.fd_season, 1)
                        if w.number == args.gameweek
                    ),
                    None,
                )
            except RuntimeError as exc:
                print(f"note: {exc}", file=sys.stderr)
            lms.change_pick(
                state, args.gameweek, args.team, args.opponent, args.home,
                gameweek_fixtures=week,
            )
            verb = f"Changed from {existing['team']}: "
        else:
            lms.record_pick(state, args.gameweek, args.team, args.opponent, args.home)
            verb = "Recorded: "
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    states[args.entrant] = state
    lms.save_states(states, path)
    print(f"{verb}{args.entrant} picks {args.team} (GW{args.gameweek})")
    return 0


def cmd_lms_result(args) -> int:
    """Manually settle a Last Man Standing gameweek — anything but a WIN
    eliminates. This is the fallback for a fixture `lms-settle` can't
    resolve (no key configured, or outside football-data.org's coverage);
    prefer `lms-settle` when it's available, since a self-reported result
    in a multi-entrant competition is a conflict of interest an automatic
    feed doesn't have. The saved record marks this one MANUAL either way."""
    from . import lms

    path = Path(args.state)
    states = lms.load_states(path)
    state = states.get(args.entrant, lms.LMSState(entrant=args.entrant))
    try:
        lms.record_result(state, args.gameweek, args.result)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    states[args.entrant] = state
    lms.save_states(states, path)
    print(lms.format_status(state))
    return 0


def cmd_lms_settle(args) -> int:
    """Fetch full-time results from football-data.org and settle every
    PENDING pick whose fixture has finished — the trusted path, since it
    does not depend on an entrant reporting their own survival.

    Works off the saved picks, not off a gameweek file: which fixture a
    pick is on is recorded with the pick, so a result settles whether it
    is this gameweek's or one the competition has already moved past."""
    from . import lms
    from .secrets import get_key
    from .sources import football_data_org as fdo
    from .sources.registry import SourceNotApprovedError
    from .sources.teams import resolve as resolve_team_name

    path = Path(args.state)
    states = lms.load_states(path)
    pending = lms.pending_gameweeks(states)
    if not pending:
        print("  Nothing to settle — no pending picks recorded.")
        return 0

    api_key = get_key("FOOTBALL_DATA_ORG_KEY")
    if not api_key:
        print(
            "No FOOTBALL_DATA_ORG_KEY found — nothing to settle "
            "automatically. Use `lms-result` to enter a result by hand.",
            file=sys.stderr,
        )
        return 1

    matches = []
    try:
        for division in args.divisions:
            if division not in fdo.COMPETITION_CODES:
                continue
            matches.extend(fdo.fetch_matches(division, args.fd_season, api_key))
    except SourceNotApprovedError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Result fetch failed: {exc}", file=sys.stderr)
        return 1

    settled = []
    for gw in pending.values():
        fixtures = [(f.home, f.away) for f in gw.fixtures]
        results = fdo.results_for_round(matches, fixtures, resolve_team_name)
        settled.extend(lms.auto_settle(states, gw, results))
    if settled:
        lms.save_states(states, path)
        for name, gameweek, result in settled:
            print(f"  {name}: GW{gameweek} {result} (auto)")
    else:
        print("  Nothing to settle yet — no finished result for any pending pick.")
    return 0


def _parse_kickoff(value: str | None):
    """football-data.org's `utcDate` as an aware datetime, or None.

    None rather than a raised error: a gameweek whose kickoff cannot be
    read is still a perfectly good gameweek to plan over — it just cannot
    be locked, and the caller decides what to do about that.
    """
    from datetime import datetime

    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _fetch_lms_horizon(known_teams: set[str], fd_season: int, horizon: int = 1):
    """Fetch the next `horizon` Premier League matchdays from
    football-data.org and resolve them onto the training corpus's names.

    One request covers the whole season, so a four-gameweek plan costs
    exactly what a one-gameweek recommendation costs — worth stating,
    because the free tier allows ten requests a minute and this is the
    call the LMS commands and the dashboard all make.

    Returned in kickoff order (see `fdo.upcoming_matchdays`), which is the
    order the picks have to be committed in. Raises RuntimeError with a
    message meant to be printed as-is on any failure — no key, no upcoming
    fixtures, or the request itself failing.
    """
    from . import lms
    from .secrets import get_key
    from .sources import football_data_org as fdo
    from .sources.registry import SourceNotApprovedError
    from .sources.teams import resolve as resolve_team_name

    api_key = get_key("FOOTBALL_DATA_ORG_KEY")
    if not api_key:
        raise RuntimeError(
            "No FOOTBALL_DATA_ORG_KEY found — cannot fetch fixtures "
            "automatically. Use --gameweek with a manually written file instead."
        )
    try:
        matches = fdo.fetch_matches("E0", fd_season, api_key)
    except SourceNotApprovedError as exc:
        raise RuntimeError(str(exc)) from exc
    except Exception as exc:
        raise RuntimeError(f"Fixture fetch failed: {exc}") from exc

    matchdays = fdo.upcoming_matchdays_with_kickoff(matches, horizon)
    if not matchdays:
        raise RuntimeError(
            "football-data.org has no upcoming Premier League fixtures right now."
        )
    return [
        lms.Gameweek(
            number=matchday,
            fixtures=tuple(
                lms.GameweekFixture(
                    home=resolve_team_name(m.home_team, known_teams) or m.home_team,
                    away=resolve_team_name(m.away_team, known_teams) or m.away_team,
                )
                for m in fdo.fixtures_for_matchday(matches, matchday)
            ),
            # The first kickoff is the moment this gameweek's pick stops
            # being changeable, so it travels with the gameweek.
            deadline_at=_parse_kickoff(kickoff),
        )
        for matchday, kickoff in matchdays
    ]


def _fetch_lms_gameweek(known_teams: set[str], fd_season: int):
    """The current matchday alone — `lms-fixtures` and `lms-recommend`."""
    return _fetch_lms_horizon(known_teams, fd_season, 1)[0]


def cmd_lms_fixtures(args) -> int:
    """Fetch the current Premier League gameweek's fixtures from
    football-data.org and save them — no manual fixture entry needed."""
    import json

    from . import lms
    from .data.football_data import load_matches

    matches = load_matches(Path(args.data), _season_range(args), tuple(args.divisions))
    known = set(matches["home_team"]) | set(matches["away_team"])
    try:
        gw = _fetch_lms_gameweek(known, args.fd_season)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    path = Path(args.gameweek)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "gameweek": gw.number,
                "fixtures": [{"home": f.home, "away": f.away} for f in gw.fixtures],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Gameweek {gw.number} — {len(gw.fixtures)} fixture(s) saved to {path}:")
    for f in gw.fixtures:
        print(f"  {f.home} v {f.away}")
    return 0


def cmd_lms_status(args) -> int:
    from . import lms

    path = Path(args.state)
    if args.entrant:
        print(lms.format_status(lms.get_state(args.entrant, path)))
        return 0
    states = lms.load_states(path)
    if not states:
        print("no Last Man Standing entrants recorded yet")
        return 0
    print("\n\n".join(lms.format_status(s) for s in states.values()))
    return 0


def main(argv: list[str] | None = None) -> int:
    # Most commands read their keys straight from os.environ (§22). Fill it
    # from the gitignored secrets/ directory first, so a key on disk works
    # without exporting it in every shell. Environment always wins.
    from .secrets import apply_to_environment

    apply_to_environment()

    parser = argparse.ArgumentParser(prog="bym")
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

    p = sub.add_parser(
        "forecast", help="forecast one ad-hoc fixture (sanity check)"
    )
    common(p)
    p.add_argument("--home", required=True)
    p.add_argument("--away", required=True)
    p.add_argument(
        "--odds", nargs=3, type=float, metavar=("H", "D", "A"),
        help="decimal odds, to blend the market in as the real engine would",
    )
    p.add_argument(
        "--extended", action="store_true",
        help="include League One/Two and the National League so cup ties "
             "against lower-division sides can be modelled. NOT "
             "PHASE1_MODEL_V1 — the frozen model's corpus is E0+E1 only.",
    )
    p.set_defaults(func=cmd_forecast)

    p = sub.add_parser("backtest", help="walk-forward ablation table (§16/§28)")
    common(p)
    p.add_argument("--test-season", type=int, required=True)
    p.add_argument("--xi", type=float, default=DEFAULT_MODEL.xi)
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser(
        "fpl-evaluate",
        help="causal Phase 1B evaluation of frozen BASELINE_FPL_001",
    )
    p.add_argument("--data", default="data", help="cached Football-Data match corpus")
    p.add_argument(
        "--seasons", nargs=2, type=int, metavar=("START", "END"), default=[2019, 2024],
        help="match-model season start-years, inclusive (default: 2019 2024)",
    )
    p.add_argument("--divisions", nargs="+", default=["E0"])
    p.add_argument("--db", default="data/bym.db")
    p.add_argument(
        "--fpl-seasons", nargs="+", type=int, default=[2022, 2023, 2024],
        help="harvested Premier League season start-years to evaluate",
    )
    p.add_argument(
        "--output",
        help="optional Markdown report path; otherwise print to stdout",
    )
    p.set_defaults(func=cmd_fpl_evaluate)

    p = sub.add_parser(
        "fpl-decision-evaluate",
        help="Phase 1B optimiser baseline and top-K stability evidence",
    )
    p.add_argument(
        "--ks", nargs="+", type=int, default=[1, 5, 10, 25, 50, 100],
        help="strictly increasing top-K search widths to compare",
    )
    p.add_argument("--output", help="optional Markdown report path; otherwise print to stdout")
    p.set_defaults(func=cmd_fpl_decision_evaluate)

    p = sub.add_parser(
        "fpl-import",
        help="store an explicit user-supplied FPL squad and candidate-price snapshot",
    )
    p.add_argument("--input", required=True, help="FPL state JSON (§13.3)")
    p.add_argument("--season", type=int, default=2026)
    p.add_argument("--db", default="data/bym.db")
    p.set_defaults(func=cmd_fpl_import)

    p = sub.add_parser(
        "fpl-plan",
        help="build a shortlist-only 2–4 Gameweek transfer and chip plan",
    )
    p.add_argument("--input", required=True, help="manager-provided Phase 2 JSON")
    p.add_argument("--output", help="optional JSON output path")
    p.set_defaults(func=cmd_fpl_plan)

    p = sub.add_parser(
        "fpl-relative",
        help="evaluate named choices against pre-deadline FPL rival scenarios",
    )
    p.add_argument("--input", required=True, help="manager-provided Phase 3 JSON")
    p.add_argument("--output", help="optional JSON output path")
    p.set_defaults(func=cmd_fpl_relative)

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

    p = sub.add_parser("watch", help="schedule automatic polling for a round (§9)")
    common(p)
    p.add_argument("--round", required=True)
    p.add_argument("--db", default="data/scheduler.db")
    p.add_argument("--loop", action="store_true", help="run the polling loop now")
    p.add_argument("--tick-seconds", type=int, default=60)
    p.add_argument(
        "--with-knowledge", action="store_true",
        help="also schedule routine knowledge research (costs search credits; "
             "market-triggered investigation runs regardless)",
    )
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("tick", help="run one scheduler pass (for cron/systemd)")
    common(p)
    p.add_argument("--db", default="data/scheduler.db")
    p.set_defaults(func=cmd_tick)

    p = sub.add_parser("jobs", help="list scheduled polling jobs")
    p.add_argument("--db", default="data/scheduler.db")
    p.set_defaults(func=cmd_jobs)

    p = sub.add_parser("activity", help="chronological activity feed (§53)")
    p.add_argument("--log", default="data/events/activity.jsonl")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--type", nargs="+", help="filter by event type")
    p.add_argument("--all", action="store_true", help="include routine snapshots")
    p.set_defaults(func=cmd_activity)

    p = sub.add_parser("health", help="engine + Ollama health check")
    p.add_argument("--db", default="data/bym.db")
    p.add_argument("--registry", default="config/source_registry.json")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("verify-live", help="live-verify each external source")
    p.add_argument("--registry", default="config/source_registry.json")
    p.add_argument("--season", type=int, default=2026)
    p.set_defaults(func=cmd_verify_live)

    p = sub.add_parser("engine-run", help="full engine pass for a round")
    common(p)
    p.add_argument("--round", required=True)
    p.add_argument("--db", default="data/bym.db")
    p.add_argument("--with-knowledge", action="store_true")
    p.add_argument("--json", help="write the RoundSummary contract to this path")
    p.set_defaults(func=cmd_engine_run)

    p = sub.add_parser(
        "serve", help="run the local decision-first web UI (UIUX.md Phase 1)"
    )
    common(p)
    p.add_argument("--round", default="rounds/2026_27_round_01.json")
    p.add_argument("--db", default="data/bym.db")
    p.add_argument("--submissions", default="data/submissions.json")
    p.add_argument("--registry", default="config/source_registry.json")
    p.add_argument("--lms-gameweek", default="data/lms_gameweek.json")
    p.add_argument("--lms-state", default="data/lms_state.json")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", default=8000, type=int)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser(
        "submission", help="submission state (§46) — the agent never submits"
    )
    p.add_argument(
        "submission_action", nargs="?", default="status",
        choices=["status", "mark-reviewed", "mark-entered"],
    )
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--season", type=int, default=2026)
    p.add_argument("--picks", nargs="+", metavar="H-A")
    p.add_argument("--golden-goal", type=int)
    p.set_defaults(func=cmd_submission)

    p = sub.add_parser(
        "readiness",
        help="how close the data-blocked backlog items are (docs/next_steps.md)",
    )
    p.add_argument("--data", default="data", help="cache directory for CSVs")
    p.add_argument(
        "--seasons", nargs=2, type=int, metavar=("START", "END"), default=[2019, 2025],
        help="season start-years, inclusive (e.g. 2019 2025)",
    )
    p.add_argument("--divisions", nargs="+", default=list(DEFAULT_DATA.divisions))
    p.add_argument("--db", default="data/bym.db")
    p.add_argument("--ui-state", default="data/ui_state.json")
    p.add_argument("--prediction-history", default="data/prediction_history.json")
    p.set_defaults(func=cmd_readiness)

    p = sub.add_parser("shadow-report", help="shadow extraction metrics")
    p.add_argument("--db", default="data/bym.db")
    p.set_defaults(func=cmd_shadow_report)

    p = sub.add_parser(
        "research", help="AVAILABILITY_ORACLE_RESEARCH_V1 (research only)"
    )
    p.add_argument(
        "research_action", nargs="?", default="status",
        choices=["status", "sample", "harvest", "prevalence", "freeze"],
    )
    p.add_argument("--research-db", default="research/data/oracle_v1.db")
    p.add_argument(
        "--max-requests", type=int,
        help="cap this harvest run; state persists so it resumes cleanly",
    )
    p.add_argument("--notes", help="freeze notes")
    p.add_argument(
        "--pace-seconds", type=float, default=6.5,
        help="delay between requests to stay under the 10/min limit",
    )
    p.add_argument(
        "--allow-partial", action="store_true",
        help="run prevalence on an incomplete harvest (NOT the "
             "pre-registered analysis)",
    )
    p.set_defaults(func=cmd_research)

    p = sub.add_parser("sources", help="source registry (§18.1)")
    p.add_argument(
        "sources_action", nargs="?", default="list", choices=["list", "approve"]
    )
    p.add_argument("source_id", nargs="?")
    p.add_argument("--registry", default="config/source_registry.json")
    p.set_defaults(func=cmd_sources)

    p = sub.add_parser("quota", help="API credit usage across sources")
    p.add_argument("--quota", default=None)
    p.set_defaults(func=cmd_quota)

    p = sub.add_parser(
        "coverage", help="detect API-Football coverage per league/season"
    )
    p.add_argument("--season", type=int, default=2026)
    p.add_argument("--divisions", nargs="+", default=["E0", "E1"])
    p.add_argument("--db", default="data/bym.db")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("score", help="score a recorded round once results are known")
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--season", type=int, default=2026)
    p.add_argument("--ledger", default="results/prospective_ledger.csv")
    p.add_argument(
        "--results",
        help='JSON: {"0": [2,0], "1": [1,1], "2": null}  (null = VOID). '
             "Omit to fetch from football-data.org via --round-file.",
    )
    p.add_argument("--round-file", help="round JSON, for fetching results")
    p.add_argument("--fd-season", type=int, default=2026)
    p.add_argument("--divisions", nargs="+", default=["E0", "E1"])
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("ledger", help="running prospective comparison across arms")
    p.add_argument("--ledger", default="results/prospective_ledger.csv")
    p.set_defaults(func=cmd_ledger)

    p = sub.add_parser(
        "lms-recommend",
        help="Last Man Standing: rank eligible clubs by P(win) for a gameweek",
    )
    common(p)
    p.add_argument(
        "--gameweek",
        help="gameweek JSON file — omit to fetch the current Premier League "
             "gameweek from football-data.org instead",
    )
    p.add_argument(
        "--fd-season", type=int, default=2026,
        help="football-data.org season start-year, used when --gameweek is omitted",
    )
    p.add_argument("--xi", type=float, default=DEFAULT_MODEL.xi)
    p.add_argument(
        "--entrant", help="pull already-used clubs from this entrant's saved state"
    )
    p.add_argument(
        "--used", nargs="+", default=[],
        help="extra club(s) to exclude ad hoc, on top of --entrant's history",
    )
    p.add_argument("--state", default="data/lms_state.json")
    p.set_defaults(func=cmd_lms_recommend)

    p = sub.add_parser(
        "lms-backtest",
        help="Last Man Standing: leak-free historical replay of weekly picks and plans",
    )
    p.add_argument("--data", default="data", help="cache directory for CSVs")
    p.add_argument(
        "--seasons", nargs=2, type=int, metavar=("START", "END"), required=True,
        help="season start-years available to training and test (e.g. 2019 2025)",
    )
    p.add_argument(
        "--test-seasons", nargs="+", type=int,
        help="season(s) to evaluate; default is every season after START",
    )
    p.add_argument("--horizon", type=int, default=DEFAULT_LMS.horizon_gameweeks)
    p.add_argument("--xi", type=float, default=DEFAULT_MODEL.xi)
    p.add_argument(
        "--refit-days", type=int, default=FROZEN_PHASE1_V1.refit_every_days,
        help="refit cadence, in days (default: %(default)s)",
    )
    p.add_argument("--csv", help="optional path for the summary CSV")
    p.set_defaults(func=cmd_lms_backtest)

    p = sub.add_parser(
        "lms-plan",
        help="Last Man Standing: plan several gameweeks at once (start here)",
    )
    common(p)
    p.add_argument(
        "--horizon", type=int, default=DEFAULT_LMS.horizon_gameweeks,
        help="gameweeks to plan over, including this one (default "
             f"{DEFAULT_LMS.horizon_gameweeks})",
    )
    p.add_argument(
        "--gameweek",
        help="fallback gameweek JSON file, used only if fixtures cannot be "
             "fetched — a one-week 'plan' is just the favourite",
    )
    p.add_argument(
        "--fd-season", type=int, default=2026,
        help="football-data.org season start-year (e.g. 2026 for 2026/27)",
    )
    p.add_argument("--xi", type=float, default=DEFAULT_MODEL.xi)
    p.add_argument(
        "--entrant", help="pull already-used clubs from this entrant's saved state"
    )
    p.add_argument(
        "--used", nargs="+", default=[],
        help="extra club(s) to exclude ad hoc, on top of --entrant's history",
    )
    p.add_argument("--state", default="data/lms_state.json")
    p.set_defaults(func=cmd_lms_plan)

    p = sub.add_parser(
        "lms-fixtures",
        help="Last Man Standing: fetch and save the current gameweek's fixtures",
    )
    common(p)
    p.add_argument("--gameweek", default="data/lms_gameweek.json", help="output file")
    p.add_argument(
        "--fd-season", type=int, default=2026,
        help="football-data.org season start-year (e.g. 2026 for 2026/27)",
    )
    p.set_defaults(func=cmd_lms_fixtures)

    p = sub.add_parser("lms-pick", help="Last Man Standing: record a pick")
    p.add_argument("--entrant", required=True)
    p.add_argument("--gameweek", type=int, required=True)
    p.add_argument("--team", required=True)
    p.add_argument("--opponent", required=True)
    p.add_argument("--home", action="store_true", help="team is at home this gameweek")
    p.add_argument("--state", default="data/lms_state.json")
    # Only consulted when replacing an existing pick, to find the kickoff
    # that closes the gameweek.
    p.add_argument("--fd-season", type=int, default=2026)
    p.set_defaults(func=cmd_lms_pick)

    p = sub.add_parser(
        "lms-result",
        help="Last Man Standing: settle a pick by hand (fallback — prefer lms-settle)",
    )
    p.add_argument("--entrant", required=True)
    p.add_argument("--gameweek", type=int, required=True)
    p.add_argument("--result", required=True, choices=["WIN", "DRAW", "LOSS"])
    p.add_argument("--state", default="data/lms_state.json")
    p.set_defaults(func=cmd_lms_result)

    p = sub.add_parser(
        "lms-settle",
        help="Last Man Standing: auto-settle pending picks from football-data.org results",
    )
    p.add_argument("--state", default="data/lms_state.json")
    p.add_argument(
        "--fd-season", type=int, default=2026,
        help="football-data.org season start-year (e.g. 2026 for 2026/27)",
    )
    p.add_argument("--divisions", nargs="+", default=["E0", "E1"])
    p.set_defaults(func=cmd_lms_settle)

    p = sub.add_parser("lms-status", help="Last Man Standing: survival status")
    p.add_argument("--entrant", help="omit to show every recorded entrant")
    p.add_argument("--state", default="data/lms_state.json")
    p.set_defaults(func=cmd_lms_status)

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
