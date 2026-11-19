from __future__ import annotations

import json
import math
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

import simulator.capacity_coupling as capacity_coupling
import simulator.chemistry.melt_activity as melt_activity
from engines.builtin.evaporation_flux import _NONTRIVIAL_FLUX_KG_HR
from engines.builtin.overhead_bleed import (
    BuiltinOverheadBleedProvider,
    OXYGEN_SPECIES,
    compressible_pressure_capacity_fraction,
)
from simulator.accounting import resolve_species_formula
from simulator.alpha_kinetics import ANALYTICAL_UPPER_BOUND_ALPHA_STATUS
from simulator.capacity_coupling import (
    CapacityCouplingRefusalError,
    CapacityShadowRefusal,
    CapacityShadowResult,
    combined_saturation,
    diagnostic_backward_euler_headspace_step,
    partition_melt_oxygen,
    solve_capacity_shadow,
)
from simulator.condensation_routing import (
    K_TRAP_STAGE_KEY,
    NA_COLLECTOR_STAGE_KEY,
)
from simulator.core import PyrolysisSimulator
from simulator.evaporation import (
    EvaporationFluxRefusal,
    _STAGE0_PHOSPHORUS_VAPOUR_CARRIERS,
    _pre_rg_effective_pressure_source,
)
from simulator.overhead import pipe_species_outflow_mol_s
from simulator.physical_constants import GAS_CONSTANT
from simulator.state import MOLAR_MASS, CampaignPhase, EvaporationFlux
from simulator.vapour_rail.activity import ActivityVerdictKind
from simulator.vapour_rail.batch import FluxEligible, PressureRefusal, PressureValue
from simulator.vapour_rail.catalog import OUT_OF_RANGE_STATUS
from simulator.vapour_rail.instrumentation import (
    EffectivePressureSource,
    VAPOUR_CARRIER_AUTHORITY_AUTHORITATIVE,
    compare_live_shadow_to_batch_flux,
    flux_pressures_from_batch,
    serialize_vapour_batch,
    vapour_carrier_authority_status,
)
from simulator.thermal_train import (
    FiniteCapacity,
    NoColdTrain,
    capacity_from_hardware,
    thermal_train_parameters_from_mapping,
)
from tests.chemistry.conftest import _build_sim


M_O2 = 0.032
M_N2 = 0.028
T_FOR_1000_PA_PER_MOL = 1000.0 / GAS_CONSTANT
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text())


def _real_capacity_sim():
    return _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
    )


def _record_transport_estimates(sim):
    """Record each owner estimate. Does not compute a conductance."""
    calls = []
    real_estimate = sim.overhead_model.estimate_transport_state

    def _estimate(flux, melt, *args, **kwargs):
        state = real_estimate(flux, melt, *args, **kwargs)
        calls.append(
            {
                "demand_kg_hr": float(flux.total_kg_hr),
                "capacity_kg_hr": float(state["pipe_conductance_kg_hr"]),
                "species_kg_hr": {
                    str(species): float(rate)
                    for species, rate in flux.species_kg_hr.items()
                    if float(rate) > 0.0
                },
            }
        )
        return state

    sim.overhead_model.estimate_transport_state = _estimate
    return calls


def _canonical_bytes(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()


def _o2_fixture(dt_hr: float = 1.0, *, max_iterations: int = 50):
    pre_mol = 0.5
    source_intercept_kg_hr = 0.064
    source_slope_kg_hr_pa = 6.4e-6
    capacity = FiniteCapacity(0.032)

    def hk_flux(partials):
        return {
            "O2": max(
                0.0,
                source_intercept_kg_hr
                - source_slope_kg_hr_pa * partials["O2"],
            )
        }

    result = solve_capacity_shadow(
        pre_holdup_mol={"O2": pre_mol},
        molar_mass_kg_mol={"O2": M_O2},
        flux_kg_hr_at_partials=hk_flux,
        capacity=capacity,
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=T_FOR_1000_PA_PER_MOL,
        volume_m3=1.0,
        dt_hr=dt_hr,
        bleed_conductance_kg_s=1.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=1.0e-30,
        p_open_Pa=1.0e9,
        vessel_rating_Pa=1.0e12,
        max_iterations=max_iterations,
    )
    slope = source_slope_kg_hr_pa * dt_hr / M_O2 * 1000.0
    intercept = (
        pre_mol
        + source_intercept_kg_hr * dt_hr / M_O2
        - capacity.value_kg_hr * dt_hr / M_O2
    ) * 1000.0
    expected = intercept / (1.0 + slope)
    return result, expected


@pytest.mark.parametrize("dt_hr", [1.0, 0.5, 0.25])
def test_o2_only_picard_matrix_matches_hand_derived_fixed_point(dt_hr):
    result, expected = _o2_fixture(dt_hr)

    assert isinstance(result, CapacityShadowResult)
    assert result.partial_pressures_Pa["O2"] == pytest.approx(
        expected, rel=1.0e-9, abs=1.0e-6
    )
    assert result.oxygen.admitted_mol == pytest.approx(dt_hr)
    assert result.oxygen.relieved_mol == 0.0
    assert result.oxygen.held_mol >= 0.0
    assert result.oxygen.debited_mol == pytest.approx(
        result.oxygen.external_mol
        + result.oxygen.admitted_mol
        + result.oxygen.relieved_mol
    )
    assert all(
        later <= earlier + 1.0e-12
        for earlier, later in zip(
            result.max_delta_history_Pa,
            result.max_delta_history_Pa[1:],
        )
    )
    assert result.mass_closure_error_pct <= 5.0e-12
    assert result.authoritative is False


@pytest.mark.parametrize("dt_hr", [1.0, 0.5, 0.25])
def test_two_species_binding_fixture_preserves_non_o2_head_partition(dt_hr):
    frozen_pre = {"O2": 0.25, "N2": 1.0}
    frozen_copy = dict(frozen_pre)

    def hk_flux(partials):
        return {
            "O2": max(0.0, 0.048 - 4.0e-6 * partials["O2"]),
            "N2": max(0.0, 0.014 - 1.0e-6 * partials["N2"]),
        }

    result = solve_capacity_shadow(
        pre_holdup_mol=frozen_pre,
        molar_mass_kg_mol={"O2": M_O2, "N2": M_N2},
        flux_kg_hr_at_partials=hk_flux,
        capacity=FiniteCapacity(0.008),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=T_FOR_1000_PA_PER_MOL,
        volume_m3=1.0,
        dt_hr=dt_hr,
        bleed_conductance_kg_s=1.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=1.0e-30,
        p_open_Pa=1.0e9,
        vessel_rating_Pa=1.0e12,
    )

    assert isinstance(result, CapacityShadowResult)
    assert frozen_pre == frozen_copy
    slope = 4.0e-6 * dt_hr / M_O2 * 1000.0
    intercept = (
        0.25 + 0.048 * dt_hr / M_O2 - 0.008 * dt_hr / M_O2
    ) * 1000.0
    assert result.partial_pressures_Pa == pytest.approx(
        {"N2": 0.0, "O2": intercept / (1.0 + slope)},
        rel=1.0e-9,
        abs=1.0e-6,
    )
    assert result.terminal_offgas_mol["N2"] == pytest.approx(
        result.bled_species_mol["N2"]
    )
    expected_n2_bled = (
        frozen_pre["N2"]
        + max(0.0, 0.014 - 1.0e-6 * 0.0) * dt_hr / M_N2
    )
    assert result.terminal_offgas_mol["N2"] == pytest.approx(
        expected_n2_bled
    )
    assert result.oxygen.held_mol >= 0.0
    assert result.oxygen.relieved_mol <= (
        result.bled_species_mol["O2"] - result.oxygen.external_mol
        - result.oxygen.admitted_mol + 1.0e-15
    )
    assert all(
        later <= earlier + 1.0e-9
        for earlier, later in zip(
            result.max_delta_history_Pa,
            result.max_delta_history_Pa[1:],
        )
    )
    assert result.mass_closure_error_pct <= 5.0e-12


def test_relief_is_capped_by_post_admission_remainder():
    partition = partition_melt_oxygen(
        bled_o2_mol=10.0,
        overhead_o2_mol=10.0,
        external_o2_holdup_mol=2.0,
        capacity=FiniteCapacity(M_O2),
        dt_hr=1.0,
        p_o2_Pa=1.0e6,
        k_relief_kg_hr_Pa=1.0,
        p_open_Pa=1.0,
        molar_mass_kg_mol=M_O2,
    )

    assert partition.external_mol == pytest.approx(2.0)
    assert partition.admitted_mol == pytest.approx(1.0)
    assert partition.relieved_mol == pytest.approx(7.0)
    assert partition.held_mol == 0.0
    assert partition.debited_mol == pytest.approx(10.0)


def test_accumulator_off_is_finite_capacity_partition_parity():
    controls = {
        "bled_o2_mol": 10.0,
        "overhead_o2_mol": 10.0,
        "external_o2_holdup_mol": 2.0,
        "capacity": FiniteCapacity(M_O2),
        "dt_hr": 1.0,
        "p_o2_Pa": 1.0e6,
        "k_relief_kg_hr_Pa": 1.0,
        "p_open_Pa": 1.0,
        "molar_mass_kg_mol": M_O2,
    }

    p2_3 = partition_melt_oxygen(**controls)
    accumulator_off = partition_melt_oxygen(
        **controls,
        accumulator_enabled=False,
        cistern_fill_kg=3.0,
        cavern_capacity_kg=4.0,
    )

    assert accumulator_off == p2_3
    assert accumulator_off.accumulated_mol == 0.0


def test_accumulator_precedes_relief_and_full_cistern_restores_relief():
    controls = {
        "bled_o2_mol": 10.0,
        "overhead_o2_mol": 10.0,
        "external_o2_holdup_mol": 0.0,
        "capacity": FiniteCapacity(M_O2),
        "dt_hr": 1.0,
        "p_o2_Pa": 1.0e6,
        "k_relief_kg_hr_Pa": 1.0,
        "p_open_Pa": 1.0,
        "molar_mass_kg_mol": M_O2,
        "accumulator_enabled": True,
        "cavern_capacity_kg": 5.0 * M_O2,
    }

    filling = partition_melt_oxygen(**controls, cistern_fill_kg=0.0)
    full = partition_melt_oxygen(
        **controls,
        cistern_fill_kg=5.0 * M_O2,
    )

    assert filling.admitted_mol == pytest.approx(1.0)
    assert filling.accumulated_mol == pytest.approx(5.0)
    assert filling.relieved_mol == pytest.approx(4.0)
    assert filling.held_mol == 0.0
    assert full.admitted_mol == pytest.approx(1.0)
    assert full.accumulated_mol == 0.0
    assert full.relieved_mol == pytest.approx(9.0)
    assert full.held_mol == 0.0


def test_relief_is_additional_to_zero_conductance_and_avoids_vessel_refusal():
    result = solve_capacity_shadow(
        pre_holdup_mol={"O2": 10.0},
        molar_mass_kg_mol={"O2": M_O2},
        flux_kg_hr_at_partials=lambda _partials: {},
        capacity=FiniteCapacity(M_O2),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=T_FOR_1000_PA_PER_MOL,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=0.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=M_O2 * 5.0e-4,
        p_open_Pa=9000.0,
        vessel_rating_Pa=9800.0,
    )

    assert isinstance(result, CapacityShadowResult)
    assert result.bled_species_mol["O2"] == pytest.approx(
        result.oxygen.relieved_mol
    )
    assert result.oxygen.external_mol == 0.0
    assert result.oxygen.admitted_mol == 0.0
    assert result.oxygen.relieved_mol > 0.0
    assert result.partial_pressures_Pa["O2"] < 9800.0
    assert result.mass_closure_error_pct <= 5.0e-12


def test_picard_source_uses_frozen_inventory_depletion_vector():
    sim = _real_capacity_sim()
    raw = EvaporationFlux(
        species_kg_hr={"Fe": 1000.0},
        alpha_authority_status_by_species={
            "Fe": "analytical_upper_bound",
        },
    )
    raw.update_totals()
    frozen_melt = sim.atom_ledger.kg_by_account("process.cleaned_melt")
    effective_rates = sim._analytic_evaporation_depletion_rates(
        raw.species_kg_hr,
        dt_hr=1.0,
        phase_scalar=1.0,
        cleaned_melt_kg=frozen_melt,
        available_o2_kg=0.0,
    )
    live_effective = sim._apply_analytic_evaporation_depletion(raw)

    assert 0.0 < effective_rates["Fe"] < raw.species_kg_hr["Fe"]
    assert live_effective.species_kg_hr == pytest.approx(effective_rates)
    assert live_effective.alpha_authority_status_by_species == {
        "Fe": "analytical_upper_bound",
    }

    fe_molar_mass = resolve_species_formula(
        "Fe", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    result = solve_capacity_shadow(
        pre_holdup_mol={},
        molar_mass_kg_mol={"Fe": fe_molar_mass},
        flux_kg_hr_at_partials=lambda _partials: dict(effective_rates),
        capacity=FiniteCapacity(1.0),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=T_FOR_1000_PA_PER_MOL,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=0.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=1.0e-30,
        p_open_Pa=1.0e9,
        vessel_rating_Pa=1.0e12,
    )

    assert isinstance(result, CapacityShadowResult)
    assert result.evaporation_flux_kg_hr == pytest.approx(effective_rates)
    assert result.mass_closure_error_pct <= 5.0e-12


def test_non_convergent_picard_returns_typed_refusal_without_last_iterate():
    result, _ = _o2_fixture(max_iterations=1)

    assert isinstance(result, CapacityShadowRefusal)
    assert not isinstance(result, CapacityShadowResult)
    assert result.reason == "picard_non_convergence"
    assert result.iterations == 1
    assert result.authoritative is False


@pytest.mark.parametrize("refusal_site", ("flux", "overhead_source"))
def test_evaporation_refusal_is_not_swallowed_by_shadow_callbacks(refusal_site):
    diagnostic = {
        "reason": "viscous_p_bulk_transport_out_of_domain",
        "evaporation_flux_status": "not_evaluated",
        "evaporation_flux_kg_hr": None,
    }

    def raise_refusal(_partials):
        raise EvaporationFluxRefusal(diagnostic["reason"], diagnostic)

    flux_callback = (
        raise_refusal if refusal_site == "flux" else lambda _partials: {"O2": 0.0}
    )
    overhead_callback = (
        raise_refusal
        if refusal_site == "overhead_source"
        else lambda _partials: {"O2": 0.0}
    )

    with pytest.raises(EvaporationFluxRefusal) as exc_info:
        solve_capacity_shadow(
            pre_holdup_mol={"O2": 0.0},
            molar_mass_kg_mol={"O2": M_O2},
            flux_kg_hr_at_partials=flux_callback,
            capacity=FiniteCapacity(0.032),
            head_bled_species_mol={},
            external_o2_holdup_mol=0.0,
            temperature_K=T_FOR_1000_PA_PER_MOL,
            volume_m3=1.0,
            dt_hr=1.0,
            bleed_conductance_kg_s=1.0,
            downstream_pressure_Pa=0.0,
            k_relief_kg_hr_Pa=1.0e-30,
            p_open_Pa=1.0e9,
            overhead_source_mol_hr_at_partials=overhead_callback,
            vessel_rating_Pa=1.0e12,
        )

    assert exc_info.value.reason == diagnostic["reason"]
    assert exc_info.value.diagnostic == diagnostic


def test_core_refusal_leaves_record_and_ledger_close_report_unchanged(
    monkeypatch,
):
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    payload = _load_yaml("thermal_train_params.yaml")
    payload["cold_train"]["runtime_enforcement"]["value"] = True
    cold_train = thermal_train_parameters_from_mapping(payload).cold_train
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (capacity_from_hardware(cold_train), cold_train),
    )
    equilibrium = sim._get_equilibrium()
    record_before = _canonical_bytes(asdict(sim.record))
    ledger_before = _canonical_bytes(sim.atom_ledger.close_report())

    monkeypatch.setattr(
        capacity_coupling,
        "solve_capacity_shadow",
        lambda **_kwargs: CapacityShadowRefusal(
            reason="picard_non_convergence",
            iterations=50,
        ),
    )
    result = sim._compute_capacity_coupling_shadow(equilibrium)

    assert isinstance(result, CapacityShadowRefusal)
    assert not isinstance(result, CapacityShadowResult)
    assert _canonical_bytes(asdict(sim.record)) == record_before
    assert _canonical_bytes(sim.atom_ledger.close_report()) == ledger_before


def test_real_finite_capacity_result_drives_live_short_session_once(monkeypatch):
    active = _real_capacity_sim()
    active.start_campaign(CampaignPhase.C0)
    _capacity, cold_train = active._cold_train_capacity_policy()
    monkeypatch.setattr(
        active,
        "_cold_train_capacity_policy",
        lambda: (FiniteCapacity(1.0e-8), cold_train),
    )
    active.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 1.0e-4},
        source="binding capacity integration fixture",
        material_origin="feedstock",
    )

    capacity = capacity_from_hardware(
        thermal_train_parameters_from_mapping().cold_train
    )
    assert isinstance(capacity, FiniteCapacity)

    shadow_results = []
    real_shadow = active._compute_capacity_coupling_shadow

    def observed_shadow(equilibrium):
        result = real_shadow(equilibrium)
        shadow_results.append(result)
        return result

    active._compute_capacity_coupling_shadow = observed_shadow
    active.step()

    assert len(shadow_results) == 1
    assert isinstance(shadow_results[0], CapacityShadowResult)
    assert sum(
        transition.reason == "overhead_bleed"
        for transition in active.atom_ledger.transitions
    ) == 1
    assert shadow_results[0].mass_closure_error_pct <= 5.0e-12


