"""Render-grounded extraction pins and isolated migration for Kunisada & Iwai (1986)."""

from __future__ import annotations

import csv
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import MethodToken, Phase, PhaseRole, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "kunisada-1986-na-silicate-dephosphorization"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
CELL_AUDIT = (
    ROOT
    / "data/literature/extracts/audits"
    / f"{SOURCE_ID}.cell-audit.csv"
)
FLUX_ADDITION_SENTENCE = (
    "30 g of the flux was put and melted in the holder made of iron, and added on to the alloy melt by means of "
    "melting the bottom of the holder."
)


RENDER_PINS = {
    "kunisada_1986_t1ii_e_equilibrium_P_in_liquid_iron": (
        "P",
        "0.020",
        2,
        "1(II)",
        "E",
        "[%P]f",
        PhaseRole.METAL,
        Decimal("0.020"),
    ),
    "kunisada_1986_t1ii_e_equilibrium_Na2O_in_slag": (
        "Na2O",
        "40.9",
        2,
        "1(II)",
        "E",
        "Na2O",
        PhaseRole.SLAG,
        Decimal("40.9"),
    ),
    "kunisada_1986_table2_flux_i_experimental_total_FeO_content": (
        "FeO",
        "28.8",
        6,
        "2",
        "I",
        "(%T.FeO)exp.",
        PhaseRole.SLAG,
        Decimal("28.8"),
    ),
}

IDENTITY_FORMULA_OVERRIDES = {
    "kunisada_1986_table2_flux_i_experimental_total_FeO_content": "FeOT",
}

