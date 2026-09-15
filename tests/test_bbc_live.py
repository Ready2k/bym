from bym.availability import bbc_live


def test_extracts_only_complete_explicit_team_sheets():
    page = """
        <article>
          <p>Liverpool: Alisson, Frimpong, Konate, Van Dijk, Kerkez,
          Mac Allister, Szoboszlai, Salah, Wirtz, Gakpo, Ekitike.
          Substitutes: Kelleher, Jones.</p>
          <p>Tottenham predicted XI: Vicario, Porro, Romero.</p>
        </article>
    """

    result = bbc_live.extract_confirmed_xis(
        page, {"Liverpool", "Tottenham"}, "https://example.test/live"
    )

    assert result == [
        bbc_live.ConfirmedXI(
            "Liverpool",
            (
                "Alisson", "Frimpong", "Konate", "Van Dijk", "Kerkez",
                "Mac Allister", "Szoboszlai", "Salah", "Wirtz", "Gakpo", "Ekitike",
            ),
            "https://example.test/live",
        )
    ]


def test_extracts_alias_and_rejects_incomplete_lineup():
    page = """
        Wolves XI: Sa, Doherty, Agbadou, Toti, Ait-Nouri, Gomes,
        Andre, Bellegarde, Sarabia, Cunha. Substitutes: Bentley.
    """

    assert bbc_live.extract_confirmed_xis(page, {"Wolves"}, "source") == []
