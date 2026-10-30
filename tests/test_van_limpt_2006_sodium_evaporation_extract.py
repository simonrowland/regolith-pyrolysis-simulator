from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import AmountBasis
from simulator.battery.score import rail_for_quantity
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]

EXTRACT = (
    ROOT / "data/literature/extracts/van-limpt-2006-sodium-evaporation.yaml"
)


def test_van_limpt_table_values_and_duplicate_mass_balance_are_preserved() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    context = extract["species"]["Na"]["context"]
    diffusion = next(row for row in context if row["observation_id"].endswith("table_i_gas_diffusion_coefficients"))
    mass_balance = next(row for row in context if row["observation_id"].endswith("table_ii_mass_balance_context"))

    assert diffusion["locator"]["page"] == 4
    assert diffusion["values"]["rows"][0]["D_NaOH_air"] == 3.12e-4
    assert diffusion["values"]["rows"][1]["D_NaOH_combustion_atmosphere"] == 2.73e-4
    assert mass_balance["locator"]["page"] == 7
    assert mass_balance["values"]["duplicate_source_observation_id"] == "van_limpt_2007_table44_sodium_mass_balance"
    assert mass_balance["values"]["rows"][4]["ratio_extractive_to_gravimetric"] == 1.00


def test_van_limpt_figures_are_inventory_only_and_have_physical_locators() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    figures = [
        row
        for species in extract["species"].values()
        for row in species["context"]
        if row["type"] == "figure_record"
    ]

    assert [row["locator"]["figure"] for row in figures] == ["3", "4", "5", "6", "7", "8", "9"]
    assert [row["locator"]["page"] for row in figures] == [6, 6, 8, 8, 8, 8, 9]
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("marker_count" in series for row in figures for series in row["values"]["series"])


def test_van_limpt_wave_facts_have_exact_source_rows_and_coordinates() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    sodium_rows = extract["species"]["Na"]["observations"]
    activity = extract["species"]["Na2O"]["observations"][0]
    balance = extract["species"]["Na2O"]["observations"][1]

    assert [row["observation_id"] for row in sodium_rows] == [
        "van_limpt_2006_measured_total_na_evaporation_rate_at_1400C",
        "van_limpt_2006_measured_total_na_evaporation_rate_at_1450_1460C",
    ]
    assert [row["values"]["value_as_printed"] for row in sodium_rows] == [
        "6.7 × 10^-5 to 7.4 × 10^-5",
        "~1.2 × 10^-4",
    ]
    assert sodium_rows[0]["values"]["T_C"] == "1400"
    assert sodium_rows[1]["values"]["T_C_range"] == ["1450", "1460"]
    assert all("quantity" not in row["values"] for row in sodium_rows)
    for row in sodium_rows:
        assert row["regime"] == "transpiration"
        assert row["locator"]["page"] == 6
        assert row["values"]["method_class"] == "measured_reduced"
        assert set(row["point_conditions"]) == {
            "composition",
            "gas_mix",
            "carrier_gas_flow_m3n_s",
            "H2O_partial_pressure_bar",
            "oxygen_partial_pressure_bar_by_gas",
        }
        for condition in row["point_conditions"].values():
            assert set(condition) == {"state", "locator"}
            assert condition["locator"]["page"] == 6
    assert sodium_rows[0]["point_conditions"]["gas_mix"]["state"]["value"] == (
        "N2/H2O or air/H2O"
    )
    assert sodium_rows[0]["point_conditions"]["H2O_partial_pressure_bar"]["state"]["value"] == (
        "0.25 ± 0.01"
    )
    assert sodium_rows[0]["point_conditions"]["carrier_gas_flow_m3n_s"]["state"]["value"] == "1.6E-4"
    assert sodium_rows[0]["point_conditions"]["oxygen_partial_pressure_bar_by_gas"]["state"]["value"] == {
        "N2_H2O": "<0.01",
        "air_H2O": "~0.16",
    }

    assert activity["locator"]["page"] == 7
    assert activity["values"]["value_as_printed"] == "about 10–15% lower"
    assert activity["values"]["method_class"] == "measured_reduced"
    assert "T_C_range_as_printed" not in activity["values"]
    assert "duration_as_printed" not in activity["values"]
    assert "quantity" not in activity["values"]

    points = balance["values"]["points"]
    assert [(row["T_C"], row["value"], row["gravimetric_weight_loss_mg"]) for row in points] == [
        ("1416", "112", "125"),
        ("1437", "146", "153"),
        ("1439", "166", "170"),
        ("1354", "69", "79"),
        ("1454", "188", "188"),
        ("1356", "85", "71"),
    ]
    assert balance["values"]["method_class"] == "measured_tabulated"
    assert "quantity" not in balance["values"]
    assert all(set(row["point_conditions"]["composition"]) == {"state", "locator"} for row in points)
    assert all(row["point_conditions"]["composition"]["locator"]["page"] == 7 for row in points)
    expected_formula = {
        "basis": "printed_formula_stoichiometry",
        "amount_basis": "mol_inventory",
        "components": [["Na2O", "1"], ["SiO2", "2"]],
    }
    assert all(
        row["point_conditions"]["composition"]["state"]["value"] == expected_formula
        for row in points
    )
    assert all(
        row["point_conditions"]["composition"]["state"]["value"] == expected_formula
        for row in sodium_rows
    )

    ratios = next(
        row["values"]["rows"]
        for row in extract["species"]["Na"]["context"]
        if row["observation_id"].endswith("table_ii_mass_balance_context")
    )
    assert [row["ratio_extractive_to_gravimetric"] for row in ratios] == [
        0.90, 0.96, 0.98, 0.87, 1.00, 1.21
    ]
    summary = next(
        row for row in extract["species"]["Na2O"]["context"]
        if row["observation_id"].endswith("printed_activity_summary")
    )
    assert summary["locator"]["page"] == 7
    assert summary["values"]["activity_surface_below_original_glass_percent"] == [10, 15]
    flux_summary = next(
        row for row in extract["species"]["Na"]["context"]
        if row["observation_id"].endswith("printed_evaporation_fluxes_context")
    )
    assert flux_summary["values"]["flow_normalization_reference_as_printed"] == (
        "T = 293.15 K and P = 101325 Pa; these are flow normalization conditions, not the experiment pressure."
    )
    assert flux_summary["values"]["experiment_total_pressure_as_printed"] == "not reported"


