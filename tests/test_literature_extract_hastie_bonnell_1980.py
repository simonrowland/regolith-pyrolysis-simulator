"""Render and isolated-migration pins for Hastie and Bonnell (1980)."""

from __future__ import annotations

import copy
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import migrate
from tests.literature_extract_test_utils import isolated_root
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/"
    "hastie-bonnell-1980-nbsir-80-2169-molecular-chemistry.yaml"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _record(document: dict, observation_id: str) -> dict:
    return next(
        row
        for item in document["species"].values()
        for collection in ("observations", "context")
        for row in item.get(collection, [])
        if row["observation_id"] == observation_id
    )


def _record_with_species(document: dict, observation_id: str) -> tuple[str, dict]:
    return next(
        (species, row)
        for species, item in document["species"].items()
        for collection in ("observations", "context")
        for row in item.get(collection, [])
        if row["observation_id"] == observation_id
    )


def _render_pins(document: dict) -> tuple[str, ...]:
    pins = (
        (
            "PO",
            "hastie_bonnell_1980_table2_po_measured_pressures",
            "points",
            0,
            "p_as_published",
            "20.0",
            22,
            "2",
            "3.1/1/4",
        ),
        (
            "HPO2",
            "hastie_bonnell_1980_table2_hpo2_measured_pressures",
            "points",
            2,
            "p_as_published",
            "2.4",
            22,
            "2",
            "6.5/1/4.2",
        ),
        (
            "PO",
            "hastie_bonnell_1980_tableD2_po_calculated_partial_pressures",
            "points",
            4,
            "p_as_published",
            "2.63(-4)",
            167,
            "D.2",
            "2/1/2",
        ),
        (
            "O2",
            "hastie_bonnell_1980_tableD2_o2_calculated_partial_pressures",
            "points",
            4,
            "p_as_published",
            "6.74(-3)",
            167,
            "D.2",
            "2/1/2",
        ),
        (
            "PO",
            "hastie_bonnell_1980_tableE4_po_delta_fH2000",
            "delta_fH",
            None,
            "delta_fH",
            "-26.8",
            201,
            "E.4",
            None,
        ),
        (
            "PO",
            "hastie_bonnell_1980_tableE4_po_S2000",
            "entropy",
            None,
            "entropy",
            "69.0",
            201,
            "E.4",
            None,
        ),
        (
            "HPO2",
            "hastie_bonnell_1980_tableE2_hpo2_S_boron_analogy",
            "entropy",
            None,
            "entropy",
            "63.2",
            197,
            "E.2",
            None,
        ),
        (
            "NaPO2",
            "hastie_bonnell_1980_tableE2_napo2_S_boron_analogy",
            "entropy",
            None,
            "entropy",
            "71.6",
            197,
            "E.2",
            None,
        ),
        (
            "LiPO2",
            "hastie_bonnell_1980_tableE2_lipo2_S_boron_analogy",
            "entropy",
            None,
            "entropy",
            "68.0",
            197,
            "E.2",
            None,
        ),
    )
    observed: list[str] = []
    for species, observation_id, container, index, field, expected, page, table, column in pins:
        actual_species, row = _record_with_species(document, observation_id)
        assert actual_species == species
        values = row["values"]
        value = values[container] if index is None else values[container][index][field]
        locator = row["locator"]
        assert str(value) == expected, species
        assert row["locator"]["page"] == page
        assert row["locator"]["table"] == table
        assert locator["page"] == page
        assert locator["table"] == table
        if index is not None:
            point = values[container][index]
            temperature_locator = point["point_conditions"]["temperature_K"]["locator"]
            assert temperature_locator["page"] == page
            assert temperature_locator["table"] == table
            assert temperature_locator["column"] == column
            assert point["flame_as_printed"].replace("H2/O2/N2=", "") == column
        observed.append(f"{species}:{page}:{table}:{expected}")
    return tuple(observed)


