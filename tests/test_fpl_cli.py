import json

from bym import cli, db
from test_fpl_phase2 import payload as phase2_payload
from test_fpl_phase3 import payload as phase3_payload

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


def test_fpl_plan_builds_a_shortlist_only_horizon(tmp_path, capsys):
    source = tmp_path / "phase2.json"
    source.write_text(json.dumps(phase2_payload()))

    assert cli.main(["fpl-plan", "--input", str(source)]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["scope"] == "SHORTLIST_ONLY_HORIZON"
    assert output["best_transfer_plan"]["steps"][0]["transfer_in"] == "Target FWD"


def test_fpl_relative_builds_a_predeadline_rival_simulation(tmp_path, capsys):
    source = tmp_path / "phase3.json"
    source.write_text(json.dumps(phase3_payload()))

    assert cli.main(["fpl-relative", "--input", str(source)]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["scope"] == "PRE_DEADLINE_NAMED_OPTIONS_ONLY"
    assert output["recommended_option"] == "Named upgrade"
