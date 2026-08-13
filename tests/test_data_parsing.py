from pathlib import Path

import pytest

from bym.data.football_data import parse_season_csv

CSV = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A,B365CH,B365CD,B365CA
E0,16/08/2024,Arsenal,Everton,2,0,1.50,4.20,7.00,1.45,4.40,7.50
E0,17/08/2024,Chelsea,Newcastle,1,1,2.10,3.40,3.60,2.30,3.30,3.20
"""

CSV_NO_CLOSING = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A
E0,16/08/2015,Arsenal,Everton,2,0,1.50,4.20,7.00
"""


def test_parses_available_and_closing_odds(tmp_path):
    p = tmp_path / "2024_E0.csv"
    p.write_text(CSV)
    df = parse_season_csv(p, 2024, "E0")
    row = df.iloc[0]
    # MARKET-AVAILABLE = earlier price set
    assert row["odds_h"] == pytest.approx(1.50)
    # MARKET-ORACLE = closing price set, kept separate
    assert row["odds_ch"] == pytest.approx(1.45)
    assert row["odds_ca"] == pytest.approx(7.50)


def test_closing_odds_nan_when_absent(tmp_path):
    p = tmp_path / "2015_E0.csv"
    p.write_text(CSV_NO_CLOSING)
    df = parse_season_csv(p, 2015, "E0")
    assert df.iloc[0]["odds_h"] == pytest.approx(1.50)
    assert df["odds_ch"].isna().all()


def test_the_corpus_carries_handicap_and_totals_odds_all_along():
    """docs/next_steps.md item 3 said no historical odds existed for these
    markets. They were always there at ~100% coverage — the parser simply
    never read the columns."""
    from pathlib import Path

    from bym.data.football_data import load_matches

    matches = load_matches(Path("data"), [2024], ("E0",), fetch=False)
    for column in ("ah_line", "odds_ah_h", "odds_ah_a", "odds_over25", "odds_under25"):
        assert column in matches.columns
        assert matches[column].notna().mean() > 0.95, column
    # And the closing set, for the MARKET-ORACLE side of the split.
    for column in ("ah_cline", "odds_cah_h", "odds_cover25"):
        assert matches[column].notna().mean() > 0.95, column


def test_handicap_lines_are_the_quarter_grid_the_market_actually_quotes():
    from pathlib import Path

    from bym.data.football_data import load_matches

    lines = load_matches(Path("data"), [2024], ("E0",), fetch=False)["ah_line"].dropna()
    assert ((lines * 4) % 1 == 0).all(), "lines should fall on the quarter grid"
    assert lines.between(-4, 4).all()