def test_all_zero_capacity_flux_preserves_and_routes_live_carrier_authority(
    monkeypatch,
):
    active = _real_capacity_sim()
    active.start_campaign(CampaignPhase.C0)
    _capacity, cold_train = active._cold_train_capacity_policy()
    monkeypatch.setattr(
        active,
        "_cold_train_capacity_policy",
        lambda: (FiniteCapacity(1.0e-8), cold_train),
    )
    active.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 1.0e-4},
        source="capacity carrier authority fixture",
        material_origin="feedstock",
    )

    rate_kg_hr = 0.0
    carrier_record = {
        "species_id": "Fe",
        "pressure": {"kind": "value", "pa": 1.0},
        "flux": {"kind": "eligible", "alpha_ref": "test:alpha"},
        "is_refused": False,
        "is_union_flux_eligible": True,
        "is_flux_active": True,
        "validation_status": "validated",
        "verdict_status": "authoritative",
        "certification_ceiling": "validated_point",
    }
    refused_na_record = {
        "species_id": "Na",
        "pressure": {"kind": "refusal", "code": "test_refusal"},
        "flux": {"kind": "refusal", "code": "test_refusal"},
        "is_refused": True,
        "is_union_flux_eligible": False,
        "is_flux_active": False,
        "validation_status": "modeled-PENDING",
        "verdict_status": "status_bearing_non_authoritative",
        "certification_ceiling": "never",
    }
    live_flux = EvaporationFlux(
        species_kg_hr={},
        carrier_authority_by_species={
            "Fe": carrier_record,
            "Na": refused_na_record,
        },
    )
    live_flux.update_totals()

    real_shadow = active._compute_capacity_coupling_shadow

    def solved_shadow(equilibrium):
        result = real_shadow(equilibrium)
        assert isinstance(result, CapacityShadowResult)
        return replace(
            result,
            evaporation_flux_kg_hr={},
        )

    monkeypatch.setattr(active, "_compute_capacity_coupling_shadow", solved_shadow)
    monkeypatch.setattr(
        active,
        "_calculate_evaporation",
        lambda equilibrium, **kwargs: live_flux,
    )
    monkeypatch.setattr(
        active,
        "_apply_analytic_evaporation_depletion",
        lambda flux: flux,
    )

    routed_fluxes = []
    real_route = active.condensation_model.route

    def observed_route(evap_flux, melt):
        routed_fluxes.append(evap_flux)
        return real_route(evap_flux, melt)

    monkeypatch.setattr(active.condensation_model, "route", observed_route)
    active.step()

    assert routed_fluxes
    routed = routed_fluxes[0]
    assert routed.species_kg_hr == {}
    assert routed.carrier_authority_by_species == {
        "Fe": carrier_record,
        "Na": refused_na_record,
    }
    assert active.condensation_model.last_sticking_alpha_provenance_notice[
        "vapour_carrier_authority_by_species"
    ] == {
        "Fe": carrier_record,
        "Na": refused_na_record,
    }
    assert active.condensation_model.last_condensation_authority_by_species[
        "Fe"
    ]["carrier_authority"] == carrier_record
    assert active.condensation_model.last_condensation_authority_by_species[
        "Na"
    ]["carrier_authority"] == refused_na_record
    assert (
        vapour_carrier_authority_status(
            routed.carrier_authority_by_species["Fe"],
            expected_species_id="Fe",
        )
        == VAPOUR_CARRIER_AUTHORITY_AUTHORITATIVE
    )


def test_default_runtime_policy_is_no_cold_train():
    sim = _real_capacity_sim()

    capacity, cold_train = sim._cold_train_capacity_policy()

    assert isinstance(capacity, NoColdTrain)
    assert capacity.reason == "runtime_enforcement_disabled"
    assert cold_train.runtime_enforcement is False


def test_missing_gamma_ideal_assertion_reaches_activity_seam(monkeypatch):
    coefficients = dict(melt_activity.MELT_OXIDE_ACTIVITY_COEFFICIENTS)
    coefficients.pop("K2O")
    monkeypatch.setattr(
        melt_activity,
        "MELT_OXIDE_ACTIVITY_COEFFICIENTS",
        coefficients,
    )

    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    # 1600 K is inside the K pressure-model domain but outside its 1500 K
    # activity-anchor domain. This isolates t-523: activity uncertainty must
    # remain numeric and flux-driving.
    sim.melt.temperature_C = 1600.0 - 273.15
    equilibrium = sim._get_equilibrium()
    effective_source = _pre_rg_effective_pressure_source(
        sim.vapor_pressures, equilibrium
    )

    provenance = sim._last_vapor_pressure_diagnostic[
        "vapor_pressure_numerator_provenance"
    ]["K"]
    assert provenance["melt_oxide_activity_model"] == (
        melt_activity.MELT_OXIDE_IDEAL_SOLUTION_MODEL
    )
    assert provenance["melt_oxide_activity_evidence_tier"] == (
        melt_activity.MELT_OXIDE_IDEAL_ASSERTION_TIER
    )
    assert "declared_ideal_solution_activity_assertion" in provenance[
        "melt_oxide_activity_warning"
    ]

    batch = sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=equilibrium.temperature_C + 273.15,
        effective_pressure_source=effective_source,
    )
    assert batch is not None
    answer = batch.channel("K")
    activity = answer.source_reaction_activity
    assert activity is not None
    assert activity.verdict is ActivityVerdictKind.STATUS_BEARING_VALUE
    assert activity.reason == "declared_ideal_solution_activity"
    assert activity.evidence_tier == (
        melt_activity.MELT_OXIDE_IDEAL_ASSERTION_TIER
    )
    assert "declared_ideal_solution_activity_assertion" in (activity.detail or "")
    assert activity.evidence_ref is None
    assert answer.is_flux_active


@pytest.mark.parametrize("temperature_K", [1800.0, 2000.0])
def test_flux_activity_operands_retain_every_pressure_carrier(temperature_K):
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = temperature_K - 273.15
    equilibrium = sim._get_equilibrium()
    diagnostic = sim._last_vapor_pressure_diagnostic

    controls, _ = sim._evaporation_flux_control_inputs(
        equilibrium,
        overhead_partials_Pa={},
        vapour_batch_flux_pressures_Pa={},
    )

    operands = controls["vapor_pressure_activities"]
    assert set(diagnostic["vapor_pressures_Pa"]) <= set(operands)
    assert operands == diagnostic["activities"]
    assert operands != equilibrium.activity_coefficients


