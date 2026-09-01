from bym.backtest import fpl_decision_evaluation as evaluation


def test_canonical_stability_run_separates_nominal_and_contingency_choices():
    result = evaluation.run(ks=(1, 5, 10, 25))

    assert 41 in result.nominal_baseline.squad_ids
    assert 41 not in result.contingency_aware.squad_ids
    assert {22, 23} <= result.contingency_aware.squad_ids
    assert result.decisions_changed_vs_nominal > 0


def test_stability_rows_are_ordered_and_report_when_the_widest_choice_is_reached():
    result = evaluation.run(ks=(1, 5, 10, 25))

    assert [row.top_k for row in result.stability] == [1, 5, 10, 25]
    assert result.first_stable_top_k in {row.top_k for row in result.stability}
    assert result.stability[-1].squad_changed_vs_widest is False
    assert result.stability[-1].decision_changes_vs_widest == 0


def test_rendered_report_disclaims_a_historical_transfer_claim():
    report = evaluation.render_markdown(evaluation.run(ks=(1, 5, 10, 25)))

    assert "not a historical transfer backtest" in report
    assert "does **not** justify" in report
