"""Football-Data.co.uk ingestion (SPEC.md §18).

Free downloadable historical results + betting odds — the primary source
for the initial training and backtesting corpus (§16). Downloads are cached
on disk so re-runs never re-fetch (§18's refresh-limit discipline).

Canonical fixture frame columns:
    date (Timestamp), season (int, start year), division (str),
    home_team, away_team (str), home_goals, away_goals (int),
    odds_h, odds_d, odds_a (float, NaN when unavailable),
    odds_ch, odds_cd, odds_ca (closing odds, NaN when unavailable)

Two odds sets, deliberately distinct (validation protocol):
    odds_h/d/a   — Football-Data's earlier price set (weekend fixtures are
                   generally collected Friday afternoon). The closest free
                   proxy to prices actually knowable at a Super 6 deadline
                   → the MARKET-AVAILABLE benchmark.
    odds_ch/cd/ca — closing odds ('C' columns, available since 2019/20).
                   These contain information from right up to kickoff —
                   including hours *after* a Super 6 deadline for late
                   kickoffs — so they are a theoretical gold standard, not
                   a fair deployable comparison → the MARKET-ORACLE
                   benchmark.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import requests

from ..config import DEFAULT_DATA, DataConfig


def season_code(start_year: int) -> str:
    """2023 -> '2324' (Football-Data.co.uk URL season code)."""
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def csv_url(start_year: int, division: str, cfg: DataConfig = DEFAULT_DATA) -> str:
    return f"{cfg.base_url}/{season_code(start_year)}/{division}.csv"


def fetch_season(
    start_year: int,
    division: str,
    dest_dir: Path,
    force: bool = False,
    cfg: DataConfig = DEFAULT_DATA,
) -> Path:
    """Download one season CSV into dest_dir, returning the cached path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{start_year}_{division}.csv"
    if path.exists() and not force:
        return path
    resp = requests.get(csv_url(start_year, division, cfg), timeout=30)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return path


def _pick_odds(
    df: pd.DataFrame, cfg: DataConfig, closing: bool = False
) -> pd.DataFrame:
    """Select H/D/A odds from the first bookmaker prefix with usable data.

    closing=True reads Football-Data's closing-price columns (B365CH etc.)
    for the MARKET-ORACLE benchmark; closing=False reads the earlier price
    set (B365H etc.) for MARKET-AVAILABLE.
    """
    suffix = "c" if closing else ""
    names = [f"odds_{suffix}h", f"odds_{suffix}d", f"odds_{suffix}a"]
    odds = pd.DataFrame(
        {n: float("nan") for n in names}, index=df.index, dtype=float
    )
    infix = "C" if closing else ""
    for prefix in cfg.odds_prefixes:
        cols = [f"{prefix}{infix}H", f"{prefix}{infix}D", f"{prefix}{infix}A"]
        if all(c in df.columns for c in cols):
            block = df[cols].apply(pd.to_numeric, errors="coerce")
            missing = odds[names[0]].isna()
            odds.loc[missing, names] = block.loc[missing].to_numpy()
    return odds


def _pick_derivative_odds(
    df: pd.DataFrame, cfg: DataConfig, closing: bool = False
) -> pd.DataFrame:
    """Select Asian-handicap and over/under-2.5 prices, plus the AH line.

    These were always in the corpus at near-total coverage — the parser
    simply never read them (docs/next_steps.md item 3 was wrong about this
    for eight months). They matter because a 1X2 price says nothing about
    *how* a result splits across scorelines, whereas a handicap prices
    supremacy and a total prices goal count, both of which bear directly
    on the score matrix Super 6 is actually scored against.

    The handicap line itself is a single column (`AHh` / `AHCh`), not per
    bookmaker: Football-Data publishes one market-size-adjusted line and
    each book's price against it.
    """
    suffix = "c" if closing else ""
    infix = "C" if closing else ""
    names = [
        f"ah_{suffix}line", f"odds_{suffix}ah_h", f"odds_{suffix}ah_a",
        f"odds_{suffix}over25", f"odds_{suffix}under25",
    ]
    out = pd.DataFrame({n: float("nan") for n in names}, index=df.index, dtype=float)

    line_col = "AHCh" if closing else "AHh"
    if line_col in df.columns:
        out[names[0]] = pd.to_numeric(df[line_col], errors="coerce")

    for prefix in cfg.derivative_odds_prefixes:
        # Handicap: B365AHH / B365CAHH.
        cols = [f"{prefix}{infix}AHH", f"{prefix}{infix}AHA"]
        if all(c in df.columns for c in cols):
            block = df[cols].apply(pd.to_numeric, errors="coerce")
            missing = out[names[1]].isna()
            out.loc[missing, [names[1], names[2]]] = block.loc[missing].to_numpy()
        # Totals: B365>2.5 / B365C>2.5.
        cols = [f"{prefix}{infix}>2.5", f"{prefix}{infix}<2.5"]
        if all(c in df.columns for c in cols):
            block = df[cols].apply(pd.to_numeric, errors="coerce")
            missing = out[names[3]].isna()
            out.loc[missing, [names[3], names[4]]] = block.loc[missing].to_numpy()
    return out


def parse_season_csv(
    path: Path,
    start_year: int,
    division: str,
    cfg: DataConfig = DEFAULT_DATA,
) -> pd.DataFrame:
    """Parse one cached CSV into the canonical fixture frame."""
    raw = path.read_bytes().decode("utf-8-sig", errors="replace")
    df = pd.read_csv(io.StringIO(raw), on_bad_lines="skip")
    df = df.dropna(subset=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])

    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df["Date"], dayfirst=True, format="mixed"),
            "season": start_year,
            "division": division,
            "home_team": df["HomeTeam"].astype(str).str.strip(),
            "away_team": df["AwayTeam"].astype(str).str.strip(),
            "home_goals": df["FTHG"].astype(int),
            "away_goals": df["FTAG"].astype(int),
        }
    )
    out = pd.concat(
        [
            out.reset_index(drop=True),
            _pick_odds(df, cfg).reset_index(drop=True),
            _pick_odds(df, cfg, closing=True).reset_index(drop=True),
            _pick_derivative_odds(df, cfg).reset_index(drop=True),
            _pick_derivative_odds(df, cfg, closing=True).reset_index(drop=True),
        ],
        axis=1,
    )
    return out.sort_values("date").reset_index(drop=True)


def team_divisions(matches: pd.DataFrame) -> dict[str, str]:
    """Each team's current division = the one of its most recent match."""
    stacked = pd.concat(
        [
            matches[["date", side, "division"]].rename(columns={side: "team"})
            for side in ("home_team", "away_team")
        ]
    ).sort_values("date")
    return stacked.groupby("team")["division"].last().to_dict()


def load_matches(
    data_dir: Path,
    seasons: list[int],
    divisions: tuple[str, ...] | None = None,
    fetch: bool = True,
    cfg: DataConfig = DEFAULT_DATA,
) -> pd.DataFrame:
    """Load (downloading if needed) the canonical match frame for the given
    season start-years and divisions."""
    divisions = divisions or cfg.divisions
    frames = []
    for year in seasons:
        for div in divisions:
            path = data_dir / f"{year}_{div}.csv"
            if not path.exists():
                if not fetch:
                    raise FileNotFoundError(f"{path} missing and fetch=False")
                fetch_season(year, div, data_dir, cfg=cfg)
            frames.append(parse_season_csv(path, year, div, cfg))
    matches = pd.concat(frames, ignore_index=True)
    return matches.sort_values("date").reset_index(drop=True)
