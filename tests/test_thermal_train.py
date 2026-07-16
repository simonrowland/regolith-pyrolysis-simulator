from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from simulator.physical_constants import GAS_CONSTANT, STEFAN_BOLTZMANN
from simulator.thermal_budget import latent_vaporization_kj_per_mol
from simulator.thermal_train import (
    OXYGEN_FUSION_ENTHALPY_J_PER_MOL,
    OXYGEN_NORMAL_BOILING_POINT_K,
    OXYGEN_NORMAL_BOILING_PRESSURE_PA,
    OXYGEN_GAMMA,
    OXYGEN_MOLAR_MASS_KG_PER_MOL,
    OXYGEN_SPECIFIC_GAS_CONSTANT_J_PER_KG_K,
    OXYGEN_SUBLIMATION_ENTHALPY_J_PER_MOL,
    OXYGEN_TRIPLE_POINT_K,
    OXYGEN_TRIPLE_POINT_PA,
    OXYGEN_VAPORIZATION_ENTHALPY_J_PER_MOL,
    cavern_regeneration_energy_J,
    capacity_from_hardware,
    claude_cycle_cold_end,
    cryogenic_tail,
    intercooled_compression,
    FiniteCapacity,
    NoColdTrain,
    mass_rate_kg_hr_to_molar_rate_mol_s,
    molar_rate_mol_s_to_mass_rate_kg_hr,
    oxygen_cp_shomate_j_per_mol_k,
    oxygen_saturation_pressure_pa,
    orifice_diameter_for_C,
    segmented_radiator_area_m2,
    solid_oxygen_cp_j_per_mol_k,
    thermal_train_overflow_kg_hr,
    vapor_cp_j_per_mol_k,
)


def test_o2_phase_anchors_and_sublimation_join_are_grounded() -> None:
    assert OXYGEN_TRIPLE_POINT_K == pytest.approx(54.361, abs=0.001)
    assert OXYGEN_TRIPLE_POINT_PA == pytest.approx(146.33, abs=0.02)
    assert oxygen_saturation_pressure_pa(OXYGEN_TRIPLE_POINT_K) == pytest.approx(
        OXYGEN_TRIPLE_POINT_PA, rel=1e-12
    )
    assert oxygen_saturation_pressure_pa(OXYGEN_NORMAL_BOILING_POINT_K) == pytest.approx(
        OXYGEN_NORMAL_BOILING_PRESSURE_PA, rel=1e-12
    )
    assert oxygen_saturation_pressure_pa(OXYGEN_TRIPLE_POINT_K - 1e-8) == pytest.approx(
        oxygen_saturation_pressure_pa(OXYGEN_TRIPLE_POINT_K + 1e-8), rel=1e-8
    )


def test_solid_o2_sublimation_and_cp_match_independent_nbsir_sidecar() -> None:
    sidecar = yaml.safe_load(
        Path("tests/fixtures/literature/thermal_train_o2_nbsir77_859.yaml").read_text()
    )
    assert oxygen_saturation_pressure_pa(44.0) == pytest.approx(
        sidecar["solid_vapor_pressure"]["modern_nist_normalized_anchor_44K_Pa"],
        rel=2e-8,
    )
    assert oxygen_saturation_pressure_pa(50.0) == pytest.approx(
        sidecar["solid_vapor_pressure"]["modern_nist_normalized_anchor_50K_Pa"],
        rel=2e-8,
    )
    assert solid_oxygen_cp_j_per_mol_k(50.0) == pytest.approx(
        sidecar["solid_heat_capacity"]["anchor_50K_J_mol_K"], rel=2e-8
    )
    with pytest.raises(ValueError, match="44 K"):
        oxygen_saturation_pressure_pa(43.999)
    with pytest.raises(ValueError, match="44 K"):
        solid_oxygen_cp_j_per_mol_k(43.999)
    with pytest.raises(ValueError, match="triple point"):
        solid_oxygen_cp_j_per_mol_k(54.362)