def test_catalog_probe_is_activity_corrected_while_flux_source_stays_pre_rg():
    """RG-1 gateway: catalog P_eff class without a flux-value cutover.

    Each species' band is derived from its verifier-probed effective-vs-pure
    separation so every pure-component reversion is independently red.
    """

    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    equilibrium = sim._get_equilibrium()
    effective_source = _pre_rg_effective_pressure_source(
        sim.vapor_pressures, equilibrium
    )
    batch = sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=equilibrium.temperature_C + 273.15,
        effective_pressure_source=effective_source,
    )
    assert batch is not None

    verifier_probe_pa = {
        # MC-5 Ca (2026-08-07): catalog Pref retargeted TE Pref_GR -> JANAF
        # Pref_GF (live move +2.465 dex at this probe's 1873.15 K). The PIN
        # moves +3.096 dex (4.6e-9 -> 5.740e-6 Pa) because the assertion is
        # band-based (within_probe_band ~1.43 dex), so the old pin sat below
        # the old live value. SIGN CHECK: pressure UP, matching more high-T
        # Ca volatilisation. See docs-private/research/2026-08-07-mc5-ca-fix/report.md.
        # b-136 / t-559 posture rework 2026-08-08: Zhang CaTiO3 α remains
        # WITHDRAWN; channel stays live on Hertz-Knudsen ideal α=1.0 as an
        # explicit status-bearing upper bound (missing kinetic α ≠ missing
        # pressure). Ca pressure probe retained. docs-private/research/2026-08-08-rp3-b136/report.md.
        "Ca": 5.740329129667688e-6,
        # b-147 Mg (2026-08-07): TE gas_rail demoted; Pref_GF path. Live catalog
        # probe at 1873.15 K moves 7.3e-3 -> 0.09398 Pa (+1.110 dex). Pref_GF-GR
        # at this T is only +0.565 dex; the extra pin motion is band-based
        # (old pin sat below old live). SIGN CHECK: pressure UP.
        # docs-private/research/2026-08-07-mgfix/report.md
        "Mg": 0.09397692153837497,
        # Constant table gamma (no T*/T / pseudo-binary mid-range).
        # t-538: seam pin unchanged (pre-rg Builtin path); catalog OOR value
        # moves via physical 1/T tangent (see K branch below).
        "K": 0.469,
        "Al": 1.4e-8,
    }
    pure_component_probe_pa = {
        "Ca": 8.35e5,
        "Mg": 6.87e5,
        "K": 4.51e6,
        "Al": 110.2,
    }
    # Premise: the legacy probes give separation R_i = P_pure,i / P_eff,i.
    # Algebra: admit one tenth of that measured log separation,
    # b_i = log10(R_i) / 10. The band therefore admits ratios
    # [R_i^-0.1, R_i^0.1] around P_eff,i, not the full raw separation.
    # Catalog a=1 answers also pass through source-reaction/fO2 evaluation;
    # the isolated re-evaluations below prove none can fit its species' band.
    probe_band_decades = {
        species_id: abs(math.log10(pure_component_probe_pa[species_id] / probe_pa))
        / 10.0
        for species_id, probe_pa in verifier_probe_pa.items()
    }

    def within_probe_band(
        species_id: str, actual: float, reference: float
    ) -> bool:
        return (
            abs(math.log10(actual / reference))
            <= probe_band_decades[species_id]
        )

    temperature_K = equilibrium.temperature_C + 273.15
    original_equilibrium_activities = dict(equilibrium.activity_coefficients)
    original_activities = dict(sim._last_vapor_pressure_diagnostic["activities"])
    original_diagnostic_activities = dict(original_activities)
    assert original_equilibrium_activities == sim._last_high_t_melt_activity["selected_parent_activities"]
    assert original_equilibrium_activities != original_activities
    baseline_catalog_pa: dict[str, float] = {}
    activity_exponents: dict[str, float] = {}
    for species_id, probe_pa in verifier_probe_pa.items():
        answer = batch.channel(species_id)
        assert isinstance(answer.pressure, PressureValue)
        catalog_pa = float(answer.pressure.pa)
        baseline_catalog_pa[species_id] = catalog_pa
        seam_pa = effective_source.pressure_pa(species_id)
        assert seam_pa is not None and seam_pa > 0.0
        assert within_probe_band(species_id, float(seam_pa), probe_pa)
        if species_id == "K":
            # K fit domain ends at 1600 K; this probe is at 1873.15 K → OOR.
            # t-538: OOR value is the physical 1/T-tangent continuation of the
            # Antoine reference (C≠0), not attenuated linear-T slope invention.
            # SIGN CHECK: unit-a log10 Pref at 1873.15 K drops ~0.81 dex vs the
            # prior ×1.5 heating amplification (5.766 → 4.960). Activity-scaled
            # catalog pressure therefore moves DOWN toward the pre-rg seam
            # (~0.71 vs ~0.69 Pa) — they can sit in the same probe band now.
            # Separation catalog≠seam is no longer required; OOR status-bearing
            # mark + a=1 reversion scale (below) still prove the catalog path.
            assert answer.extra.get("out_of_range") is True
            assert answer.extra["status"] == OUT_OF_RANGE_STATUS
            assert answer.extra.get("acquisition_flag")
            assert answer.verdict_status == "status_bearing_non_authoritative"
            assert answer.certification_ceiling == "never"
            assert math.isfinite(catalog_pa) and 0.0 < catalog_pa
            assert catalog_pa < pure_component_probe_pa[species_id]
            # Physical OOR can land near the pre-rg seam; pin the live value
            # so a future regression that re-inflates heating OOR is visible.
            assert catalog_pa == pytest.approx(0.7101916366096568, rel=1.0e-9)
        else:
            assert not answer.extra.get("out_of_range", False)
            assert within_probe_band(species_id, catalog_pa, probe_pa)
            assert within_probe_band(species_id, catalog_pa, float(seam_pa))
        activity = answer.source_reaction_activity
        assert activity is not None
        assert activity.provider == "BuiltinVaporPressureProvider"
        if species_id == "K":
            # b-121: OOD table gamma is status-bearing (not POINT) while the
            # HEAD oodfix seam still publishes PressureValue + FluxEligible.
            assert (
                activity.verdict
                is ActivityVerdictKind.STATUS_BEARING_VALUE
            )
            assert activity.reason == "out_of_gamma_domain"
            assert answer.is_flux_active
        else:
            assert activity.verdict is ActivityVerdictKind.POINT
            assert activity.value == pytest.approx(
                original_activities[species_id], rel=0.0, abs=0.0
            )
        assert activity.evidence_ref is not None
        # Structured external-evidence provenance (INCOMING activity landing).
        assert "DOI " in activity.evidence_ref
        assert "_last_vapor_pressure_diagnostic" not in activity.evidence_ref
        evaluator = sim.vapour_rail_catalog.species[species_id].evaluator
        assert evaluator is not None
        activity_exponents[species_id] = evaluator.activity_exponent
    try:
        for species_id in verifier_probe_pa:
            # Mutate one species at a time from the same baseline. This proves
            # every activity=1 reversion independently leaves its own band.
            mutated_activities = dict(original_activities)
            mutated_activities[species_id] = 1.0
            equilibrium.activity_coefficients = dict(mutated_activities)
            sim._last_vapor_pressure_diagnostic["activities"] = dict(
                mutated_activities
            )
            a1_reversion_batch = sim._resolve_evaporation_vapour_batch(
                equilibrium,
                temperature_K=temperature_K,
                effective_pressure_source=effective_source,
            )
            assert a1_reversion_batch is not None
            a1_answer = a1_reversion_batch.channel(species_id)
            assert isinstance(a1_answer.pressure, PressureValue)
            assert a1_answer.source_reaction_activity is not None
            assert a1_answer.source_reaction_activity.value == pytest.approx(1.0)
            if batch.channel(species_id).extra.get("out_of_range", False):
                exponent = activity_exponents[species_id]
                assert exponent > 0.0
                expected_scale = (1.0 / original_activities[species_id]) ** exponent
                assert (
                    float(a1_answer.pressure.pa) / baseline_catalog_pa[species_id]
                ) == pytest.approx(expected_scale, rel=1.0e-12, abs=0.0)
            seam_pa = effective_source.pressure_pa(species_id)
            assert seam_pa is not None and seam_pa > 0.0
            assert not within_probe_band(
                species_id,
                float(a1_answer.pressure.pa),
                float(seam_pa),
            )
    finally:
        equilibrium.activity_coefficients = original_equilibrium_activities
        sim._last_vapor_pressure_diagnostic["activities"] = (
            original_diagnostic_activities
        )

    # Source-reaction oxygen fugacity is intrinsic melt fO2, not transport
    # headspace pO2. Varying only transport pO2 must leave catalog P_eff fixed.
    original_catalog_pa = {
        species_id: float(batch.channel(species_id).pressure.pa)
        for species_id in verifier_probe_pa
    }
    sim._last_vapor_pressure_diagnostic["pO2_bar"] = 1.0e-3
    transport_mutation_batch = sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=temperature_K,
        effective_pressure_source=effective_source,
    )
    assert transport_mutation_batch is not None
    for species_id, expected_pa in original_catalog_pa.items():
        assert transport_mutation_batch.channel(species_id).pressure.pa == (
            pytest.approx(expected_pa, rel=0.0, abs=0.0)
        )

    # The exact intrinsic fO2 used by the pressure solve is carried on that
    # solve's diagnostic. A later/mismatched EquilibriumResult.fO2_log must not
    # retune catalog P_eff while activities and the effective seam stay fixed.
    assert sim._last_vapor_pressure_diagnostic["source_reaction_fO2_bar"] > 0.0
    equilibrium_fO2_log = equilibrium.fO2_log
    equilibrium.fO2_log = float(equilibrium_fO2_log) + 6.0
    result_mutation_batch = sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=temperature_K,
        effective_pressure_source=effective_source,
    )
    equilibrium.fO2_log = equilibrium_fO2_log
    assert result_mutation_batch is not None
    for species_id, expected_pa in original_catalog_pa.items():
        assert result_mutation_batch.channel(species_id).pressure.pa == (
            pytest.approx(expected_pa, rel=0.0, abs=0.0)
        )

    # Golden-neutral cutover guard: batch channels own eligibility, but the
    # numeric operand still comes from the typed equilibrium-backend seam.
    flux_pressures, flux_report = flux_pressures_from_batch(
        batch,
        effective_pressure_source=effective_source,
    )
    assert flux_report["selection_source"] == (
        "equilibrium_backend_effective_pressure_pre_rg"
    )
    for species_id in verifier_probe_pa:
        assert flux_pressures[species_id] == pytest.approx(
            effective_source.pressure_pa(species_id), rel=0.0, abs=0.0
        )
        assert flux_report["selected_pressure_source_by_species"][species_id] == (
            effective_source.source_id
        )
    # b-145: TiO/TiO2_gas status-bearing OOR flux-eligible. b-136 posture
    # rework keeps them live via Hertz-Knudsen ideal α=1.0 upper bound after
    # Zhang cascade proxy withdrawal (refuse would zero Ti flux and negate
    # the observable effect of the b-145 pressure fix).
    for species_id in ("TiO", "TiO2_gas"):
        answer = batch.channel(species_id)
        assert isinstance(answer.pressure, PressureValue)
        assert isinstance(answer.flux, FluxEligible)
        assert answer.extra.get("out_of_range") is True
        assert answer.extra["status"] == OUT_OF_RANGE_STATUS
        assert answer.extra.get("acquisition_flag")
        assert answer.verdict_status == "status_bearing_non_authoritative"
        assert answer.certification_ceiling == "never"
        seam_pa = effective_source.pressure_pa(species_id)
        assert seam_pa is not None and math.isfinite(seam_pa) and seam_pa > 0.0
        assert flux_pressures[species_id] == pytest.approx(
            seam_pa, rel=0.0, abs=0.0
        )
        assert species_id in flux_report["extrapolated_flux_species"]
        assert flux_report["selected_pressure_source_by_species"][species_id] == (
            effective_source.source_id
        )
    assert batch.channel("K").extra.get("out_of_range") is True
    assert flux_pressures["K"] != pytest.approx(
        batch.channel("K").pressure.pa, rel=1.0e-6
    )
    assert "K" in flux_report["extrapolated_flux_species"]

    assert effective_source.source_id == (
        "equilibrium_backend_effective_pressure_pre_rg"
    )


def test_catalog_intrinsic_fo2_refuses_without_exact_solve_diagnostic():
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    equilibrium = sim._get_equilibrium()
    temperature_K = equilibrium.temperature_C + 273.15
    diagnostic = dict(sim._last_vapor_pressure_diagnostic)
    assert diagnostic["pO2_bar"] > 0.0
    assert math.isfinite(float(equilibrium.fO2_log))

    # Keep valid transport and EquilibriumResult redox values available. The
    # catalog must still refuse when the exact intrinsic fO2 carried by the
    # pressure solve is absent or malformed; neither is an admissible fallback.
    for mutation in ("missing", "malformed"):
        mutated_diagnostic = dict(diagnostic)
        if mutation == "missing":
            mutated_diagnostic.pop("source_reaction_fO2_bar")
        else:
            mutated_diagnostic["source_reaction_fO2_bar"] = "bogus"
        sim._last_vapor_pressure_diagnostic = mutated_diagnostic
        batch = sim._resolve_evaporation_vapour_batch(
            equilibrium,
            temperature_K=temperature_K,
            effective_pressure_source=EffectivePressureSource(
                "test_no_flux_activation",
                {},
            ),
        )
        assert batch is not None
        for species_id in ("Ca", "Mg", "K", "Al"):
            answer = batch.channel(species_id)
            assert isinstance(answer.pressure, PressureRefusal)
            assert "intrinsic_melt oxygen fugacity" in (
                answer.extra.get("detail") or ""
            )


def test_default_off_preserves_hot_fe_redox_split_head_result(monkeypatch):
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    monkeypatch.setattr(
        sim,
        "_compute_capacity_coupling_shadow",
        lambda _equilibrium: (_ for _ in ()).throw(
            AssertionError("default-off live path must not compute capacity")
        ),
    )

    snapshot = sim.step()
    # d-252's 1 mbar Ar cover and 200 C/h ramp move this first step from
    # 1600 C to 1400 C. The old hard-vacuum step was 1.39e6% over transport
    # capacity; the new Ar-film diffusion-limited value is 2.47%.
    # Physics seq 1003 classifies Ti below the 1e-12 kg/hr negligibility
    # threshold here, so it is absent from snapshot flux/status and is a
    # legitimate no-flux carrier for this pin.
    ceiling_flux_species = {"Ca", "CaO_gas", "TiO", "TiO2_gas"}
    from tests.studio_pin_support import capacity_pin_values

    pins = capacity_pin_values(snapshot)
    # 5a7121241, brought in by bef9af1e7 (last old first-parent: 4b6555249),
    # executes flagged broad-proxy alphas instead of the unmeasured fallback
    # of 1. At this 1400 C step CrO and AlO equilibrium pressure, activity,
    # and pO2 are unchanged. The executed alphas are the declared proxies:
    # CrO 0.9 and AlO 0.3. Series flux is J = dP / (r_interface + r_gas),
    # with r_interface = 1/(alpha * k_HK) and r_gas independent of alpha
    # (engines/builtin/evaporation_flux.py), so the rates do not scale by
    # those alpha ratios. Saturation and melt mass move with the two rates.
    assert pins["non_phosphorus_kg_hr"] == pytest.approx(
        0.06423669542196676, rel=1e-12, abs=1e-12
    )
    assert ceiling_flux_species <= set(
        snapshot.evap_flux.alpha_authority_status_by_species
    )
    assert {
        snapshot.evap_flux.alpha_authority_status_by_species[species]
        for species in ceiling_flux_species
    } == {"analytical_upper_bound"}
    assert sim._alpha_authority_status_by_species_engaged == (
        snapshot.evap_flux.alpha_authority_status_by_species
    )

    assert sim._equipment is not None
    assert snapshot.overhead.initial_throat_area_m2 == pytest.approx(
        sim._equipment.pipe.initial_throat_area_m2
    )

    assert tuple(
        pins[key]
        for key in (
            "hour",
            "temperature_C",
            "total_kg_hr",
            "transport_saturation_pct",
            "melt_mass_kg",
        )
    ) == pytest.approx(
        # Same proxy-alpha step as the non-phosphorus pin above. Hour and
        # temperature stay on the d-252 1 mbar Ar, 200 C/h ramp.
        (
            1,
            1400.0,
            0.06423669542196676,
            2.4701307118784706,
            998.7072055627636,
        ),
        rel=1.0e-12,
        abs=1.0e-12,
    )
    # The split adds collector and trap captures after their evaporations.
    # b1113 also condenses Ca, Cr, and Mn on cold stages. The freeze-out
    # cartridge then swings each trap deposit: solids leave the alkali
    # product, and liquid Na and K return to the train. A Ca residual
    # still reaches terminal offgas; the assert below keeps that.
    transition_reasons = tuple(
        transition.reason for transition in sim.atom_ledger.transitions
    )
    expected_transition_order = (
        "evaporate_SiO",
        "condense_SiO",
        "alkali cartridge accumulates this hour of condensate",
        "melt-out leaves this solid off the alkali product",
        "alkali cartridge accumulates this hour of condensate",
        "melt-out leaves this solid off the alkali product",
        "evaporate_Mg",
        "condense_Mg",
        "alkali cartridge accumulates this hour of condensate",
        "melt-out leaves this solid off the alkali product",
        "evaporate_Fe",
        "condense_Fe",
        "alkali cartridge accumulates this hour of condensate",
        "melt-out leaves this solid off the alkali product",
        "evaporate_Na",
        "condense_Na",
        "alkali cartridge accumulates this hour of condensate",
        "melt-out returns liquid alkali to the train",
        "evaporate_K",
        "condense_K",
        "alkali cartridge accumulates this hour of condensate",
        "melt-out returns liquid alkali to the train",
        "evaporate_AlO",
        "evaporate_Ca",
        "condense_Ca",
        "evaporate_CaO_gas",
        "evaporate_Cr",
        "condense_Cr",
        "alkali cartridge accumulates this hour of condensate",
        "melt-out leaves this solid off the alkali product",
        "evaporate_CrO",
        "evaporate_K2",
        "evaporate_K2O_gas",
        "evaporate_MgO_gas",
        "evaporate_Mn",
        "condense_Mn",
        "alkali cartridge accumulates this hour of condensate",
        "melt-out leaves this solid off the alkali product",
        "evaporate_Na2",
        "evaporate_Na2O_gas",
        "evaporate_SiO2_gas",
        "evaporate_TiO",
        "evaporate_TiO2_gas",
        "fe_redox_respeciation",
        "overhead_bleed",
    )
    # d-402 adds accounted wall holdup for reactive vapours. The established
    # evaporation/condensation order remains intact around those transitions.
    actual_order = iter(transition_reasons)
    assert all(
        any(actual == expected for actual in actual_order)
        for expected in expected_transition_order
    )
    assert "retain_condensation_holdup_AlO" in transition_reasons
    assert sim.atom_ledger.project_account_kg("terminal.offgas").get(
        "Ca", 0.0
    ) > 0.0
    # d-237 retains phosphorus in C0, omitting these carriers from the ledger.
    assert {"evaporate_P2", "evaporate_PO", "evaporate_PO2"}.isdisjoint(
        transition.reason for transition in sim.atom_ledger.transitions
    )
    assert "Ti" not in snapshot.evap_flux.species_kg_hr
    assert "Ti" not in snapshot.evap_flux.alpha_authority_status_by_species
    assert snapshot.mass_balance_error_pct <= 5.0e-12