def test_van_limpt_isolated_migration_keeps_unknown_quantities_and_conditions() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert check_all_fidelity_samples_match(
        extract, label="van-limpt-2006-sodium-evaporation"
    ) == []

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    rows = migrator.result.observations
    assert len(rows) == 9
    assert not migrator.result.validation.hard_issues
    assert all(row.value.kind.value == "unavailable" for row in rows.values())
    assert all(not row.identity.quantity.is_value for row in rows.values())
    assert all(
        rail_for_quantity(None, species_formula=row.identity.species.formula) is None
        for row in rows.values()
    )

    table_rows = [row for key, row in rows.items() if "p007_t02" in key]
    assert len(table_rows) == 6
    for row, temperature_c in zip(
        table_rows, ("1416", "1437", "1439", "1354", "1454", "1356")
    ):
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal(temperature_c) + Decimal("273.15")
        assert row.identity.composition is None
        assert row.point_conditions["composition"].state.is_value
        assert row.point_conditions["composition"].locator.page == 7
        composition = row.point_conditions["composition"].state.value
        assert composition.basis == "printed_formula_stoichiometry"
        assert composition.amount_basis is AmountBasis.MOL_INVENTORY
        assert dict(composition.components) == {
            "Na2O": Decimal("1"),
            "SiO2": Decimal("2"),
        }
        assert row.evidence.class_.value.value == "measured_tabulated"

    rate_rows = [row for key, row in rows.items() if "measured_total_na_evaporation_rate_at_" in key]
    assert len(rate_rows) == 2
    expected_rate_conditions = {
        "composition",
        "gas_mix",
        "carrier_gas_flow_m3n_s",
        "H2O_partial_pressure_bar",
        "oxygen_partial_pressure_bar_by_gas",
    }
    rate_1400 = next(row for row in rate_rows if "1400C" in row.observation_id)
    for row in rate_rows:
        expected_conditions = expected_rate_conditions | (
            {"temperature_K"} if row is rate_1400 else set()
        )
        assert set(row.point_conditions) == expected_conditions
        assert all(row.point_conditions[key].state.is_value for key in expected_rate_conditions)
        assert all(row.point_conditions[key].locator.page == 6 for key in expected_rate_conditions)
        printed_composition = row.point_conditions["composition"].state.value
        assert row.identity.composition is not None
        assert row.identity.composition.is_value
        assert row.identity.composition.value == printed_composition
    assert rate_1400.point_conditions["temperature_K"].locator.page == 6
    assert rate_1400.identity.temperature_K.value == Decimal("1673.15")
    assert rate_1400.point_conditions["gas_mix"].state.value == "N2/H2O or air/H2O"
    assert rate_1400.point_conditions["gas_mix"].locator.page == 6
    assert rate_1400.point_conditions["composition"].state.is_value
    rate_composition = rate_1400.identity.composition.value
    assert rate_composition.amount_basis is AmountBasis.MOL_INVENTORY
    assert rate_composition.basis == "printed_formula_stoichiometry"
    assert dict(rate_composition.components) == {
        "Na2O": Decimal("1"),
        "SiO2": Decimal("2"),
    }
    assert rate_1400.point_conditions["carrier_gas_flow_m3n_s"].state.value == Decimal("0.00016")
    rate_range = next(row for row in rate_rows if "1450_1460C" in row.observation_id)
    assert not rate_range.identity.temperature_K.is_value
    assert "[1723.15, 1733.15]" in rate_range.identity.temperature_K.reason
    assert "temperature_K" not in rate_range.point_conditions
    rate_range_composition = rate_range.identity.composition.value
    assert rate_range_composition.amount_basis is AmountBasis.MOL_INVENTORY
    assert dict(rate_range_composition.components) == {
        "Na2O": Decimal("1"),
        "SiO2": Decimal("2"),
    }
    assert all(row.evidence.class_.value.value == "measured_reduced" for row in rate_rows)

    activity = next(row for key, row in rows.items() if "relative_to_original_glass" in key)
    assert activity.identity.temperature_K is None or not activity.identity.temperature_K.is_value
    assert not activity.point_conditions
    assert activity.evidence.class_.value.value == "measured_reduced"


def test_van_limpt_fidelity_pin_rejects_mutated_table_cell() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    mutated["species"]["Na2O"]["observations"][1]["values"]["points"][0]["value"] = "113"

    errors = check_all_fidelity_samples_match(
        mutated, label="van-limpt-2006-sodium-evaporation"
    )
    assert any("fidelity sample value mismatch" in error for error in errors)