SC456_REDUCED_ROWS = {
    "kunisada_1986_carbon_calculated_amount_flux_E",
    "kunisada_1986_carbon_calculated_increment_flux_E",
    "kunisada_1986_initial_dephosphorization_rate_flux_C2",
    "kunisada_1986_initial_dephosphorization_rate_fluxes_E_to_I",
    "kunisada_1986_na2o_loss_flux_A",
    "kunisada_1986_na2o_loss_flux_B",
    "kunisada_1986_na2o_loss_flux_C",
    "kunisada_1986_na2o_loss_flux_D",
    *(f"kunisada_1986_table2_flux_{letter}_calculated_FeO_content" for letter in "efghi"),
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _rows(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        row["observation_id"]: (formula, row)
        for formula, group in document["species"].items()
        for row in group.get("observations", [])
    }


def _assert_sc456_reduced_classes(document: dict) -> None:
    rows = _rows(document)
    assert SC456_REDUCED_ROWS <= rows.keys()
    for observation_id in SC456_REDUCED_ROWS:
        values = rows[observation_id][1]["values"]
        assert values["method_class"] == "measured_reduced"
        assert values["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"
        assert values["derivation"]["raw_inputs"]["locator"]


def _assert_quench_equilibration_method(document: dict) -> None:
    assert document["experiments"]
    assert all(
        experiment["method"] == "quench_equilibration"
        for experiment in document["experiments"]
    )
    phase_rows = [
        row
        for _, row in _rows(document).values()
        if row.get("values", {}).get("quantity") == "equilibrium_phase_composition"
    ]
    assert len(phase_rows) == 202
    assert all(row["regime"] == "quench_equilibration" for row in phase_rows)
    methods = next(
        context["values"]
        for group in document["species"].values()
        for context in group.get("context", [])
        if context["observation_id"] == "kunisada_1986_experimental_methods"
    )
    assert methods["method_detail_as_printed"].startswith(
        "“The experiment was carried out in a Tammann furnace"
    )
    assert FLUX_ADDITION_SENTENCE in methods["method_detail_as_printed"]
    assert methods["method_detail_as_printed"].index(FLUX_ADDITION_SENTENCE) > methods[
        "method_detail_as_printed"
    ].index("After the temperature of the melt was measured")
    assert methods["method_detail_as_printed"].index(FLUX_ADDITION_SENTENCE) < methods[
        "method_detail_as_printed"
    ].index("After then, the melt was picked up")


def test_tables_match_render_samples_and_complete_cell_audit() -> None:
    document = _document()
    _assert_sc456_reduced_classes(document)
    rows = _rows(document)

    with CELL_AUDIT.open(encoding="utf-8", newline="") as stream:
        cells = list(csv.DictReader(stream))
    assert len(cells) == 298
    assert all(
        cell["confirmation"].startswith("PDF page render verified") for cell in cells
    )
    assert {
        table: len(
            {
                cell["printed_data_row"]
                for cell in cells
                if cell["table"] == table
            }
        )
        for table in ("1(I)", "1(II)", "2")
    } == {"1(I)": 27, "1(II)": 5, "2": 5}

    fidelity = {sample["observation_id"]: sample for sample in document["fidelity_samples"]}
    for observation_id, (
        formula,
        printed,
        page,
        table,
        row_label,
        column,
        _role,
        _migrated_value,
    ) in RENDER_PINS.items():
        source_formula, source_row = rows[observation_id]
        assert source_formula == formula
        assert source_row["values"]["value"] == printed
        assert source_row["units"] == "%"
        assert source_row["values"]["value_as_published"]["unit"] == "%"
        assert source_row["locator"]["page"] == page
        assert source_row["locator"]["table"] == table
        assert source_row["locator"]["row_label"] == row_label
        assert source_row["locator"]["column"] == column
        assert fidelity[observation_id]["value"] == printed
        assert fidelity[observation_id]["locator"]["page"] == page
        assert fidelity[observation_id]["locator"]["table"] == table


def test_isolated_migration_binds_rows_conditions_and_exact_schema_gaps() -> None:
    document = _document()
    source_rows = _rows(document)
    migrator = migrate_one_extract(ROOT, EXTRACT, index=None, aliases={})
    migrator.finalize()
    migrated = migrator.result.observations

    expected_ids = {f"{SOURCE_ID}::{observation_id}" for observation_id in source_rows}
    assert set(migrated) == expected_ids
    assert len(migrated) == 278
    assert len(migrator.result.validation.hard_issues) == 0
    assert len(migrator.result.validation.issues) == 440
    assert {issue.reason.value for issue in migrator.result.validation.issues} == {
        "identity_incomplete"
    }
    assert not migrator.result.registry_issues
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values()) == 72

    expected_quantity_unknown = {
        f"{SOURCE_ID}::{observation_id}"
        for observation_id, (_formula, row) in source_rows.items()
        if row.get("values", {}).get("admission_status") == "typed_refusal"
    }
    actual_quantity_unknown = {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.quantity.is_unknown
    }
    assert actual_quantity_unknown == expected_quantity_unknown
    assert len(actual_quantity_unknown) == 72
    assert all(
        row.identity.species is not None
        and row.identity.species.phase.is_value
        and row.identity.species.phase.value is Phase.L
        for row in migrated.values()
    )

    for observation_id, (_formula, source_row) in source_rows.items():
        source_conditions = source_row["point_conditions"]
        migrated_row = migrated[f"{SOURCE_ID}::{observation_id}"]
        assert source_conditions["temperature_C"]["state"]
        assert source_conditions["temperature_C"]["locator"]
        assert migrated_row.point_conditions is not None
        temperature = migrated_row.point_conditions["temperature_K"]
        assert temperature.locator is not None
        source_temperature = source_conditions["temperature_C"]["state"]
        assert temperature.state.tag.value == source_temperature["tag"]
        if source_temperature["tag"] == "value":
            assert temperature.state.value.is_finite()
        for condition_name in ("fO2_Pa", "total_pressure_Pa"):
            condition = source_conditions[condition_name]
            assert condition["state"]["tag"] == "unknown"
            assert condition["locator"]
            migrated_condition = migrated_row.point_conditions.get(condition_name)
            if migrated_condition is not None:
                assert migrated_condition.state.is_unknown
                assert migrated_condition.locator is not None
            identity_axis = getattr(migrated_row.identity, condition_name)
            if identity_axis is not None:
                assert not identity_axis.is_value

    phase_rows = [
        row
        for row in migrated.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    ]
    assert len(phase_rows) == 202
    assert all(row.identity.composition.is_value for row in phase_rows)
    assert all(row.point_conditions["composition"].state.is_value for row in phase_rows)
    assert all(
        migrator.result.experiments[row.experiment_id].method.is_value
        and migrator.result.experiments[row.experiment_id].method.value
        is MethodToken.QUENCH_EQUILIBRATION
        for row in phase_rows
    )

    contexts = [
        context
        for context_rows in migrator.result.context_by_work.values()
        for context in context_rows
    ]
    assert len(contexts) == 15
    assert {
        context["context_id"]
        for context in contexts
    } == {
        f"{SOURCE_ID}::context::{context['observation_id']}"
        for group in document["species"].values()
        for context in group.get("context", [])
    }
    context_rows = {
        context["observation_id"]: context
        for group in document["species"].values()
        for context in group.get("context", [])
    }
    assert len(context_rows) == 15
    methods = context_rows["kunisada_1986_experimental_methods"]["values"]
    assert methods["crucible_as_printed"] == (
        "recrystallized magnesia (MgO), 40 mm I.D., 60 mm H"
    )
    assert methods["method_detail_as_printed"].startswith(
        "“The experiment was carried out in a Tammann furnace"
    )
    assert FLUX_ADDITION_SENTENCE in methods["method_detail_as_printed"]
    assert context_rows["kunisada_1986_experimental_methods"]["locator"]["section"] == (
        "II. Experimental Method"
    )
    assert methods["furnace_and_tube_as_printed"] == (
        "Tammann furnace with an alumina reaction tube"
    )
    assert methods["atmosphere_as_printed"] == (
        "argon dehydrated by P2O5 and deoxidized through Mg chips heated at 500 °C"
    )
    assert "Pt-6%Rh/Pt-30%Rh" in methods["temperature_measurement_as_printed"]
    assert methods["phosphorus_analysis_as_printed"] == "Mo-blue colorimetry"
    assert methods["oxygen_analysis_as_printed"] == (
        "inert gas fusion-thermal conductivity method"
    )
    assert methods["carbon_analysis_as_printed"] == "infrared absorption spectrometry"
    assert methods["slag_analysis_as_printed"] == (
        "fluorescent X-ray and JIS-standard analyses"
    )
    assert context_rows["kunisada_1986_equation_6"]["values"][
        "equation_as_printed"
    ] == "2P + 5(FeO) = (P2O5) + 5Fe"
    for equation_id in (
        "kunisada_1986_equation_6",
        "kunisada_1986_equation_7",
        "kunisada_1986_equation_8",
    ):
        assert context_rows[equation_id]["locator"]["page"] == 7
        assert context_rows[equation_id]["locator"]["published_page"] == 127
        assert context_rows[equation_id]["locator"]["pdf_page_index"] == 6
    assert context_rows["kunisada_1986_equation_7"]["values"][
        "equation_as_printed"
    ] == "k_P = (%P2O5)/([%P]^2 · (%FeO)^5)"
    assert context_rows["kunisada_1986_equation_8"]["values"][
        "equation_as_printed"
    ] == "log k_P = 8.67 log(%Na2O) - 14.55 (1600 °C)"
    assert context_rows["kunisada_1986_equation_8"]["values"][
        "temperature_dependence_as_printed"
    ] == (
        "The temperature dependence of log k_P was not clear at this time "
        "as shown in the figure."
    )
    assert context_rows["kunisada_1986_equation_8"]["values"][
        "total_iron_basis_as_printed"
    ] == (
        "where, (%Fe_tO) calculated from the total iron content in the slag "
        "was used instead of (%FeO)."
    )


    figure_contexts = [
        context
        for context in context_rows.values()
        if context["type"] == "figure_caption"
    ]
    assert len(figure_contexts) == 11
    assert {context["locator"]["figure"] for context in figure_contexts} == {
        str(number) for number in range(1, 12)
    }
    assert all(
        context["values"].get("caption_as_printed") for context in figure_contexts
    )
    assert all(
        context["values"]["digitization"] == "not digitized; caption inventory only"
        for context in figure_contexts
    )

    for observation_id, (
        formula,
        _printed,
        page,
        table,
        row_label,
        _column,
        phase_role,
        migrated_value,
    ) in RENDER_PINS.items():
        row = migrated[f"{SOURCE_ID}::{observation_id}"]
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == migrated_value
        assert row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert row.identity.species.formula == IDENTITY_FORMULA_OVERRIDES.get(
            observation_id, formula
        )
        assert row.identity.species.phase.value is Phase.L
        assert row.identity.phase_role.value is phase_role
        assert row.identity.temperature_K.is_value
        assert row.identity.composition.is_value
        assert row.locator.page == page
        assert row.locator.table == table
        assert row.locator.note is not None or row.locator.table == "2"
        for axis in (row.identity.total_pressure_Pa, row.identity.fO2_Pa):
            assert axis.is_unknown
        assert row.point_conditions["total_pressure_Pa"].state.is_unknown
        assert row.point_conditions["fO2_Pa"].state.is_unknown
        condition_locator = row.point_conditions["composition"].locator
        assert condition_locator is not None
        assert condition_locator.page == 2
        assert condition_locator.table == "1(II)"
        assert condition_locator.note is not None
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["temperature_K"].state.value == Decimal("1873.15")

    t1i_rows = {
        observation_id: source_row
        for observation_id, (_formula, source_row) in source_rows.items()
        if source_row["locator"].get("table") == "1(I)"
    }
    assert len(t1i_rows) == 216
    assert sum(
        row["values"].get("admission_status") == "admitted"
        for row in t1i_rows.values()
    ) == 162
    experiments = {
        experiment["experiment_id"]: experiment
        for experiment in document["experiments"]
    }
    for observation_id, source_row in t1i_rows.items():
        source_experiment = experiments[source_row["experiment"]]
        migrated_row = migrated[f"{SOURCE_ID}::{observation_id}"]
        temperature = migrated_row.point_conditions["temperature_K"].state
        assert temperature.is_value
        assert temperature.value == Decimal(source_row["values"]["T_C"]) + Decimal(
            "273.15"
        )
        if source_row["values"].get("admission_status") == "admitted":
            sample_composition = source_experiment["sample"]["printed_composition"]
            expected_composition = sample_composition["state"]["value"]
            point_composition = migrated_row.point_conditions["composition"]
            assert point_composition.state.is_value
            assert point_composition.locator is not None
            assert point_composition.locator.table == "1(I)"
            assert point_composition.locator.note == sample_composition[
                "locator"
            ]["note"]
            assert source_row["locator"]["row_label"] in (
                point_composition.locator.note
            )
            assert migrated_row.identity.composition.is_value
            actual_composition = dict(
                migrated_row.identity.composition.value.components
            )
            assert set(actual_composition) == set(expected_composition)
            assert all(
                actual_composition[component] == Decimal(amount)
                for component, amount in expected_composition.items()
            )

    table1_total_iron_rows = [
        (species, row)
        for species, row in _rows(document).values()
        if row["locator"].get("table") in {"1(I)", "1(II)"}
        and row["locator"].get("column") == "T. Fe"
    ]
    assert len(table1_total_iron_rows) == 32
    for species, row in table1_total_iron_rows:
        assert species == "Fe"
        assert row["values"].get("formula", "Fe") == "Fe"
        migrated_row = migrated[f"{SOURCE_ID}::{row['observation_id']}"]
        assert migrated_row.identity.species.formula == "Fe"
        assert "T.Fe" in dict(migrated_row.identity.composition.value.components)

    table2_total_iron_rows = [
        row
        for _, row in _rows(document).values()
        if row["locator"].get("table") == "2"
        and row["locator"].get("column") == "(%T.FeO)exp."
    ]
    assert len(table2_total_iron_rows) == 5
    for row in table2_total_iron_rows:
        assert row["values"]["formula"] == "FeOT"
        migrated_row = migrated[f"{SOURCE_ID}::{row['observation_id']}"]
        assert migrated_row.identity.species.formula == "FeOT"


def test_sc456_reduced_class_pin_rejects_quote_reversion() -> None:
    document = _document()
    target = next(iter(SC456_REDUCED_ROWS))
    _rows(document)[target][1]["values"]["method_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_sc456_reduced_classes(document)


def test_quench_equilibration_method_pin_rejects_static_reversion() -> None:
    document = _document()
    _assert_quench_equilibration_method(document)

    mutated = deepcopy(document)
    mutated["experiments"][0]["method"] = "static_equilibrium"
    with pytest.raises(AssertionError):
        _assert_quench_equilibration_method(mutated)

    mutated = deepcopy(document)
    first_slag_row = next(
        row
        for _, row in _rows(mutated).values()
        if row.get("values", {}).get("phase_label") == "slag"
        and row.get("values", {}).get("basis") == "wt_percent"
    )
    first_slag_row["regime"] = "static_equilibrium"
    with pytest.raises(AssertionError):
        _assert_quench_equilibration_method(mutated)