def test_default_off_c0_pipe_cap_preserves_hot_fe_redox_split_head_result(monkeypatch):
    sim = _real_capacity_sim()
    sim.campaign_mgr.campaigns["C0"]["p_total_mbar_default"] = 0.0
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    monkeypatch.setattr(
        sim,
        "_compute_capacity_coupling_shadow",
        lambda _equilibrium: (_ for _ in ()).throw(
            AssertionError("default-off live path must not compute the capacity shadow")
        ),
    )

    phosphorus_carriers = _STAGE0_PHOSPHORUS_VAPOUR_CARRIERS
    p2o5_source_kg = sim.atom_ledger.kg_by_account("process.cleaned_melt")["P2O5"]
    p2o5_projection_kg = sim.melt.composition_kg["P2O5"]
    source_before_kg = float(
        sim.atom_ledger.total_kg_by_account("process.cleaned_melt")
    )
    transport_calls = _record_transport_estimates(sim)

    snapshot = sim.step()

    # Default-off skips the optional shadow. This step still hits the
    # mandatory C0 pipe cap: one estimate is over capacity, and the
    # admitted release matches that estimate within the flux floor.
    binding = [
        call
        for call in transport_calls
        if call["demand_kg_hr"] > call["capacity_kg_hr"]
    ]
    assert len(binding) == 1
    demand_kg_hr = binding[0]["demand_kg_hr"]
    capacity_kg_hr = binding[0]["capacity_kg_hr"]
    demand_species = binding[0]["species_kg_hr"]
    cap_record = sim._last_subsolidus_bakeout["transport_cap"]
    assert cap_record["demand_kg_hr"] == pytest.approx(
        demand_kg_hr, rel=0.0, abs=0.0
    )
    assert cap_record["capacity_kg_hr"] == pytest.approx(
        capacity_kg_hr, rel=0.0, abs=0.0
    )
    assert cap_record["scale"] == pytest.approx(
        capacity_kg_hr / demand_kg_hr, rel=0.0, abs=0.0
    )
    released_kg_hr = snapshot.evap_flux.total_kg_hr
    released_species = {
        str(species): float(rate)
        for species, rate in snapshot.evap_flux.species_kg_hr.items()
        if float(rate) > 0.0
    }
    assert abs(released_kg_hr - capacity_kg_hr) <= _NONTRIVIAL_FLUX_KG_HR
    assert demand_kg_hr - capacity_kg_hr > _NONTRIVIAL_FLUX_KG_HR

    def _committed_vapor_kg(species: str) -> float:
        credited = 0.0
        for transition in sim.atom_ledger.transitions:
            if transition.reason != f"evaporate_{species}":
                continue
            for lot in transition.credits:
                credited += float(lot.species_kg.get(species, 0.0))
        return credited

    retained_total_kg = 0.0
    for species in set(demand_species) | set(released_species):
        demand_i = demand_species.get(species, 0.0)
        released_i = released_species.get(species, 0.0)
        committed_i = _committed_vapor_kg(species)
        retained_i = demand_i - committed_i
        assert abs(committed_i - released_i) <= _NONTRIVIAL_FLUX_KG_HR
        assert abs(retained_i - (demand_i - released_i)) <= _NONTRIVIAL_FLUX_KG_HR
        retained_total_kg += retained_i
    assert abs(
        retained_total_kg - (demand_kg_hr - released_kg_hr)
    ) <= _NONTRIVIAL_FLUX_KG_HR
    # Overhead bleed credits O2 after the cap. That credit is not in the
    # admitted vapour flux, and it is the only extra cleaned-melt debit.
    bleed_o2_kg = 0.0
    for transition in sim.atom_ledger.transitions:
        if transition.reason != "overhead_bleed":
            continue
        for lot in transition.credits:
            bleed_o2_kg += float(lot.species_kg.get(OXYGEN_SPECIES, 0.0))
    source_after_kg = float(
        sim.atom_ledger.total_kg_by_account("process.cleaned_melt")
    )
    assert abs(snapshot.melt_mass_kg - source_after_kg) <= _NONTRIVIAL_FLUX_KG_HR
    assert abs(
        (source_before_kg - source_after_kg) - (released_kg_hr + bleed_o2_kg)
    ) <= _NONTRIVIAL_FLUX_KG_HR
    assert sim._subsolidus_pending_release is None
    assert snapshot.mass_balance_error_pct <= 5.0e-12
    assert snapshot.overhead.transport_saturation_pct == pytest.approx(
        100.0,
        rel=0.0,
        abs=100.0 * _NONTRIVIAL_FLUX_KG_HR / capacity_kg_hr,
    )
    assert snapshot.hour == 1

    assert set(phosphorus_carriers).isdisjoint(snapshot.evap_flux.species_kg_hr)
    reasons = {
        transition.reason for transition in sim.atom_ledger.transitions
    }
    assert {
        f"evaporate_{carrier}" for carrier in phosphorus_carriers
    }.isdisjoint(reasons)
    assert {
        f"condense_{carrier}" for carrier in phosphorus_carriers
    }.isdisjoint(reasons)
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt")["P2O5"] == (
        p2o5_source_kg
    )
    assert sim.melt.composition_kg["P2O5"] == p2o5_projection_kg
    for carrier in phosphorus_carriers:
        authority = sim.condensation_model.last_condensation_authority_by_species.get(
            carrier
        )
        if authority is None:
            continue
        assert authority.get("mass_disposition") != "declared_cleanup_offgas"
        evidence = authority.get("routing_authorization") or {}
        assert evidence.get("stage") != "stage0_p_carriers"
        assert authority.get("remaining_mass_kg_hr") in (None, 0.0)
    assert "evaporate_Fe" in reasons
    assert "condense_Fe" in reasons
    assert "fe_redox_respeciation" in reasons

    assert sim._equipment is not None
    assert snapshot.overhead.initial_throat_area_m2 == pytest.approx(
        sim._equipment.pipe.initial_throat_area_m2
    )

    payload = _load_yaml("thermal_train_params.yaml")
    payload["cold_train"]["runtime_enforcement"]["value"] = True
    params = thermal_train_parameters_from_mapping(payload)
    monkeypatch.setattr(
        "simulator.thermal_train.thermal_train_parameters_from_mapping",
        lambda: params,
    )
    enforced = _real_capacity_sim()
    enforced.start_campaign(CampaignPhase.C0)
    enforced.melt.temperature_C = 1600.0
    calls = []

    def finite_capacity_engaged(_equilibrium):
        calls.append(True)
        return CapacityShadowRefusal("finite_capacity_engaged", 0)

    monkeypatch.setattr(
        enforced,
        "_compute_capacity_coupling_shadow",
        finite_capacity_engaged,
    )
    with pytest.raises(CapacityCouplingRefusalError, match="finite_capacity_engaged"):
        enforced.step()
    assert calls == [True]


def test_default_off_c0_cover_does_not_apply_pipe_cap():
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    assert sim.melt.p_total_mbar == 1.0
    assert sim.melt.background_gas_species == "Ar"
    sim.melt.temperature_C = 1600.0
    transport_calls = _record_transport_estimates(sim)

    snapshot = sim.step()

    assert transport_calls
    owner_demand_kg_hr = transport_calls[0]["demand_kg_hr"]
    for owner_result in transport_calls:
        assert owner_result["demand_kg_hr"] == pytest.approx(owner_demand_kg_hr)
        assert owner_result["demand_kg_hr"] < owner_result["capacity_kg_hr"]
    bakeout = getattr(sim, "_last_subsolidus_bakeout", None) or {}
    assert "transport_cap" not in bakeout
    assert snapshot.evap_flux.total_kg_hr == pytest.approx(
        owner_demand_kg_hr
    )


def test_hot_non_c0_campaign_keeps_analytical_ceiling_flux_species():
    """Ca/Ti ceiling flux stays live on a hot campaign the C0 cap does not scale."""
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C4)
    sim.melt.temperature_C = 1600.0

    snapshot = sim.step()

    ceiling_flux_species = {"Ca", "CaO_gas", "Ti", "TiO", "TiO2_gas"}
    status = snapshot.evap_flux.alpha_authority_status_by_species
    assert ceiling_flux_species <= set(status)
    assert {
        status[species] for species in ceiling_flux_species
    } == {ANALYTICAL_UPPER_BOUND_ALPHA_STATUS}
    assert sim._alpha_authority_status_by_species_engaged == status
    assert sim.melt.campaign == CampaignPhase.C4
    bakeout = getattr(sim, "_last_subsolidus_bakeout", None) or {}
    assert "transport_cap" not in bakeout
    assert snapshot.overhead.transport_saturation_pct > 100.0

    reasons = [transition.reason for transition in sim.atom_ledger.transitions]
    reason_set = set(reasons)
    for required in (
        "evaporate_Ca",
        "evaporate_CaO_gas",
        "evaporate_Ti",
        "evaporate_TiO",
        "evaporate_TiO2_gas",
        "condense_Ca",
    ):
        assert required in reason_set, required

    # The old single-Stage-4 condensate roster does not describe the split:
    # read the production route's stage inventories for the designated alkalis
    # and preserve unassigned oxide vapors in the terminal offgas projection.
    stages = {stage.stage_key: stage for stage in sim.train.stages}
    na_collector = stages[NA_COLLECTOR_STAGE_KEY].collected_kg
    k_trap = stages[K_TRAP_STAGE_KEY].collected_kg
    assert na_collector["Na"] > 0.0
    assert na_collector["Mg"] > 0.0
    assert k_trap["K"] > 0.0
    offgas = sim.atom_ledger.project_account_kg("terminal.offgas")
    for species in (
        "Al2O",
        "AlO",
        "CaO_gas",
        "CrO",
        "MgO_gas",
        "SiO2_gas",
        "TiO",
        "TiO2_gas",
    ):
        assert offgas.get(species, 0.0) > 0.0, species


def test_unscaled_c0_commits_reversible_si_condensation():
    """Vacuum and configured throat geometry admit reversible Si condensation."""
    sim = _real_capacity_sim()
    sim.campaign_mgr.campaigns["C0"]["p_total_mbar_default"] = 0.0
    sim.campaign_mgr.campaigns["C0"]["condenser_geometry"] = {
        "initial_throat_area_m2": 10.0
    }
    sim.start_campaign(CampaignPhase.C0)
    assert sim.melt.p_total_mbar == 0.0
    assert (
        sim._overhead_condenser_geometry_config["initial_throat_area_m2"]
        == 10.0
    )
    sim.melt.temperature_C = 1600.0
    transport_calls = _record_transport_estimates(sim)

    snapshot = sim.step()

    assert transport_calls
    owner_demand_kg_hr = transport_calls[0]["demand_kg_hr"]
    assert all(
        call["demand_kg_hr"] <= call["capacity_kg_hr"]
        for call in transport_calls
    )
    assert snapshot.evap_flux.total_kg_hr == pytest.approx(
        owner_demand_kg_hr
    )
    bakeout = getattr(sim, "_last_subsolidus_bakeout", None) or {}
    assert "transport_cap" not in bakeout
    assert "condense_Si" in {
        transition.reason for transition in sim.atom_ledger.transitions
    }
    assert sim.melt.campaign == CampaignPhase.C0


def test_explicit_runtime_enforcement_uses_configured_finite_capacity(monkeypatch):
    payload = _load_yaml("thermal_train_params.yaml")
    payload["cold_train"]["runtime_enforcement"]["value"] = True
    params = thermal_train_parameters_from_mapping(payload)
    monkeypatch.setattr(
        "simulator.thermal_train.thermal_train_parameters_from_mapping",
        lambda: params,
    )
    sim = _real_capacity_sim()

    capacity, cold_train = sim._cold_train_capacity_policy()

    assert isinstance(capacity, FiniteCapacity)
    assert capacity.value_kg_hr == pytest.approx(9.856)
    assert cold_train.runtime_enforcement is True


def test_live_overhead_bleed_routes_binding_capacity_surge_to_accumulator(
    monkeypatch,
):
    sim = _real_capacity_sim()
    o2_molar_mass = resolve_species_formula(
        "O2", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    cold_train = SimpleNamespace(
        accumulator_enabled=True,
        relief={
            "k_relief_kg_hr_Pa": 1.0e-3,
            "p_open_Pa": 800000.0,
            "vessel_rating_Pa": 1000000.0,
        },
    )
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (FiniteCapacity(o2_molar_mass), cold_train),
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 10.0 * o2_molar_mass},
        source="live accumulator surge fixture",
        material_origin="feedstock",
    )

    result = sim._dispatch_overhead_bleed(
        force_drain_all=True,
    )
    diagnostic = dict(result.diagnostic or {})

    assert diagnostic["o2_admitted_mol"] == pytest.approx(1.0)
    assert diagnostic["o2_accumulated_mol"] == pytest.approx(9.0)
    assert diagnostic["o2_relieved_mol"] == 0.0
    assert sim.atom_ledger.mol_by_account(
        "reservoir.oxygen_cistern_liquid_inventory"
    )["O2"] == pytest.approx(9.0)


def test_live_and_picard_share_evaporation_control_construction(monkeypatch):
    sim = _real_capacity_sim()
    sim.melt.temperature_C = 1600.0
    sim.melt.p_total_mbar = 5.0
    sim.overhead.pressure_mbar = 12.0
    equilibrium = SimpleNamespace(
        vapor_pressures_Pa={"Fe": 100.0},
        vapor_pressures_source={},
        activity_coefficients={},
        liquid_fraction=1.0,
        diagnostics={},
    )
    sim._last_vapor_pressure_diagnostic = {}
    partials = {"Fe": 100.0, "N2": 200.0}
    effective_pressure_source = _pre_rg_effective_pressure_source(
        sim.vapor_pressures,
        equilibrium,
    )
    vapour_batch = sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=sim.melt.temperature_C + 273.15,
        effective_pressure_source=effective_pressure_source,
    )
    batch_pressures, batch_overlay = flux_pressures_from_batch(
        vapour_batch,
        effective_pressure_source=effective_pressure_source,
    )
    batch_overlay.update(
        compare_live_shadow_to_batch_flux(
            batch=vapour_batch,
            live_pressures_Pa=None,
            batch_flux_pressures_Pa=batch_pressures,
        )
    )
    expected_partials = dict(partials)
    for species in batch_pressures:
        expected_partials.setdefault(species, 0.0)
    expected, _ = sim._evaporation_flux_control_inputs(
        equilibrium,
        overhead_partials_Pa=expected_partials,
        overhead_pressure_pa=sim._evaporation_overhead_total_pressure_Pa(
            expected_partials
        ),
        vapour_batch_flux_pressures_Pa=batch_pressures,
        vapour_batch_report=serialize_vapour_batch(vapour_batch),
        vapour_batch_flux_overlay=batch_overlay,
    )
    monkeypatch.setattr(
        sim,
        "_resolve_evaporation_vapour_batch",
        lambda _equilibrium, *, temperature_K, effective_pressure_source: vapour_batch,
    )
    captured = []

    def capture_dispatch(_intent, *, control_inputs, **_kwargs):
        captured.append(control_inputs)
        return SimpleNamespace(
            status="ok",
            diagnostic={"evaporation_flux_kg_hr": {}},
        )

    monkeypatch.setattr(sim, "_dispatch_only", capture_dispatch)
    sim._calculate_evaporation(
        equilibrium,
        overhead_partials_override_Pa=partials,
    )

    assert captured == [expected]
    assert "vapour_batch_flux_pressures_Pa" in captured[0]
    assert captured[0]["vapour_batch_flux_pressures_Pa"] == batch_pressures
    assert captured[0]["overhead_pressure_pa"] == pytest.approx(500.0)


