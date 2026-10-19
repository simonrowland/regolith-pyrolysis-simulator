"""Pins the bounded, explicitly unscored table evidence for Van et al. (2023)."""

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/van-der-waal-2023-volatile-loss-isotope-fractionation.yaml"


def test_van_der_waal_2023_context_and_isolated_migration() -> None:
    payload = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = payload["species"]["Zn"]["context"]
    by_id = {row["observation_id"]: row for row in context}

    table_43 = by_id["van_der_waal_2023_table_4_3_trace_element_abundances"]
    assert table_43["locator"]["pdf_page_index"] == 133
    assert table_43["values"]["experiment_count"] == 76
    assert table_43["values"]["scope_status"] == "not_transcribed_in_thesis_pass"

    table_415 = by_id["van_der_waal_2023_table_4_15_activity_coefficients"]
    assert table_415["locator"]["page"] == 163
    assert table_415["locator"]["pdf_page_index"] == 162
    assert len(table_415["values"]["rows"]) == 16
    assert table_415["values"]["rows"][3]["gamma_as_printed"] == (
        "1.9·10^-2 ± 1·10^-2 – 0.51 ± 0.13"
    )
    assert table_415["values"]["rows"][3]["sources"] == "1, 4, 5"

    table_21 = by_id["van_der_waal_2023_table_2_1_experimental_redox_conditions"]
    assert table_21["values"]["row_count"] == 8
    assert table_21["values"]["rows"][0] == {
        "T_C": 1300, "log_fO2": -0.68, "air_ml_min": 200, "CO_ml_min": 0,
        "CO2_ml_min": 0, "delta_IW": 10.08, "delta_FMQ": 6.89,
    }
    assert table_21["values"]["rows"][-1]["delta_FMQ"] == -1.46

    figures = by_id["van_der_waal_2023_thesis_figure_inventory"]["values"]["rows"]
    assert len(figures) == 111
    assert figures[0]["figure"] == "1.1"
    assert figures[-1]["figure"] == "A5.17"

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.observations
