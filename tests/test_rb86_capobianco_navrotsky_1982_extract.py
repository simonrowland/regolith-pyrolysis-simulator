from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml
import pytest

from simulator.battery.migrate import Migrator
from tests.literature_extract_test_helpers import iter_locators


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "rb86-capobianco-navrotsky-1982"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"

# Independent transcriptions from the 600 dpi physical-page 2 render.
TABLE_1_RENDER = [
    ("0†", "0", "2.382", "quoted_attributed", "Robie et al. (1979)"),
    ("23.6", "23.6", "2.625", "measured_direct", None),
    ("49.2", "49.2", "2.896", "measured_direct", None),
    ("76.0", "76.0", "3.143", "measured_direct", None),
    ("100.0††", "100.0", "3.368", "quoted_attributed", "Riebling (1966)"),
]
TABLE_2_RENDER = [
    ("99.5", "8.417(3)*", "13.290(3)", "7.343(2)", "94.35(2)", "116.24(2)", "90.93(2)", "measured_direct"),
    ("74.0", "8.341(7)", "13.169(7)", "7.278(5)", "94.37(7)", "116.26(5)", "90.60(5)", "measured_direct"),
    ("47.4", "8.282(5)", "13.128(19)", "7.215(6)", "93.93(15)", "116.21(5)", "90.73(8)", "measured_direct"),
    ("23", "8.209(1)", "12.942(1)", "7.145(1)", "93.67(3)", "116.16(2)", "90.21(2)", "measured_direct"),
    ("0*", "8.149", "12.880", "7.106", "93.37", "116.30", "90.28", "quoted_attributed"),
]
TABLE_3_RENDER = [
    ("Glasses", "99.5†", "99.5 mol% NaAlGe3O8; silica comprises the remainder", "−8.497", "0.995", 4),
    ("Glasses", "84.1", "84.1 mol% NaAlGe3O8", "−6.293", "0.360", 5),
    ("Glasses", "66.2", "66.2 mol% NaAlGe3O8", "−3.888", "0.262", 4),
    ("Glasses", "48.2", "48.2 mol% NaAlGe3O8", "−1.538", "0.342", 4),
    ("Glasses", "30.4", "30.4 mol% NaAlGe3O8", "1.596", "0.382", 5),
    ("Glasses", "19.8", "19.8 mol% NaAlGe3O8", "3.249", "0.488", 4),
    ("Glasses", "10.0", "10.0 mol% NaAlGe3O8", "4.289", "0.286", 3),
    ("Glasses", "0.0", "0.0 mol% NaAlGe3O8", "5.418", "0.110", 4),
    ("Crystals", "99.5†", "99.5 mol% NaAlGe3O8; silica comprises the remainder", "4.229", "0.206", 5),
    ("Crystals", "74.0", "74.0 mol% NaAlGe3O8", "7.433", "0.158", 5),
    ("Crystals", "47.4", "47.4 mol% NaAlGe3O8", "11.006", "0.340", 4),
    ("Crystals", "23.6", "23.6 mol% NaAlGe3O8", "14.548", "0.156", 4),
]


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text())


def _table_context(document: dict, table_id: str) -> dict:
    return next(
        context
        for body in document["species"].values()
        for context in body.get("context", [])
        if context["observation_id"] == table_id
    )


def _table1_grid(document: dict) -> list[tuple]:
    context = _table_context(document, "rb86_capobianco_1982_table1_glass_density_context")
    assert context["locator"]["page"] == 2
    assert context["locator"]["pdf_page_index"] == 1
    assert context["values"]["estimated_error_as_printed"] == "±0.002"
    return [
        (
            row["sample_id"],
            row["mol_percent_NaAlGe3O8"],
            row["density_g_cm3_as_printed"],
            row["evidence_class"],
            row.get("attribution"),
        )
        for row in context["values"]["rows"]
    ]


