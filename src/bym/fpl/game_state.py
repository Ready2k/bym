"""User-supplied FPL game-state import (docs/SPEC_FPL.md §4, §13.3, §15).

§13.1/§13.3 rule this module exists to satisfy: `bym` never queries
`fantasy.premierleague.com/api/*`, so the mutable pre-deadline squad state
(bank, free transfers, chips, the user's 15) and the full transfer
candidate universe's current prices (needed to compare every legal
replacement, not just the 15 already owned) must come from an explicit
user import instead of a live read. This module is that import path:
parse a JSON payload, validate its shape, resolve every named player
against the existing `fpl_player`/`fpl_player_season` identity store
(`player_store`), and persist the result as two append-only rows.

Two persisted concepts, kept separate per §4/§15 even though one row's
JSON contains what the other exposes structurally:

- `fpl_game_state_snapshots` — the generic, source-agnostic provenance
  wrapper (§15): source/schema/observed-at/content-hash/normalised-state.
  `raw_payload_location` is always NULL here — §13.4 data minimisation
  means whole raw payloads are not retained by default, and no
  SourceRecord for `user_import` grants that retention.
- `fpl_squad_snapshots` — the FPL-domain structured decode (§4): season,
  gameweek, bank, free transfers, chips, the 15 players with their
  purchase/current/selling prices, the XI/bench/captaincy split.

Both tables are append-only. Deadline snapshots are immutable (§4,
§11.2's discipline restated for the import side) — nothing here ever
UPDATEs a stored row, only INSERTs a new one. Re-importing byte-identical
content is idempotent via `content_hash`, the same `dedupe_key` idiom
`availability/store.py` uses for repeated polls of one source.

The full candidate-player price list is not a separate permanent SQL
table (§4/§15 do not name one) — it lives inside
`fpl_game_state_snapshots.normalised_state` as exactly the "normalised
minimum required to recreate a recommendation" §15 asks for, and
`candidate_universe()` reads it back out as a plain `dict[int,
CandidatePrice]`.

Import JSON shape (documented here since there is no schema file yet):

```json
{
  "gameweek": 3,
  "as_of": "2026-08-17T12:00:00+00:00",
  "deadline": "2026-08-20T17:30:00+00:00",
  "bank": 1.5,
  "free_transfers": 2,
  "active_chip": null,
  "chips_available": ["wildcard", "bench_boost", "triple_captain", "free_hit"],
  "candidate_universe_complete": true,
  "candidate_players": [
    {"name": "Erling Haaland", "club": "Man City", "position": "FWD", "price": 15.0}
  ],
  "squad": [
    {"name": "Erling Haaland", "purchase_price": 14.5, "current_price": 15.0, "selling_price": 14.8}
  ],
  "starters": ["Erling Haaland", "..."],
  "bench_order": ["...", "...", "...", "..."],
  "captain": "Erling Haaland",
  "vice_captain": "Mohamed Salah",
  "ownership_evidence": null
}
```

`candidate_players` is the full transfer-candidate universe (§13.3), not
just the user's 15 — every `squad` entry must name a player who also
appears there, since that is where its position/club/current-price live.
Players are matched by name (case-insensitive), the same identity
resolution `ingest.py` uses, falling back to `player_store.
get_or_create_player` for a candidate the store has never seen — an FPL
price list routinely includes players with no historical match stats.

This module never validates FPL squad-legality (2 GK/5 DEF/5 MID/3 FWD,
club limits, budget) — only that the payload is internally consistent
shape-wise. Squad legality is the optimiser's job, in a later task.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from ..db import to_iso, utcnow
from . import player_store
from .players import Position

SOURCE_ID = "user_import"
SCHEMA_VERSION = "fpl_import_v1"

_POSITION_VALUES = {p.value for p in Position}

REQUIRED_KEYS = (
    "gameweek",
    "as_of",
    "bank",
    "free_transfers",
    "chips_available",
    "candidate_universe_complete",
    "candidate_players",
    "squad",
    "starters",
    "bench_order",
    "captain",
    "vice_captain",
)


class ImportValidationError(ValueError):
    """The import payload could not be parsed: a required field is
    missing, or the squad/XI/bench/captaincy shape is internally
    inconsistent. Never raised for FPL squad-illegality (formation,
    budget, club limits) — that check does not live here."""


@dataclass
class CandidatePrice:
    """One entry in the transfer candidate universe (§13.3) — every
    player with a current FPL price, whether the user owns them or not."""

    player_id: int
    name: str
    club: str
    position: str
    price: float


@dataclass
class SquadPlayer:
    """One of the user's 15, with the three prices §4 tracks separately:
    what it cost, what it is worth now, and what selling it would yield
    (these diverge once a price has risen after purchase)."""

    player_id: int
    purchase_price: float
    current_price: float
    selling_price: float


@dataclass
class SquadSnapshot:
    """The FPL-domain decode persisted to `fpl_squad_snapshots` (§4)."""

    season: int
    gameweek: int
    as_of: str
    deadline: str | None
    bank: float
    free_transfers: int
    active_chip: str | None
    chips_available: list[str]
    players: list[SquadPlayer]
    starter_ids: list[int]
    captain_id: int
    vice_captain_id: int
    bench_order: list[int]
    source_kind: str = "IMPORT"
    id: int | None = None
    game_state_snapshot_id: int | None = None


@dataclass
class GameStateSnapshot:
    """The generic provenance wrapper persisted to
    `fpl_game_state_snapshots` (§15)."""

    source_id: str
    source_schema_version: str
    observed_at: str
    content_hash: str
    normalised_state: dict = field(default_factory=dict)
    raw_payload_location: str | None = None
    id: int | None = None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ImportValidationError(message)


def _resolve_player(conn: sqlite3.Connection, name: str) -> int:
    player = player_store.find_player_by_name(conn, name)
    if player is None:
        player, _ = player_store.get_or_create_player(conn, name)
    return player.id


def parse_import(
    conn: sqlite3.Connection, payload: dict, season: int
) -> tuple[dict[int, CandidatePrice], SquadSnapshot]:
    """Parse and validate an import payload, resolving every named player
    against the existing identity store. Raises `ImportValidationError`
    naming exactly what is wrong and where; never silently coerces bad
    input into something plausible-looking."""
    if not isinstance(payload, dict):
        raise ImportValidationError("import payload must be a JSON object")

    missing = [k for k in REQUIRED_KEYS if k not in payload]
    _require(not missing, f"import payload missing required key(s): {', '.join(missing)}")

    universe, candidates_by_name = _parse_candidate_universe(conn, payload["candidate_players"])
    squad_players, name_to_player_id = _parse_squad(payload["squad"], candidates_by_name)

    complete = payload["candidate_universe_complete"]
    _require(
        isinstance(complete, bool),
        "candidate_universe_complete must be a boolean",
    )
    if complete:
        _require(
            len(universe) > len(squad_players),
            "a complete candidate universe must include at least one unowned player",
        )

    starter_ids = _resolve_named_group(
        payload["starters"], 11, "starters", name_to_player_id
    )
    bench_ids = _resolve_named_group(
        payload["bench_order"], 4, "bench_order", name_to_player_id
    )
    _require(
        not (set(starter_ids) & set(bench_ids)),
        "a player cannot appear in both starters and bench_order",
    )
    # No explicit "do starters+bench cover all 15" check needed: both are
    # disjoint subsets of the 15-name squad set with sizes 11 and 4, so a
    # union of size 15 is forced to equal the full squad.

    captain_id = _resolve_one_name(payload.get("captain"), "captain", name_to_player_id)
    vice_captain_id = _resolve_one_name(
        payload.get("vice_captain"), "vice_captain", name_to_player_id
    )
    _require(captain_id != vice_captain_id, "captain and vice_captain must be different players")

    bank = payload.get("bank")
    free_transfers = payload.get("free_transfers")
    _require(bank is not None, "bank is required")
    _require(free_transfers is not None, "free_transfers is required")

    chips_available = payload["chips_available"]
    _require(isinstance(chips_available, list), "chips_available must be a list")

    snapshot = SquadSnapshot(
        season=season,
        gameweek=int(payload["gameweek"]),
        as_of=payload["as_of"],
        deadline=payload.get("deadline"),
        bank=float(bank),
        free_transfers=int(free_transfers),
        active_chip=payload.get("active_chip"),
        chips_available=list(chips_available),
        players=squad_players,
        starter_ids=starter_ids,
        captain_id=captain_id,
        vice_captain_id=vice_captain_id,
        bench_order=bench_ids,
    )
    return universe, snapshot


def _parse_candidate_universe(
    conn: sqlite3.Connection, raw: object
) -> tuple[dict[int, CandidatePrice], dict[str, CandidatePrice]]:
    _require(
        isinstance(raw, list) and len(raw) > 0,
        "candidate_players must be a non-empty list",
    )
    universe: dict[int, CandidatePrice] = {}
    by_name: dict[str, CandidatePrice] = {}
    for i, entry in enumerate(raw):
        _require(isinstance(entry, dict), f"candidate_players[{i}] must be an object")
        for key in ("name", "club", "position", "price"):
            _require(key in entry, f"candidate_players[{i}] missing '{key}'")
        position = entry["position"]
        _require(
            position in _POSITION_VALUES,
            f"candidate_players[{i}] has unknown position {position!r}",
        )
        name = entry["name"]
        player_id = _resolve_player(conn, name)
        candidate = CandidatePrice(
            player_id=player_id,
            name=name,
            club=entry["club"],
            position=position,
            price=float(entry["price"]),
        )
        universe[player_id] = candidate
        by_name[name.casefold()] = candidate
    return universe, by_name


def _parse_squad(
    raw: object, candidates_by_name: dict[str, CandidatePrice]
) -> tuple[list[SquadPlayer], dict[str, int]]:
    _require(
        isinstance(raw, list) and len(raw) == 15,
        f"squad must contain exactly 15 players, got "
        f"{len(raw) if isinstance(raw, list) else type(raw).__name__}",
    )
    squad_players: list[SquadPlayer] = []
    name_to_player_id: dict[str, int] = {}
    for i, entry in enumerate(raw):
        _require(isinstance(entry, dict), f"squad[{i}] must be an object")
        for key in ("name", "purchase_price", "current_price", "selling_price"):
            _require(key in entry, f"squad[{i}] missing '{key}'")
        name = entry["name"]
        candidate = candidates_by_name.get(name.casefold())
        _require(
            candidate is not None,
            f"squad player {name!r} is not in candidate_players — the candidate "
            "universe must include every owned player",
        )
        key = name.casefold()
        _require(key not in name_to_player_id, f"squad contains {name!r} more than once")
        name_to_player_id[key] = candidate.player_id
        squad_players.append(
            SquadPlayer(
                player_id=candidate.player_id,
                purchase_price=float(entry["purchase_price"]),
                current_price=float(entry["current_price"]),
                selling_price=float(entry["selling_price"]),
            )
        )
    return squad_players, name_to_player_id


def _resolve_one_name(name: object, label: str, name_to_player_id: dict[str, int]) -> int:
    _require(isinstance(name, str) and name, f"{label} is required")
    key = name.casefold()
    _require(key in name_to_player_id, f"{label} {name!r} is not one of the 15 squad players")
    return name_to_player_id[key]


def _resolve_named_group(
    raw: object, expected_count: int, label: str, name_to_player_id: dict[str, int]
) -> list[int]:
    _require(
        isinstance(raw, list) and len(raw) == expected_count,
        f"{label} must contain exactly {expected_count} players, got "
        f"{len(raw) if isinstance(raw, list) else type(raw).__name__}",
    )
    ids = [_resolve_one_name(name, f"{label} entry", name_to_player_id) for name in raw]
    _require(len(set(ids)) == expected_count, f"{label} contains duplicate players")
    return ids


def _normalised_state(
    universe: dict[int, CandidatePrice],
    snapshot: SquadSnapshot,
    candidate_universe_complete: bool,
) -> dict:
    """The parsed, resolved payload — a normalised structure a caller can
    reconstruct `CandidatePrice`/`SquadSnapshot` from, not a copy of the
    arbitrary JSON the user actually sent."""
    return {
        "candidate_universe_complete": candidate_universe_complete,
        "candidate_players": [
            {
                "player_id": c.player_id,
                "name": c.name,
                "club": c.club,
                "position": c.position,
                "price": c.price,
            }
            for c in universe.values()
        ],
        "squad_snapshot": {
            "season": snapshot.season,
            "gameweek": snapshot.gameweek,
            "as_of": snapshot.as_of,
            "deadline": snapshot.deadline,
            "bank": snapshot.bank,
            "free_transfers": snapshot.free_transfers,
            "active_chip": snapshot.active_chip,
            "chips_available": snapshot.chips_available,
            "players": [
                {
                    "player_id": p.player_id,
                    "purchase_price": p.purchase_price,
                    "current_price": p.current_price,
                    "selling_price": p.selling_price,
                }
                for p in snapshot.players
            ],
            "starter_ids": snapshot.starter_ids,
            "captain_id": snapshot.captain_id,
            "vice_captain_id": snapshot.vice_captain_id,
            "bench_order": snapshot.bench_order,
            "source_kind": snapshot.source_kind,
        },
    }


def _content_hash(payload: dict) -> str:
    """Hash of the raw payload, used as the idempotency key — canonical
    (sorted-key) JSON so key ordering in the source file cannot change
    the hash of byte-identical content."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _row_to_game_state(row: sqlite3.Row) -> GameStateSnapshot:
    return GameStateSnapshot(
        id=row["id"],
        source_id=row["source_id"],
        source_schema_version=row["source_schema_version"],
        observed_at=row["observed_at"],
        content_hash=row["content_hash"],
        normalised_state=json.loads(row["normalised_state"]),
        raw_payload_location=row["raw_payload_location"],
    )


