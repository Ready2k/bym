from pathlib import Path

import pytest

from super6.data.football_data import parse_season_csv

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