def test_no_cold_train_solves_end_partial_from_pipe_geometry():
    # The old early return handed head_bled back and never called the
    # flux. With a pipe, the end partial is the quasi-steady root.
    calls = {"n": 0}

    def flux(_partials):
        calls["n"] += 1
        return {"O2": _SOURCE_02_KG_H}

    result = _commit_o2(
        _STRANDED_O2_KG,
        _SOURCE_02_KG_H,
        1.0,
        flux=flux,
        head_bled={"O2": 1.25},
    )

    assert isinstance(result, CapacityShadowResult)
    assert calls["n"] > 0
    assert result.iterations != 0
    assert result.authoritative is False
    assert result.bled_species_mol["O2"] != pytest.approx(1.25, rel=1.0e-6)
    assert result.partial_pressures_Pa["O2"] == pytest.approx(
        _ROOT_02_PA,
        abs=5.0e-9,
    )
    assert result.partial_pressures_Pa["O2"] != pytest.approx(20.0, abs=1.0)


def test_invalid_capacity_tag_is_typed_refusal_before_flux_arithmetic():
    result = solve_capacity_shadow(
        pre_holdup_mol={"O2": 1.0},
        molar_mass_kg_mol={"O2": M_O2},
        flux_kg_hr_at_partials=lambda _: (_ for _ in ()).throw(
            AssertionError("flux arithmetic must not run")
        ),
        capacity=SimpleNamespace(value_kg_hr=math.nan),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=300.0,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=1.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=0.0,
        p_open_Pa=1.0,
    )

    assert isinstance(result, CapacityShadowRefusal)
    assert not isinstance(result, CapacityShadowResult)
    assert result.reason == (
        "cold_train_capacity must be NoColdTrain or FiniteCapacity"
    )


@pytest.mark.parametrize("value", [math.nan, math.inf, 0.0, -1.0])
def test_malformed_finite_capacity_is_refused_before_arithmetic(value):
    capacity = object.__new__(FiniteCapacity)
    object.__setattr__(capacity, "value_kg_hr", value)
    object.__setattr__(capacity, "p_ref_Pa", None)
    object.__setattr__(capacity, "T_ref_K", None)

    result = solve_capacity_shadow(
        pre_holdup_mol={"O2": 1.0},
        molar_mass_kg_mol={"O2": M_O2},
        flux_kg_hr_at_partials=lambda _: (_ for _ in ()).throw(
            AssertionError("flux arithmetic must not run")
        ),
        capacity=capacity,
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=300.0,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=1.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=0.0,
        p_open_Pa=1.0,
    )

    assert isinstance(result, CapacityShadowRefusal)
    assert not isinstance(result, CapacityShadowResult)
    assert result.reason == "cold_train_capacity must be finite and positive"


def test_combined_saturation_uses_max_and_pipe_wins_exact_tie():
    tie = combined_saturation(
        total_evaporation_kg_hr=2.0,
        oxygen_evaporation_kg_hr=1.0,
        pipe_capacity_kg_hr=2.0,
        capacity=FiniteCapacity(1.0),
    )
    oxygen = combined_saturation(
        total_evaporation_kg_hr=1.0,
        oxygen_evaporation_kg_hr=2.0,
        pipe_capacity_kg_hr=2.0,
        capacity=FiniteCapacity(1.0),
    )

    assert tie.combined == 1.0
    assert tie.binding_cause == "pipe"
    assert oxygen.combined == 2.0
    assert oxygen.binding_cause == "oxygen"


@pytest.mark.parametrize(
    ("total_rate", "oxygen_rate", "pipe_capacity", "oxygen_capacity", "label"),
    [
        (2.0, 0.1, 1.0, 1.0, "pipe saturated"),
        (1.0, 2.0, 10.0, 1.0, "O2 capacity saturated"),
        (2.0, 2.0, 1.0, 1.0, "pipe saturated"),
    ],
    ids=["pipe-win", "oxygen-win", "exact-tie-pipe"],
)
def test_loop3_renders_live_binding_cause(
    total_rate,
    oxygen_rate,
    pipe_capacity,
    oxygen_capacity,
    label,
):
    saturation = combined_saturation(
        total_evaporation_kg_hr=total_rate,
        oxygen_evaporation_kg_hr=oxygen_rate,
        pipe_capacity_kg_hr=pipe_capacity,
        capacity=FiniteCapacity(oxygen_capacity),
    )
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = SimpleNamespace(
        temperature_C=1500.0,
        campaign=CampaignPhase.C2A,
        campaign_hour=0.0,
    )
    sim.overhead = SimpleNamespace(
        transport_saturation_pct=saturation.combined * 100.0,
        transport_binding_cause=saturation.binding_cause,
        turbine_limited=False,
    )
    sim.campaign_mgr = SimpleNamespace(
        get_temp_target=lambda *_args: (1600.0, 100.0)
    )

    sim._update_temperature()

    assert label in sim._last_throttle_reason


def test_total_pressure_vessel_rating_refuses_after_convergence():
    flux_calls = 0

    def hk_flux(_partials):
        nonlocal flux_calls
        flux_calls += 1
        return {"O2": 0.032}

    result = solve_capacity_shadow(
        pre_holdup_mol={"O2": 1.0, "N2": 2.0},
        molar_mass_kg_mol={"O2": M_O2, "N2": M_N2},
        flux_kg_hr_at_partials=hk_flux,
        capacity=FiniteCapacity(0.032),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=T_FOR_1000_PA_PER_MOL,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=0.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=1.0e-12,
        p_open_Pa=1.0,
        vessel_rating_Pa=1500.0,
    )

    assert isinstance(result, CapacityShadowRefusal)
    assert result.reason.startswith("vessel_total_pressure_exceeds_rating:")
    assert result.iterations > 0
    assert flux_calls == result.iterations


def test_zero_bleed_saturation_boundary_refuses_on_picard_non_convergence():
    result = solve_capacity_shadow(
        pre_holdup_mol={},
        molar_mass_kg_mol={"N2": M_N2},
        flux_kg_hr_at_partials=lambda partials: {
            "N2": 1.0e-9 if partials["N2"] < 1.0e-6 else 0.0
        },
        capacity=FiniteCapacity(1.0),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=T_FOR_1000_PA_PER_MOL,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=1.0,
        downstream_pressure_Pa=100.0,
        k_relief_kg_hr_Pa=1.0e-30,
        p_open_Pa=1.0e9,
        vessel_rating_Pa=1.0e12,
        max_iterations=3,
    )

    assert isinstance(result, CapacityShadowRefusal)
    assert result.reason == "picard_non_convergence"
    assert result.iterations == 3


def test_live_capacity_refusal_precedes_overhead_bleed_commit(monkeypatch):
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    _capacity, cold_train = sim._cold_train_capacity_policy()
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (FiniteCapacity(1.0e-8), cold_train),
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 1.0e-4},
        source="binding capacity refusal fixture",
        material_origin="feedstock",
    )
    before_bleeds = sum(
        transition.reason == "overhead_bleed"
        for transition in sim.atom_ledger.transitions
    )
    monkeypatch.setattr(
        capacity_coupling,
        "solve_capacity_shadow",
        lambda **_kwargs: CapacityShadowRefusal(
            reason="vessel_total_pressure_exceeds_rating:2000>1500",
            iterations=3,
        ),
    )

    with pytest.raises(CapacityCouplingRefusalError) as exc_info:
        sim.step()

    assert exc_info.value.refusal.iterations == 3
    assert sum(
        transition.reason == "overhead_bleed"
        for transition in sim.atom_ledger.transitions
    ) == before_bleeds


@pytest.mark.parametrize(
    ("species", "rate_kg_hr", "capacity_kg_hr", "rating_Pa"),
    [
        ("O2", 2.0e-6, 1.0e-6, 1.0e9),
        ("SiO", 1.0e-3, 1.0e-6, 1.0e9),
        ("N2", 2.8e-5, 1.0, 1.0e-3),
    ],
)
def test_current_hour_source_cannot_bypass_capacity_or_vessel_refusal(
    monkeypatch,
    species,
    rate_kg_hr,
    capacity_kg_hr,
    rating_Pa,
):
    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    cold_train = SimpleNamespace(relief={
        "k_relief_kg_hr_Pa": 1.0e-3,
        "p_open_Pa": min(1.0, rating_Pa / 2.0),
        "vessel_rating_Pa": rating_Pa,
    })
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (FiniteCapacity(capacity_kg_hr), cold_train),
    )
    flux = EvaporationFlux(species_kg_hr={species: rate_kg_hr})
    flux.update_totals()
    monkeypatch.setattr(sim, "_calculate_evaporation", lambda *_a, **_k: flux)
    calls = []

    def refused(**kwargs):
        calls.append(kwargs)
        return CapacityShadowRefusal("vessel_or_capacity_refusal", 2)

    monkeypatch.setattr(capacity_coupling, "solve_capacity_shadow", refused)

    with pytest.raises(CapacityCouplingRefusalError):
        sim.step()

    assert len(calls) == 1


@pytest.mark.parametrize("downstream_pressure_Pa", [500.0, 2000.0])
def test_pipe_saturation_uses_executable_pressure_ratio_capacity(
    downstream_pressure_Pa,
):
    conductance = 1.0e-8
    result = solve_capacity_shadow(
        pre_holdup_mol={},
        molar_mass_kg_mol={"N2": M_N2},
        flux_kg_hr_at_partials=lambda _partials: {"N2": M_N2},
        capacity=FiniteCapacity(1.0),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=T_FOR_1000_PA_PER_MOL,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=conductance,
        downstream_pressure_Pa=downstream_pressure_Pa,
        k_relief_kg_hr_Pa=1.0e-30,
        p_open_Pa=1.0e9,
        vessel_rating_Pa=1.0e12,
    )

    assert isinstance(result, CapacityShadowResult)
    total_pressure_Pa = sum(result.partial_pressures_Pa.values())
    fraction = compressible_pressure_capacity_fraction(
        total_pressure_Pa / 100000.0,
        downstream_pressure_Pa / 100000.0,
    )
    executable_capacity = conductance * fraction * 3600.0
    expected = (
        M_N2 / executable_capacity if executable_capacity > 0.0 else math.inf
    )
    assert result.saturation.pipe == pytest.approx(expected)
    provider_bled = BuiltinOverheadBleedProvider._bled_species_mol(
        {"N2": 1.0},
        total_mol=1.0,
        total_kg=M_N2,
        controls={
            "bleed_conductance_kg_s": conductance,
            "p_total_bar": total_pressure_Pa / 100000.0,
            "p_downstream_bar": downstream_pressure_Pa / 100000.0,
            "dt_hr": 1.0,
        },
    )
    assert result.bled_species_mol == pytest.approx(provider_bled)


def test_picard_source_includes_stoichiometric_oxide_vapor_oxygen():
    sim = _real_capacity_sim()
    sp_data = sim.vapor_pressures["oxide_vapors"]["SiO"]
    stoich = sim._evaporation_stoich("SiO", sp_data)
    sio_rate_kg_hr = 1.0e-3
    sio_molar_mass = resolve_species_formula(
        "SiO", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    o2_molar_mass = resolve_species_formula(
        "O2", sim.species_formula_registry
    ).molar_mass_kg_per_mol()

    result = solve_capacity_shadow(
        pre_holdup_mol={},
        molar_mass_kg_mol={"SiO": sio_molar_mass, "O2": o2_molar_mass},
        flux_kg_hr_at_partials=lambda _partials: {"SiO": sio_rate_kg_hr},
        overhead_source_mol_hr_at_partials=lambda _partials: (
            sim._project_evaporation_overhead_source_mol_hr(
                {"SiO": sio_rate_kg_hr},
                {"SiO": stoich},
            )
        ),
        capacity=FiniteCapacity(1.0),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=T_FOR_1000_PA_PER_MOL,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=1.0e-10,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=1.0e-30,
        p_open_Pa=1.0e9,
        vessel_rating_Pa=1.0e12,
    )

    assert isinstance(result, CapacityShadowResult)
    expected_o2_mol = (
        sio_rate_kg_hr
        * stoich["O2_per_product_kg"]
        / o2_molar_mass
    )
    assert 0.0 < result.partial_pressures_Pa["O2"] < expected_o2_mol * 1000.0
    expected_o2_kg_hr = expected_o2_mol * o2_molar_mass
    assert result.saturation.oxygen == pytest.approx(expected_o2_kg_hr)
    assert result.saturation.pipe == pytest.approx(
        (sio_rate_kg_hr + expected_o2_kg_hr) / (1.0e-10 * 3600.0)
    )
    assert result.saturation.combined == result.saturation.pipe


def test_binding_cold_train_does_not_turn_redox_no_capacity_into_fo2_move(
    monkeypatch,
):
    sim = _real_capacity_sim()
    capacity = capacity_from_hardware(
        thermal_train_parameters_from_mapping().cold_train
    )
    assert isinstance(capacity, FiniteCapacity)
    sim.melt.temperature_C = 1600.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim.melt.oxygen_reservoir.reference_T_K = 1873.15
    sim._sync_oxygen_reservoir_mirror()
    before_transitions = len(sim.atom_ledger.transitions)
    monkeypatch.setattr(
        sim,
        "_melt_redox_capacity_mol_per_ln_fO2",
        lambda **_kwargs: 0.0,
    )

    reservoir = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:evaporative_metal_loss": 1.0},
        exchange_direction="redox_source:evaporative_loss",
        temperature_K=1873.15,
    )

    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(-9.0)
    assert reservoir.redox_source_terms_applied is False
    assert reservoir.redox_source_skip_reason == "no_melt_redox_capacity"
    assert len(sim.atom_ledger.transitions) == before_transitions


