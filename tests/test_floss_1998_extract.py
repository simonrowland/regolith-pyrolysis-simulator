from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "literature"
    / "extracts"
    / "floss-1998-hibonite-evaporation-residues.yaml"
)


def _table_row(context: dict, run_id: str) -> dict:
    return next(
        row
        for row in context["rows"]
        if row.get("run_id", row.get("sample")) == run_id
    )


def test_extract_locators_use_physical_pdf_pages() -> None:
    doc = yaml.safe_load(EXTRACT.read_text())
    expected = {
        191: 1,
        192: 2,
        195: 5,
        196: 6,
        199: 9,
        200: 10,
        201: 11,
    }

    def check(value: object) -> None:
        if isinstance(value, dict):
            if "published_page" in value and "pdf_page_index" in value:
                folio = value["published_page"]
                assert value["page"] == expected[folio]
                assert value["pdf_page_index"] == value["page"] - 1
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(doc)


def test_corrected_trace_element_cells_are_pinned() -> None:
    doc = yaml.safe_load(EXTRACT.read_text())
    context = next(
        item["values"]
        for item in doc["species"]["SiO2"]["context"]
        if item["observation_id"]
        == "floss_1998_tables3_4_glass_and_phase_compositions"
    )

    table6 = context["table6_SIMs_trace_element_ppm_context"]
    mh16 = _table_row(table6, "MH-16")
    assert mh16["values"][table6["elements"].index("Nd")] == "18496 ± 7363"

    table7 = context["table7_INAA_trace_element_context"]
    mh8 = _table_row(table7, "MH-8")
    co_column = table7["elements"].index("Co_ppm")
    assert mh8["values"][co_column] == "11"
    assert "Co_ppm" in mh8["high_error_cells"]


def test_table7_italicized_high_error_cells_are_pinned() -> None:
    doc = yaml.safe_load(EXTRACT.read_text())
    context = next(
        item["values"]
        for item in doc["species"]["SiO2"]["context"]
        if item["observation_id"]
        == "floss_1998_tables3_4_glass_and_phase_compositions"
    )
    table7 = context["table7_INAA_trace_element_context"]
    expected = {
        "MH-1": {"Co_ppm", "Ce_ppm"},
        "MH-2": {"Ce_ppm"},
        "MH-5": set(),
        "MH-9": {"Mn_ppm", "Co_ppm", "Ce_ppm"},
        "MH-3": {"Ce_ppm"},
        "MH-10": {"Mn_ppm", "Co_ppm", "Ce_ppm"},
        "MH-6": {"Mn_ppm"},
        "MH-8": {"Co_ppm", "Sc_ppm", "Ce_ppm"},
        "MH-7": {"Sc_ppm", "Ce_ppm"},
    }
    for run_id, cells in expected.items():
        assert set(_table_row(table7, run_id).get("high_error_cells", [])) == cells

    mh6 = _table_row(table7, "MH-6")
    assert mh6["values"][table7["elements"].index("Sc_ppm")] == "0.75"
    assert "Sc_ppm" not in mh6["high_error_cells"]


def test_residue_compositions_migrate_as_declared_partial_vectors() -> None:
    doc = yaml.safe_load(EXTRACT.read_text())
    table_context = next(
        item["values"]
        for item in doc["species"]["SiO2"]["context"]
        if item["observation_id"]
        == "floss_1998_tables3_4_glass_and_phase_compositions"
    )
    assert set(table_context["untyped_constituents"]) == {
        "REE2O3",
        "Y2O3",
        "ZrO2",
        "HfO2",
        "UO2",
        "ThO2",
    }
    table4 = table_context["table4_glass_compositions_by_sample"]
    table5 = table_context[
        "table5_additional_bulk_oxide_values_not_covered_by_closed_migrator_fields"
    ]

    def numeric_cells(columns: list[str], cells: list[object]) -> dict[str, Decimal]:
        values = {}
        for formula, raw in zip(columns, cells, strict=True):
            try:
                value = Decimal(str(raw))
            except InvalidOperation:
                continue
            if formula in {"Al2O3", "CaO", "TiO2"}:
                values[formula] = value
        return values

    def typed_cells(values: dict[str, object]) -> dict[str, Decimal]:
        return {formula: Decimal(str(amount)) for formula, amount in values.items()}

    expected: dict[str, tuple[dict[str, Decimal], set[str]]] = {}
    for source_observation in doc["species"]["SiO2"]["observations"]:
        values = source_observation["values"]
        for row in values.get("series", []):
            if "composition_mol" not in row:
                continue
            if source_observation["observation_id"].startswith("floss_1998_table4_"):
                printed = numeric_cells(table4["columns"], table4[row["run_id"]])
                assert typed_cells(row["composition_wt_pct"]) == printed
                assert set(row["untyped_constituents"]) == {
                    "REE2O3", "Y2O3", "ZrO2", "HfO2", "UO2", "ThO2"
                }
            else:
                printed = {
                    "Al2O3": Decimal(str(row["Al2O3_wt_pct"])),
                    "CaO": Decimal(str(row["CaO_wt_pct"])),
                }
                table5_ti = numeric_cells(table5["columns"], table5[row["run_id"]])
                if "TiO2" in table5_ti:
                    printed["TiO2"] = table5_ti["TiO2"]
                assert typed_cells(row["composition_wt_pct"]) == printed
                assert set(row["untyped_constituents"]) == {
                    "REE2O3", "Y2O3", "ZrO2", "HfO2", "ThO2"
                }
            expected[source_observation["observation_id"]] = (
                {
                    formula: Decimal(amount)
                    for formula, amount in row["composition_mol"].items()
                },
                set(row["untyped_constituents"]),
            )

    migrator = Migrator(EXTRACT.parents[3], index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    rows = [
        observation
        for observation in migrator.result.observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value.value == "residue_component_composition"
    ]

    assert len(rows) == 64
    assert expected
    for observation in rows:
        parent_id = next(
            parent_id
            for parent_id in expected
            if observation.observation_id.startswith(f"{doc['source_id']}::{parent_id}::")
        )
        composition = observation.point_conditions["composition"]
        assert composition.state.is_value
        assert composition.state.value.proxy_flag.startswith("partial_vector:")
        assert "untyped=" in composition.state.value.proxy_flag
        assert dict(composition.state.value.components) == expected[parent_id][0]
        flagged_untyped = set(
            composition.state.value.proxy_flag.split("untyped=", 1)[1].split(",")
        )
        assert flagged_untyped == expected[parent_id][1]
        assert observation.identity.temperature_K.is_value
        atmosphere = observation.point_conditions["atmosphere"]
        assert atmosphere.locator is not None
