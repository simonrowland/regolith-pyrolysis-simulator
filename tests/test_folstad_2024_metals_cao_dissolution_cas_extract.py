"""Source-grounded extraction and migration checks for Folstad et al. (2024)."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Quantity
from simulator.battery.migrate import Migrator, wt_pct_to_mole_fraction
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/folstad-2024-metals-cao-dissolution-cas.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_extract_validates_and_fidelity_mutation_is_detected() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []

    mutated = deepcopy(document)
    first = next(
        row
        for row in mutated["species"]["SiO2"]["observations"]
        if row["observation_id"] == "folstad_2024_table3_liquidus_slag1"
    )
    first["values"]["points"][0]["T_C"] = 1491
    assert check_all_fidelity_samples_match(mutated, label=EXTRACT.stem)


def test_liquidus_rows_migrate_with_their_composition_and_temperature() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    records = list(migrator.result.observations.values())
    assert len(records) == 3
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues

    expected = {
        1490: {"SiO2": 56, "CaO": 15, "Al2O3": 29},
        1550: {"SiO2": 38, "CaO": 20, "Al2O3": 42},
        1420: {"SiO2": 56, "CaO": 21, "Al2O3": 23},
    }
    for record in records:
        assert record.identity.quantity.is_value
        assert record.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE
        assert record.point_conditions is not None
        temperature = record.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.locator is not None
        assert temperature.locator.pdf_page_index == 4
        printed_C = next(
            temperature_C
            for temperature_C in expected
            if temperature.state.value == Decimal(temperature_C) + Decimal("273.15")
        )
        assert temperature.state.value == Decimal(printed_C) + Decimal("273.15")
        assert record.identity.composition is not None
        assert record.identity.composition.is_value
        condition = record.point_conditions["composition"]
        assert condition.state.is_value
        assert condition.locator is not None
        assert condition.locator.pdf_page_index == 4
        expected_composition = wt_pct_to_mole_fraction(
            {name: Decimal(amount) for name, amount in expected[printed_C].items()}
        )
        assert record.identity.composition.value == expected_composition
        assert record.evidence.class_.is_value
        assert record.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED


def test_table_context_and_deferred_figures_preserve_printed_cells() -> None:
    context = _document()["species"]["SiO2"]["context"]
    table1 = next(
        item
        for item in context
        if item.get("observation_id") == "folstad_2024_table1_previous_diffusion_summary"
    )["values"]
    assert table1["printed_rows"] == len(table1["rows"]) == 57
    assert table1["rows"][0]["D_as_printed"] == "1 × 3 × 10−6"
    assert any(row["D_as_printed"] == "6.9· × 0−7" for row in table1["rows"])
    expected_author_groups = [
        (1, 1, "Towers et al. [3] (1953)"),
        (2, 2, "Towers and Chipman [4] (1957)"),
        (3, 10, "Niwa [5] (1957)"),
        (11, 26, "Saitô and Maruya [6] (1958)"),
        (27, 29, "Goto et al. [7] (1977)"),
        (30, 38, "Liang et al. [8] (1996)"),
        (39, 39, "Amini et al. [9] (2006)"),
        (40, 43, "Zhang and Chou [10] (2011)"),
        (44, 57, "Ren et al. [11] (2013)"),
    ]
    for first_row, last_row, author in expected_author_groups:
        assert all(
            row["authors_as_printed"] == author
            for row in table1["rows"][first_row - 1:last_row]
        )

    expected_corrected_rows = {
        3: ("Niwa [5] (1957)", {"SiO2": "40", "CaO": "40", "Al2O3": "20"}, "1350", "3.3 × 10−7"),
        6: ("Niwa [5] (1957)", {"SiO2": "39", "CaO": "43", "Al2O3": "18"}, "1350", "5.2 × 10−7"),
        11: ("Saitô and Maruya [6] (1958)", {"SiO2": "41", "CaO": "40", "Al2O3": "19"}, "1350", "3.9 × 10−7"),
        20: ("Saitô and Maruya [6] (1958)", {"SiO2": "34", "CaO": "46", "Al2O3": "20"}, "1440", "3.8 × 10−7"),
        21: ("Saitô and Maruya [6] (1958)", {"SiO2": "34", "CaO": "46", "Al2O3": "20"}, "1485", "7.1 × 10−7"),
        30: ("Liang et al. [8] (1996)", {"SiO2": "60", "CaO": "30", "Al2O3": "10"}, "1500", "7.41 × 10−7"),
        31: ("Liang et al. [8] (1996)", {"SiO2": "55", "CaO": "25", "Al2O3": "20"}, "1500", "5.18 × 10−7"),
        32: ("Liang et al. [8] (1996)", {"SiO2": "65", "CaO": "20", "Al2O3": "15"}, "1500", "3.3 × 10−7"),
        33: ("Liang et al. [8] (1996)", {"SiO2": "65", "CaO": "25", "Al2O3": "10"}, "1500", "4.78 × 10−7"),
    }
    for row_number, expected in expected_corrected_rows.items():
        row = table1["rows"][row_number - 1]
        assert (
            row["authors_as_printed"],
            row["composition_wt_percent"],
            row["temperature_C_as_printed"],
            row["D_as_printed"],
        ) == expected

    table4 = next(
        item
        for item in context
        if item.get("observation_id") == "folstad_2024_table4_fitted_rate_parameters"
    )["values"]
    assert table4["rows"][2]["D_as_printed"] == "4.2 × 10−6"
    assert table4["prose_D_slag3_as_printed"] == "5.7 × 10−6 cm2/s"

    layer = next(
        item
        for item in context
        if item.get("observation_id") == "folstad_2024_text_measured_interfacial_layer_cao"
    )
    assert layer["locator"]["page"] == 8
    assert layer["locator"]["pdf_page_index"] == 7
    assert layer["values"]["locator"]["pdf_page_index"] == 7
    onset = next(
        item
        for item in context
        if item.get("observation_id") == "folstad_2024_text_measured_dissolution_onset"
    )
    assert onset["locator"]["page"] == 13
    assert onset["locator"]["pdf_page_index"] == 12
    assert onset["values"]["value_as_printed"] == "approximately 1300–1400 °C"
    disintegration = next(
        item
        for item in context
        if item.get("observation_id") == "folstad_2024_text_measured_cao_disintegration_temperature"
    )
    assert disintegration["values"]["temperature_as_printed"] == "1400–1500 °C"
    assert disintegration["locator"]["pdf_page_index"] == 6

    figures = [item for item in context if item.get("type") == "figure_record"]
    assert len(figures) == 11
    for figure in figures:
        assert figure["values"]["digitisation"] == "deferred to a figure pass"
        assert "points" not in figure["values"]
        assert figure["values"].get("species")
        assert figure["values"].get("phase")
        assert figure["values"].get("temperature_as_printed")
        assert figure["values"].get("axes_as_printed")
        assert figure["values"].get("caption")
        assert figure["values"].get("series") is not None
        assert all("marker_count" in series for series in figure["values"]["series"])