def _table_row_counts(document: dict) -> dict[str, int]:
    records = (
        "hastie_bonnell_1980_main_table2_full_rendered_table",
        "hastie_bonnell_1980_table3_reaction_parameters_typed_refusal",
        "hastie_bonnell_1980_table4_typical_reaction_rates_typed_refusal",
        "hastie_bonnell_1980_appendixB_table2_full_rendered_table",
        "hastie_bonnell_1980_appendixD_tableD1_typed_refusal",
        "hastie_bonnell_1980_appendixD_tableD2_typed_refusal",
        "hastie_bonnell_1980_appendixE_tableE1_coefficients_typed_refusal",
        "hastie_bonnell_1980_appendixE_unnumbered_po_hf_comparison",
        "hastie_bonnell_1980_appendixE_tableE2_full_comparison_table",
        "hastie_bonnell_1980_appendixE_tableE3_full_rendered_table",
        "hastie_bonnell_1980_appendixE_tableE4_full_rendered_table",
    )
    counts = {}
    for observation_id in records:
        values = _record(document, observation_id)["values"]
        rows = values.get("rows_as_printed", values.get("comparison_rows_as_printed"))
        counts[observation_id] = len(rows)
    return counts


def test_rendered_values_table_rows_and_mutation_are_pinned() -> None:
    document = _document()
    _render_pins(document)
    assert check_all_fidelity_samples_match(document, label="hastie-bonnell-1980") == []
    assert _table_row_counts(document) == {
        "hastie_bonnell_1980_main_table2_full_rendered_table": 10,
        "hastie_bonnell_1980_table3_reaction_parameters_typed_refusal": 15,
        "hastie_bonnell_1980_table4_typical_reaction_rates_typed_refusal": 10,
        "hastie_bonnell_1980_appendixB_table2_full_rendered_table": 6,
        "hastie_bonnell_1980_appendixD_tableD1_typed_refusal": 3,
        "hastie_bonnell_1980_appendixD_tableD2_typed_refusal": 7,
        "hastie_bonnell_1980_appendixE_tableE1_coefficients_typed_refusal": 4,
        "hastie_bonnell_1980_appendixE_unnumbered_po_hf_comparison": 3,
        "hastie_bonnell_1980_appendixE_tableE2_full_comparison_table": 6,
        "hastie_bonnell_1980_appendixE_tableE3_full_rendered_table": 4,
        "hastie_bonnell_1980_appendixE_tableE4_full_rendered_table": 8,
    }

    table2 = _record(document, "hastie_bonnell_1980_main_table2_full_rendered_table")
    rows = {row[0]: row[1:] for row in table2["values"]["rows_as_printed"]}
    assert rows["P2"][:3] == ["<0.05 (0.005)", "1.2 (0.4)*", "<0.2"]
    assert rows["PO2"][:3] == ["<0.05 (2.1)", "<0.4 (0.006)", "<0.4"]
    assert rows["HPO"][:3] == ["<0.05 (0.02)", "<0.6 (0.002)", "<0.2"]

    formation_pins = (
        ("HPO2", "hastie_bonnell_1980_appendixD_hpo2_delta_fH_third_law", "-94", 162, None),
        ("NaPO2", "hastie_bonnell_1980_tableD2_napo2_delta_fH_third_law", "-123.5", 167, "D.2"),
        ("LiPO2", "hastie_bonnell_1980_tableD2_lipo2_delta_fH_third_law", "-115.2", 167, "D.2"),
        ("HPO2", "hastie_bonnell_1980_tableE3_hpo2_delta_fH", "-96.0", 199, "E.3"),
        ("NaPO2", "hastie_bonnell_1980_tableE3_napo2_delta_fH", "-123.5", 199, "E.3"),
        ("LiPO2", "hastie_bonnell_1980_tableE3_lipo2_delta_fH", "-115.0", 199, "E.3"),
        ("HPO2", "hastie_bonnell_1980_appendixE_tableE4_hpo2_delta_fH2000", "-97.9", 201, "E.4"),
    )
    for species, observation_id, expected, page, table in formation_pins:
        actual_species, row = _record_with_species(document, observation_id)
        assert actual_species == species
        assert row["values"]["quantity"] == "delta_fH"
        assert row["values"]["admission_status"] == "admitted"
        assert row["values"]["delta_fH"] == expected
        assert row["locator"]["page"] == page
        assert row["locator"].get("table") == table
        assert "hastie_bonnell_1980_appendixE_po_delta_fH298_present_study" in row[
            "values"
        ]["derivation"]["inputs"]

    changed = copy.deepcopy(document)
    _record(
        changed, "hastie_bonnell_1980_tableD2_o2_calculated_partial_pressures"
    )["values"]["points"][4]["p_as_published"] = "6.75(-3)"
    with pytest.raises(AssertionError):
        _render_pins(changed)