def test_o2_shomate_anchor_and_validity_refusal() -> None:
    a, b, c, d, e = (31.32234, -20.23531, 57.86644, -36.50624, -0.007374)
    t = 298.15 / 1000.0
    independently_computed = a + b * t + c * t**2 + d * t**3 + e / t**2
    assert oxygen_cp_shomate_j_per_mol_k(298.15) == pytest.approx(independently_computed, rel=1e-12)
    assert OXYGEN_GAMMA == pytest.approx(1.395)
    assert OXYGEN_SPECIFIC_GAS_CONSTANT_J_PER_KG_K == pytest.approx(259.8)
    with pytest.raises(ValueError, match="100 K"):
        oxygen_cp_shomate_j_per_mol_k(99.999)
    with pytest.raises(ValueError, match="2000 K"):
        oxygen_cp_shomate_j_per_mol_k(2000.001)


def test_independent_o2_phase_enthalpies_and_public_alkali_latent_accessor() -> None:
    assert OXYGEN_SUBLIMATION_ENTHALPY_J_PER_MOL == pytest.approx(8199.5)
    assert OXYGEN_FUSION_ENTHALPY_J_PER_MOL == pytest.approx(444.0)
    assert OXYGEN_VAPORIZATION_ENTHALPY_J_PER_MOL == pytest.approx(6820.0)
    assert latent_vaporization_kj_per_mol("Na") == pytest.approx(97.42)
    assert latent_vaporization_kj_per_mol("K") == pytest.approx(76.90)


def test_monatomic_cp_is_exact_five_halves_r() -> None:
    assert vapor_cp_j_per_mol_k("Na", 1000.0) == 2.5 * GAS_CONSTANT


def test_hand_fixture_catches_mass_molar_hour_and_watt_conversions() -> None:
    rate = mass_rate_kg_hr_to_molar_rate_mol_s("Na", 1.0)
    expected_rate = 1.0 / (3600.0 * 0.02298976928)
    assert rate == pytest.approx(expected_rate, rel=2e-9)
    result = segmented_radiator_area_m2(
        {"Na": rate},
        temperature_in_K=1000.01,
        temperature_out_K=1000.0,
        sink_temperature_K=0.0,
        emissivity=1.0,
        segment_K=1.0,
    )
    expected_load_W = expected_rate * (2.5 * GAS_CONSTANT) * 0.01
    expected_area = expected_load_W / (STEFAN_BOLTZMANN * 1000.005 ** 4)
    assert result["sensible_load_W"] == pytest.approx(expected_load_W, rel=2e-9)
    assert result["area_m2"] == pytest.approx(expected_area, rel=2e-8)
    assert molar_rate_mol_s_to_mass_rate_kg_hr("Na", rate) == pytest.approx(1.0)


def test_na_latent_panel_catches_kilojoule_to_joule_conversion() -> None:
    rate = mass_rate_kg_hr_to_molar_rate_mol_s("Na", 1.0)
    crossing = 753.15
    result = segmented_radiator_area_m2(
        {"Na": rate},
        temperature_in_K=800.0,
        temperature_out_K=700.0,
        sink_temperature_K=0.0,
        emissivity=1.0,
        segment_K=50.0,
        latent_crossings_K={"Na": crossing},
    )
    expected_latent_W = (1.0 / (3600.0 * 0.02298976928)) * 97420.0
    expected_latent_area = expected_latent_W / (STEFAN_BOLTZMANN * crossing**4)
    assert rate == pytest.approx(0.0120826692, rel=2e-8)
    assert result["latent_load_W"] == pytest.approx(expected_latent_W, rel=2e-9)
    assert result["latent_area_m2"] == pytest.approx(expected_latent_area, rel=2e-9)


def test_off_grid_crossing_splits_sensible_grid_exactly() -> None:
    rate = 1.0
    crossing = 753.15
    result = segmented_radiator_area_m2(
        {"Na": rate},
        temperature_in_K=800.0,
        temperature_out_K=700.0,
        sink_temperature_K=0.0,
        emissivity=1.0,
        segment_K=50.0,
        latent_crossings_K={"Na": crossing},
    )
    expected = 2.5 * GAS_CONSTANT * (800.0 - crossing)
    assert result["sensible_load_W"] == pytest.approx(expected, rel=1e-12)


