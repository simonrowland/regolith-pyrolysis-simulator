from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import PerBasis
from simulator.battery.migrate import (
    Migrator,
    convert_pressure_to_pa,
    wt_pct_to_mole_fraction,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/tachibana-2002-enstatite-evaporation-kinetics.yaml"
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import validate_literature_extracts as vle  # noqa: E402


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_tachibana_2002_table3_values_match_600_dpi_render_pins() -> None:
    document = _document()
    assert vle.validate_extract_document(
        document,
        path=EXTRACT,
        expected_source_id="tachibana-2002-enstatite-evaporation-kinetics",
        check_fidelity_match=True,
    ) == []

    species = document["species"]["enstatite"]
    assert len(species["observations"]) == 6
    for observation in species["observations"]:
        assert len(observation["values"]["points"]) == 9
        assert all("T_C" in point for point in observation["values"]["points"])
        assert all(
            set(point["point_conditions"])
            == {"temperature_K", "duration_h", "hydrogen_partial_pressure_bar"}
            for point in observation["values"]["points"]
        )

    table3 = next(
        row
        for row in species["context"]
        if row["observation_id"] == "tachibana_2002_table3_hydrogen_conditions_and_raw_results"
    )
    assert len(table3["values"]["rows_as_printed"]) == 27
    assert table3["values"]["rows_as_printed"][22][7] == "24.2"
    assert table3["values"]["rows_as_printed"][22][8] == "0.01561 (436)"

    context = {row["observation_id"]: row for row in species["context"]}
    conditions = context["tachibana_2002_experimental_conditions"]["values"]
    assert conditions["vacuum_preheat_C_as_printed"] == 500
    assert conditions["vacuum_preheat_duration_h_as_printed"] == "0.5 to 5"

    residue = context["tachibana_2002_forsterite_residue_composition"]
    ir_feature = residue["values"][
        "orthoenstatite_IR_absorption_feature_as_printed"
    ]
    assert ir_feature["value"]["point"] == "405"
    assert "approximate" not in ir_feature["value"]
    assert ir_feature["locator"]["published_page"] == 716
    assert residue["locator"]["published_page"] == 718
    assert context["tachibana_2002_textual_kinetics_results"]["locator"][
        "published_page"
    ] == 718

    densities = context["tachibana_2002_table6_hydrogen_flux"]["values"][
        "quoted_attributed_values"
    ]
    assert densities["attribution"] == "Carmichael, 1989"
    assert densities["method_class"] == "quoted_attributed"
    assert (
        densities["values"]["average_enstatite_density"]["value"]["point"]
        == "3.209"
    )
    assert (
        densities["values"]["average_forsterite_density"]["value"]["point"]
        == "3.213"
    )

    solar_model = context["tachibana_2002_solar_nebula_model_context"]
    assert solar_model["locator"]["published_page"] == 725
    assert context["tachibana_2002_fig10_solar_nebula_model"]["locator"][
        "published_page"
    ] == 726
    assert solar_model["values"]["kappa_en_activation_energy_restatement"][
        "value"
    ]["point"] == "458"
    assert solar_model["values"]["kappa_en_activation_energy_restatement"][
        "locator"
    ]["published_page"] == 726
    assert solar_model["values"]["kappa_en_activation_energy_restatement"][
        "units"
    ] == "kJ/mol"
    assert solar_model["values"]["kappa_en_activation_energy_restatement"][
        "method_class"
    ] == "model_derived"


def test_tachibana_2002_migration_preserves_conditions_for_every_row() -> None:
    document = _document()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.observations) == 54

    table3 = next(
        row
        for row in document["species"]["enstatite"]["context"]
        if row["observation_id"] == "tachibana_2002_table3_hydrogen_conditions_and_raw_results"
    )
    raw_rows = table3["values"]["rows_as_printed"]
    pressures = [
        Decimal(value.replace(" × 10^", "E"))
        for value in (
            table3["values"]["pressure_group_1_bar_as_printed"],
            table3["values"]["pressure_group_2_bar_as_printed"],
            table3["values"]["pressure_group_3_bar_as_printed"],
        )
    ]

    for observation in result.observations.values():
        conditions = observation.point_conditions
        assert conditions is not None
        expected_condition_keys = {
            "temperature_K",
            "duration_h",
            "hydrogen_partial_pressure_bar",
        }
        is_mass_loss_fraction = (
            observation.identity.quantity.is_value
            and observation.identity.quantity.value.value == "mass_loss_fraction"
        )
        if is_mass_loss_fraction:
            expected_condition_keys.update({"composition", "printed_composition"})
        assert set(conditions) == expected_condition_keys
        expected_row = observation.locator.note.removeprefix("row=")
        assert observation.locator.note == f"row={expected_row}"
        raw_row = raw_rows[int(expected_row) - 1]
        for key in conditions:
            assert conditions[key].state.is_value
            assert conditions[key].locator is not None
            assert conditions[key].locator.published_page == 721
            assert conditions[key].locator.table == "3"
        temperature = conditions["temperature_K"]
        assert temperature.state.value == observation.identity.temperature_K.value
        assert observation.identity.temperature_K.value == (
            Decimal(raw_row[0]) + Decimal("273.15")
        )
        assert conditions["duration_h"].state.value == Decimal(raw_row[1])
        pressure_index = (int(expected_row) - 1) // 9
        assert conditions["hydrogen_partial_pressure_bar"].state.value == pressures[
            pressure_index
        ]
        if is_mass_loss_fraction:
            expected_pressure, conversion = convert_pressure_to_pa(
                pressures[pressure_index], "bar"
            )
            assert conversion == "bar_to_Pa"
            assert expected_pressure is not None
            total_pressure = observation.identity.total_pressure_Pa
            assert total_pressure is not None and total_pressure.is_value
            assert total_pressure.value == expected_pressure

    fraction_rows = {
        observation.locator.note.removeprefix("row="): observation
        for observation in result.observations.values()
        if observation.locator is not None
        and observation.locator.note is not None
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value.value == "mass_loss_fraction"
    }
    assert set(fraction_rows) == {str(row) for row in range(1, 28)}

    expected_composition = wt_pct_to_mole_fraction(
        {"MgO": Decimal("39.83"), "SiO2": Decimal("60.04")}
    )
    for row in ("1", "10", "19"):
        observation = fraction_rows[row]
        composition = observation.identity.composition
        assert composition is not None and composition.is_value
        assert composition.value == expected_composition

        per = observation.identity.per
        assert per is not None and per.is_value
        assert per.value is PerBasis.INITIAL_MASS


def test_tachibana_2002_deferred_figures_are_context_only() -> None:
    document = _document()
    figures = [
        row
        for row in document["species"]["enstatite"]["context"]
        if row.get("type") == "figure_record"
    ]
    assert len(figures) == 10
    for figure in figures:
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["axes_as_printed"]
        assert values["series"]
        assert all("marker_count" in series for series in values["series"])
        assert "points" not in values


def test_tachibana_2002_fidelity_pin_rejects_a_mutated_table3_value() -> None:
    document = _document()
    assert vle.check_all_fidelity_samples_match(document, label="tachibana-2002") == []

    document["species"]["enstatite"]["observations"][0]["values"]["points"][0][
        "mass_loss_pct"
    ] = "7.8"
    assert vle.check_all_fidelity_samples_match(
        document, label="tachibana-2002-mutated"
    )