def _row_to_squad_snapshot(row: sqlite3.Row) -> SquadSnapshot:
    return SquadSnapshot(
        id=row["id"],
        game_state_snapshot_id=row["game_state_snapshot_id"],
        season=row["season"],
        gameweek=row["gameweek"],
        as_of=row["as_of"],
        deadline=row["deadline"],
        bank=row["bank"],
        free_transfers=row["free_transfers"],
        active_chip=row["active_chip"],
        chips_available=json.loads(row["chips_available"]),
        players=[SquadPlayer(**p) for p in json.loads(row["players"])],
        starter_ids=json.loads(row["starter_ids"]),
        captain_id=row["captain_id"],
        vice_captain_id=row["vice_captain_id"],
        bench_order=json.loads(row["bench_order"]),
        source_kind=row["source_kind"],
    )


def store_import(
    conn: sqlite3.Connection,
    payload: dict,
    season: int,
    now: datetime | None = None,
) -> tuple[GameStateSnapshot, SquadSnapshot]:
    """Parse, validate and persist an import payload. Re-importing
    byte-identical content is a no-op that returns the existing rows
    rather than raising or duplicating — idempotency via `content_hash`,
    matching `availability/store.py`'s `record_event` pattern."""
    content_hash = _content_hash(payload)
    existing = conn.execute(
        "SELECT * FROM fpl_game_state_snapshots WHERE source_id = ? AND content_hash = ?",
        (SOURCE_ID, content_hash),
    ).fetchone()
    if existing is not None:
        game_state = _row_to_game_state(existing)
        squad_row = conn.execute(
            "SELECT * FROM fpl_squad_snapshots WHERE game_state_snapshot_id = ?",
            (existing["id"],),
        ).fetchone()
        return game_state, _row_to_squad_snapshot(squad_row)

    universe, snapshot = parse_import(conn, payload, season)
    normalised = _normalised_state(
        universe, snapshot, payload["candidate_universe_complete"]
    )
    observed_at = to_iso(now or utcnow())

    cursor = conn.execute(
        "INSERT INTO fpl_game_state_snapshots "
        "(source_id, source_schema_version, observed_at, content_hash, "
        "normalised_state, raw_payload_location) VALUES (?, ?, ?, ?, ?, ?)",
        (SOURCE_ID, SCHEMA_VERSION, observed_at, content_hash, json.dumps(normalised), None),
    )
    game_state_id = int(cursor.lastrowid)

    cursor = conn.execute(
        "INSERT INTO fpl_squad_snapshots "
        "(game_state_snapshot_id, season, gameweek, as_of, deadline, bank, "
        "free_transfers, active_chip, chips_available, players, starter_ids, "
        "captain_id, vice_captain_id, bench_order, source_kind) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            game_state_id,
            snapshot.season,
            snapshot.gameweek,
            snapshot.as_of,
            snapshot.deadline,
            snapshot.bank,
            snapshot.free_transfers,
            snapshot.active_chip,
            json.dumps(snapshot.chips_available),
            json.dumps(
                [
                    {
                        "player_id": p.player_id,
                        "purchase_price": p.purchase_price,
                        "current_price": p.current_price,
                        "selling_price": p.selling_price,
                    }
                    for p in snapshot.players
                ]
            ),
            json.dumps(snapshot.starter_ids),
            snapshot.captain_id,
            snapshot.vice_captain_id,
            json.dumps(snapshot.bench_order),
            snapshot.source_kind,
        ),
    )
    snapshot.id = int(cursor.lastrowid)
    snapshot.game_state_snapshot_id = game_state_id

    game_state = GameStateSnapshot(
        id=game_state_id,
        source_id=SOURCE_ID,
        source_schema_version=SCHEMA_VERSION,
        observed_at=observed_at,
        content_hash=content_hash,
        normalised_state=normalised,
        raw_payload_location=None,
    )
    return game_state, snapshot