def test_radiator_grid_splits_at_o2_shomate_band_boundary() -> None:
    result = segmented_radiator_area_m2(
        {"O2": 1.0},
        temperature_in_K=750.0,
        temperature_out_K=650.0,
        sink_temperature_K=0.0,
        emissivity=1.0,
        segment_K=200.0,
    )
    expected = (
        oxygen_cp_shomate_j_per_mol_k(675.0) * 50.0
        + oxygen_cp_shomate_j_per_mol_k(725.0) * 50.0
    )
    assert result["sensible_load_W"] == pytest.approx(expected, rel=1e-12)


def test_segmented_radiator_isothermal_and_zero_sink_limit() -> None:
    result = segmented_radiator_area_m2(
        {"Na": 2.0},
        temperature_in_K=800.001,
        temperature_out_K=800.0,
        sink_temperature_K=0.0,
        emissivity=0.8,
        segment_K=1e-4,
    )
    expected_q = 2.0 * 2.5 * GAS_CONSTANT * 0.001
    expected_a = expected_q / (0.8 * STEFAN_BOLTZMANN * 800.0005 ** 4)
    assert result["area_m2"] == pytest.approx(expected_a, rel=2e-7)


def test_passive_radiator_refuses_day_sink_floor() -> None:
    result = segmented_radiator_area_m2(
        {"O2": 1.0},
        temperature_in_K=300.0,
        temperature_out_K=150.0,
        sink_temperature_K=250.0,
        emissivity=0.85,
        segment_K=50.0,
        sink_margin_K=10.0,
    )
    assert result["status"] == "passive_refused"
    assert result["area_m2"] is None
    assert result["active_lift_W"] > 0.0


def test_intercooler_and_shaft_accounting_identity() -> None:
    result = intercooled_compression(
        2.0,
        pressure_suction_Pa=100.0,
        pressure_discharge_Pa=20000.0,
        stages=3,
        inlet_temperature_K=150.0,
        eta_isen=0.75,
    )
    assert result["intercooler_reject_W"] == pytest.approx(
        result["compressor_shaft_W"] * 0.75, rel=1e-12
    )
    assert result["intercooler_reject_W_per_stage"] * 3 == pytest.approx(
        result["intercooler_reject_W"]
    )


def test_cryo_carnot_identity_and_reject_load() -> None:
    result = cryogenic_tail(
        1.0,
        temperature_floor_K=150.0,
        temperature_frost_K=OXYGEN_TRIPLE_POINT_K,
        temperature_reject_K=300.0,
        eta_2ndlaw=1.0,
        segment_K=10.0,
    )
    assert result["refrigeration_work_W"] == pytest.approx(
        result["cold_load_W"] * (300.0 / OXYGEN_TRIPLE_POINT_K - 1.0), rel=1e-12
    )
    assert result["reject_load_W"] == pytest.approx(
        result["cold_load_W"] + result["refrigeration_work_W"]
    )


def test_cryo_refuses_frost_temperature_below_gas_cp_validity_edge() -> None:
    with pytest.raises(ValueError, match="triple point"):
        cryogenic_tail(
            1.0,
            temperature_floor_K=150.0,
            temperature_frost_K=54.0,
            temperature_reject_K=300.0,
            eta_2ndlaw=1.0,
            segment_K=10.0,
        )


def test_capacity_from_hardware_is_tagged_pure_rating_minimum() -> None:
    assert isinstance(capacity_from_hardware(None), NoColdTrain)
    assert isinstance(
        capacity_from_hardware({
            "compressor_mass_flow_limit_kg_hr": None,
            "refrigeration_freeze_rate_kg_hr": None,
        }),
        NoColdTrain,
    )
    result = capacity_from_hardware({
        "compressor_mass_flow_limit_kg_hr": 12.0,
        "refrigeration_freeze_rate_kg_hr": 9.5,
        "p_ref_Pa": 45000.0,
        "T_ref_K": 120.0,
        "orifice_diameter_m": 1000.0,
    })
    assert isinstance(result, FiniteCapacity)
    assert result.value_kg_hr == 9.5
    assert result.p_ref_Pa == 45000.0
    assert result.T_ref_K == 120.0


