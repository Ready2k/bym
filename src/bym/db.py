"""Relational persistence (SPEC.md §19, §26).

SQLite is the single-node default; PostgreSQL is the migration target for
multiple instances/workers, multi-user access, meaningful concurrent
writes, or hosted HA. Application code must therefore avoid
SQLite-specific semantics — no `INSERT OR REPLACE`, no implicit rowid
dependence, no dynamic typing tricks.

The four requirements §26 imposes at either engine:

    foreign_keys = ON      referential integrity actually enforced
    WAL mode               readers don't block the writer
    schema migrations      versioned and applied in order
    UTC timestamps         stored as ISO8601 with an explicit offset

Timestamps are text ISO8601-with-offset rather than a native type,
because that is the one representation both engines read identically.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# Ordered, append-only. Never edit a migration that has shipped; add a new
# one. The index in this list is the schema version.
MIGRATIONS: list[str] = [
    # 0001 — scheduler jobs (§9)
    """
    CREATE TABLE IF NOT EXISTS jobs (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        job_type       TEXT NOT NULL,
        round_number   INTEGER,
        round_path     TEXT,
        deadline_at    TEXT NOT NULL,
        next_run_at    TEXT NOT NULL,
        status         TEXT NOT NULL,
        attempt_count  INTEGER NOT NULL DEFAULT 0,
        last_run_at    TEXT,
        last_error     TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs (status, next_run_at);
    """,
    # 0002 — team availability intelligence (Increment 3A)
    """
    CREATE TABLE IF NOT EXISTS availability_events (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        round_number        INTEGER,
        home_team           TEXT NOT NULL,
        away_team           TEXT NOT NULL,
        team                TEXT NOT NULL,
        player_name         TEXT NOT NULL,
        player_id           TEXT,
        resolution          TEXT NOT NULL,
        event_type          TEXT NOT NULL,
        claim_status        TEXT NOT NULL,
        source_id           TEXT NOT NULL,
        source_reliability  TEXT NOT NULL,
        source_timestamp    TEXT NOT NULL,
        observed_at         TEXT NOT NULL,
        role                TEXT NOT NULL DEFAULT 'UNKNOWN',
        importance          TEXT NOT NULL DEFAULT 'UNKNOWN',
        expires_at          TEXT,
        supersedes_event_id INTEGER,
        retracted_by_id     INTEGER,
        dedupe_key          TEXT NOT NULL UNIQUE,
        note                TEXT,
        FOREIGN KEY (supersedes_event_id) REFERENCES availability_events (id),
        FOREIGN KEY (retracted_by_id)     REFERENCES availability_events (id)
    );
    CREATE INDEX IF NOT EXISTS idx_avail_fixture
        ON availability_events (home_team, away_team, team, player_name);

    CREATE TABLE IF NOT EXISTS lineups (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        round_number  INTEGER,
        home_team     TEXT NOT NULL,
        away_team     TEXT NOT NULL,
        team          TEXT NOT NULL,
        kind          TEXT NOT NULL,          -- EXPECTED | CONFIRMED
        source_id     TEXT NOT NULL,
        generated_at  TEXT NOT NULL,
        observed_at   TEXT NOT NULL,
        confidence    TEXT,                   -- EXPECTED lineups only
        players       TEXT NOT NULL           -- JSON array of names
    );
    CREATE INDEX IF NOT EXISTS idx_lineups_fixture
        ON lineups (home_team, away_team, team, kind);
    """,
    # 0003 — source capability detection and fixture mapping (Increment 3B)
    """
    CREATE TABLE IF NOT EXISTS source_capabilities (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id   TEXT NOT NULL,
        league_id   INTEGER NOT NULL,
        season      INTEGER NOT NULL,
        feature     TEXT NOT NULL,
        available   INTEGER NOT NULL,
        checked_at  TEXT NOT NULL,
        UNIQUE (source_id, league_id, season, feature)
    );

    CREATE TABLE IF NOT EXISTS fixture_mappings (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id    TEXT NOT NULL,
        external_id  TEXT NOT NULL,
        home_team    TEXT NOT NULL,
        away_team    TEXT NOT NULL,
        kickoff_at   TEXT,
        league_id    INTEGER,
        season       INTEGER,
        mapped_at    TEXT NOT NULL,
        UNIQUE (source_id, external_id)
    );
    CREATE INDEX IF NOT EXISTS idx_fixture_map_teams
        ON fixture_mappings (source_id, home_team, away_team);
    """,
    # 0004 — knowledge agent evidence and claims (Increment 4)
    """
    CREATE TABLE IF NOT EXISTS evidence_documents (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        acquisition_source TEXT NOT NULL,
        origin_domain      TEXT NOT NULL,
        origin_authority   TEXT NOT NULL,
        url                TEXT NOT NULL,
        canonical          TEXT NOT NULL,
        title              TEXT,
        byline             TEXT,
        published_at       TEXT,
        retrieved_at       TEXT NOT NULL,
        content_hash       TEXT NOT NULL,
        story_cluster_id   TEXT,
        snippet            TEXT,
        UNIQUE (url, content_hash)
    );
    CREATE INDEX IF NOT EXISTS idx_evidence_cluster
        ON evidence_documents (story_cluster_id);

    CREATE TABLE IF NOT EXISTS candidate_claims (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        evidence_id         INTEGER NOT NULL,
        round_number        INTEGER,
        home_team           TEXT,
        away_team           TEXT,
        team                TEXT,
        player_name         TEXT,
        player_external_id  TEXT,
        claim_type          TEXT NOT NULL,
        availability        TEXT,
        certainty           TEXT,
        expected_role       TEXT,
        event_at            TEXT,
        observed_at         TEXT NOT NULL,
        supporting_evidence TEXT NOT NULL,
        source_url          TEXT NOT NULL,
        expires_at          TEXT,
        verdict             TEXT NOT NULL,
        rejection_reason    TEXT,
        story_cluster_id    TEXT,
        FOREIGN KEY (evidence_id) REFERENCES evidence_documents (id)
    );
    CREATE INDEX IF NOT EXISTS idx_claims_fixture
        ON candidate_claims (home_team, away_team, verdict);
    """,
    # 0005 — claim-extraction provenance (engine activation)
    """
    CREATE TABLE IF NOT EXISTS extraction_runs (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        provider          TEXT NOT NULL,
        model             TEXT,
        model_digest      TEXT,
        host              TEXT,
        prompt_version    TEXT NOT NULL,
        extractor_version TEXT NOT NULL,
        mode              TEXT NOT NULL,
        requested_at      TEXT NOT NULL,
        completed_at      TEXT,
        evidence_hash     TEXT NOT NULL,
        evidence_id       INTEGER,
        status            TEXT NOT NULL,
        error             TEXT,
        claims_emitted    INTEGER NOT NULL DEFAULT 0,
        claims_accepted   INTEGER NOT NULL DEFAULT 0,
        claims_rejected   INTEGER NOT NULL DEFAULT 0,
        promoted          INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (evidence_id) REFERENCES evidence_documents (id)
    );
    CREATE INDEX IF NOT EXISTS idx_extraction_runs_evidence
        ON extraction_runs (evidence_id);
    """,
    # 0006 — claim promotion flag, so shadow-mode claims are visibly unpromoted
    """
    ALTER TABLE candidate_claims ADD COLUMN promoted INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE candidate_claims ADD COLUMN extraction_run_id INTEGER;
    """,
    # 0007 — FPL season-scoped player identity (SPEC_FPL.md §4 Phase 1A).
    # Table names are pluralised to match this file's existing convention;
    # they are the same entities SPEC_FPL.md §4 calls `fpl_player` and
    # `fpl_player_season`. Club identity stays the TEXT name every other
    # part of bym already keys on (round.py, availability, LMS used_clubs)
    # rather than introducing a second, numeric team_id scheme alongside it.
    """
    CREATE TABLE IF NOT EXISTS fpl_players (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_name TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_fpl_players_name ON fpl_players (canonical_name);

    CREATE TABLE IF NOT EXISTS fpl_player_seasons (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id      INTEGER NOT NULL REFERENCES fpl_players (id),
        season         INTEGER NOT NULL,
        fpl_element_id INTEGER,
        team           TEXT NOT NULL,
        position       TEXT NOT NULL,
        start_price    REAL,
        external_ids   TEXT NOT NULL DEFAULT '{}',
        UNIQUE (player_id, season)
    );
    CREATE INDEX IF NOT EXISTS idx_fpl_player_seasons_season
        ON fpl_player_seasons (season, team);
    """,
    # 0008 — FPL historical player-match statistics (SPEC_FPL.md §4 Phase 1A).
    # Columns not populated by the free-tier api_football ingester (xg, xa,
    # own_goals, clearances, recoveries, cbit/cbirt, bps) stay nullable
    # rather than defaulted to 0 — 0 would claim "observed zero", not
    # "not supplied by this source".
    """
    CREATE TABLE IF NOT EXISTS fpl_player_match_stats (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id              INTEGER NOT NULL REFERENCES fpl_players (id),
        fixture_id             TEXT NOT NULL,
        season                 INTEGER NOT NULL,
        source_id              TEXT NOT NULL,
        source_record_version  TEXT NOT NULL,
        dataset_manifest_id    TEXT,
        observed_at            TEXT NOT NULL,
        minutes                INTEGER NOT NULL DEFAULT 0,
        goals                  INTEGER NOT NULL DEFAULT 0,
        assists                INTEGER NOT NULL DEFAULT 0,
        shots                  INTEGER,
        xg                     REAL,
        xa                     REAL,
        saves                  INTEGER,
        yellow_cards           INTEGER NOT NULL DEFAULT 0,
        red_cards              INTEGER NOT NULL DEFAULT 0,
        own_goals              INTEGER,
        penalties_missed       INTEGER,
        penalties_saved        INTEGER,
        tackles                INTEGER,
        interceptions          INTEGER,
        blocks                 INTEGER,
        clearances             INTEGER,
        recoveries             INTEGER,
        cbit_actions           INTEGER,
        cbirt_actions          INTEGER,
        bps                    INTEGER,
        UNIQUE (player_id, fixture_id, source_id)
    );
    CREATE INDEX IF NOT EXISTS idx_fpl_match_stats_fixture
        ON fpl_player_match_stats (fixture_id);
    CREATE INDEX IF NOT EXISTS idx_fpl_match_stats_player_season
        ON fpl_player_match_stats (player_id, season);
    """,
    # 0009 — FPL game-state import persistence (SPEC_FPL.md §4, §13.3, §15
    # Phase 1A). Two tables deliberately: fpl_game_state_snapshots is the
    # generic, source-agnostic provenance wrapper (§15) and
    # fpl_squad_snapshots is its FPL-domain decode (§4), linked by FK. Both
    # are append-only — deadline/game-state snapshots are immutable
    # (§4, §11.2) — so neither table gets an UPDATE path, only INSERT.
    # UNIQUE(source_id, content_hash) is the idempotency gate: re-importing
    # byte-identical content must not manufacture a second row, the same
    # dedupe_key idiom availability_events uses.
    """
    CREATE TABLE IF NOT EXISTS fpl_game_state_snapshots (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id              TEXT NOT NULL,
        source_schema_version  TEXT NOT NULL,
        observed_at            TEXT NOT NULL,
        content_hash           TEXT NOT NULL,
        normalised_state       TEXT NOT NULL,
        raw_payload_location   TEXT,
        UNIQUE (source_id, content_hash)
    );
    CREATE INDEX IF NOT EXISTS idx_fpl_game_state_snapshots_source
        ON fpl_game_state_snapshots (source_id, observed_at);

    CREATE TABLE IF NOT EXISTS fpl_squad_snapshots (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        game_state_snapshot_id  INTEGER NOT NULL
            REFERENCES fpl_game_state_snapshots (id),
        season                  INTEGER NOT NULL,
        gameweek                INTEGER NOT NULL,
        as_of                   TEXT NOT NULL,
        deadline                TEXT,
        bank                    REAL NOT NULL,
        free_transfers          INTEGER NOT NULL,
        active_chip             TEXT,
        chips_available         TEXT NOT NULL DEFAULT '[]',
        players                 TEXT NOT NULL,
        starter_ids             TEXT NOT NULL,
        captain_id              INTEGER NOT NULL,
        vice_captain_id         INTEGER NOT NULL,
        bench_order             TEXT NOT NULL,
        source_kind             TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_fpl_squad_snapshots_season_gw
        ON fpl_squad_snapshots (season, gameweek);
    """,
    # 0010 — fixture kickoff time on match stats (SPEC_FPL.md §12.1, Phase
    # 1B). `observed_at` is when a stat row was INGESTED (always after the
    # fact, for a historical fixture), never when the fixture itself was
    # played — walk-forward evaluation needs the latter to order fixtures
    # chronologically and enforce a causal cutoff (fpl.causality), so it
    # gets its own column rather than overloading observed_at's meaning.
    """
    ALTER TABLE fpl_player_match_stats ADD COLUMN kickoff_at TEXT;
    CREATE INDEX IF NOT EXISTS idx_fpl_match_stats_kickoff
        ON fpl_player_match_stats (kickoff_at);
    """,
    # 0011 — projection/recommendation persistence (SPEC_FPL.md §4, §11.2,
    # §15, Phase 1B). Deliberately deferred from Phase 1A until the
    # projection/scoring shape had actually been exercised (§18) — see
    # docs/MANIFEST_FPL.md. `model_version` is part of the uniqueness key
    # on the two projection tables, not just a label: §12.3 forbids a
    # later model version from overwriting an earlier one's row, since a
    # frozen baseline's projections must stay reconstructable even after
    # a challenger version exists. `fpl_recommendation_snapshots` carries
    # no uniqueness constraint at all — every call is a new immutable
    # observation (§11.2), never an update, including repeated pre-
    # deadline reforecasts.
    """
    CREATE TABLE IF NOT EXISTS fpl_fixture_projections (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id              INTEGER NOT NULL REFERENCES fpl_players (id),
        fixture_id             TEXT NOT NULL,
        season                 INTEGER NOT NULL,
        gameweek               INTEGER NOT NULL,
        p_appearance           REAL NOT NULL,
        p_start                REAL NOT NULL,
        p_60_plus              REAL NOT NULL,
        expected_minutes       REAL NOT NULL,
        goal_lambda            REAL NOT NULL,
        assist_lambda          REAL NOT NULL,
        p_clean_sheet          REAL NOT NULL,
        expected_saves         REAL,
        expected_defcon_points REAL,
        expected_bonus         REAL,
        expected_points        REAL NOT NULL,
        points_variance        REAL,
        model_version          TEXT NOT NULL,
        computed_at            TEXT NOT NULL,
        UNIQUE (player_id, fixture_id, model_version)
    );
    CREATE INDEX IF NOT EXISTS idx_fpl_fixture_proj_gw
        ON fpl_fixture_projections (season, gameweek);

    CREATE TABLE IF NOT EXISTS fpl_gameweek_projections (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id         INTEGER NOT NULL REFERENCES fpl_players (id),
        season            INTEGER NOT NULL,
        gameweek          INTEGER NOT NULL,
        expected_points   REAL NOT NULL,
        points_variance   REAL,
        quantiles         TEXT,
        simulation_key    TEXT,
        fixtures_played   INTEGER NOT NULL,
        model_version     TEXT NOT NULL,
        computed_at       TEXT NOT NULL,
        UNIQUE (player_id, season, gameweek, model_version)
    );
    CREATE INDEX IF NOT EXISTS idx_fpl_gw_proj_gw
        ON fpl_gameweek_projections (season, gameweek);

    CREATE TABLE IF NOT EXISTS fpl_recommendation_snapshots (
        id                        INTEGER PRIMARY KEY AUTOINCREMENT,
        season                    INTEGER NOT NULL,
        gameweek                  INTEGER NOT NULL,
        game_state_snapshot_id    INTEGER REFERENCES fpl_game_state_snapshots (id),
        computed_at               TEXT NOT NULL,
        model_version             TEXT NOT NULL,
        top_k                     INTEGER NOT NULL,
        squad_ids                 TEXT NOT NULL,
        starter_ids               TEXT NOT NULL,
        bench_order               TEXT NOT NULL,
        captain_id                INTEGER NOT NULL,
        vice_captain_id           INTEGER NOT NULL,
        transfers_in              TEXT NOT NULL,
        transfers_out             TEXT NOT NULL,
        hit_cost                  REAL NOT NULL,
        xi_expected_points        REAL NOT NULL,
        evaluated_expected_points REAL NOT NULL,
        net_expected_points       REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_fpl_recommendation_snapshots_gw
        ON fpl_recommendation_snapshots (season, gameweek);
    """,
    # 0012 — resumable historical-fixture harvest bookkeeping (SPEC_FPL.md
    # §5.2/§5.6, Phase 1B corpus build). Deliberately its own table, not a
    # reuse of `fixture_mappings` (Increment 3B's availability/lineup
    # mapping) — different subsystem, different lifecycle fields
    # (player_stats_status/attempt_count), and coupling them would make
    # neither easy to reason about alone. `player_stats_status` values:
    # PENDING | FETCHED | NO_DATA | RETRYABLE_FAILURE | FINAL_FAILURE.
    # NO_DATA means the API was queried and legitimately returned zero
    # player rows — never fabricated, never re-attempted, and never
    # confused with a fixture that hasn't been tried yet.
    """
    CREATE TABLE IF NOT EXISTS fpl_fixture_harvest_state (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id           TEXT NOT NULL,
        season               INTEGER NOT NULL,
        competition          TEXT NOT NULL,
        home_team            TEXT NOT NULL,
        away_team            TEXT NOT NULL,
        kickoff_at           TEXT,
        player_stats_status  TEXT NOT NULL DEFAULT 'PENDING',
        attempt_count        INTEGER NOT NULL DEFAULT 0,
        last_attempt_at      TEXT,
        content_hash         TEXT,
        row_count            INTEGER,
        UNIQUE (fixture_id, competition, season)
    );
    CREATE INDEX IF NOT EXISTS idx_fpl_harvest_state_status
        ON fpl_fixture_harvest_state (season, competition, player_stats_status);
    """,
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime) -> str:
    """UTC ISO8601 with an explicit offset (§26)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    """Apply outstanding migrations in order. Returns the new version."""
    current = schema_version(conn)
    for index in range(current, len(MIGRATIONS)):
        conn.executescript(MIGRATIONS[index])
        assert isinstance(index, int) and index >= 0, "Migration index must be a plain int"
        conn.execute(f"PRAGMA user_version = {index + 1}")
    return schema_version(conn)


@contextmanager
def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        migrate(conn)
        yield conn
    finally:
        conn.close()