def test_isolated_migration_preserves_values_conditions_and_refusals(tmp_path: Path) -> None:
    document = _document()
    result = migrate(isolated_root(tmp_path, document), write=False)
    observations = list(result.observations.values())
    assert len(observations) == 86
    assert len(result.experiments) == 20
    assert len(result.validation.hard_issues) == 0
    unavailable = [
        row for row in observations if row.value.kind is ValueKind.UNAVAILABLE
    ]
    unavailable_by_reason = Counter()
    for row in unavailable:
        reason = row.value.unavailable_reason or ""
        if "unsupported printed unit for entropy" in reason:
            unavailable_by_reason["unsupported_entropy_unit"] += 1
        elif "do not establish log10 K" in reason:
            unavailable_by_reason["reaction_log_k_standard_state"] += 1
        else:
            unavailable_by_reason["quantity_unknown_schema_gap"] += 1
    assert dict(unavailable_by_reason) == {
        "quantity_unknown_schema_gap": 7,
        "reaction_log_k_standard_state": 1,
    }

    entropy_source_rows = [
        row
        for item in document["species"].values()
        for row in item.get("observations", [])
        if row.get("values", {}).get("quantity") == "S"
    ]
    assert len(entropy_source_rows) == 18
    assert all(row["units"] == "cal/mol/K" for row in entropy_source_rows)
    assert all(
        row["values"]["units_as_printed"] == "cal/(deg mol)"
        for row in entropy_source_rows
    )

    def migrated(observation_id: str, temperature: str):
        return next(
            row
            for row in observations
            if f"::{observation_id}::T={temperature}" in row.observation_id
        )

    def migrated_source(observation_id: str):
        return next(
            row for row in observations if f"::{observation_id}" in row.observation_id
        )

    table2_po = migrated("hastie_bonnell_1980_table2_po_measured_pressures", "2100")
    assert table2_po.value.point == Decimal("202.650")
    assert (table2_po.locator.page, table2_po.locator.table) == (22, "2")
    assert table2_po.identity.species.formula == "PO"
    assert table2_po.identity.temperature_K.value == Decimal("2100")
    assert table2_po.identity.composition.tag.value == "unknown"
    assert table2_po.identity.fO2_Pa.tag.value == "unknown"
    assert table2_po.identity.total_pressure_Pa.tag.value == "unknown"
    assert table2_po.point_conditions["gas_mix"].state.value == "H2/O2/N2=3.1/1/4; NTP burner-input mole ratio"

    appendix_d_o2 = migrated(
        "hastie_bonnell_1980_tableD2_o2_calculated_partial_pressures", "2450"
    )
    assert appendix_d_o2.value.point == Decimal("682.93050")
    assert (appendix_d_o2.locator.page, appendix_d_o2.locator.table) == (167, "D.2")
    assert appendix_d_o2.identity.species.formula == "O2"
    assert appendix_d_o2.identity.temperature_K.value == Decimal("2450")
    assert appendix_d_o2.identity.composition.tag.value == "unknown"
    assert appendix_d_o2.identity.fO2_Pa.tag.value == "unknown"
    assert appendix_d_o2.identity.total_pressure_Pa.tag.value == "unknown"
    assert appendix_d_o2.point_conditions["gas_mix"].state.value == "H2/O2/N2=2/1/2"

    e4_po = next(
        row
        for row in observations
        if "::hastie_bonnell_1980_tableE4_po_delta_fH2000" in row.observation_id
    )
    assert e4_po.value.point == Decimal("-112.1312")
    assert (e4_po.locator.page, e4_po.locator.table) == (201, "E.4")
    assert e4_po.identity.temperature_K.value == Decimal("2000")

    e4_po_entropy = next(
        row
        for row in observations
        if "::hastie_bonnell_1980_tableE4_po_S2000" in row.observation_id
    )
    assert e4_po_entropy.value.kind is ValueKind.POINT
    assert e4_po_entropy.value.point == Decimal("288.6960")
    assert (e4_po_entropy.locator.page, e4_po_entropy.locator.table) == (201, "E.4")
    assert e4_po_entropy.identity.temperature_K.value == Decimal("2000")

    formation_migration_pins = (
        ("hastie_bonnell_1980_appendixD_hpo2_delta_fH_third_law", "-393.2960", 162, None, "4"),
        ("hastie_bonnell_1980_tableD2_napo2_delta_fH_third_law", "-516.7240", 167, "D.2", "5.5"),
        ("hastie_bonnell_1980_tableD2_lipo2_delta_fH_third_law", "-481.9968", 167, "D.2", "5.8"),
        ("hastie_bonnell_1980_tableE3_hpo2_delta_fH", "-401.6640", 199, "E.3", None),
        ("hastie_bonnell_1980_tableE3_napo2_delta_fH", "-516.7240", 199, "E.3", None),
        ("hastie_bonnell_1980_tableE3_lipo2_delta_fH", "-481.1600", 199, "E.3", None),
        ("hastie_bonnell_1980_appendixE_tableE4_hpo2_delta_fH2000", "-409.6136", 201, "E.4", None),
    )
    for observation_id, expected, page, table, uncertainty in formation_migration_pins:
        row = migrated_source(observation_id)
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(expected)
        assert row.identity.quantity.value.value == "delta_fH"
        assert row.locator.page == page
        assert row.locator.table == table
        if uncertainty is None:
            assert row.uncertainty.kind.value == "none"
        else:
            assert row.uncertainty.kind.value == "printed"
            assert row.uncertainty.verbatim["value"] == uncertainty
    assert migrated_source(
        "hastie_bonnell_1980_appendixD_hpo2_delta_fH_third_law"
    ).identity.temperature_K.value == Decimal("298.15")
    assert migrated_source(
        "hastie_bonnell_1980_appendixE_tableE4_hpo2_delta_fH2000"
    ).identity.temperature_K.value == Decimal("2000")

    entropy_ids = {row["observation_id"] for row in entropy_source_rows}
    entropy_migrated = [
        row
        for row in observations
        if any(f"::{observation_id}" in row.observation_id for observation_id in entropy_ids)
    ]
    assert len(entropy_migrated) == 18
    assert all(row.value.kind is ValueKind.POINT for row in entropy_migrated)

    boron_analogy_pins = (
        ("hastie_bonnell_1980_tableE2_hpo2_S_boron_analogy", "264.4288"),
        ("hastie_bonnell_1980_tableE2_napo2_S_boron_analogy", "299.5744"),
        ("hastie_bonnell_1980_tableE2_lipo2_S_boron_analogy", "284.5120"),
    )
    for observation_id, expected in boron_analogy_pins:
        source_row = _record(document, observation_id)
        assert source_row["values"]["method_class"] == "model_derived"
        derivation = source_row["values"]["derivation"]
        assert derivation["raw_inputs"]["state"]["value"] == "not_printed"
        assert derivation["method"]["locator"]["page"] == 196
        assert "Jensen (1969)" in derivation["references"]
        assert "JANAF (1971)" in derivation["references"]
        row = migrated_source(observation_id)
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(expected)
        assert row.locator.page == 197
        assert row.locator.table == "E.2"

    unknown_quantity_ids = {
        row.observation_id.split("::", 2)[1]
        for row in observations
        if row.identity.quantity.tag.value == "unknown"
    }
    assert unknown_quantity_ids == {
        "hastie_bonnell_1980_appendixD_tableD1_typed_refusal",
        "hastie_bonnell_1980_appendixD_tableD2_typed_refusal",
        "hastie_bonnell_1980_appendixE_tableE1_coefficients_typed_refusal",
        "hastie_bonnell_1980_appendixE_tableE2_bond_energies_typed_refusal",
        "hastie_bonnell_1980_appendixE_tableE3_untyped_parameters_typed_refusal",
        "hastie_bonnell_1980_table3_reaction_parameters_typed_refusal",
        "hastie_bonnell_1980_table4_typical_reaction_rates_typed_refusal",
    }
    assert len(unknown_quantity_ids) == 7
    assert all(
        row.identity.species is None
        or row.identity.species.phase.tag.value != "unknown"
        for row in observations
    )
    assert all("temperature_K" in row.point_conditions for row in observations)
    assert all(
        row.identity.total_pressure_Pa.tag.value == "unknown"
        for row in (table2_po, appendix_d_o2)
    )