def candidate_universe(game_state: GameStateSnapshot) -> dict[int, CandidatePrice]:
    """Read the transfer candidate universe back out of a loaded
    snapshot — the "expose it as a plain Python structure" half of §13.3,
    so a caller never has to re-parse `normalised_state` by hand."""
    return {
        c["player_id"]: CandidatePrice(**c)
        for c in game_state.normalised_state["candidate_players"]
    }


def candidate_universe_is_complete(game_state: GameStateSnapshot) -> bool:
    """Whether the import author explicitly attested that prices cover the
    full transfer universe, not merely the current fifteen.

    This is deliberately an attestation rather than a guessed player-count
    threshold: the permitted import source is the only component that can
    know it supplied every eligible player for this FPL season.
    """
    return bool(game_state.normalised_state.get("candidate_universe_complete", False))


def load_snapshot(
    conn: sqlite3.Connection, game_state_snapshot_id: int
) -> tuple[GameStateSnapshot, SquadSnapshot]:
    """Load one immutable FPL state and its decoded squad snapshot.

    Recommendation code must be anchored to a particular observed state,
    rather than silently selecting whichever import happened to be latest.
    Keeping that choice explicit is what makes a later recommendation row
    reproducible when several pre-deadline imports exist for one Gameweek.
    """
    state_row = conn.execute(
        "SELECT * FROM fpl_game_state_snapshots WHERE id = ?", (game_state_snapshot_id,)
    ).fetchone()
    if state_row is None:
        raise ValueError(f"FPL game-state snapshot #{game_state_snapshot_id} does not exist")
    squad_row = conn.execute(
        "SELECT * FROM fpl_squad_snapshots WHERE game_state_snapshot_id = ?",
        (game_state_snapshot_id,),
    ).fetchone()
    if squad_row is None:
        raise ValueError(
            f"FPL game-state snapshot #{game_state_snapshot_id} has no squad snapshot"
        )
    return _row_to_game_state(state_row), _row_to_squad_snapshot(squad_row)
