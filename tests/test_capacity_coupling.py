from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import simulator.capacity_coupling as capacity_coupling
from engines.builtin.overhead_bleed import (
    BuiltinOverheadBleedProvider,
    compressible_pressure_capacity_fraction,
)
from simulator.accounting import resolve_species_formula
from simulator.capacity_coupling import (
    CapacityCouplingRefusalError,
    CapacityShadowRefusal,
    CapacityShadowResult,
    combined_saturation,
    partition_melt_oxygen,
    solve_capacity_shadow,
)
from simulator.core import PyrolysisSimulator
from simulator.evaporation import _pre_rg_effective_pressure_source
from simulator.physical_constants import GAS_CONSTANT
from simulator.state import CampaignPhase, EvaporationFlux
from simulator.vapour_rail.activity import ActivityVerdictKind
from simulator.vapour_rail.batch import FluxEligible, PressureRefusal, PressureValue
from simulator.vapour_rail.catalog import OUT_OF_RANGE_STATUS
from simulator.vapour_rail.instrumentation import (
    EffectivePressureSource,
    compare_live_shadow_to_batch_flux,
    flux_pressures_from_batch,
    serialize_vapour_batch,
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
    raw = EvaporationFlux(species_kg_hr={"Fe": 1000.0})
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


def test_default_runtime_policy_is_no_cold_train():
    sim = _real_capacity_sim()

    capacity, cold_train = sim._cold_train_capacity_policy()

    assert isinstance(capacity, NoColdTrain)
    assert capacity.reason == "runtime_enforcement_disabled"
    assert cold_train.runtime_enforcement is False


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
        "Ca": 4.6e-9,
        "Mg": 7.3e-3,
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
    original_activities = dict(equilibrium.activity_coefficients)
    original_diagnostic_activities = dict(
        sim._last_vapor_pressure_diagnostic["activities"]
    )
    assert original_activities == original_diagnostic_activities
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
            assert answer.extra.get("out_of_range") is True
            assert answer.extra["status"] == OUT_OF_RANGE_STATUS
            assert answer.extra.get("acquisition_flag")
            assert answer.verdict_status == "status_bearing_non_authoritative"
            assert answer.certification_ceiling == "never"
            assert math.isfinite(catalog_pa) and 0.0 < catalog_pa
            assert catalog_pa < pure_component_probe_pa[species_id]
            assert not within_probe_band(species_id, catalog_pa, float(seam_pa))
        else:
            assert not answer.extra.get("out_of_range", False)
            assert within_probe_band(species_id, catalog_pa, probe_pa)
            assert within_probe_band(species_id, catalog_pa, float(seam_pa))
        activity = answer.source_reaction_activity
        assert activity is not None
        assert activity.verdict is ActivityVerdictKind.POINT
        assert activity.value == pytest.approx(
            original_activities[species_id], rel=0.0, abs=0.0
        )
        assert activity.provider == "BuiltinVaporPressureProvider"
        assert activity.evidence_ref is not None
        assert "_last_vapor_pressure_diagnostic.activities" in activity.evidence_ref
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
        equilibrium.activity_coefficients = original_activities
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

    assert sim._equipment is not None
    assert snapshot.overhead.initial_throat_area_m2 == pytest.approx(
        sim._equipment.pipe.initial_throat_area_m2
    )

    assert (
        snapshot.hour,
        snapshot.temperature_C,
        snapshot.evap_flux.total_kg_hr,
        snapshot.overhead.transport_saturation_pct,
        snapshot.melt_mass_kg,
    ) == pytest.approx(
        # 2026-07-21 B1 vapor-package regen: corrected (higher) P_sat raises
        # hour-1 total evaporation 1.422 -> 2.621 kg/hr; transport saturation
        # and evaporated melt mass follow the same shift. Head-result values
        # recomputed from the executable under the quiesced gate.
        #
        # 2026-08-02 VR-11 P1-2 (CrO2 refusal): policy-only alpha no longer
        # admits CrO2, so the shared Cr2O3 parent-oxide split no longer
        # debits a CrO2 channel.  Per-species probe shows ONLY Cr moves
        # (+1.5539e-07 kg/hr); Al/Ca/Fe/K/Mg/Mn/Na/SiO/Ti byte-identical.
        # Total / transport-saturation / melt-mass followers regenerated
        # from the executable under the same default-off head-result probe.
        # regenerated under CI engine config (studio grind overlay) per the
        # 5c6c015 doctrine; laptop-config value was identical (studio emit
        # 2026-08-02 @ tip 0de9c6d confirmed bit-match for the head-result trio).
        # 2026-08-04 regrind batch (b-137 six-carrier activation): the trio
        # moves at parts-per-billion because TiO2(g)/TiO/CaO(g)/AlO/Al2O/CrO3
        # add ~7.8e-5 Pa of new head pressure (TiO2 7.19e-5, TiO 4.44e-6,
        # AlO 5.63e-7, CrO3 1.27e-6, Al2O 4.8e-10, CaO 1.43e-10 — per
        # scripts/vapour_head_pressure_table.py on this tree) whose flux
        # couples into the Fe line only through shared O2/thermal state:
        # flux +9.27e-9 kg/hr, energy +2.29e-3 kWh-line, follower -1.1e-8.
        # Fe/Na/K per-species pressures byte-identical (seam-selected, OOD).
        # Pending studio-config confirmation at the batch gate.
        # 2026-08-04 b-142 category-2 fix: the 1550 C continuation now carries
        # TiO=1.0355070316502984e-6 Pa and TiO2(g)=2.9802561250738806e-5 Pa,
        # both OOR/status-bearing/non-certifying. PressureValue -> FluxEligible
        # -> seam-selected analytic depletion adds 1.0014035860409567e-6 and
        # 3.222946295412925e-5 kg/hr respectively; the three Ti carriers still
        # share one grouped TiO2 parent draw and debit once. Total flux rises
        # +3.323086654027918e-5 kg/hr, transport saturation +4.4160473477095366,
        # and the grouped parent debit lowers melt mass -3.3481727086837054e-5 kg.
        # CrO3 remains absent from transitions for a separate physical reason:
        # raw 8.160077335392753e-7 kg/hr consumes 0.240001560107647 kg O2/kg,
        # but the head has zero overhead O2, so the allocator correctly zeros it.
        # 2026-08-05 phosphorus-carrier union: at 1550 C, PO=0.00209063758295 Pa,
        # PO2=0.000168203490621 Pa, and P2=5.32056804627e-9 Pa dominate the six
            # new P channels (P4/P4O6/P4O10 are positive but negligible here). Their
            # three executable transitions add 0.0023652645523379867 kg/hr total flux,
            # +518.9310568999499 transport-saturation points, and lower melt mass by
            # 0.0034963082154035874 kg. Values come from the executable head-pressure
        # table and this quiesced default-off probe on the resolved union tree.
        # 2026-08-05 MC-1 additive lunar traces: the low-Ti declaration grows by
        # 0.2057916 wt% without changing any major-oxide row. Existing load-time
        # whole-feed normalization plus Stage-0 disposition leaves
        # 998.7714742180782 kg in cleaned melt (0.8852837472074954 kg is inert
        # trace inventory; 0.987143121334345 kg is explicitly routed to slag,
        # chloride, and sulfide accounts). The slightly diluted major-oxide
        # activities lower flux by 0.0031308794125107 kg/hr and transport
            # saturation by 1433.775192481 points; post-step melt follows by
            # -1.2253948978958 kg. Recomputed from this quiesced executable probe.
            # 2026-08-05 MC-4A systematic carriers: the executable 1550 C head
            # table adds CrO=0.0012405690130816466 Pa and makes CrO2 pressure-
            # positive at 0.0011084725622752761 Pa. CrO follows PressureValue ->
            # FluxEligible -> shared-Cr2O3 sum-once depletion, raising total flux
            # +0.001389175252095587 kg/hr and transport saturation
            # +215.12939772568643 points while lowering melt mass
            # -0.0015528073208770365 kg. CrO2 still makes no head transition here:
            # its oxidizing reaction needs overhead O2 and this probe starts at
            # zero. Al2/Al2O2/Al2O3(g)/AlO2/Ca2 heads are <=4.89e-12 Pa and do
            # not move the reported trio. Recomputed from the executable probe.
            (
                1,
                1550.0,
                2.6220321073606474,
                1161283.2255116678,
                996.1402608722016,
        ),
        rel=1.0e-12,
        abs=1.0e-12,
    )
    # The status-bearing out-of-domain point path removes one earlier
    # zero-quantity transition; the hot-tail mechanism is otherwise unchanged.
    # 2026-08-04 regrind batch: +3 transitions = evaporate_AlO, evaporate_Al2O,
    # evaporate_CaO_gas (new carrier channels; per-carrier ledger transitions,
    # grouped parent draw unchanged). b-142 adds evaporate_TiO and
    # evaporate_TiO2_gas through the status-bearing OOR continuation described
    # above (+2 transitions, 23 -> 25); all three Ti carrier debits still sum
    # into one TiO2 parent draw. CrO3 adds none because its O2-consuming route
    # is starved by the fixture's zero overhead O2, not by pressure/domain gating.
    # The phosphorus union adds evaporate_P2 and evaporate_PO2. Regrind now
    # rejects a whole coupled carrier transition when any nonzero ledger leg is
    # at or below the 1e-12 kg floor, so the earlier PO dust transition is no
    # longer committed (28 -> 27); snapshot flux and ledger remain identical.
    # MC-4A adds one evaporate_CrO transition (1550 C head
    # 0.0012405690130816466 Pa) through the shared Cr2O3 parent path. CrO2's
    # 0.0011084725622752761 Pa head remains transition-free here because the
    # oxidizing leg has no overhead O2 to debit (27 -> 28).
    assert len(sim.atom_ledger.transitions) == 28
    assert tuple(
        transition.reason for transition in sim.atom_ledger.transitions[-5:]
    ) == (
        "condense_Ti",
        "evaporate_TiO",
        "evaporate_TiO2_gas",
        "fe_redox_respeciation",
        "overhead_bleed",
    )
    assert snapshot.mass_balance_error_pct <= 5.0e-12

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


def test_no_cold_train_reuses_head_bleed_without_evaluating_shadow_flux():
    head = {"O2": 1.25, "N2": 0.75}

    def forbidden_flux(_partials):
        raise AssertionError("NoColdTrain must not recompute HEAD operations")

    result = solve_capacity_shadow(
        pre_holdup_mol=head,
        molar_mass_kg_mol={"O2": M_O2, "N2": M_N2},
        flux_kg_hr_at_partials=forbidden_flux,
        capacity=NoColdTrain(),
        head_bled_species_mol=head,
        external_o2_holdup_mol=0.0,
        temperature_K=300.0,
        volume_m3=1.0,
        dt_hr=1.0,
        bleed_conductance_kg_s=1.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=0.0,
        p_open_Pa=1.0,
    )

    assert isinstance(result, CapacityShadowResult)
    assert result.bled_species_mol == head
    assert result.terminal_offgas_mol == {"N2": 0.75}
    assert result.iterations == 0


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
