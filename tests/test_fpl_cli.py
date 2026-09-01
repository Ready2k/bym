import json

from bym import cli, db

from test_fpl_game_state import sample_payload


def test_fpl_import_persists_a_user_snapshot_without_retaining_raw_payload(
    tmp_path, capsys
):
    source = tmp_path / "fpl-state.json"
    source.write_text(json.dumps(sample_payload()))
    database = tmp_path / "fpl.db"

    assert cli.main([
        "fpl-import", "--input", str(source), "--season", "2026", "--db", str(database)
    ]) == 0

    output = capsys.readouterr().out
    assert "GW3" in output
    assert "17 priced candidates" in output
    with db.connect(database) as conn:
        stored = conn.execute("SELECT raw_payload_location FROM fpl_game_state_snapshots").fetchone()
        assert stored["raw_payload_location"] is None


def test_fpl_import_refuses_invalid_json(tmp_path, capsys):
    source = tmp_path / "broken.json"
    source.write_text("not JSON")

    assert cli.main(["fpl-import", "--input", str(source), "--db", str(tmp_path / "fpl.db")]) == 2

    assert "not valid JSON" in capsys.readouterr().err
