from pathlib import Path

import pytest
import requests

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


# --- Failed downloads must never reach the cache ------------------------
#
# Football-Data answers a not-yet-published season with 300 Multiple
# Choices. requests does not follow a 300 (no unambiguous Location) and
# raise_for_status() does not raise on 3xx, so the HTML body used to be
# written to disk as <year>_<div>.csv. Because the cache check is a bare
# path.exists(), that poisoned file was then returned on every later run
# — surfacing as a KeyError from dropna, in another function, about a
# download nobody suspected.

HTML_300 = (
    b'<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN">\n'
    b"<html><head>\n<title>300 Multiple Choices</title>\n</head><body>\n"
    b"<h1>Multiple Choices</h1>\n</body></html>\n"
)


class _FakeResponse:
    def __init__(self, status_code, content):
        self.status_code = status_code
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


def _serve(monkeypatch, status_code, content):
    from bym.data import football_data

    monkeypatch.setattr(
        football_data.requests,
        "get",
        lambda *a, **k: _FakeResponse(status_code, content),
    )


def test_a_300_response_is_not_cached_as_a_season(tmp_path, monkeypatch):
    from bym.data.football_data import SeasonUnavailable, fetch_season

    _serve(monkeypatch, 300, HTML_300)
    with pytest.raises(SeasonUnavailable):
        fetch_season(2026, "E1", tmp_path)
    assert list(tmp_path.iterdir()) == [], "a failed fetch left a file behind"


def test_a_200_that_is_not_a_csv_is_not_cached_either(tmp_path, monkeypatch):
    """The status code is not the only way this goes wrong — a proxy or
    captive portal can return the same HTML with a cheerful 200."""
    from bym.data.football_data import SeasonDownloadError, fetch_season

    _serve(monkeypatch, 200, HTML_300)
    with pytest.raises(SeasonDownloadError):
        fetch_season(2026, "E1", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_good_body_still_caches_and_parses(tmp_path, monkeypatch):
    from bym.data.football_data import fetch_season, parse_season_csv

    _serve(monkeypatch, 200, CSV.encode())
    path = fetch_season(2024, "E0", tmp_path)
    assert path.read_text() == CSV
    assert len(parse_season_csv(path, 2024, "E0")) == 2


def test_a_bom_prefixed_body_is_recognised(tmp_path, monkeypatch):
    """Football-Data serves these with a UTF-8 BOM, so the header check
    has to see past it or every real download looks like a failure."""
    from bym.data.football_data import fetch_season

    _serve(monkeypatch, 200, "﻿".encode() + CSV.encode())
    assert fetch_season(2024, "E0", tmp_path).exists()


def test_an_already_poisoned_cache_explains_itself(tmp_path):
    """Installs that ran the old code still have the bad file on disk."""
    from bym.data.football_data import SeasonDownloadError, parse_season_csv

    p = tmp_path / "2026_E1.csv"
    p.write_bytes(HTML_300)
    with pytest.raises(SeasonDownloadError, match="not a Football-Data CSV"):
        parse_season_csv(p, 2026, "E1")


def test_an_unpublished_division_is_skipped_loudly_not_silently(
    tmp_path, monkeypatch
):
    """Half a season is usable; a corpus quietly narrower than the one
    asked for is not (see DataConfig.divisions)."""
    from bym.data import football_data
    from bym.data.football_data import load_matches

    (tmp_path / "2026_E0.csv").write_text(CSV)

    def fake_get(url, **kwargs):
        return _FakeResponse(300, HTML_300)

    monkeypatch.setattr(football_data.requests, "get", fake_get)

    with pytest.warns(UserWarning, match="not published yet"):
        matches = load_matches(tmp_path, [2026], ("E0", "E1"))
    assert len(matches) == 2
    assert not (tmp_path / "2026_E1.csv").exists()


def test_nothing_available_at_all_is_an_error_not_an_empty_frame(
    tmp_path, monkeypatch
):
    from bym.data.football_data import SeasonUnavailable, load_matches

    _serve(monkeypatch, 300, HTML_300)
    with pytest.warns(UserWarning):
        with pytest.raises(SeasonUnavailable, match="no season data available"):
            load_matches(tmp_path, [2026], ("E0", "E1"))