def _table2_grid(document: dict) -> list[tuple]:
    context = _table_context(document, "rb86_capobianco_1982_table2_unit_cell_context")
    assert context["locator"]["page"] == 2
    assert context["locator"]["pdf_page_index"] == 1
    return [
        (
            row["sample_id"],
            row["a0_A_as_printed"],
            row["b0_A_as_printed"],
            row["c0_A_as_printed"],
            row["alpha_deg_as_printed"],
            row["beta_deg_as_printed"],
            row["gamma_deg_as_printed"],
            row["evidence_class"],
        )
        for row in context["values"]["rows"]
    ]


def _table3_grid(document: dict) -> list[tuple]:
    context = _table_context(document, "rb86_capobianco_1982_table3_enthalpy_solution_context")
    assert context["locator"]["page"] == 3
    assert context["locator"]["pdf_page_index"] == 2
    assert context["values"]["temperature_as_printed"] == "708°C"
    return [
        (
            row["block"],
            row["sample_id"],
            row["composition_as_printed"],
            row["delta_H_sol_kcal_mol_as_printed"],
            row["uncertainty_two_sd_kcal_mol_as_printed"],
            row["successful_runs"],
        )
        for row in context["values"]["rows"]
    ]


def _migrate() -> Migrator:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator


def test_render_transcribed_tables_bind_every_cell_to_sample_and_block() -> None:
    document = _document()
    assert _table1_grid(document) == TABLE_1_RENDER
    assert _table2_grid(document) == TABLE_2_RENDER
    assert _table3_grid(document) == TABLE_3_RENDER

    for locator in iter_locators(document):
        assert locator["page"] == locator["pdf_page_index"] + 1


def test_mutating_a_render_cell_or_extract_cell_breaks_table_binding() -> None:
    document = _document()
    changed_render = deepcopy(TABLE_3_RENDER)
    changed_render[1] = (*changed_render[1][:3], "−6.294", *changed_render[1][4:])
    with pytest.raises(AssertionError):
        assert _table3_grid(document) == changed_render

    changed_extract = deepcopy(document)
    table3 = _table_context(
        changed_extract, "rb86_capobianco_1982_table3_enthalpy_solution_context"
    )
    table3["values"]["rows"][1]["delta_H_sol_kcal_mol_as_printed"] = "−6.294"
    with pytest.raises(AssertionError):
        assert _table3_grid(changed_extract) == TABLE_3_RENDER


def test_isolated_migration_preserves_typed_species_temperature_and_conditions() -> None:
    document = _document()
    migrator = _migrate()
    rows = migrator.result.observations
    assert len(rows) == 3
    assert not migrator.result.validation.hard_issues

    expected_formulas = {
        "rb86_capobianco_1982_ge_albite_melting_point_quoted": "NaAlGe3O8",
        "rb86_capobianco_1982_si_albite_melting_point_quoted": "NaAlSi3O8",
        "rb86_capobianco_1982_si_albite_heat_fusion_quoted": "NaAlSi3O8",
    }
    expected_transition_values = {
        "rb86_capobianco_1982_ge_albite_melting_point_quoted": Decimal("1341.15"),
        "rb86_capobianco_1982_si_albite_melting_point_quoted": Decimal("1373.15"),
    }

    for observation_id, expected_formula in expected_formulas.items():
        migrated = rows[f"{SOURCE_ID}::{observation_id}"]
        assert migrated.identity.species.formula == expected_formula

    for observation_id, expected_value in expected_transition_values.items():
        migrated = rows[f"{SOURCE_ID}::{observation_id}"]
        assert migrated.value.point == expected_value
        pressure = migrated.point_conditions["total_pressure_Pa"]
        assert pressure.state.is_unknown
        assert pressure.locator is not None

    heat_fusion = rows[f"{SOURCE_ID}::rb86_capobianco_1982_si_albite_heat_fusion_quoted"]
    assert heat_fusion.identity.temperature_K.is_value
    assert heat_fusion.identity.temperature_K.value == Decimal("1373.15")
    pressure = heat_fusion.point_conditions["total_pressure_Pa"]
    assert pressure.state.is_unknown
    assert pressure.locator is not None
