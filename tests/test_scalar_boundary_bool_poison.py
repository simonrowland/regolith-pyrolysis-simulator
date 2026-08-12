"""Regression coverage for booleans at declared numeric boundaries."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import patch

import numpy as np
import pytest

from engines.alphamelts.parser import _control_float
from engines.builtin._common import (
    control_float as builtin_control_float,
    resolve_request_vacuum_floor_bar,
)
from engines.builtin.cco_redox_buffer import _require_finite_positive
from engines.builtin.evaporation_flux import (
    EvaporationFluxConfigurationError,
    _coerce_alpha_by_species,
    _coerce_alpha_envelope_by_species,
    _validated_stir_factor,
)
from engines.builtin.ca_aluminothermic_step import _finite_float as ca_finite_float
from engines.builtin.condensation_route import BuiltinCondensationRouteProvider
from engines.builtin.oxygen_bubbler import _finite_nonnegative_control
from simulator.accounting.queries import (
    AccountingError,
    _stage0_optional_float,
    _stage0_positive_float,
    _stage0_required_non_negative_float,
)
from simulator.backends import _parse_control_quantization_config
from simulator.campaigns import CampaignManager
from simulator.chemistry.structural_activity import (
    _positive_float as structural_positive_float,
)
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.dto import (
    _DECLARED_REAL_SCALAR_CONTROL_INPUTS,
    _LEGITIMATE_BOOLEAN_CONTROL_INPUTS,
    IntentRequest,
    LedgerTransitionProposal,
    ProviderAccountView,
)
from simulator.chemistry.phase_context import (
    InvalidLiquidFractionError,
    _caller_fraction,
    _finite as phase_finite,
    _optional_fraction,
)
from simulator.coating_lifespan import (
    FoulingProjectionError,
    campaigns_to_resinter_total,
    _finite_float as coating_finite_float,
    project_lifecycle,
)
from simulator.coating_rate import continuous_wall_deposition_flux
from simulator.cost_ledger import CostLot, CostVector
from simulator.accounting.lots import PoolWithdrawalError, allocate_pool_withdrawal
from simulator.condensation import (
    CondensationModel,
    _coerce_alpha_s,
    _flowing_species_partial_pressures_pa,
    _validate_alkali_activity_entry,
)
from simulator.core import (
    PyrolysisSimulator,
    _feedstock_surface_pressure_mbar,
)
from simulator.environment import _positive_finite_bar
from simulator.equipment import EquipmentDesigner
from simulator.evaporation_classes import _scalar_runtime_alpha
from simulator.feedstock_composition import _representative_number
from simulator.furnace_materials import _finite_float as furnace_finite_float
from simulator.lab_geometry import LabGeometryError, _required_finite
from simulator.mre_ladder import MRE_REFERENCE_TEMPERATURE_K, _coerce_temperature_K
from simulator.mre_ladder import coerce_mre_decomposition_voltage
from simulator.melt_regime import melt_regime
from simulator.melt_backend.alphamelts import (
    AlphaMELTSConfigurationError,
    _validated_timeout_s,
)
from simulator.melt_backend.imcc_sf04.adapter import (
    _EXPECTED_PARENT_OXIDES,
    ImccMalformedDatapackError,
    _as_fraction,
    load_datapack,
)
from simulator.melt_backend.magemin import MAGEMinBackend
from simulator.melt_backend.sulfsat import SulfSatGate
from simulator.melt_backend.thermoengine import ThermoEngineBackend
from simulator.melt_backend.vaporock import VapoRockBackend
from simulator.melt_backend.liquidus import (
    EquilibriumCrystallizationPathResult,
    LiquidFractionPathPoint,
    LiquidusSolidusResult,
    MeltFractionSample,
    _temperature_grid,
    build_equilibrium_crystallization_path,
    find_liquidus_solidus_by_fraction,
)
from simulator.optimize.cli import _non_negative_int, _positive_float, _positive_int
from simulator.optimize.doe import _optional_float as doe_optional_float
from simulator.optimize.evalspec import EvalSpec
from simulator.optimize.evaluate import (
    EvaluationInputError,
    _finite_optional_float,
    _positive_eval_mass_kg,
)
from simulator.optimize.objective import (
    ObjectiveComputationError,
    ObjectiveProfileError,
    _finite_float as objective_finite_float,
    _positive_profile_int,
    _positive_runtime_int,
)
from simulator.optimize.physics import GATE_ORDER
from simulator.optimize.profiles import (
    ProfileValidationError,
    _validate_constraints,
    _validate_furnace_temperature_cap,
    constrained_max_profile,
)
from simulator.optimize.study import (
    StudyConfig,
    StudyAbort,
    _finite as study_finite,
    _finite_or_infinite,
    _positive_int as study_positive_int,
    _resolve_two_phase_config,
)
from simulator.overhead import (
    OverheadConfigurationError,
    OverheadGasModel,
    _required_positive_finite_float,
)
from engines.builtin.overhead_bleed import controlled_flow_capacity
from engines.builtin.overhead_gas_equilibrium import (
    BuiltinOverheadGasEquilibriumProvider,
)
from simulator.reduced_real_determinism import (
    ControlQuantization,
    _control_float as reduced_control_float,
)
from simulator.run_executor import _coerce_nonnegative_hours
from simulator.runner import CampaignPhase, PyrolysisRun, RunnerError
from simulator.runner import (
    PresetRunnerError,
    _additives_with_c3_alkali_dosing,
    _c3_alkali_dosing_kg_by_species,
    _apply_sio_wall_sweep_controls,
    _positive_mass_kg,
    _prepare_sio_campaign_start,
    _preset_duration_h,
    build_sio_yield_report,
)
from simulator.scalar_boundary import is_declared_real_scalar
from simulator.session import _finite_float as session_finite_float, drive_session
from simulator.stage0_harness import _positive_float as stage0_positive_float
from simulator.state import CondensationTrain, MeltState, clamp_stir_factor
from simulator.thermal_budget import _finite as thermal_finite
from simulator.interpolation_uncertainty import (
    _finite_float as interpolation_finite,
    _stable_point_key,
)
from simulator.reduced_real_cache_interpolation import (
    _simplex_barycentric_weights,
    _weighted_average_scalar,
)
from simulator.transport_regime import _require_nonnegative, _require_positive
from simulator.wall_advisor import (
    _optional_float as wall_optional_float,
    _validated_knob,
)
from simulator.vapour_rail.kinetics_anchors import (
    KineticsAnchorError,
    alpha_provenance_from_mapping,
)
from simulator.vapour_rail.activity import (
    StandardStateIdentity,
    activity_from_chemical_potentials,
    henrian_unknown_gamma_upper_bound,
    prove_pressure_monotone_nondecreasing_in_activity,
)
from simulator.vapour_rail.channels import (
    CHANNEL_O2,
    REACTION_PLANE_MELT_INTERFACE,
    ReactionThermoInputs,
    clamp_physical_pO2_bar,
    compile_channel_term_from_binding,
    compile_o2_channel_term,
    o2_potential_from_pO2_bar,
)
from simulator.vapour_rail.catalog import (
    CatalogCompileError,
    CompiledPressureEvaluator,
    PressureObservable,
    ValidationStatus,
    _ReferencePressureModel,
)
from simulator.vapour_rail.request import (
    VapourRequestConstructionError,
    _positive_mol,
    _require_readable_mol,
)


BOOL_POISON = (True, False, np.bool_(True), np.bool_(False))

EXPECTED_NUMERIC_CONTROL_INPUTS = frozenset(
    {
        "T_K",
        "T_C",
        "ambient_pressure_bar",
        "available_kg",
        "bleed_conductance_kg_s",
        "bleed_conductance_kg_s_per_bar",
        "ca_condenser_temperature_C",
        "ca_shuttle_rate_fraction",
        "ca_shuttle_reserve_ca_product_fraction",
        "carrier_stoichiometry",
        "capture_fraction",
        "capture_mol",
        "captured_ca_mol",
        "cavern_capacity_kg",
        "char_c_mol",
        "condensed_kg",
        "current_A",
        "dn_to_headspace_mol",
        "dt_hr",
        "escaped_source_kg_override",
        "extent_fraction",
        "external_o2_in_overhead_mol",
        "feed_kg",
        "feo_mol",
        "gas_temperature_K",
        "headspace_temperature_K",
        "headspace_volume_m3",
        "hold_temp_C",
        "internal_o2_capacity_mol",
        "intrinsic_fO2_log",
        "k_mix_per_hr",
        "k_relief_kg_hr_Pa",
        "liquid_fraction",
        "log_fO2",
        "melt_density_kg_m3",
        "melt_fO2_log",
        "melt_sio2_kg",
        "melt_surface_area_m2",
        "melt_surface_renewal_base_kg_s_m2_pa",
        "mol_Al_produced",
        "mol_Al_product",
        "native_fe_mol",
        "native_fe_vapor_mol",
        "o2_bubbler_eta_absorb_default",
        "o2_bubbler_kg_per_hr",
        "o2_bubbler_target_fO2_log",
        "o2_mol",
        "o2_per_c_mol",
        "objective_extent_mol",
        "oxidant_kg",
        "overhead_pressure_pa",
        "pO2_bar",
        "pO2_mbar",
        "p_downstream_bar",
        "p_open_Pa",
        "p_ref_Pa",
        "p_total_bar",
        "p_total_mbar",
        "pipe_diameter_m",
        "pressure_bar",
        "rate_kg_hr",
        "reagent_available_kg",
        "remaining_kg_hr",
        "solid_char_c_kg",
        "source_stoichiometry",
        "temperature_C",
        "temperature_K",
        "thermo_margin_kj_per_mol_o2",
        "transport_extent_mol",
        "vacuum_floor_bar",
        "vessel_rating_Pa",
        "voltage_V",
        "wall_deposit_fraction",
        "wall_temperature_K",
    }
)

EXPECTED_BOOLEAN_CONTROL_INPUTS = frozenset(
    {
        "accumulator_enabled",
        "active_ca_condensation_route",
        "allow_partial_extent",
        "allow_unmeasured_alpha_fallback",
        "back_reduction",
        "commit_empty_transition",
        "dedicated_ca_condenser",
        "diagnostic_only",
        "force_drain_all",
        "gas_resistance_enabled",
        "kinetic_driven_above_crossover",
        "melt_resistance_enabled",
        "product_routing",
        "route_uncaptured_to_wall",
        "thermo_margin_favorable",
        "vapour_batch_flux_shadow_equal",
    }
)

def _evalspec(**changes: Any) -> EvalSpec:
    values = {
        "recipe_id": "recipe",
        "feedstock_recipe_digest": "feedstock-digest",
        "feedstock_id": "feedstock",
        "profile_id": "profile",
        "fidelity": "high",
        "code_version": "test",
        "data_digests": {},
    }
    values.update(changes)
    return EvalSpec(**values)


def _intent_request(**changes: Any) -> IntentRequest:
    values = {
        "intent": ChemistryIntent.VAPOR_PRESSURE,
        "account_view": ProviderAccountView({}, {}),
        "temperature_C": 1200.0,
        "pressure_bar": 1.0,
        "fO2_log": -8.0,
    }
    values.update(changes)
    return IntentRequest(**values)


def _dummy_sio_sim() -> SimpleNamespace:
    manager = SimpleNamespace(
        overrides={},
        get_temp_target=lambda campaign, campaign_hour, melt: (1000.0, 5.0),
        _clamp_to_furnace_max=lambda value: value,
    )
    sim = SimpleNamespace(
        setpoints={},
        campaign_mgr=manager,
        melt=SimpleNamespace(
            temperature_C=25.0,
            pO2_mbar=0.0,
            p_total_mbar=0.0,
            atmosphere=None,
        ),
        overhead=SimpleNamespace(composition={}),
        _condensation_model=None,
        _configure_overhead_headspace=lambda campaign: None,
        _current_melt_redox_fO2_log=lambda: -8.0,
        _refresh_oxygen_reservoir_without_exchange=lambda **kwargs: None,
    )
    return sim


def _sio_start(value: Any) -> float:
    sim = _dummy_sio_sim()
    _prepare_sio_campaign_start(sim, t_low_c=value)
    return sim.melt.temperature_C


def _sio_ramp(value: Any) -> float:
    sim = _dummy_sio_sim()
    _prepare_sio_campaign_start(sim, ramp_c_per_hr=value)
    return sim.campaign_mgr.overrides["C2A"]["ramp_rate"]


def _sio_hold(value: Any) -> float:
    sim = _dummy_sio_sim()
    _prepare_sio_campaign_start(sim, t_hold_c=value)
    target, _ = sim.campaign_mgr.get_temp_target(CampaignPhase.C2A, 0, sim.melt)
    return target


def _sio_po2(value: Any) -> float:
    sim = _dummy_sio_sim()
    _apply_sio_wall_sweep_controls(sim, pO2_mbar=value)
    return sim.melt.pO2_mbar


def _sio_liner(value: Any) -> float:
    sim = _dummy_sio_sim()
    _apply_sio_wall_sweep_controls(sim, liner_temperature_c=value)
    return sim.campaign_mgr.overrides["C2A"]["overhead_headspace"][
        "liner_temperature_C"
    ]


def _alkali_entry(value: Any) -> None:
    _validate_alkali_activity_entry(
        Path("test.yaml"),
        "Na",
        {
            "ledger_species": "Na",
            "authoritative_ledger": False,
            "ledger_credit_species": "Na",
            "saturation": {
                "nominal_cold_wall": value,
                "primary_anchor": {"citation": "test"},
            },
            "status": "diagnostic",
            "ledger_forbidden": ["test"],
        },
    )


def _profile_fraction(key: str, value: Any) -> None:
    _validate_constraints(
        {"gates": [GATE_ORDER[0]], key: value},
        source="test-profile",
    )


def _two_phase(source: str, field: str, value: Any) -> Any:
    block = {"enabled": True, field: value}
    if source == "override":
        return _resolve_two_phase_config({}, block)
    return _resolve_two_phase_config({"two_phase_certify": block}, None)


def _campaign_scalar(method_name: str, field: str, value: Any) -> Any:
    manager = SimpleNamespace(
        _campaign_config=lambda campaign: {},
        _campaign_overrides=lambda campaign: {field: value},
    )
    method = getattr(CampaignManager, method_name)
    if method_name == "_c2a_staged_stage_depletion_log_slope_epsilon_per_hr":
        return method(manager, {field: value})
    return method(manager)


def _campaign_ramp(value: Any) -> float:
    manager = SimpleNamespace(
        _campaign_overrides=lambda campaign: {"ramp_rate": value},
    )
    return CampaignManager._apply_ramp_override(
        manager,
        CampaignPhase.C2A,
        None,
        5.0,
    )[1]


def _campaign_ramp_alias(field: str, value: Any) -> float:
    manager = SimpleNamespace(
        _campaign_overrides=lambda campaign: {field: value},
    )
    return CampaignManager._apply_ramp_override(
        manager,
        CampaignPhase.C2A,
        None,
        5.0,
    )[1]


def _drive_hours(value: Any) -> list[Any]:
    session = SimpleNamespace(is_complete=lambda: True)
    return list(drive_session(session, value, None))


def _quantization(value: Any) -> Any:
    return _parse_control_quantization_config(
        {
            "t_k_quantum": value,
            "pressure_bar_quantum": 0.1,
            "log_fo2_quantum": 0.1,
            "composition_sig_figs": 6,
        },
        unavailable_error_cls=ValueError,
    )


def _headspace_temperature(value: Any) -> float:
    sim = SimpleNamespace(
        melt=SimpleNamespace(temperature_C=1200.0),
        _overhead_headspace_config={"temperature_offset_K": value},
    )
    return PyrolysisSimulator._headspace_temperature_K(sim)


def _headspace_downstream_pressure(value: Any) -> float:
    sim = SimpleNamespace(
        melt=SimpleNamespace(atmosphere=SimpleNamespace(name="VACUUM")),
        _overhead_headspace_config={"downstream_pressure_bar": value},
    )
    return PyrolysisSimulator._headspace_downstream_pressure_bar(sim)


def _oxygen_exchange_k(field: str, value: Any) -> tuple[float, str]:
    sim = SimpleNamespace(_oxygen_exchange_config=lambda: {field: value})
    return PyrolysisSimulator._oxygen_exchange_k_m_s(sim, 1773.15)


def _oxygen_exchange_temperature(value: Any) -> tuple[float, str]:
    sim = SimpleNamespace(_oxygen_exchange_config=lambda: {})
    return PyrolysisSimulator._oxygen_exchange_k_m_s(sim, value)


def _oxygen_exchange_depth(value: Any) -> float:
    sim = SimpleNamespace(
        _oxygen_exchange_config=lambda: {"effective_melt_depth_m": value}
    )
    return PyrolysisSimulator._oxygen_exchange_effective_melt_depth_m(sim)


def _alpha_provenance(**changes: Any) -> Any:
    payload = {"value": 0.5, "source": "measured KEMS test anchor"}
    payload.update(changes)
    return alpha_provenance_from_mapping("Fe", payload)


def _magemin_config(field: str, value: Any) -> bool:
    backend = MAGEMinBackend()
    backend._locate_binary = lambda explicit: Path("/bin/true")
    backend._import_magemin_bridge = lambda requested: ("subprocess", None)
    config = {
        "warm_worker": True,
        "warm_pool_size": 1,
        "warm_call_timeout_s": 1.0,
        "worker_startup_timeout_s": 1.0,
    }
    config[field] = value
    return backend.initialize(
        config
    )


def _thermoengine_config(field: str, value: Any) -> bool:
    class DummyTransport:
        engine_version = "test"

        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def initialize(self) -> None:
            return None

        def health_check(self, *, timeout_s: float) -> tuple[bool, str]:
            return True, "ok"

        def close(self) -> None:
            return None

    backend = ThermoEngineBackend()
    backend._initialize_vaporock_delegate = lambda: None
    with patch(
        "simulator.melt_backend.thermoengine.ThermoEngineTransport",
        DummyTransport,
    ):
        return backend.initialize({field: value})


def _vaporock_config(field: str, value: Any) -> tuple[bool, str | None]:
    backend = VapoRockBackend()
    result = backend.initialize({field: value})
    return result, backend._last_error


def _project_lifecycle_threshold(field: str, value: Any) -> Any:
    kwargs = {
        "trajectory": (),
        "segment_area_m2": {},
        "rho_deposit_kg_m3": None,
        "thickness_limit_m": None,
        "resinter_threshold_kg": None,
    }
    kwargs[field] = value
    return project_lifecycle(**kwargs)


def _validate_melt_pressure(value: Any) -> None:
    melt = MeltState(pO2_mbar=value, p_total_mbar=2.0)
    melt.validate_melt_pressures()


def _set_stir_factor(value: Any) -> None:
    melt = MeltState()
    melt.stir_factor = value


def _request_with_controls(**controls: Any) -> IntentRequest:
    return _intent_request(control_inputs=controls)


def _standard_state() -> StandardStateIdentity:
    return StandardStateIdentity(
        convention="test",
        phase="liquid",
        reference_pressure_bar=1.0,
    )


def _sulfsat_scalar(field: str, value: Any) -> str:
    gate = SulfSatGate()
    gate.is_available = lambda: True
    values = {
        "liquid_comp_wt": {"SiO2": 50.0},
        "T_K": 1473.15,
        "P_bar": 1.0,
        "fO2_log": -8.0,
        "S_input_ppm": 100.0,
    }
    values[field] = value
    return gate.compute_sulfur_saturation(**values).calibration_status


def _sulfsat_composition(value: Any) -> str:
    gate = SulfSatGate()
    gate.is_available = lambda: True
    return gate.compute_sulfur_saturation(
        liquid_comp_wt={"SiO2": value},
        T_K=1473.15,
        P_bar=1.0,
        fO2_log=-8.0,
        S_input_ppm=100.0,
    ).calibration_status


def _compiled_pressure_activity(value: Any) -> Any:
    evaluator = CompiledPressureEvaluator(
        species_id="test",
        evaluator_family="tabulated_equilibrium",
        pressure_observable=PressureObservable.EQUILIBRIUM_PARTIAL_PRESSURE,
        species_basis="test",
        valid_temperature_K=(900.0, 1100.0),
        validation_status=ValidationStatus.PENDING,
        reference_model=_ReferencePressureModel(
            evaluator_family="tabulated_equilibrium",
            coefficients={},
            points=((900.0, 1.0), (1100.0, 1.0)),
        ),
        extrapolation_policy="conservative_slope_continuation",
        out_of_range_status="out_of_range_conservative_continuation",
        acquisition_flag="test",
        activity_exponent=1.0,
    )
    return evaluator.evaluate(
        1000.0,
        reaction_inputs=ReactionThermoInputs(
            reaction_id="test",
            state_fingerprint="test",
            activities={"FeO": value},
        ),
    )


def _condensation_model(field: str, value: Any) -> CondensationModel:
    kwargs = {field: value}
    return CondensationModel(CondensationTrain.create_default(), **kwargs)


def _configure_condensation(field: str, value: Any) -> CondensationModel:
    model = CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(**{field: value})
    return model


@dataclass(frozen=True)
class HardCase:
    id: str
    call: Callable[[Any], Any]
    exception: type[BaseException]
    honest_values: tuple[Any, ...] = (1, 1.5)


HARD_CASES = (
    HardCase("campaign_float", lambda v: CampaignManager._float(v, 0.0), ValueError),
    HardCase("session_finite", lambda v: session_finite_float(v, "field"), ValueError),
    HardCase("phase_caller_fraction", _caller_fraction, InvalidLiquidFractionError, (0, 0.5)),
    HardCase("phase_finite", lambda v: phase_finite(v, "field"), ValueError),
    HardCase("lab_geometry", lambda v: _required_finite(v, "holder.area_m2"), LabGeometryError),
    HardCase("overhead", lambda v: _required_positive_finite_float(v, "field"), OverheadConfigurationError),
    HardCase("transport_positive", lambda v: _require_positive(v, name="x", category="invalid_x"), TypeError),
    HardCase("transport_nonnegative", lambda v: _require_nonnegative(v, name="x", category="invalid_x"), TypeError, (0, 0.5)),
    HardCase("thermal_budget", lambda v: thermal_finite(v, "field"), ValueError),
    HardCase("oxygen_bubbler", lambda v: _finite_nonnegative_control({"x": v}, "x"), ValueError, (0, 0.5)),
    HardCase("wall_advisor", lambda v: _validated_knob("x", v), TypeError, (0, 0.5)),
    HardCase("furnace_materials", lambda v: furnace_finite_float(v, "field"), ValueError),
    HardCase("preset_duration", lambda v: _preset_duration_h(v, "duration_h", {}), PresetRunnerError),
    HardCase("profile_stream_fraction", lambda v: _profile_fraction("stream_purity_min", v), ProfileValidationError, (1, 0.5)),
    HardCase("profile_extraction_fraction", lambda v: _profile_fraction("extraction_min_fraction", v), ProfileValidationError, (1, 0.5)),
    HardCase("profile_furnace_cap", lambda v: _validate_furnace_temperature_cap(v, source="profile", where="constraints.furnace_T_max_C"), ProfileValidationError, (1300, 1300.5)),
    HardCase("constrained_furnace", lambda v: constrained_max_profile({}, furnace_T_max_C=v), TypeError),
    HardCase("constrained_cycle", lambda v: constrained_max_profile({}, cycle_time_max_h=v), TypeError),
    HardCase("evalspec_hours", lambda v: _evalspec(hours=v), TypeError, (2,)),
    HardCase("evalspec_mass", lambda v: _evalspec(mass_kg=v), TypeError),
    HardCase("pyrolysis_hours", lambda v: PyrolysisRun(feedstock_id="feed", hours=v), TypeError),
    HardCase("runner_mass", _positive_mass_kg, RunnerError),
    HardCase("sio_start", _sio_start, TypeError),
    HardCase("sio_ramp", _sio_ramp, TypeError),
    HardCase("sio_hold", _sio_hold, TypeError),
    HardCase("sio_po2", _sio_po2, TypeError, (0, 0.5)),
    HardCase("sio_liner", _sio_liner, TypeError),
    HardCase("intent_temperature", lambda v: _intent_request(temperature_C=v), TypeError),
    HardCase("intent_pressure", lambda v: _intent_request(pressure_bar=v), TypeError),
    HardCase("intent_fo2", lambda v: _intent_request(fO2_log=v), TypeError),
    HardCase(
        "builtin_control_float",
        lambda v: builtin_control_float({"x": v}, "x"),
        ValueError,
    ),
    HardCase("alpha_scalar", _coerce_alpha_by_species, TypeError),
    HardCase("alpha_species", lambda v: _coerce_alpha_by_species({"Fe": v}), TypeError),
    HardCase("alpha_envelope", lambda v: _coerce_alpha_envelope_by_species({"Fe": [v, 1.5]}), TypeError, (0, 0.5)),
    HardCase("cco", lambda v: _require_finite_positive(v, "temperature_K"), TypeError),
    HardCase("alkali_activity", _alkali_entry, ValueError),
    HardCase("core_declared_range", lambda v: PyrolysisSimulator._declared_nonnegative_number([v, 1.5], "field"), ValueError, (0, 0.5)),
    HardCase("surface_pressure", lambda v: _feedstock_surface_pressure_mbar({"surface_pressure_mbar": v}), TypeError, (0, 0.5)),
    HardCase("environment_surface_pressure", lambda v: _feedstock_surface_pressure_mbar({"environment": {"surface_pressure_mbar": v}}), TypeError, (0, 0.5)),
    HardCase("vacuum_floor", lambda v: _positive_finite_bar(v, field="vacuum_floor_bar"), ValueError),
    HardCase("cli_positive_int", _positive_int, argparse.ArgumentTypeError),
    HardCase("cli_nonnegative_int", _non_negative_int, argparse.ArgumentTypeError, (0, 1.5)),
    HardCase("cli_positive_float", _positive_float, argparse.ArgumentTypeError),
    HardCase("doe_optional_float", doe_optional_float, TypeError),
    HardCase("eval_mass", _positive_eval_mass_kg, EvaluationInputError),
    HardCase("objective_runtime", lambda v: _positive_runtime_int(v, "runtime"), ObjectiveComputationError),
    HardCase("objective_finite", lambda v: objective_finite_float(v, "value"), ObjectiveComputationError),
    HardCase("study_finite", lambda v: study_finite(v, "value"), StudyAbort),
    HardCase("study_finite_or_infinite", lambda v: _finite_or_infinite(v, "value"), StudyAbort),
    HardCase("study_positive_int", lambda v: study_positive_int(v, "value"), StudyAbort),
    HardCase("two_phase_override_top_k", lambda v: _two_phase("override", "top_k", v), TypeError),
    HardCase("two_phase_override_threshold", lambda v: _two_phase("override", "disagreement_threshold", v), TypeError),
    HardCase("two_phase_profile_top_k", lambda v: _two_phase("profile", "top_k", v), TypeError),
    HardCase("two_phase_profile_threshold", lambda v: _two_phase("profile", "disagreement_threshold", v), TypeError),
    HardCase("run_executor_hours", _coerce_nonnegative_hours, TypeError, (0, 1.5)),
    HardCase(
        "campaign_depletion_fraction",
        lambda v: _campaign_scalar(
            "_c2a_staged_depletion_flux_decay_fraction",
            "depletion_flux_decay_fraction",
            v,
        ),
        ValueError,
        (0, 0.5),
    ),
    HardCase(
        "campaign_depletion_slope",
        lambda v: _campaign_scalar(
            "_c2a_staged_stage_depletion_log_slope_epsilon_per_hr",
            "depletion_log_slope_epsilon_per_hr",
            v,
        ),
        ValueError,
        (0, 0.5),
    ),
    HardCase("campaign_ramp", _campaign_ramp, ValueError),
    HardCase(
        "campaign_temperature_ramp_alias",
        lambda v: _campaign_ramp_alias("temperature_ramp_C_per_h", v),
        ValueError,
    ),
    HardCase(
        "campaign_ramp_rate_alias",
        lambda v: _campaign_ramp_alias("ramp_rate_C_per_h", v),
        ValueError,
    ),
    HardCase(
        "evaporation_stir",
        lambda v: _validated_stir_factor(v, axis="axial"),
        EvaporationFluxConfigurationError,
        (0, 1.5),
    ),
    HardCase(
        "objective_profile_int",
        lambda v: _positive_profile_int(v, "field"),
        ObjectiveProfileError,
        (1, 2.0),
    ),
    HardCase(
        "coating_finite",
        lambda v: coating_finite_float(v, "field"),
        FoulingProjectionError,
    ),
    HardCase(
        "coating_lifecycle_thickness",
        lambda v: _project_lifecycle_threshold("thickness_limit_m", v),
        FoulingProjectionError,
    ),
    HardCase(
        "coating_lifecycle_resinter",
        lambda v: _project_lifecycle_threshold("resinter_threshold_kg", v),
        FoulingProjectionError,
    ),
    HardCase(
        "coating_resinter_total",
        lambda v: campaigns_to_resinter_total(
            {},
            resinter_threshold_kg=v,
            authoritative_for_resinter=False,
        ),
        FoulingProjectionError,
    ),
    HardCase("alphamelts_timeout", _validated_timeout_s, AlphaMELTSConfigurationError),
    HardCase(
        "magemin_warm_timeout",
        lambda v: _magemin_config("warm_call_timeout_s", v),
        ValueError,
    ),
    HardCase(
        "magemin_pool_size",
        lambda v: _magemin_config("warm_pool_size", v),
        ValueError,
        (1, 2.0),
    ),
    HardCase(
        "magemin_startup_timeout",
        lambda v: _magemin_config("worker_startup_timeout_s", v),
        ValueError,
    ),
    HardCase(
        "thermoengine_health_timeout",
        lambda v: _thermoengine_config("thermoengine_health_timeout_s", v),
        ValueError,
    ),
    HardCase(
        "thermoengine_equilibrate_timeout",
        lambda v: _thermoengine_config("thermoengine_equilibrate_timeout_s", v),
        ValueError,
    ),
    HardCase("quantization", _quantization, ValueError),
    HardCase(
        "quantization_direct",
        lambda v: ControlQuantization(v, 0.1, 0.1, 6),
        ValueError,
    ),
    HardCase(
        "quantization_pressure",
        lambda v: ControlQuantization(0.1, v, 0.1, 6),
        ValueError,
    ),
    HardCase(
        "quantization_fo2",
        lambda v: ControlQuantization(0.1, 0.1, v, 6),
        ValueError,
    ),
    HardCase(
        "quantization_sig_figs",
        lambda v: ControlQuantization(0.1, 0.1, 0.1, v),
        ValueError,
        (1,),
    ),
    HardCase("imcc_fraction", _as_fraction, TypeError),
    HardCase("headspace_temperature_offset", _headspace_temperature, TypeError),
    HardCase(
        "headspace_downstream_pressure",
        _headspace_downstream_pressure,
        TypeError,
        (0, 0.5),
    ),
    HardCase("oxygen_exchange_temperature", _oxygen_exchange_temperature, ValueError),
    HardCase(
        "oxygen_exchange_k_ref",
        lambda v: _oxygen_exchange_k("k_O_ref_m_s", v),
        ValueError,
    ),
    HardCase(
        "oxygen_exchange_k_min",
        lambda v: _oxygen_exchange_k("k_O_min_m_s", v),
        ValueError,
        (1.0e-6, 2.0e-6),
    ),
    HardCase(
        "oxygen_exchange_k_max",
        lambda v: _oxygen_exchange_k("k_O_max_m_s", v),
        ValueError,
    ),
    HardCase(
        "oxygen_exchange_reference_temperature",
        lambda v: _oxygen_exchange_k("T_ref_K", v),
        ValueError,
        (1000, 1500.0),
    ),
    HardCase(
        "oxygen_exchange_activation_energy",
        lambda v: _oxygen_exchange_k("Ea_J_mol", v),
        ValueError,
    ),
    HardCase("oxygen_exchange_depth", _oxygen_exchange_depth, ValueError),
    HardCase(
        "alpha_provenance_value",
        lambda v: _alpha_provenance(value=v),
        KineticsAnchorError,
        (0, 0.5),
    ),
    HardCase(
        "alpha_provenance_envelope",
        lambda v: _alpha_provenance(envelope=[v, 1.0]),
        KineticsAnchorError,
        (0, 0.5),
    ),
    HardCase(
        "alpha_provenance_temperature_range",
        lambda v: _alpha_provenance(temperature_range_K=[v, 1500.0]),
        KineticsAnchorError,
    ),
    HardCase(
        "alpha_provenance_tier",
        lambda v: _alpha_provenance(tier=v),
        KineticsAnchorError,
        (1, 2.0),
    ),
    HardCase("session_drive_hours", _drive_hours, TypeError),
    HardCase("wall_service_temperature", wall_optional_float, TypeError),
    HardCase(
        "runner_additives",
        lambda v: _additives_with_c3_alkali_dosing({"C": v}, {}),
        RunnerError,
    ),
    HardCase(
        "runner_c3_na_dosing",
        lambda v: _c3_alkali_dosing_kg_by_species(
            {"campaigns": {"C3": {"alkali_dosing": {"Na_kg": v}}}}
        ),
        RunnerError,
        (0, 0.5),
    ),
    HardCase(
        "runner_c3_k_dosing",
        lambda v: _c3_alkali_dosing_kg_by_species(
            {"campaigns": {"C3": {"alkali_dosing": {"K_kg": v}}}}
        ),
        RunnerError,
        (0, 0.5),
    ),
    HardCase(
        "account_pool_withdrawal",
        lambda v: allocate_pool_withdrawal({"feed": 2.0}, v),
        PoolWithdrawalError,
    ),
    HardCase(
        "cost_vector",
        lambda v: CostVector(electrical_kWh=v),
        TypeError,
    ),
    HardCase(
        "cost_vector_scale",
        lambda v: CostVector(electrical_kWh=1.0).scale(v),
        TypeError,
    ),
    HardCase(
        "cost_lot",
        lambda v: CostLot("lot", "account", "Fe", v, CostVector()),
        TypeError,
    ),
    HardCase(
        "melt_regime",
        lambda v: melt_regime(liquid_fraction=v),
        TypeError,
        (0, 0.5),
    ),
    HardCase(
        "wall_deposition_flux",
        lambda v: continuous_wall_deposition_flux(
            bulk_pressure_pa=v,
            equilibrium_pressure_pa=0.5,
            collision_coefficient_mol_m2_s_pa=1.0,
            sticking_coefficient=1.0,
            gas_resistance_pa_m2_s_mol=0.0,
            wall_temperature_K=1000.0,
        ),
        TypeError,
    ),
    HardCase(
        "controlled_flow_capacity",
        lambda v: controlled_flow_capacity(
            pipe_capacity_kg_hr=v,
            equipment_capacity_kg_hr=2.0,
            evolved_flux_kg_hr=1.0,
            upstream_pressure_bar=1.0,
        ),
        TypeError,
    ),
    HardCase(
        "melt_fraction_sample_temperature",
        lambda v: MeltFractionSample(v, 0.5),
        TypeError,
    ),
    HardCase(
        "melt_fraction_sample_fraction",
        lambda v: MeltFractionSample(1000.0, v),
        TypeError,
        (0, 0.5),
    ),
    HardCase(
        "liquidus_result",
        lambda v: LiquidusSolidusResult(
            liquidus_T_C=v,
            solidus_T_C=0.0,
            liquid_fraction=0.5,
        ),
        TypeError,
    ),
    HardCase(
        "liquidus_result_iterations",
        lambda v: LiquidusSolidusResult(iterations=v),
        TypeError,
    ),
    HardCase(
        "equilibrium_path_result_temperature",
        lambda v: EquilibriumCrystallizationPathResult(liquidus_T_C=v),
        TypeError,
    ),
    HardCase(
        "equilibrium_path_result_fraction",
        lambda v: EquilibriumCrystallizationPathResult(liquid_fraction=v),
        TypeError,
        (0, 0.5),
    ),
    HardCase(
        "equilibrium_path_result_iterations",
        lambda v: EquilibriumCrystallizationPathResult(iterations=v),
        TypeError,
    ),
    HardCase(
        "liquid_fraction_path_temperature",
        lambda v: LiquidFractionPathPoint(v, 0.5),
        TypeError,
    ),
    HardCase(
        "liquid_fraction_path_fraction",
        lambda v: LiquidFractionPathPoint(1000.0, v),
        TypeError,
        (0, 0.5),
    ),
    HardCase(
        "liquid_fraction_path_composition",
        lambda v: LiquidFractionPathPoint(1000.0, 0.5, {"SiO2": v}),
        TypeError,
    ),
    HardCase(
        "provider_account_nested_mol",
        lambda v: ProviderAccountView({"melt": {"FeO": v}}, {}),
        TypeError,
        (0, 0.5),
    ),
    HardCase(
        "ledger_proposal_debit",
        lambda v: LedgerTransitionProposal(
            {"melt": {"FeO": v}},
            {"product": {"FeO": 1.0}},
        ),
        TypeError,
        (0, 0.5),
    ),
    HardCase(
        "ledger_proposal_credit",
        lambda v: LedgerTransitionProposal(
            {"melt": {"FeO": 1.0}},
            {"product": {"FeO": v}},
        ),
        TypeError,
        (0, 0.5),
    ),
    HardCase(
        "ledger_proposal_atom_balance",
        lambda v: LedgerTransitionProposal({}, {}, atom_balance_proof={"Fe": v}),
        TypeError,
        (0, 0.5),
    ),
    HardCase(
        "stage0_required_accounting",
        lambda v: _stage0_required_non_negative_float(v, "mass"),
        AccountingError,
        (0, 0.5),
    ),
    HardCase(
        "vapour_request_readable_mol",
        lambda v: _require_readable_mol(v, species_id="FeO", account="melt"),
        VapourRequestConstructionError,
        (0, 0.5),
    ),
    HardCase(
        "vapour_request_positive_mol",
        lambda v: _positive_mol({"FeO": v}, "FeO"),
        VapourRequestConstructionError,
        (0, 0.5),
    ),
    HardCase(
        "chemical_potential_mu",
        lambda v: activity_from_chemical_potentials(v, 0.0, 1000.0),
        TypeError,
    ),
    HardCase(
        "chemical_potential_mu0",
        lambda v: activity_from_chemical_potentials(0.0, v, 1000.0),
        TypeError,
    ),
    HardCase(
        "chemical_potential_temperature",
        lambda v: activity_from_chemical_potentials(0.0, 0.0, v),
        TypeError,
    ),
    HardCase(
        "o2_term_signed_stoichiometry",
        lambda v: compile_o2_channel_term(
            signed_nu_o2=v,
            target_nu=1.0,
            reaction_plane=REACTION_PLANE_MELT_INTERFACE,
        ),
        TypeError,
    ),
    HardCase(
        "o2_term_target_stoichiometry",
        lambda v: compile_o2_channel_term(
            signed_nu_o2=-1.0,
            target_nu=v,
            reaction_plane=REACTION_PLANE_MELT_INTERFACE,
        ),
        TypeError,
    ),
    HardCase(
        "bound_channel_signed_stoichiometry",
        lambda v: compile_channel_term_from_binding(
            participant_formula="O2",
            channel_id=CHANNEL_O2,
            signed_nu=v,
            target_nu=1.0,
            required_plane=REACTION_PLANE_MELT_INTERFACE,
        ),
        TypeError,
    ),
    HardCase(
        "bound_channel_target_stoichiometry",
        lambda v: compile_channel_term_from_binding(
            participant_formula="O2",
            channel_id=CHANNEL_O2,
            signed_nu=-1.0,
            target_nu=v,
            required_plane=REACTION_PLANE_MELT_INTERFACE,
        ),
        TypeError,
    ),
    HardCase("physical_po2_clamp", clamp_physical_pO2_bar, TypeError),
    HardCase(
        "o2_potential_temperature",
        lambda v: o2_potential_from_pO2_bar(
            pO2_bar=1.0,
            temperature_K=v,
            reaction_plane=REACTION_PLANE_MELT_INTERFACE,
        ),
        TypeError,
    ),
    HardCase(
        "o2_potential_reference_pressure",
        lambda v: o2_potential_from_pO2_bar(
            pO2_bar=1.0,
            temperature_K=1000.0,
            reaction_plane=REACTION_PLANE_MELT_INTERFACE,
            pO2_reference_bar=v,
        ),
        TypeError,
    ),
    HardCase(
        "default_vacuum_floor",
        lambda v: resolve_request_vacuum_floor_bar(_intent_request(), floor_bar=v),
        ValueError,
    ),
    HardCase(
        "compiled_pressure_activity",
        _compiled_pressure_activity,
        CatalogCompileError,
    ),
    HardCase(
        "condensation_constructor_wall_area",
        lambda v: _condensation_model("wall_surface_area_m2", v),
        ValueError,
    ),
    HardCase(
        "condensation_constructor_wall_temperature",
        lambda v: _condensation_model("wall_temperature_C", v),
        ValueError,
    ),
    HardCase(
        "condensation_wall_temperature",
        lambda v: _configure_condensation("wall_temperature_C", v),
        ValueError,
    ),
    HardCase(
        "condensation_gas_temperature",
        lambda v: _configure_condensation("gas_temperature_C", v),
        ValueError,
    ),
    HardCase(
        "condensation_overhead_pressure",
        lambda v: _configure_condensation("overhead_pressure_mbar", v),
        ValueError,
        (0, 0.5),
    ),
    HardCase(
        "condensation_segment_temperature",
        lambda v: _configure_condensation(
            "pipe_segment_temperatures_C",
            {"stage_0_to_stage_1": v},
        ),
        ValueError,
    ),
    HardCase(
        "flowing_total_pressure",
        lambda v: _flowing_species_partial_pressures_pa({}, v),
        ValueError,
        (0, 0.5),
    ),
    HardCase(
        "flowing_reported_partial_pressure",
        lambda v: _flowing_species_partial_pressures_pa(
            {},
            100.0,
            reported_partial_pressures_mbar={"Fe": v},
        ),
        ValueError,
        (0, 0.5),
    ),
    HardCase("melt_pressure", _validate_melt_pressure, ValueError),
    HardCase("melt_stir_setter", _set_stir_factor, TypeError),
)


@pytest.mark.parametrize("raw", BOOL_POISON)
@pytest.mark.parametrize("case", HARD_CASES, ids=lambda case: case.id)
def test_bool_like_values_hard_refuse(case: HardCase, raw: Any) -> None:
    with pytest.raises(case.exception) as exc_info:
        case.call(raw)
    assert type(exc_info.value) is case.exception


@pytest.mark.parametrize("case", HARD_CASES, ids=lambda case: case.id)
def test_hard_boundaries_keep_honest_numeric_inputs(case: HardCase) -> None:
    for raw in case.honest_values:
        case.call(raw)


@dataclass(frozen=True)
class SoftCase:
    id: str
    call: Callable[[Any], Any]
    expected: Any
    honest_values: tuple[Any, ...] = (1, 1.5)


SOFT_CASES = (
    SoftCase("phase_optional_fraction", _optional_fraction, None, (1, 0.5)),
    SoftCase("mre_temperature", _coerce_temperature_K, MRE_REFERENCE_TEMPERATURE_K),
    SoftCase("alphamelts_control", lambda v: _control_float({"x": v}, "x", 9.0), 9.0),
    SoftCase(
        "builtin_control_default",
        lambda v: builtin_control_float({"x": v}, "x", 9.0),
        9.0,
    ),
    SoftCase(
        "alpha_provenance_legacy_alpha",
        lambda v: alpha_provenance_from_mapping(
            "Fe",
            {"alpha": v, "source": "measured KEMS test anchor"},
        ).value,
        None,
        (0, 0.5),
    ),
    SoftCase("stage0_positive", stage0_positive_float, None),
    SoftCase("runtime_alpha", lambda v: _scalar_runtime_alpha(v)[0], None),
    SoftCase("feedstock_range", lambda v: _representative_number([v, 1.0], "field"), None),
    SoftCase("core_representative_range", lambda v: PyrolysisSimulator._representative_number([v, 1.0]), None),
    SoftCase("eval_optional_float", _finite_optional_float, None),
    SoftCase("reduced_control", lambda v: reduced_control_float({"controls": {"x": v}}, "x"), None),
    SoftCase("structural_activity", structural_positive_float, 0.0),
    SoftCase("condensation_alpha", _coerce_alpha_s, 0.0),
    SoftCase(
        "equipment_geometry",
        EquipmentDesigner._positive_float_or_none,
        None,
    ),
    SoftCase(
        "overhead_liner_temperature",
        lambda v: OverheadGasModel._optional_float(v, 9.0),
        9.0,
    ),
    SoftCase("ca_finite", lambda v: ca_finite_float(v, 9.0), 9.0),
    SoftCase("interpolation_finite", interpolation_finite, None),
    SoftCase("mre_voltage_range", lambda v: coerce_mre_decomposition_voltage([v, 2.0]), None),
    SoftCase(
        "overhead_activity_mapping",
        lambda v: BuiltinOverheadGasEquilibriumProvider._positive_float_mapping(
            {"FeO": v}
        ).get("FeO"),
        None,
    ),
    SoftCase(
        "condensation_wall_temperature",
        lambda v: BuiltinCondensationRouteProvider._wall_temperature_K(
            {"wall_temperature_K": v},
            "process.wall_deposit",
        ),
        None,
    ),
    SoftCase(
        "condensation_wall_fraction",
        lambda v: BuiltinCondensationRouteProvider._wall_deposit_fraction(
            {"wall_deposit_fraction": v}
        ),
        0.0,
        (0, 0.5),
    ),
    SoftCase(
        "simplex_coordinates",
        lambda v: _simplex_barycentric_weights(
            [v],
            [[0.0], [1.0]],
        ),
        None,
        (0, 0.5),
    ),
    SoftCase(
        "vaporock_pool_size",
        lambda v: _vaporock_config("warm_pool_size", v)[0],
        False,
        (1, 2.0),
    ),
    SoftCase(
        "vaporock_warm_timeout",
        lambda v: _vaporock_config("warm_call_timeout_s", v)[0],
        False,
    ),
    SoftCase(
        "vaporock_startup_timeout",
        lambda v: _vaporock_config("worker_startup_timeout_s", v)[0],
        False,
    ),
    SoftCase(
        "liquidus_finder_parameter",
        lambda v: find_liquidus_solidus_by_fraction(
            lambda temperature: 0.5,
            min_T_C=v,
        ).status,
        "not_converged",
        (400, 400.5),
    ),
    SoftCase("stage0_accounting_positive", _stage0_positive_float, 0.0),
    SoftCase("stage0_accounting_optional", _stage0_optional_float, None),
    SoftCase(
        "activity_exponent_monotonicity",
        prove_pressure_monotone_nondecreasing_in_activity,
        False,
    ),
    SoftCase(
        "henrian_activity_exponent",
        lambda v: henrian_unknown_gamma_upper_bound(
            component_id="FeO",
            activity_exponent=v,
            standard_state=_standard_state(),
        ).value,
        None,
    ),
    SoftCase(
        "henrian_mole_fraction",
        lambda v: henrian_unknown_gamma_upper_bound(
            component_id="FeO",
            activity_exponent=1.0,
            standard_state=_standard_state(),
            mole_fraction=v,
        ).reason,
        "declared_ideal_solution_activity",
        (0, 0.5),
    ),
    SoftCase("stir_factor_clamp", clamp_stir_factor, 0.0),
    SoftCase(
        "stable_point_sequence",
        lambda v: _stable_point_key({"sequence_index": v, "point_id": "x"}),
        (10**12, "x"),
    ),
    SoftCase(
        "weighted_cache_result",
        lambda v: _weighted_average_scalar(
            [{"payload": {"equilibrium_result": {"x": v}}}],
            [1.0],
            "x",
        ),
        None,
    ),
    SoftCase(
        "overhead_melt_spec_fraction",
        lambda v: BuiltinOverheadGasEquilibriumProvider._coerce_melt_speciation_specs(
            {
                "Fe": {
                    "parent_oxide": "FeO",
                    "reference_oxide": "SiO2",
                    "reference_species": "SiO",
                    "fraction": v,
                    "activity_ratio_scale": 1.0,
                }
            }
        ).get("Fe"),
        None,
        (0, 0.5),
    ),
    SoftCase(
        "overhead_oxide_activity",
        lambda v: BuiltinOverheadGasEquilibriumProvider._oxide_activity_proxy_gamma_1(
            {"FeO": v}
        ).get("FeO"),
        None,
    ),
    SoftCase(
        "wall_deposit_account_fraction",
        lambda v: BuiltinCondensationRouteProvider._wall_deposit_account_fractions(
            {"wall_deposit_account_fractions": {"process.wall_deposit": v}},
            frozenset({"process.wall_deposit"}),
        ),
        {"process.wall_deposit": 1.0},
        (0.5, 1.5),
    ),
    SoftCase("sulfsat_temperature", lambda v: _sulfsat_scalar("T_K", v), "unavailable"),
    SoftCase("sulfsat_pressure", lambda v: _sulfsat_scalar("P_bar", v), "unavailable"),
    SoftCase("sulfsat_fo2", lambda v: _sulfsat_scalar("fO2_log", v), "unavailable"),
    SoftCase("sulfsat_input_s", lambda v: _sulfsat_scalar("S_input_ppm", v), "unavailable"),
    SoftCase(
        "sulfsat_fe3fet",
        lambda v: _sulfsat_scalar("Fe3Fet_Liq", v),
        "unavailable",
        (0, 0.5),
    ),
    SoftCase("sulfsat_composition", _sulfsat_composition, "unavailable"),
    SoftCase(
        "flowing_species_rate",
        lambda v: _flowing_species_partial_pressures_pa({"Fe": v}, 100.0),
        {},
    ),
)


@pytest.mark.parametrize("raw", BOOL_POISON)
@pytest.mark.parametrize("case", SOFT_CASES, ids=lambda case: case.id)
def test_bool_like_values_soft_refuse(case: SoftCase, raw: Any) -> None:
    assert case.call(raw) == case.expected


@pytest.mark.parametrize("case", SOFT_CASES, ids=lambda case: case.id)
def test_soft_boundaries_keep_honest_numeric_inputs(case: SoftCase) -> None:
    for raw in case.honest_values:
        assert case.call(raw) is not None


@pytest.mark.parametrize("raw", BOOL_POISON)
def test_shared_helper_rejects_bool_like_values(raw: Any) -> None:
    assert not is_declared_real_scalar(raw)


def test_shared_helper_accepts_int_float_and_compatibility_scalars() -> None:
    for raw in (1, 1.5, np.int64(2), np.float64(2.5)):
        assert is_declared_real_scalar(raw)
    assert is_declared_real_scalar("2.5", allow_numeric_str=True)
    assert not is_declared_real_scalar("2.5")
    assert not is_declared_real_scalar("not numeric", allow_numeric_str=True)


@pytest.mark.parametrize("raw", (3, 3.0, 3.7, np.int64(3), "3"))
def test_liquidus_max_points_keeps_base_numeric_coercions(raw: Any) -> None:
    expected = (1000.0, 1001.0, 1002.0)
    assert _temperature_grid(
        1000.0,
        1002.0,
        grid_step_C=1.0,
        max_points=raw,
    ) == expected

    result = build_equilibrium_crystallization_path(
        lambda _temperature_C: (0.5, {"SiO2": 100.0}),
        solidus_T_C=1000.0,
        liquidus_T_C=1002.0,
        grid_step_C=1.0,
        max_points=raw,
    )
    assert result.status == "ok"
    assert tuple(point.temperature_C for point in result.liquid_fraction_path) == expected


@pytest.mark.parametrize("raw", BOOL_POISON)
def test_liquidus_max_points_rejects_boolean_values(raw: Any) -> None:
    with pytest.raises(TypeError, match="max_points must be numeric"):
        _temperature_grid(
            1000.0,
            1002.0,
            grid_step_C=1.0,
            max_points=raw,
        )

    result = build_equilibrium_crystallization_path(
        lambda _temperature_C: (0.5, {"SiO2": 100.0}),
        solidus_T_C=1000.0,
        liquidus_T_C=1002.0,
        grid_step_C=1.0,
        max_points=raw,
    )
    assert result.status == "not_converged"
    assert result.warnings == (
        "equilibrium crystallization path failed: max_points must be numeric",
    )


@pytest.mark.parametrize("raw", (3, 3.0, 3.7, np.int64(3), "3"))
def test_liquidus_result_iterations_keep_base_int_coercions(raw: Any) -> None:
    assert LiquidusSolidusResult(iterations=raw).iterations == 3
    assert EquilibriumCrystallizationPathResult(iterations=raw).iterations == 3


@pytest.mark.parametrize("raw", (3, 3.0, 3.7, np.int64(3)))
def test_liquidus_bisection_limit_keeps_base_numeric_inputs(raw: Any) -> None:
    result = find_liquidus_solidus_by_fraction(
        lambda temperature_C: min(1.0, max(0.0, (temperature_C - 1000.0) / 10.0)),
        min_T_C=1000.0,
        max_T_C=1010.0,
        scan_step_C=5.0,
        max_bisection_iterations=raw,
    )
    assert not any("invalid finder parameter" in warning for warning in result.warnings)


@pytest.mark.parametrize("raw", BOOL_POISON)
def test_liquidus_bisection_limit_rejects_boolean_values(raw: Any) -> None:
    result = find_liquidus_solidus_by_fraction(
        lambda _temperature_C: 0.5,
        max_bisection_iterations=raw,
    )
    assert result.status == "not_converged"
    assert result.warnings == (
        "invalid finder parameter: max_bisection_iterations must be numeric",
    )


def _write_imcc_datapack(path: Path, domain_value: Any) -> None:
    parents = list(_EXPECTED_PARENT_OXIDES)
    rows = [
        {
            "complex": f"test_complex_{index}",
            "nu": {parent: 0 for parent in parents},
            "A": 0,
            "B": 0,
            "T_domain_K": [domain_value, 3000],
            "T_domain_basis": "test",
        }
        for index in range(38)
    ]
    path.write_text(
        json.dumps(
            {
                "imcc_sf04_datapack_version": "test",
                "parents": parents,
                "rows": rows,
            }
        )
    )


def test_imcc_synthetic_datapack_refuses_on_published_hash_before_field_validation(
    tmp_path: Path,
) -> None:
    # The published-identity default-deny gate (9000d5ab) validates the full-pack
    # canonical hash BEFORE any field-level validation, so a base-modified
    # synthetic pack can no longer reach the T_domain scalar boundary through
    # load_datapack at all. Bool-poison coverage for that boundary lives in the
    # shared-helper unit tests; this test pins the gate ORDERING so a future
    # reordering that exposes field validation to unpublished packs is caught.
    datapack_path = tmp_path / "synthetic-domain.json"
    _write_imcc_datapack(datapack_path, True)
    with pytest.raises(
        ImccMalformedDatapackError,
        match="canonical hash mismatch",
    ):
        load_datapack(datapack_path)


@pytest.mark.parametrize(
    ("raw", "expected"),
    (("<0.5", 0.5), (">2.5", 2.5), ("~1.4", 1.4), ("±0.05", 0.05)),
)
def test_mre_decorated_numeric_strings_remain_accepted(
    raw: str,
    expected: float,
) -> None:
    assert coerce_mre_decomposition_voltage(raw) == expected


def test_control_input_census_is_independent_and_complete() -> None:
    assert _DECLARED_REAL_SCALAR_CONTROL_INPUTS == EXPECTED_NUMERIC_CONTROL_INPUTS
    assert _LEGITIMATE_BOOLEAN_CONTROL_INPUTS == EXPECTED_BOOLEAN_CONTROL_INPUTS
    assert not (
        EXPECTED_NUMERIC_CONTROL_INPUTS & EXPECTED_BOOLEAN_CONTROL_INPUTS
    )


@pytest.mark.parametrize("field_name", sorted(EXPECTED_NUMERIC_CONTROL_INPUTS))
@pytest.mark.parametrize("raw", BOOL_POISON)
def test_kernel_control_numeric_fields_refuse_boolean_missing_input(
    field_name: str,
    raw: bool,
) -> None:
    with pytest.raises(TypeError, match="is missing"):
        _intent_request(control_inputs={field_name: raw})


@pytest.mark.parametrize("field_name", sorted(EXPECTED_NUMERIC_CONTROL_INPUTS))
@pytest.mark.parametrize("raw", (1, 1.5))
def test_kernel_control_numeric_fields_keep_honest_scalars(
    field_name: str,
    raw: int | float,
) -> None:
    request = _intent_request(control_inputs={field_name: raw})
    assert request.control_inputs[field_name] == raw


@pytest.mark.parametrize(
    "field_name",
    sorted(EXPECTED_BOOLEAN_CONTROL_INPUTS),
)
@pytest.mark.parametrize("flag", (True, False))
def test_kernel_declared_boolean_controls_remain_boolean(
    field_name: str,
    flag: bool,
) -> None:
    request = _intent_request(control_inputs={field_name: flag})
    assert request.control_inputs[field_name] is flag


@pytest.mark.parametrize("raw", BOOL_POISON)
def test_runtime_alpha_bool_is_classified_as_missing_input(raw: Any) -> None:
    assert _scalar_runtime_alpha(raw) == (None, "missing")
    assert _scalar_runtime_alpha({"value": raw})[0] is None


@pytest.mark.parametrize("raw", BOOL_POISON)
def test_public_sio_report_refuses_bool_hours_before_file_loading(raw: Any) -> None:
    with pytest.raises(TypeError):
        build_sio_yield_report(feedstock_id="lunar_mare_low_ti", hours=raw)


@pytest.mark.parametrize("flag", (True, False))
def test_legitimate_boolean_fields_remain_boolean(flag: bool) -> None:
    spec = _evalspec(
        c5_enabled=flag,
        allow_fallback_vapor=flag,
        force_builtin_vapor_pressure=flag,
        stop_at_stage0_exit=flag,
    )
    assert spec.c5_enabled is flag
    assert spec.allow_fallback_vapor is flag
    assert spec.force_builtin_vapor_pressure is flag
    assert spec.stop_at_stage0_exit is flag
    assert _resolve_two_phase_config({}, flag).enabled is flag
    assert _resolve_two_phase_config(
        {}, {"enabled": flag, "top_k": 2}
    ).enabled is flag