@pytest.mark.parametrize("finite_headspace_enabled", [False, True])
def test_overhead_model_leaves_provider_partition_mirrors_unset(
    finite_headspace_enabled,
):
    sim = _real_capacity_sim()
    sim.overhead_model._finite_headspace_enabled = finite_headspace_enabled
    if finite_headspace_enabled:
        sim.overhead_model._headspace_volume_m3 = 1.0

    gas = sim.overhead_model.update(
        EvaporationFlux(),
        sim.melt,
        sim.train,
        actual_O2_kg_hr=1.0,
        actual_O2_mol_hr=1.0 / M_O2,
        overhead_holdup_mol={},
        cold_train_capacity=FiniteCapacity(0.1),
    )

    assert gas.turbine_limited is False
    assert gas.O2_vented_kg_hr == 0.0
    assert gas.turbine_flow_kg_hr == 0.0
    assert gas.melt_offgas_O2_mol_hr == pytest.approx(1.0 / M_O2)


# d-450 chunk 2. Canonical O2 headspace. Masses use MOLAR_MASS, not the
# Picard fixture's 0.032 kg/mol. Roots below are checked against a
# bisection of pipe_species_outflow_mol_s, not against a re-derived pipe.
_HS_T_K = 1904.7700354769336
_HS_V_M3 = 0.08538380762699734
_HS_D_M = 0.12
_HS_L_M = 1.0
_STRANDED_O2_KG = 9.661691571909952
_FLOOR_O2_KG = 3.5e-6
_SOURCE_02_KG_H = 0.2
_CREDIT_KG_H = 8.83
_ROOT_02_PA = 25.695650006
_ROOT_02_BLED_KG = 9.8616871391
_ROOT_02_HOLD_KG = 4.4328315148e-6
_ONE_STEP_PA = 248.85783689
_SONIC_CROSSING_KG_H = 5.4971906841
_CREDIT_ROOT_PA = 222.82427409
_CREDIT_BLED_KG = 8.8299650599
_CREDIT_HOLD_KG = 3.8440065311e-5


def _o2_molar_mass_kg() -> float:
    return MOLAR_MASS["O2"] / 1000.0


def _holdup_kg(partial_pa: float) -> float:
    return (
        partial_pa
        * _HS_V_M3
        * _o2_molar_mass_kg()
        / (GAS_CONSTANT * _HS_T_K)
    )


def _moles_of_kg(mass_kg: float) -> float:
    return mass_kg / _o2_molar_mass_kg()


def _pipe_o2_kg_h(pressure_pa: float) -> float:
    outflow = pipe_species_outflow_mol_s(
        pressure_pa,
        0.0,
        _HS_T_K,
        {"O2": 1.0},
        _HS_D_M,
        _HS_L_M,
    )
    return outflow.species_mol_s["O2"] * _o2_molar_mass_kg() * 3600.0


def _bisect_pipe_pa(target_kg_h: float, low_pa: float, high_pa: float) -> float:
    low, high = low_pa, high_pa
    for _ in range(80):
        mid = 0.5 * (low + high)
        if _pipe_o2_kg_h(mid) < target_kg_h:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


def _commit_o2(
    mass_kg: float,
    source_kg_hr: float,
    dt_hr: float,
    *,
    flux=None,
    head_bled=None,
    pipe: bool = True,
    cap_kg_s=None,
    downstream_pa: float = 0.0,
):
    if flux is None:
        def flux(_partials, rate=source_kg_hr):
            return {"O2": rate}

    kwargs = dict(
        pre_holdup_mol={"O2": _moles_of_kg(mass_kg)},
        molar_mass_kg_mol={"O2": _o2_molar_mass_kg()},
        flux_kg_hr_at_partials=flux,
        capacity=NoColdTrain("runtime_enforcement_disabled"),
        head_bled_species_mol={} if head_bled is None else head_bled,
        external_o2_holdup_mol=0.0,
        temperature_K=_HS_T_K,
        volume_m3=_HS_V_M3,
        dt_hr=dt_hr,
        bleed_conductance_kg_s=0.0,
        downstream_pressure_Pa=downstream_pa,
        k_relief_kg_hr_Pa=0.0,
        p_open_Pa=1.0,
    )
    if pipe:
        kwargs["pipe_diameter_m"] = _HS_D_M
        kwargs["pipe_length_m"] = _HS_L_M
    if cap_kg_s is not None:
        kwargs["outflow_cap_kg_s"] = cap_kg_s
    return solve_capacity_shadow(**kwargs)


def _diagnostic_o2(
    mass_kg: float,
    source_kg_hr: float,
    dt_hr: float,
    *,
    flux=None,
    pipe: bool = True,
    cap_kg_s=None,
):
    if flux is None:
        def flux(_partials, rate=source_kg_hr):
            return {"O2": rate}

    kwargs = dict(
        pre_holdup_mol={"O2": _moles_of_kg(mass_kg)},
        molar_mass_kg_mol={"O2": _o2_molar_mass_kg()},
        flux_kg_hr_at_partials=flux,
        temperature_K=_HS_T_K,
        volume_m3=_HS_V_M3,
        dt_hr=dt_hr,
        downstream_pressure_Pa=0.0,
        capacity=NoColdTrain("runtime_enforcement_disabled"),
    )
    if pipe:
        kwargs["pipe_diameter_m"] = _HS_D_M
        kwargs["pipe_length_m"] = _HS_L_M
    if cap_kg_s is not None:
        kwargs["outflow_cap_kg_s"] = cap_kg_s
    return diagnostic_backward_euler_headspace_step(**kwargs)


def _as_result(result) -> CapacityShadowResult:
    assert isinstance(result, CapacityShadowResult)
    assert not isinstance(result, CapacityShadowRefusal)
    return result


def _partition_commit(steps: int, dt_hr: float, mass_kg: float, source_kg_hr: float):
    moles = _moles_of_kg(mass_kg)
    bled_kg = 0.0
    substep_bled: list[float] = []
    result = None
    for _ in range(steps):
        result = _as_result(
            _commit_o2(moles * _o2_molar_mass_kg(), source_kg_hr, dt_hr)
        )
        step_bled = result.bled_species_mol["O2"] * _o2_molar_mass_kg()
        substep_bled.append(step_bled)
        bled_kg += step_bled
        moles = (
            result.partial_pressures_Pa["O2"]
            * _HS_V_M3
            / (GAS_CONSTANT * _HS_T_K)
        )
    assert result is not None
    return result, bled_kg, moles * _o2_molar_mass_kg(), substep_bled


def test_stiff_holdup_converges_to_quasi_steady_root():
    root_pa = _bisect_pipe_pa(_SOURCE_02_KG_H, 1.0, 100.0)
    result = _as_result(_commit_o2(_STRANDED_O2_KG, _SOURCE_02_KG_H, 1.0))
    pressure = result.partial_pressures_Pa["O2"]
    holdup = _holdup_kg(pressure)
    bled = result.bled_species_mol["O2"] * _o2_molar_mass_kg()

    assert pressure == pytest.approx(root_pa, rel=1.0e-10, abs=1.0e-9)
    assert pressure == pytest.approx(_ROOT_02_PA, abs=5.0e-9)
    assert holdup == pytest.approx(_ROOT_02_HOLD_KG, rel=1.0e-9)
    assert bled == pytest.approx(
        _STRANDED_O2_KG + _SOURCE_02_KG_H - holdup,
        rel=1.0e-12,
    )
    assert bled == pytest.approx(_ROOT_02_BLED_KG, abs=5.0e-10)
    assert pressure != pytest.approx(_ONE_STEP_PA, abs=1.0)
    assert result.saturation.binding_cause == "pipe"
    assert result.authoritative is False


def test_one_backward_euler_step_is_not_the_root():
    # Independent bisection of R = 0 on the production pipe. Not the commit.
    n0 = _moles_of_kg(_STRANDED_O2_KG)
    q_mol_s = _SOURCE_02_KG_H / _o2_molar_mass_kg() / 3600.0
    dt_s = 3600.0

    def residual(pressure: float) -> float:
        moles = pressure * _HS_V_M3 / (GAS_CONSTANT * _HS_T_K)
        outflow_mol_s = _pipe_o2_kg_h(pressure) / _o2_molar_mass_kg() / 3600.0
        return moles - n0 - dt_s * (q_mol_s - outflow_mol_s)

    low, high = 1.0, 1.0e4
    assert residual(low) < 0.0 < residual(high)
    for _ in range(80):
        mid = 0.5 * (low + high)
        if residual(mid) < 0.0:
            low = mid
        else:
            high = mid
    bisected = 0.5 * (low + high)

    one = _as_result(_diagnostic_o2(_STRANDED_O2_KG, _SOURCE_02_KG_H, 1.0))
    assert one.partial_pressures_Pa["O2"] == pytest.approx(bisected, abs=1.0e-6)
    assert one.partial_pressures_Pa["O2"] == pytest.approx(_ONE_STEP_PA, abs=1.0e-8)

    moles = n0
    for _ in range(4):
        step = _as_result(_diagnostic_o2(moles * _o2_molar_mass_kg(), _SOURCE_02_KG_H, 0.25))
        moles = (
            step.partial_pressures_Pa["O2"]
            * _HS_V_M3
            / (GAS_CONSTANT * _HS_T_K)
        )
    assert abs(step.partial_pressures_Pa["O2"] - one.partial_pressures_Pa["O2"]) > 1.0e-6
    assert step.partial_pressures_Pa["O2"] == pytest.approx(_ROOT_02_PA, abs=1.0e-6)


def test_fixed_boundary_end_pressure_is_timestep_independent():
    partitions = ((1, 1.0), (4, 0.25), (8, 0.125))
    triples = [
        _partition_commit(steps, dt_hr, _STRANDED_O2_KG, _SOURCE_02_KG_H)
        for steps, dt_hr in partitions
    ]
    for (_steps, dt_hr), (result, bled, holdup, substeps) in zip(partitions, triples):
        assert result.partial_pressures_Pa["O2"] == pytest.approx(_ROOT_02_PA, abs=5.0e-9)
        assert bled == pytest.approx(_ROOT_02_BLED_KG, abs=5.0e-10)
        assert holdup == pytest.approx(_ROOT_02_HOLD_KG, rel=1.0e-9)
        # First slice books the stranded holdup plus this slice of the
        # source, minus the root holdup. The gap is the source slice
        # (~0.2 kg/h * dt), so it is not a 1 kg departure from m0.
        assert substeps[0] == pytest.approx(
            _STRANDED_O2_KG + _SOURCE_02_KG_H * dt_hr - _ROOT_02_HOLD_KG,
            abs=5.0e-10,
        )
        for later in substeps[1:]:
            assert later == pytest.approx(_SOURCE_02_KG_H * dt_hr, abs=5.0e-10)
    # A later substep, already at the root, books only its own source.
    _result, _bled, _holdup, quarter_steps = triples[1]
    assert quarter_steps[0] == pytest.approx(
        _STRANDED_O2_KG + 0.05 - _ROOT_02_HOLD_KG,
        abs=5.0e-10,
    )
    for later in quarter_steps[1:]:
        assert later == pytest.approx(0.05, abs=5.0e-10)

    # Previous-hour map: a partial at or above P_eq drops the species, so
    # the next step sees a zero partial and the full forward rate. One
    # long step alternates. A short step does not drain back through P_eq.
    p_eq = 10.0
    floor_moles = _moles_of_kg(_FLOOR_O2_KG)

    def map_hours(dt_hr: float, hours: int):
        prev = 0.0
        moles = floor_moles
        sources = []
        pressures = []
        for _ in range(hours):
            source = 0.0 if prev >= p_eq else _SOURCE_02_KG_H
            sources.append(source)
            step = _as_result(
                _diagnostic_o2(moles * _o2_molar_mass_kg(), source, dt_hr)
            )
            prev = step.partial_pressures_Pa["O2"]
            moles = prev * _HS_V_M3 / (GAS_CONSTANT * _HS_T_K)
            pressures.append(prev)
        return sources, pressures

    long_sources, long_pressures = map_hours(1.0, 4)
    short_sources, short_pressures = map_hours(0.001 / 3600.0, 4)
    assert long_sources == [_SOURCE_02_KG_H, 0.0, _SOURCE_02_KG_H, 0.0]
    assert max(long_pressures) - min(long_pressures) > 1.0
    assert long_pressures[0] > p_eq > long_pressures[1]
    assert short_sources[0] == _SOURCE_02_KG_H
    assert short_sources[1:] == [0.0, 0.0, 0.0]
    assert min(short_pressures) > p_eq
    assert long_sources != short_sources

    # The commit of a source that really depends on the trial partial
    # does not alternate. Every partition ends on the same pressure.
    def true_flux(partials, _p_eq=p_eq):
        partial = partials["O2"]
        return {"O2": _SOURCE_02_KG_H * max(0.0, (_p_eq - partial) / _p_eq)}

    committed = []
    for steps, dt_hr in ((1, 1.0), (4, 0.25)):
        moles = floor_moles
        last = None
        for _ in range(steps):
            last = _as_result(
                _commit_o2(
                    moles * _o2_molar_mass_kg(),
                    _SOURCE_02_KG_H,
                    dt_hr,
                    flux=true_flux,
                )
            )
            moles = (
                last.partial_pressures_Pa["O2"]
                * _HS_V_M3
                / (GAS_CONSTANT * _HS_T_K)
            )
        assert last is not None
        committed.append(last.partial_pressures_Pa["O2"])
    assert committed[0] == pytest.approx(committed[1], abs=1.0e-6)
    assert committed[0] < p_eq


def test_credit_hour_root():
    root_pa = _bisect_pipe_pa(_CREDIT_KG_H, 100.0, 400.0)
    result = _as_result(_commit_o2(_FLOOR_O2_KG, _CREDIT_KG_H, 1.0))
    pressure = result.partial_pressures_Pa["O2"]
    holdup = _holdup_kg(pressure)
    bled = result.bled_species_mol["O2"] * _o2_molar_mass_kg()

    assert pressure == pytest.approx(root_pa, rel=1.0e-10, abs=1.0e-8)
    assert pressure == pytest.approx(_CREDIT_ROOT_PA, abs=5.0e-8)
    assert holdup == pytest.approx(_CREDIT_HOLD_KG, rel=1.0e-9)
    assert bled == pytest.approx(_FLOOR_O2_KG + _CREDIT_KG_H - holdup, rel=1.0e-12)
    assert bled == pytest.approx(_CREDIT_BLED_KG, abs=5.0e-10)
    assert result.saturation.binding_cause == "choked_throat"


