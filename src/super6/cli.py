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


def _layer_credentials() -> dict:
    """Credentials and extractor for the availability and knowledge layers.

    Absent credentials are passed through as None rather than omitted, so
    each layer reports "skipped: no key" as a structured event instead of
    the loop silently doing less than it appears to.
    """
    import os

    from .knowledge.config import build_extractor, from_env

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
            print("Run with --loop to start polling, or invoke `super6 tick` "
                  "from cron/systemd.")
            return 0

        print("Polling. Ctrl-C to stop; state is persisted, so restarting resumes.")
        try:
            while True:
                outcomes = worker.run_due_jobs(
                    conn, api_key, lambda: router, **_layer_credentials()
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

    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("ODDS_API_KEY is not set.", file=sys.stderr)
        return 1
    matches = load_matches(Path(args.data), _season_range(args), tuple(args.divisions))
    router = _fit_router(matches, FROZEN_PHASE1_V1.xi, FROZEN_PHASE1_V1)

    with scheduler.connect(Path(args.db)) as conn:
        outcomes = worker.run_due_jobs(
            conn, api_key, lambda: router, **_layer_credentials()
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
        print("No scheduled jobs. Use `super6 watch --round ...`.")
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
        )

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
    p.add_argument("--db", default="data/super6.db")
    p.add_argument("--registry", default="config/source_registry.json")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("verify-live", help="live-verify each external source")
    p.add_argument("--registry", default="config/source_registry.json")
    p.add_argument("--season", type=int, default=2026)
    p.set_defaults(func=cmd_verify_live)

    p = sub.add_parser("engine-run", help="full engine pass for a round")
    common(p)
    p.add_argument("--round", required=True)
    p.add_argument("--db", default="data/super6.db")
    p.add_argument("--with-knowledge", action="store_true")
    p.add_argument("--json", help="write the RoundSummary contract to this path")
    p.set_defaults(func=cmd_engine_run)

    p = sub.add_parser(
        "serve", help="run the local decision-first web UI (UIUX.md Phase 1)"
    )
    common(p)
    p.add_argument("--round", default="rounds/example_round.json")
    p.add_argument("--db", default="data/super6.db")
    p.add_argument("--submissions", default="data/submissions.json")
    p.add_argument("--registry", default="config/source_registry.json")
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

    p = sub.add_parser("shadow-report", help="shadow extraction metrics")
    p.add_argument("--db", default="data/super6.db")
    p.set_defaults(func=cmd_shadow_report)

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
    p.add_argument("--db", default="data/super6.db")
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