def test_choked_orifice_worked_vector_round_trip_and_gates() -> None:
    diameter = orifice_diameter_for_C(
        9.8561694011,
        0.80,
        45000.0,
        120.0,
        back_pressure_Pa=21000.0,
    )
    assert diameter == pytest.approx(0.005, abs=1e-12)
    area = 3.141592653589793 * diameter**2 / 4.0
    assert area == pytest.approx(1.9634954e-5, rel=1e-7)
    forward_kg_hr = (
        0.80
        * area
        * 45000.0
        * (OXYGEN_GAMMA / (OXYGEN_SPECIFIC_GAS_CONSTANT_J_PER_KG_K * 120.0)) ** 0.5
        * (2.0 / (OXYGEN_GAMMA + 1.0))
        ** ((OXYGEN_GAMMA + 1.0) / (2.0 * (OXYGEN_GAMMA - 1.0)))
        * 3600.0
    )
    assert forward_kg_hr == pytest.approx(9.8561694011, abs=1e-2)
    assert oxygen_saturation_pressure_pa(120.0) == pytest.approx(1.0e6, rel=0.20)
    with pytest.raises(ValueError, match="vapor gate"):
        orifice_diameter_for_C(9.856, 0.8, 1.2e6, 120.0, back_pressure_Pa=21000.0)
    with pytest.raises(ValueError, match="choked gate"):
        orifice_diameter_for_C(9.856, 0.8, 45000.0, 120.0, back_pressure_Pa=30000.0)


def test_claude_cycle_seven_nodes_close_mass_and_open_plant_energy() -> None:
    result = claude_cycle_cold_end(
        9.856,
        liquid_yield=0.25,
        expander_bypass_fraction=0.20,
        pressure_low_Pa=21000.0,
        pressure_high_Pa=600000.0,
        inlet_temperature_K=120.0,
        reject_temperature_K=300.0,
        eta_isen=0.75,
        makeup_pressure_Pa=45000.0,
    )
    mass = result["mass_basis"]
    assert len(result["nodes"]) == 7
    assert result["nodes"][0]["P_Pa"] == 45000.0
    assert result["nodes"][6]["mass_flow_kg_hr"] == pytest.approx(
        mass["separator_inlet_kg_hr"]
    )
    assert {edge["device"] for edge in result["edges"]} >= {
        "metering_orifice", "dry_work_expander", "jt_valve", "separator_liquid"
    }
    assert mass["circulation_kg_hr"] == pytest.approx(9.856 / 0.25)
    assert mass["return_kg_hr"] == pytest.approx((1.0 - 0.25) * 9.856 / 0.25)
    assert mass["separator_vapor_kg_hr"] >= 0.0
    assert max(abs(value) for value in result["mass_residuals"].values()) < 1e-12
    energy = result["energy"]
    cp_mass = oxygen_cp_shomate_j_per_mol_k(120.0) / OXYGEN_MOLAR_MASS_KG_PER_MOL
    pressure_ratio = 600000.0 / 21000.0
    exponent = (OXYGEN_GAMMA - 1.0) / OXYGEN_GAMMA
    expected_turbine_W = (
        mass["expander_bypass_kg_hr"]
        / 3600.0
        * 0.75
        * cp_mass
        * result["nodes"][4]["T_K"]
        * (1.0 - pressure_ratio ** -exponent)
    )
    assert energy["turbine_work_out_W"] == pytest.approx(expected_turbine_W)
    assert result["nodes"][6]["T_K"] == pytest.approx(result["nodes"][4]["T_K"])
    assert result["nodes"][6]["h_J_kg"] == pytest.approx(result["nodes"][4]["h_J_kg"])
    assert energy["net_work_W"] == pytest.approx(
        energy["compressor_work_W"] - energy["turbine_work_out_W"]
    )
    assert energy["reject_load_W"] == pytest.approx(
        energy["cold_load_W"] + energy["net_work_W"]
    )
    assert abs(energy["plant_relative_residual"]) <= 1e-6
    assert max(abs(value) for value in energy["device_residuals_W"].values()) < 1e-9
    with pytest.raises(ValueError, match="must be <= 1"):
        claude_cycle_cold_end(
            1.0,
            liquid_yield=0.6,
            expander_bypass_fraction=0.5,
            pressure_low_Pa=21000.0,
            pressure_high_Pa=600000.0,
            inlet_temperature_K=120.0,
            reject_temperature_K=300.0,
            eta_isen=0.75,
        )
    with pytest.raises(ValueError, match="positive dry-expander flow"):
        claude_cycle_cold_end(
            1.0,
            liquid_yield=0.25,
            expander_bypass_fraction=0.0,
            pressure_low_Pa=21000.0,
            pressure_high_Pa=600000.0,
            inlet_temperature_K=120.0,
            reject_temperature_K=300.0,
            eta_isen=0.75,
        )


