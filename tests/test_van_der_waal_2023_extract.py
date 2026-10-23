"""Pins the compiled, unscored activity-coefficient ranges for Van et al. (2023)."""

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
    assert table_415["values"]["reason"] == (
        "compiled literature ranges; no per-value T or melt composition and endpoints "
        "not mapped to sources (quoted, second-hand). Primaries listed below"
    )
    assert table_415["values"]["reference_state_as_printed"] == "not stated"
    expected_rows = [
        ("Li", "1.8 ± 0.2 – 15.4 ± 3.1", "5"),
        ("V", "4.8 ± 0.32", "4"),
        ("Cu", "3.5 – 11.8 ± 1.7", "2, 3, 4, 5"),
        ("Zn", "1.9·10^-2 ± 1·10^-2 – 0.51 ± 0.13", "1, 4, 5"),
        ("Ga", "1.82 ± 0.53", "4"),
        ("Ge", "3.7·10^-3 ± 1·10^-3 – 0.11 ± 0.01", "4, 5"),
        ("As", "—", "—"),
        ("Rb", "1.3·10^-4 ± 1.5·10^-5", "5"),
        ("Ag", "0.11 ± 0.023 – 0.67", "4, 5"),
        ("Cd", "0.15 – 1561 ± 206", "4, 5"),
        ("In", "0.02 – 0.039 ± 8.5·10^-3", "3, 4"),
        ("Sn", "1.6·10^-4 ± 3.6·10^-4", "4"),
        ("Sb", "1.90 ± 0.43", "4"),
        ("Cs", "3.4·10^-5", "*"),
        ("Tl", "2535 ± 146", "4"),
        ("Pb", "0.17 – 0.50 ± 0.11", "3, 4, 5"),
    ]
    assert [
        (row["element"], row["gamma_as_printed"], row["sources"])
        for row in table_415["values"]["rows"]
    ] == expected_rows

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