def test_fast_branch_matches_resolved_transient():
    from scipy.integrate import solve_ivp

    result = _as_result(_commit_o2(_STRANDED_O2_KG, _SOURCE_02_KG_H, 1.0))
    pressure = result.partial_pressures_Pa["O2"]
    holdup = _holdup_kg(pressure)
    bled = result.bled_species_mol["O2"] * _o2_molar_mass_kg()
    assert bled == pytest.approx(_STRANDED_O2_KG + _SOURCE_02_KG_H - holdup, rel=1.0e-12)

    q_kg_s = _SOURCE_02_KG_H / 3600.0
    molar = _o2_molar_mass_kg()

    def rhs(_time, state):
        mass = max(state[0], 0.0)
        partial = mass * GAS_CONSTANT * _HS_T_K / (molar * _HS_V_M3)
        outflow = pipe_species_outflow_mol_s(
            partial,
            0.0,
            _HS_T_K,
            {"O2": max(mass / molar, 1.0e-30)},
            _HS_D_M,
            _HS_L_M,
        )
        return [q_kg_s - outflow.species_mol_s["O2"] * molar]

    integrated = solve_ivp(
        rhs,
        (0.0, 5.0),
        [_STRANDED_O2_KG],
        method="DOP853",
        rtol=1.0e-8,
        atol=1.0e-14,
    )
    assert integrated.success
    arrived = float(integrated.y[0, -1])
    arrived_pa = arrived * GAS_CONSTANT * _HS_T_K / (molar * _HS_V_M3)
    assert arrived_pa == pytest.approx(pressure, abs=1.0e-4)
    assert float(integrated.t[-1]) < 3600.0
    # Tail books the root rate for the rest of the hour. The integrated
    # holdup is what the pipe trajectory actually reached.
    tail_bled = (
        (_STRANDED_O2_KG - arrived)
        + q_kg_s * float(integrated.t[-1])
        + q_kg_s * (3600.0 - float(integrated.t[-1]))
    )
    assert tail_bled == pytest.approx(bled, rel=1.0e-9)

    coarse = _as_result(_diagnostic_o2(_STRANDED_O2_KG, _SOURCE_02_KG_H, 1.0))
    coarse_bled = coarse.bled_species_mol["O2"] * molar
    assert abs(coarse_bled - bled) > 1.0e-5

    # Pressure-dependent source. Summing the end-state rate of four
    # coarse steps is not the hour's 0.2 kg. The commit's identity is.
    def shaped(partials, root=pressure):
        partial = partials["O2"]
        if partial >= 100.0:
            return {"O2": 0.0}
        return {"O2": _SOURCE_02_KG_H * (100.0 - partial) / (100.0 - root)}

    moles = _moles_of_kg(_STRANDED_O2_KG)
    booked = 0.0
    for _ in range(4):
        step = _as_result(
            _diagnostic_o2(
                moles * molar,
                _SOURCE_02_KG_H,
                0.25,
                flux=shaped,
            )
        )
        end = step.partial_pressures_Pa["O2"]
        booked += shaped({"O2": end})["O2"] * 0.25
        moles = end * _HS_V_M3 / (GAS_CONSTANT * _HS_T_K)
    assert abs(booked - _SOURCE_02_KG_H) > 0.01


def test_closed_line_accumulates_the_source():
    mass0 = 0.01
    source = _SOURCE_02_KG_H
    commit = _as_result(
        _commit_o2(mass0, source, 1.0, pipe=False, cap_kg_s=0.0)
    )
    assert _holdup_kg(commit.partial_pressures_Pa["O2"]) == pytest.approx(
        mass0 + source,
        rel=1.0e-12,
    )
    assert commit.bled_species_mol["O2"] * _o2_molar_mass_kg() == pytest.approx(
        0.0,
        abs=1.0e-12,
    )
    assert commit.saturation.binding_cause == "line_closed"

    one = _as_result(_diagnostic_o2(mass0, source, 1.0, pipe=False, cap_kg_s=0.0))
    half = _as_result(_diagnostic_o2(mass0, source, 0.5, pipe=False, cap_kg_s=0.0))
    second = _as_result(
        _diagnostic_o2(
            _holdup_kg(half.partial_pressures_Pa["O2"]),
            source,
            0.5,
            pipe=False,
            cap_kg_s=0.0,
        )
    )
    assert abs(one.partial_pressures_Pa["O2"] - second.partial_pressures_Pa["O2"]) <= 1.0e-6
    bled_one = one.bled_species_mol["O2"] * _o2_molar_mass_kg()
    bled_halves = (half.bled_species_mol["O2"] + second.bled_species_mol["O2"]) * _o2_molar_mass_kg()
    assert abs(bled_one - bled_halves) <= max(1.0e-12, 1.0e-9 * abs(bled_one))
    assert commit.partial_pressures_Pa["O2"] == pytest.approx(
        one.partial_pressures_Pa["O2"],
        abs=1.0e-6,
    )


def test_cap_below_source_is_exact_in_one_step():
    mass0 = 0.01
    source = _SOURCE_02_KG_H
    cap_kg_h = 0.05
    commit = _as_result(
        _commit_o2(mass0, source, 1.0, pipe=False, cap_kg_s=cap_kg_h / 3600.0)
    )
    assert _holdup_kg(commit.partial_pressures_Pa["O2"]) == pytest.approx(
        mass0 + source - cap_kg_h,
        rel=1.0e-12,
    )
    assert commit.bled_species_mol["O2"] * _o2_molar_mass_kg() == pytest.approx(
        cap_kg_h,
        rel=1.0e-12,
    )
    assert commit.saturation.binding_cause == "controlled_o2_equipment"

    one = _as_result(
        _diagnostic_o2(mass0, source, 1.0, pipe=False, cap_kg_s=cap_kg_h / 3600.0)
    )
    half = _as_result(
        _diagnostic_o2(mass0, source, 0.5, pipe=False, cap_kg_s=cap_kg_h / 3600.0)
    )
    second = _as_result(
        _diagnostic_o2(
            _holdup_kg(half.partial_pressures_Pa["O2"]),
            source,
            0.5,
            pipe=False,
            cap_kg_s=cap_kg_h / 3600.0,
        )
    )
    assert abs(one.partial_pressures_Pa["O2"] - second.partial_pressures_Pa["O2"]) <= 1.0e-6
    bled_one = one.bled_species_mol["O2"] * _o2_molar_mass_kg()
    bled_halves = (
        half.bled_species_mol["O2"] + second.bled_species_mol["O2"]
    ) * _o2_molar_mass_kg()
    assert abs(bled_one - bled_halves) <= max(1.0e-12, 1.0e-9 * abs(bled_one))
    assert commit.partial_pressures_Pa["O2"] == pytest.approx(
        one.partial_pressures_Pa["O2"],
        abs=1.0e-6,
    )


def test_outflow_argument_is_trial_pressure():
    recorded: list[tuple[float, float]] = []
    real = pipe_species_outflow_mol_s

    def spy(upstream_pa, downstream_pa, temperature_K, holdup_mol, diameter_m, length_m):
        recorded.append((float(upstream_pa), math.fsum(holdup_mol.values())))
        return real(
            upstream_pa,
            downstream_pa,
            temperature_K,
            holdup_mol,
            diameter_m,
            length_m,
        )

    with patch("simulator.capacity_coupling.pipe_species_outflow_mol_s", spy):
        result = _as_result(_commit_o2(_STRANDED_O2_KG, _SOURCE_02_KG_H, 1.0))

    assert recorded
    stranded_mol = _moles_of_kg(_STRANDED_O2_KG)
    commanded_pa = max(0.2 * 100.0, 1.0)
    for upstream_pa, moles in recorded:
        reconstructed = moles * GAS_CONSTANT * _HS_T_K / _HS_V_M3
        assert abs(upstream_pa - reconstructed) <= max(1.0e-6, 1.0e-12 * upstream_pa)
        at_command = abs(upstream_pa - commanded_pa) <= 1.0e-9
        at_stranded = abs(moles - stranded_mol) <= 1.0e-6 * stranded_mol
        assert not (at_command and at_stranded)
    assert result.partial_pressures_Pa["O2"] == pytest.approx(_ROOT_02_PA, abs=5.0e-9)
    assert result.partial_pressures_Pa["O2"] != pytest.approx(commanded_pa, abs=1.0)


def test_source_above_the_sonic_crossing_returns_the_sonic_root():
    crossing_pa = _bisect_pipe_pa(_SONIC_CROSSING_KG_H, 50.0, 300.0)
    sonic_pa = _bisect_pipe_pa(_CREDIT_KG_H, crossing_pa, 400.0)
    result = _as_result(_commit_o2(_FLOOR_O2_KG, _CREDIT_KG_H, 1.0))
    pressure = result.partial_pressures_Pa["O2"]

    assert sonic_pa > crossing_pa
    assert pressure == pytest.approx(sonic_pa, rel=1.0e-10, abs=1.0e-8)
    assert pressure == pytest.approx(_CREDIT_ROOT_PA, abs=5.0e-8)
    assert result.saturation.binding_cause == "choked_throat"
    assert _pipe_o2_kg_h(pressure) == pytest.approx(_CREDIT_KG_H, abs=1.0e-8)


def _integrated_stranded_kg(dt_s: float) -> float:
    """Holdup after dt_s from the production pipe. Not the linearised step.

    scipy's BDF integrates dm/dt = q - Q(P(m)) on pipe_species_outflow_mol_s
    at rtol 1e-10. On a megapascal state that band is wider than 1e-6 Pa;
    the choked piece uses the closed form instead.
    """

    from scipy.integrate import solve_ivp

    q_kg_s = _SOURCE_02_KG_H / 3600.0
    molar = _o2_molar_mass_kg()

    def rhs(_time, state):
        mass = max(float(state[0]), 0.0)
        partial = mass * GAS_CONSTANT * _HS_T_K / (molar * _HS_V_M3)
        outflow = pipe_species_outflow_mol_s(
            partial,
            0.0,
            _HS_T_K,
            {"O2": max(mass / molar, 1.0e-30)},
            _HS_D_M,
            _HS_L_M,
        )
        return [q_kg_s - outflow.species_mol_s["O2"] * molar]

    integrated = solve_ivp(
        rhs,
        (0.0, dt_s),
        [_STRANDED_O2_KG],
        method="BDF",
        rtol=1.0e-10,
        atol=1.0e-16,
    )
    assert integrated.success
    return float(integrated.y[0, -1])


def _choked_mass_rate_s(pressure_pa: float) -> float:
    """Q/m from the production pipe. Not the integrator's stencil."""

    mass = _holdup_kg(pressure_pa)
    outflow = pipe_species_outflow_mol_s(
        pressure_pa,
        0.0,
        _HS_T_K,
        {"O2": mass / _o2_molar_mass_kg()},
        _HS_D_M,
        _HS_L_M,
    )
    return outflow.species_mol_s["O2"] * _o2_molar_mass_kg() / mass


def _stranded_oracle_pa(dt_s: float) -> float:
    """End pressure of dm/dt = q − Q(m).

    On the choked piece Q/m is constant, so
    m(t) = q/a + (m0 − q/a) exp(−a t). Two pressures have to give the
    same a, or this is not that piece. Once the closed form has left
    1000 Pa the reference is the BDF of the pipe law.
    Unit check: (kg/s) / (1/s) = kg.
    """

    high = _choked_mass_rate_s(1.0e5)
    low = _choked_mass_rate_s(1.0e3)
    assert high == pytest.approx(low, rel=1.0e-9)
    rate = high
    source_kg_s = _SOURCE_02_KG_H / 3600.0
    mass = source_kg_s / rate + (
        _STRANDED_O2_KG - source_kg_s / rate
    ) * math.exp(-rate * dt_s)
    pressure = (
        mass * GAS_CONSTANT * _HS_T_K / (_o2_molar_mass_kg() * _HS_V_M3)
    )
    if pressure >= 1000.0:
        return pressure
    arrived = _integrated_stranded_kg(dt_s)
    return arrived * GAS_CONSTANT * _HS_T_K / (_o2_molar_mass_kg() * _HS_V_M3)


@pytest.mark.parametrize("dt_s", [1.0, 0.25, 0.05, 1.0e-4])
def test_stiff_slow_step_commits_the_integrated_drain(dt_s: float):
    # Hour-103 inventory, classified slow: the one-step residual has a
    # root, and the commit is the integral, not a refusal and not that
    # single backward-Euler pressure. 0.25 s crosses the sonic kink.
    # The design floor is 1e-6 Pa. On the choked piece the oracle is the
    # closed form; once that form has left the piece it is BDF at
    # rtol 1e-10, whose band at 40 Pa is far under the floor.
    result = _as_result(
        _commit_o2(_STRANDED_O2_KG, _SOURCE_02_KG_H, dt_s / 3600.0)
    )
    pressure = result.partial_pressures_Pa["O2"]
    holdup = _holdup_kg(pressure)
    bled = result.bled_species_mol["O2"] * _o2_molar_mass_kg()
    source_kg = _SOURCE_02_KG_H * dt_s / 3600.0
    arrived_pa = _stranded_oracle_pa(dt_s)
    diagnostic = _as_result(
        _diagnostic_o2(_STRANDED_O2_KG, _SOURCE_02_KG_H, dt_s / 3600.0)
    )
    diagnostic_pa = diagnostic.partial_pressures_Pa["O2"]

    assert pressure == pytest.approx(arrived_pa, abs=1.0e-6)
    assert abs(pressure - diagnostic_pa) > abs(pressure - arrived_pa)
    assert bled == pytest.approx(
        _STRANDED_O2_KG + source_kg - holdup,
        abs=1.0e-9,
    )
    if arrived_pa > 1000.0:
        assert result.saturation.binding_cause == "choked_throat"
    if arrived_pa < 100.0:
        assert result.saturation.binding_cause == "pipe"