def test_cavern_regeneration_triple_point_identity() -> None:
    result = cavern_regeneration_energy_J(
        7.0,
        storage_temperature_K=OXYGEN_TRIPLE_POINT_K,
        cavern_thermal_mass_J_per_K=5e7,
        segment_K=1.0,
    )
    assert result["oxygen_sensible_J"] == 0.0
    assert result["cavern_walls_J"] == 0.0
    assert result["total_J"] == 7.0 * OXYGEN_FUSION_ENTHALPY_J_PER_MOL


def test_cavern_regeneration_uses_segmented_solid_cp_below_triple_point() -> None:
    fine = cavern_regeneration_energy_J(
        1.0,
        storage_temperature_K=45.0,
        cavern_thermal_mass_J_per_K=0.0,
        segment_K=0.1,
    )
    coarse = cavern_regeneration_energy_J(
        1.0,
        storage_temperature_K=45.0,
        cavern_thermal_mass_J_per_K=0.0,
        segment_K=5.0,
    )
    assert fine["oxygen_sensible_J"] == pytest.approx(432.23400598, rel=3e-6)
    assert coarse["oxygen_sensible_J"] != pytest.approx(fine["oxygen_sensible_J"], rel=1e-5)


def test_overflow_is_diagnostic_capacity_difference() -> None:
    assert thermal_train_overflow_kg_hr(8.0, 10.0) == 0.0
    assert thermal_train_overflow_kg_hr(12.5, 10.0) == 2.5


def test_cold_train_assumptions_are_closed_classed_ranged_and_provenanced() -> None:
    payload = yaml.safe_load(Path("data/thermal_train_params.yaml").read_text())
    assert payload["schema_version"] == "thermal-train-v2"
    cold_train = payload["cold_train"]
    assert set(cold_train) == {
        "runtime_enforcement", "rating", "orifice", "relief", "cycle", "endpoint",
    }
    entries = [cold_train["runtime_enforcement"], cold_train["endpoint"]]
    for section in ("rating", "orifice", "relief", "cycle"):
        entries.extend(cold_train[section].values())
    for entry in entries:
        assert entry["source_tag"] == "assumption"
        assert entry["assumption_class"]
        assert entry["range"]
        assert entry["provenance"]


def test_thermal_train_imports_only_public_latent_accessor_and_no_optimizer() -> None:
    source = Path("simulator/thermal_train.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append((node.module or "", {alias.name for alias in node.names}))
    assert not any(module.startswith("simulator.optimize") for module, _names in imports)
    assert any(
        module == "simulator.thermal_budget" and "latent_vaporization_kj_per_mol" in names
        for module, names in imports
    )
    assert not any("_LATENT_VAPORIZATION_KJ_PER_MOL" in names for _module, names in imports)
