"""Regression pins for Dong et al. (2009) deferred figure records."""

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/dong-2009-cr-v-slag-activities.yaml"


def test_dong_2009_figure_records_are_deferred_and_migrate_as_context() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    expected = {
        "dong_2009_fig3_equilibration_time": ("3", 5, "concentration_series"),
        "dong_2009_fig4_pt_cross_section": ("4", 5, "concentration_series"),
        "dong_2009_fig5a_cr_activity": ("5", 6, "activity"),
        "dong_2009_fig5b_cr_log_activity_coefficient": ("5", 6, "activity_coefficient"),
        "dong_2009_fig6a_v_activity": ("6", 6, "activity"),
        "dong_2009_fig6b_v_log_activity_coefficient": ("6", 6, "activity_coefficient"),
        "dong_2009_fig7a_cro_activity": ("7", 7, "activity"),
        "dong_2009_fig7b_vo15_activity": ("7", 7, "activity"),
        "dong_2009_fig8a_slag_cr": ("8", 8, "concentration_series"),
        "dong_2009_fig8b_slag_v": ("8", 8, "concentration_series"),
    }
    records = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["context"]
        if row["values"].get("method_class") in {"figure_only", "model_derived"}
    }
    assert set(records) == set(expected)
    conditions = document["species"]["Cr"]["context"][0]["values"]
    assert conditions["temperature_K"] == [1803, 1823, 1873, 1923]
    assert conditions["oxygen_partial_pressure_Pa"] == ["10^-3", "10^-4", "10^-5"]
    assert conditions["equilibration_duration_h"] == 20
    assert records["dong_2009_fig5b_cr_log_activity_coefficient"]["values"][
        "printed_coefficients"
    ] == [-4.42, 11.39, -7.35]
    assert records["dong_2009_fig8a_slag_cr"]["values"]["printed_coefficients"] == [
        1.31,
        12481,
        0.84,
        1.59,
        -6.77,
    ]
    for observation_id, (figure, page, quantity) in expected.items():
        record = records[observation_id]
        assert record["locator"]["figure"] == figure
        assert record["locator"]["page"] == page
        assert record["locator"]["pdf_page_index"] == page - 1
        assert record["values"]["typed_quantity"] == quantity
        assert record["values"]["digitisation"] == "deferred to a figure pass"
        assert "numeric_points" not in record["values"]

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert not [o for o in result.observations.values() if o.source_id == EXTRACT.stem]
    contexts = [
        row
        for rows in result.context_by_work.values()
        for row in rows
        if row["context_id"].startswith(f"{EXTRACT.stem}::context::")
    ]
    assert len(contexts) == 11
    assert not result.validation.hard_issues