def test_holdup_below_downstream_keeps_its_mass():
    # No source and P_up <= P_down: outflow is 0, so the holdup stays
    # and the bleed is 0. The fast root at P_down is not this step.
    held = _as_result(
        _commit_o2(_holdup_kg(10.0), 0.0, 1.0, downstream_pa=1000.0)
    )
    assert held.partial_pressures_Pa["O2"] == pytest.approx(10.0, abs=1.0e-6)
    assert held.bled_species_mol["O2"] * _o2_molar_mass_kg() == pytest.approx(
        0.0,
        abs=1.0e-12,
    )

    empty = _as_result(_commit_o2(0.0, 0.0, 1.0, downstream_pa=1.0))
    assert empty.partial_pressures_Pa["O2"] == pytest.approx(0.0, abs=1.0e-9)
    assert empty.bled_species_mol["O2"] * _o2_molar_mass_kg() == pytest.approx(
        0.0,
        abs=1.0e-12,
    )


def _commit_o2_n2(
    mass_o2_kg: float,
    n2_mol: float,
    source_kg_hr: float,
    dt_hr: float,
    *,
    flux=None,
):
    molar_n2 = MOLAR_MASS["N2"] / 1000.0
    if flux is None:
        def flux(_partials, rate=source_kg_hr):
            return {"O2": rate}

    return solve_capacity_shadow(
        pre_holdup_mol={
            "O2": _moles_of_kg(mass_o2_kg),
            "N2": n2_mol,
        },
        molar_mass_kg_mol={"O2": _o2_molar_mass_kg(), "N2": molar_n2},
        flux_kg_hr_at_partials=flux,
        capacity=NoColdTrain("runtime_enforcement_disabled"),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=_HS_T_K,
        volume_m3=_HS_V_M3,
        dt_hr=dt_hr,
        bleed_conductance_kg_s=0.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=0.0,
        p_open_Pa=1.0,
        pipe_diameter_m=_HS_D_M,
        pipe_length_m=_HS_L_M,
    )


def test_failed_fast_sweep_on_two_species_refuses():
    # The unforced hour is the pure-O2 root. A fast sweep that misses
    # its tolerance refuses. It does not publish the slow state.
    unforced = _as_result(_commit_o2_n2(_FLOOR_O2_KG, 1.0e-6, _SOURCE_02_KG_H, 1.0))
    assert unforced.partial_pressures_Pa["O2"] == pytest.approx(_ROOT_02_PA, abs=5.0e-9)
    assert unforced.partial_pressures_Pa["N2"] == pytest.approx(0.0, abs=1.0e-9)

    entered: list[str] = []

    def sweep(*args, **kwargs):
        mode = args[3] if len(args) > 3 else kwargs["mode"]
        entered.append(mode)
        if mode == "fast":
            raise capacity_coupling._BalanceRefused("headspace_balance_nonconvergence")
        raise AssertionError(mode)

    def slow(*args, **kwargs):
        entered.append("slow")
        raise AssertionError("slow branch")

    with (
        patch.object(capacity_coupling, "_sweep", sweep),
        patch.object(capacity_coupling, "_slow_converge", slow),
    ):
        refused = _commit_o2_n2(_FLOOR_O2_KG, 1.0e-6, _SOURCE_02_KG_H, 1.0)

    assert isinstance(refused, CapacityShadowRefusal)
    assert refused.reason == "headspace_balance_nonconvergence"
    assert entered == ["fast"]


def _two_root_flux(partials: dict) -> dict[str, float]:
    # 1e8 kg/h below 2e8 Pa. The test requires the production pipe at
    # 1e8 Pa to lie under that rate, so a grid that stops at 1e8 Pa has
    # no sign change. The drop to 0 at 2e8 Pa and the step to 1e20 kg/h
    # at 5e8 Pa are the two crossings, both in the extension to 1e12 Pa.
    total = math.fsum(partials.values())
    if total < 2.0e8:
        rate = 1.0e8
    elif total < 5.0e8:
        rate = 0.0
    else:
        rate = 1.0e20
    return {"O2": rate}


def test_second_root_above_1e8_pa_refuses_on_both_paths():
    assert _pipe_o2_kg_h(1.0e8) < 1.0e8
    assert _pipe_o2_kg_h(3.0e8) > 0.0
    assert _pipe_o2_kg_h(6.0e8) < 1.0e20

    one = _commit_o2(
        _FLOOR_O2_KG,
        0.0,
        1.0,
        flux=_two_root_flux,
    )
    assert isinstance(one, CapacityShadowRefusal)
    assert one.reason == "headspace_balance_multiple_roots"

    two = _commit_o2_n2(
        _FLOOR_O2_KG,
        1.0e-6,
        0.0,
        1.0,
        flux=_two_root_flux,
    )
    assert isinstance(two, CapacityShadowRefusal)
    assert two.reason == "headspace_balance_multiple_roots"


# d-450 second fold. Mixture drain and the free-molecular stencil.
# Oracles call pipe_species_outflow_mol_s or its BDF. They do not call
# the linearised step.
_HS_PA_PER_MOL = GAS_CONSTANT * _HS_T_K / _HS_V_M3
_MOLECULAR_DECAY_S = 5.127738455


def _outflow_at(moles: dict[str, float]):
    pressure = math.fsum(moles.values()) * _HS_PA_PER_MOL
    return pipe_species_outflow_mol_s(
        pressure,
        0.0,
        _HS_T_K,
        moles,
        _HS_D_M,
        _HS_L_M,
    )


def _decay_rates(moles: dict[str, float]) -> dict[str, float]:
    outflow = _outflow_at(moles)
    return {
        species: outflow.species_mol_s[species] / moles[species]
        for species in moles
    }


def _bdf_partials(
    moles: dict[str, float],
    source_kg_hr: dict[str, float],
    dt_s: float,
    *,
    rtol: float,
) -> dict[str, float]:
    """Independent BDF of dn_i/dt = S_i − Q_i(pipe_species_outflow_mol_s)."""

    from scipy.integrate import solve_ivp

    species = tuple(moles)
    masses = {"O2": _o2_molar_mass_kg(), "N2": MOLAR_MASS["N2"] / 1000.0}
    source_mol_s = {
        name: source_kg_hr.get(name, 0.0) / masses[name] / 3600.0
        for name in species
    }

    def rhs(_time, state):
        holdup = {}
        pressure = 0.0
        for index, name in enumerate(species):
            amount = max(float(state[index]), 0.0)
            pressure += amount * _HS_PA_PER_MOL
            if amount > 0.0:
                holdup[name] = amount
        if pressure <= 0.0 or not holdup:
            return [source_mol_s[name] for name in species]
        outflow = pipe_species_outflow_mol_s(
            pressure,
            0.0,
            _HS_T_K,
            holdup,
            _HS_D_M,
            _HS_L_M,
        )
        return [
            source_mol_s[name] - float(outflow.species_mol_s.get(name, 0.0))
            for name in species
        ]

    integrated = solve_ivp(
        rhs,
        (0.0, dt_s),
        [moles[name] for name in species],
        method="BDF",
        rtol=rtol,
        atol=1.0e-16,
    )
    assert integrated.success
    return {
        name: float(integrated.y[index, -1]) * _HS_PA_PER_MOL
        for index, name in enumerate(species)
    }


def _assert_near_bdf_or_its_band(commit_pa: float, bdf_pa: float, analytic_pa: float):
    # A megapascal BDF at rtol 1e-10 misses the closed form by ~1e-4 Pa,
    # which is coarser than the 1e-6 Pa floor. The commit has to match
    # the closed form to that floor, and it may not sit farther from
    # the BDF than the closed form does, plus the same floor.
    assert commit_pa == pytest.approx(analytic_pa, abs=1.0e-6)
    band = abs(bdf_pa - analytic_pa) + 1.0e-6
    assert commit_pa == pytest.approx(bdf_pa, abs=band)


@pytest.mark.parametrize("dt_s", [0.01, 0.05])
def test_equal_moles_stay_on_one_choked_exponential(dt_s: float):
    # Continuum splits by mole fraction. With no source, x is invariant,
    # so equal moles stay equal. Q_i/n_i is one number on the choked piece.
    moles = {"O2": 10.0, "N2": 10.0}
    outflow = _outflow_at(moles)
    rates = _decay_rates(moles)
    assert outflow.binding_cause == "choked_throat"
    assert outflow.blend_weight == 0.0
    assert rates["O2"] == pytest.approx(rates["N2"], rel=1.0e-12)
    result = _as_result(_commit_o2_n2(10.0 * _o2_molar_mass_kg(), 10.0, 0.0, dt_s / 3600.0))
    analytic = 10.0 * _HS_PA_PER_MOL * math.exp(-rates["O2"] * dt_s)
    bdf = _bdf_partials(moles, {}, dt_s, rtol=1.0e-10)
    assert abs(result.partial_pressures_Pa["O2"] - result.partial_pressures_Pa["N2"]) <= 1.0e-9
    for species, molar in (("O2", _o2_molar_mass_kg()), ("N2", MOLAR_MASS["N2"] / 1000.0)):
        partial = result.partial_pressures_Pa[species]
        _assert_near_bdf_or_its_band(partial, bdf[species], analytic)
        n_end = partial / _HS_PA_PER_MOL
        n_analytic = 10.0 * math.exp(-rates["O2"] * dt_s)
        assert result.bled_species_mol[species] == pytest.approx(10.0 - n_end, abs=1.0e-12)
        assert result.bled_species_mol[species] * molar == pytest.approx(
            (10.0 - n_analytic) * molar,
            abs=1.0e-12,
        )


def test_equal_mass_uses_the_same_choked_rate():
    molar_n2 = MOLAR_MASS["N2"] / 1000.0
    moles = {"O2": 1.0 / _o2_molar_mass_kg(), "N2": 1.0 / molar_n2}
    rates = _decay_rates(moles)
    assert rates["O2"] == pytest.approx(rates["N2"], rel=1.0e-12)
    dt_s = 0.01
    result = _as_result(_commit_o2_n2(1.0, moles["N2"], 0.0, dt_s / 3600.0))
    bdf = _bdf_partials(moles, {}, dt_s, rtol=1.0e-10)
    for species in moles:
        analytic = moles[species] * _HS_PA_PER_MOL * math.exp(-rates[species] * dt_s)
        _assert_near_bdf_or_its_band(
            result.partial_pressures_Pa[species],
            bdf[species],
            analytic,
        )


def test_trace_beside_stranded_oxygen_keeps_the_oxygen_partial():
    # A species source breaks the mole-fraction invariant. The choked
    # piece is still Q_i = a n_i with one a, so
    # n_O2 = S/a + (n0 − S/a) exp(−a t) and n_N2 = n0 exp(−a t).
    molar_o2 = _o2_molar_mass_kg()
    n_o2 = _STRANDED_O2_KG / molar_o2
    n_n2 = 1.0e-3
    moles = {"O2": n_o2, "N2": n_n2}
    rates = _decay_rates(moles)
    assert rates["O2"] == pytest.approx(rates["N2"], rel=1.0e-12)
    rate = rates["O2"]
    source_mol_s = _SOURCE_02_KG_H / molar_o2 / 3600.0
    dt_s = 0.05
    decay = math.exp(-rate * dt_s)
    analytic = {
        "O2": (source_mol_s / rate + (n_o2 - source_mol_s / rate) * decay) * _HS_PA_PER_MOL,
        "N2": n_n2 * decay * _HS_PA_PER_MOL,
    }
    result = _as_result(
        _commit_o2_n2(_STRANDED_O2_KG, n_n2, _SOURCE_02_KG_H, dt_s / 3600.0)
    )
    bdf = _bdf_partials(moles, {"O2": _SOURCE_02_KG_H}, dt_s, rtol=1.0e-10)
    for species in moles:
        _assert_near_bdf_or_its_band(
            result.partial_pressures_Pa[species],
            bdf[species],
            analytic[species],
        )
    n_end_o2 = result.partial_pressures_Pa["O2"] / _HS_PA_PER_MOL
    booked = result.bled_species_mol["O2"]
    assert booked == pytest.approx(n_o2 + source_mol_s * dt_s - n_end_o2, abs=1.0e-9)
    assert booked * molar_o2 == pytest.approx(
        (n_o2 + source_mol_s * dt_s - analytic["O2"] / _HS_PA_PER_MOL) * molar_o2,
        abs=1.0e-9,
    )


@pytest.mark.parametrize("dt_s", [0.01, 0.1, 1.0])
def test_free_molecular_oxygen_is_the_exponential(dt_s: float):
    # P(t) = 0.02 exp(−a t), a = 5.127738455 s^-1, the free-molecular
    # Q/m. The old stencil stepped off that piece and returned α = 0.
    n0 = 0.02 / _HS_PA_PER_MOL
    result = _as_result(_commit_o2(_holdup_kg(0.02), 0.0, dt_s / 3600.0))
    analytic = 0.02 * math.exp(-_MOLECULAR_DECAY_S * dt_s)
    bdf = _bdf_partials({"O2": n0}, {}, dt_s, rtol=1.0e-12)
    partial = result.partial_pressures_Pa["O2"]
    assert partial == pytest.approx(analytic, abs=1.0e-6)
    assert partial == pytest.approx(bdf["O2"], abs=1.0e-6)
    assert result.saturation.binding_cause == "molecular_conductance"


def test_molecular_mixture_separates_by_effusion_speed():
    # Free-molecular Q_i = C_i n_i / V with C_i ∝ sqrt(1/M_i). Equal
    # partials do not stay equal. A mole-fraction split would keep them
    # equal and fail this test.
    moles = {"O2": 0.01 / _HS_PA_PER_MOL, "N2": 0.01 / _HS_PA_PER_MOL}
    outflow = _outflow_at(moles)
    rates = _decay_rates(moles)
    assert outflow.blend_weight == 1.0
    assert outflow.binding_cause == "molecular_conductance"
    molar_n2 = MOLAR_MASS["N2"] / 1000.0
    assert rates["O2"] / rates["N2"] == pytest.approx(
        math.sqrt(molar_n2 / _o2_molar_mass_kg()),
        rel=1.0e-4,
    )
    dt_s = 0.1
    result = _as_result(
        _commit_o2_n2(0.01 / _HS_PA_PER_MOL * _o2_molar_mass_kg(), moles["N2"], 0.0, dt_s / 3600.0)
    )
    bdf = _bdf_partials(moles, {}, dt_s, rtol=1.0e-12)
    for species in moles:
        analytic = 0.01 * math.exp(-rates[species] * dt_s)
        assert result.partial_pressures_Pa[species] == pytest.approx(analytic, abs=1.0e-6)
        assert result.partial_pressures_Pa[species] == pytest.approx(bdf[species], abs=1.0e-6)
    ratio = result.partial_pressures_Pa["O2"] / result.partial_pressures_Pa["N2"]
    assert ratio == pytest.approx(
        math.exp(-(rates["O2"] - rates["N2"]) * dt_s),
        rel=1.0e-6,
    )
    assert abs(ratio - 1.0) > 1.0e-3
