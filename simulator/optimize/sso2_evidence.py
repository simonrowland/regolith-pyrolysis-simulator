"""SSO-2 owner-recipe evidence report surface."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from simulator.account_ids import METAL_PHASE_ACCOUNTS
from simulator.accounting.formulas import resolve_species_formula
from simulator.condensation_routing import accepted_species_for_stage_number
from simulator.core import PyrolysisSimulator
from simulator.optimize.physics import GateMargin, PhysicsConstraintSet
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.optimize.sso_r_owner_surface import (
    OWNER_CERTIFICATION_ASSERTION,
    OWNER_CERTIFIED_SURFACE_SOURCE,
    OWNER_RECIPE_GAS_COVER_MODE,
    OWNER_RECIPE_PN2_MBAR,
    OWNER_RECIPE_PO2_MBAR,
    OWNER_RECIPE_TOTAL_PRESSURE_MBAR,
)
from simulator.runner import PyrolysisRun
from simulator.run_executor import RunExecution, RunExecutor
from simulator.session import SimSession
from simulator.state import CampaignPhase


SSO2_OWNER_RECIPE_ID = "sso2_pn2_fe_drain_silica"
SSO2_FEEDSTOCK_ID = "lunar_mare_low_ti"
SSO2_STAGE_NUMBER = 3
SSO2_CERTIFIED_DOSE_SPECIES = "Na"
SSO2_CERTIFIED_DOSE_CALIBRATION_T_C = 1150.0
SSO2_CERTIFIED_PN2_MBAR = OWNER_RECIPE_PN2_MBAR
SSO2_CERTIFIED_PO2_MBAR = OWNER_RECIPE_PO2_MBAR
SSO2_CERTIFIED_TOTAL_PRESSURE_MBAR = OWNER_RECIPE_TOTAL_PRESSURE_MBAR
SSO2_CERTIFIED_SURFACE_SOURCE = OWNER_CERTIFIED_SURFACE_SOURCE
SSO2_SILICA_SPECIES = ("SiO", "SiO2", "Si")
SSO2_FE_TAP_ACCOUNT = "terminal.drain_tap_material"
SSO2_METAL_PHASE_ACCOUNT = "process.metal_phase"
SSO2_METAL_PHASE_ACCOUNTS = METAL_PHASE_ACCOUNTS
SSO2_MASS_BALANCE_TOLERANCE_PCT = 5.0e-12
SSO2_CHUNK3B_READER_HANDOFF = (
    "PhysicsConstraintSet.delivered_stream_purity plus the SSO-2 profile/objective "
    "reader added in chunk 3b; it must consume Stage 3 Fe contamination and Fe tap "
    "evidence, not just captured_stage_3_silica."
)
_EPS = 1.0e-12


def sso2_owner_recipe_patch() -> RecipePatch:
    """Named SSO-2 owner recipe preset; no purity threshold is invented here."""

    return RecipePatch.from_nested({
        "furnace_max_T_C": 1700.0,
        "campaigns": {
            "C2A_staged": {
                "order": "fe_then_sio",
                "default_hold_T_C": 1670.0,
                "max_hold_hr": 9,
                "stages": {
                    "fe_hot_hold": {
                        "duration_h": 1,
                        "ramp_rate_C_per_hr": 150.0,
                        "gas_cover_mode": OWNER_RECIPE_GAS_COVER_MODE,
                        "pO2_mbar": SSO2_CERTIFIED_PO2_MBAR,
                        "p_total_mbar": SSO2_CERTIFIED_TOTAL_PRESSURE_MBAR,
                    },
                    "sio_window": {
                        "target_C": 1650.0,
                        "duration_h": 3,
                        "ramp_rate_C_per_hr": 175.0,
                        "gas_cover_mode": OWNER_RECIPE_GAS_COVER_MODE,
                        "pO2_mbar": SSO2_CERTIFIED_PO2_MBAR,
                        "p_total_mbar": SSO2_CERTIFIED_TOTAL_PRESSURE_MBAR,
                    },
                },
            }
        },
    })


def sso2_owner_recipe_setpoints_patch(
    schema: RecipeSchema | None = None,
) -> Mapping[str, Any]:
    active_schema = schema or RecipeSchema()
    return active_schema.to_setpoints_patch(sso2_owner_recipe_patch())


def build_sso2_owner_recipe_execution(
    *,
    hours: int = 9,
    mass_kg: float = 1000.0,
    backend_name: str = "stub",
) -> RunExecution:
    setpoints_patch = sso2_owner_recipe_setpoints_patch()
    dose_kg = _certified_full_feo_equiv_na_dose_kg(
        mass_kg=float(mass_kg),
        backend_name=backend_name,
        setpoints_patch=setpoints_patch,
    )
    run = PyrolysisRun(
        feedstock_id=SSO2_FEEDSTOCK_ID,
        campaign="C2A_staged",
        hours=int(hours),
        mass_kg=float(mass_kg),
        backend_name=backend_name,
        additives_kg={SSO2_CERTIFIED_DOSE_SPECIES: dose_kg},
        setpoints_patch=setpoints_patch,
    )
    session = SimSession().start(run._session_config())
    _apply_certified_na_dose(session.simulator)
    return RunExecutor().execute_session(session, hours=int(hours))


def _certified_full_feo_equiv_na_dose_kg(
    *,
    mass_kg: float,
    backend_name: str,
    setpoints_patch: Mapping[str, Any],
) -> float:
    calibration_run = PyrolysisRun(
        feedstock_id=SSO2_FEEDSTOCK_ID,
        campaign="C2A_staged",
        hours=0,
        mass_kg=float(mass_kg),
        backend_name=backend_name,
        additives_kg={SSO2_CERTIFIED_DOSE_SPECIES: float(mass_kg)},
        setpoints_patch=setpoints_patch,
    )
    config = calibration_run._session_config()
    sim = PyrolysisSimulator(
        None,
        config.setpoints,
        config.feedstocks,
        config.vapor_pressures,
    )
    sim.load_batch(
        config.feedstock_id,
        config.mass_kg,
        additives_kg={SSO2_CERTIFIED_DOSE_SPECIES: float(mass_kg)},
    )
    base_feo_mol = float(
        sim.atom_ledger.mol_by_account("process.cleaned_melt").get("FeO", 0.0)
        or 0.0
    )
    before = len(sim.atom_ledger.transitions)
    _commit_certified_na_reduction(sim)
    transitions = sim.atom_ledger.transitions[before:]
    committed = [
        transition
        for transition in transitions
        if transition.name == "c3_na_shuttle_reduction"
    ]
    if not committed:
        raise RuntimeError("SSO-2 Na dose calibration did not reduce FeO")
    transition = committed[-1]
    reagent_mol = sim._transition_species_mol(
        transition,
        side="debits",
        account="process.reagent_inventory",
        species=SSO2_CERTIFIED_DOSE_SPECIES,
    )
    feo_mol = sim._transition_species_mol(
        transition,
        side="debits",
        account="process.cleaned_melt",
        species="FeO",
    )
    if reagent_mol <= 0.0 or feo_mol <= 0.0 or base_feo_mol <= 0.0:
        raise RuntimeError(
            "SSO-2 Na dose calibration transition had no FeO/reagent debit"
        )
    formula = resolve_species_formula(
        SSO2_CERTIFIED_DOSE_SPECIES,
        sim.species_formula_registry,
    )
    return base_feo_mol * (reagent_mol / feo_mol) * formula.molar_mass_kg_per_mol()


def _apply_certified_na_dose(sim: PyrolysisSimulator) -> None:
    before = len(sim.atom_ledger.transitions)
    _commit_certified_na_reduction(sim)
    committed = [
        transition
        for transition in sim.atom_ledger.transitions[before:]
        if transition.name == "c3_na_shuttle_reduction"
    ]
    if not committed:
        raise RuntimeError(
            "SSO-2 certified Na dose did not produce c3_na_shuttle_reduction"
        )
    sim.start_campaign(CampaignPhase.C2A_STAGED)


def _commit_certified_na_reduction(sim: PyrolysisSimulator) -> None:
    sim._init_shuttle_inventory(CampaignPhase.C3_NA)
    sim.melt.campaign = CampaignPhase.C3_NA
    sim.melt.temperature_C = SSO2_CERTIFIED_DOSE_CALIBRATION_T_C
    sim.melt.target_temperature_C = SSO2_CERTIFIED_DOSE_CALIBRATION_T_C
    sim.melt.campaign_hour = 1
    sim._shuttle_inject_Na(target_stage="feo_cleanup", liquid_fraction=1.0)


def sso2_owner_recipe_evidence(
    run_execution: Any,
    *,
    constraints: PhysicsConstraintSet | None = None,
) -> dict[str, Any]:
    active_constraints = constraints or PhysicsConstraintSet()
    trace = getattr(run_execution, "trace", None)
    purity_margin = active_constraints.delivered_stream_purity(trace)
    coating_margin = active_constraints.coating(trace)
    stage_species_kg, trace_status, trace_reason = _stage_species_kg_from_trace(trace)
    stage3_kg = {
        species: kg
        for (stage, species), kg in stage_species_kg.items()
        if stage == SSO2_STAGE_NUMBER
    }
    accepted = accepted_species_for_stage_number(SSO2_STAGE_NUMBER)
    stage3_total_kg = sum(stage3_kg.values())
    stage3_stream_available = trace_status == "available" and stage3_total_kg > _EPS
    if trace_status != "available":
        stage_status = trace_status
        stage_reason = trace_reason
    elif not stage3_stream_available:
        stage_status = "missing_stage_3_stream"
        stage_reason = "Stage 3 condensed stream is absent or zero"
    else:
        stage_status = "available"
        stage_reason = ""
    accepted_kg = sum(kg for species, kg in stage3_kg.items() if species in accepted)
    stage3_purity = accepted_kg / stage3_total_kg if stage3_stream_available else None
    stage3_fe_kg = stage3_kg.get("Fe", 0.0) if stage3_stream_available else None
    stage3_fe_wt_pct = (
        stage3_fe_kg / stage3_total_kg * 100.0
        if stage3_stream_available and stage3_fe_kg is not None
        else None
    )

    sim = getattr(run_execution, "simulator", run_execution)
    ledger = getattr(sim, "atom_ledger", None)
    tap_kg_by_species, tap_status, tap_reason = _ledger_account_kg(
        ledger, SSO2_FE_TAP_ACCOUNT
    )
    metal_phase_kg_by_species, metal_status, metal_reason = _ledger_accounts_kg(
        ledger, SSO2_METAL_PHASE_ACCOUNTS
    )
    product_fe_kg = _product_species_kg(sim, "Fe")
    native_split_count = _transition_count(ledger, "native_fe_saturation_split")
    dose_transition_count = _transition_count(ledger, "c3_na_shuttle_reduction")
    stage_gas_snapshot = _stage_gas_snapshot_payload(run_execution)
    native_partition_basis = _native_fe_partition_basis(run_execution, sim)
    fe_tap_total_kg = sum(tap_kg_by_species.values())
    fe_tap_si_impurity_kg = sum(
        tap_kg_by_species.get(species, 0.0) for species in SSO2_SILICA_SPECIES
    )
    fe_tap_si_impurity_wt_pct = _wt_pct(fe_tap_si_impurity_kg, fe_tap_total_kg)
    stage1_kg = {
        species: kg
        for (stage, species), kg in stage_species_kg.items()
        if stage == 1
    }
    stage1_total_kg = sum(stage1_kg.values())
    stage1_si_impurity_kg = sum(stage1_kg.get(species, 0.0) for species in SSO2_SILICA_SPECIES)
    stage1_si_impurity_wt_pct = _wt_pct(stage1_si_impurity_kg, stage1_total_kg)
    partition_basis_available = native_partition_basis.get("status") == "available"
    dependency_status = (
        "available"
        if native_split_count > 0 and partition_basis_available
        else "missing_fe_drain_vapor_partition"
    )
    dependency_reason = (
        ""
        if dependency_status == "available"
        else (
            "no native_fe_saturation_split transition observed for this run"
            if native_split_count <= 0
            else str(
                native_partition_basis.get(
                    "status_reason",
                    "native Fe partition basis missing",
                )
            )
        )
    )
    mass_balance = _mass_balance_closure(run_execution, trace)

    if trace_status != "available":
        status = trace_status
        status_reason = trace_reason
    elif not stage3_stream_available:
        status = "missing_stage_3_stream"
        status_reason = stage_reason
    elif dependency_status != "available":
        status = dependency_status
        status_reason = dependency_reason
    elif tap_status != "available":
        status = "missing_fe_tap_evidence"
        status_reason = tap_reason
    elif not purity_margin.feasible:
        status = "stage_stream_purity_failed"
        status_reason = purity_margin.detail
    else:
        status = "available"
        status_reason = ""

    return {
        "recipe_id": SSO2_OWNER_RECIPE_ID,
        "status": status,
        "status_reason": status_reason,
        "feedstock": SSO2_FEEDSTOCK_ID,
        "recipe_patch": sso2_owner_recipe_patch().to_nested(),
        "certified_sso_r_surface": _certified_surface_payload(
            sim,
            dose_transition_count=dose_transition_count,
            stage_gas_snapshot=stage_gas_snapshot,
        ),
        "reader_handoff_chunk3b": SSO2_CHUNK3B_READER_HANDOFF,
        "stage_3": {
            "status": stage_status,
            "status_reason": stage_reason,
            "accepted_species": sorted(accepted),
            "accepted_species_reader": "accepted_species_for_stage_number(3)",
            "silica_species_kg": _species_values(stage3_kg, SSO2_SILICA_SPECIES, stage3_stream_available),
            "silica_species_mol": _species_mol_values(
                run_execution, stage3_kg, SSO2_SILICA_SPECIES, stage3_stream_available
            ),
            "Fe_kg": stage3_fe_kg,
            "Fe_wt_pct": stage3_fe_wt_pct,
            "total_kg": stage3_total_kg if stage3_stream_available else None,
            "purity_fraction": stage3_purity,
            "purity_margin": (
                stage3_purity - active_constraints.stream_purity_min.value
                if stage3_purity is not None
                else None
            ),
            "purity_threshold": _threshold_payload(active_constraints.stream_purity_min),
        },
        "delivered_stream_purity": _gate_margin_payload(purity_margin),
        "fe_tap": {
            "status": tap_status,
            "status_reason": tap_reason,
            "account": SSO2_FE_TAP_ACCOUNT,
            "Fe_kg": tap_kg_by_species.get("Fe", 0.0) if tap_status == "available" else None,
            "total_kg": fe_tap_total_kg if tap_status == "available" else None,
            "SiO_Si_impurity_kg": (
                fe_tap_si_impurity_kg if tap_status == "available" else None
            ),
            "SiO_Si_impurity_wt_pct": (
                fe_tap_si_impurity_wt_pct if tap_status == "available" else None
            ),
            "species_kg": dict(sorted(tap_kg_by_species.items())) if tap_status == "available" else {},
        },
        "metal_product_path": {
            "status": metal_status,
            "status_reason": metal_reason,
            "account": SSO2_METAL_PHASE_ACCOUNT,
            "account_scope": list(SSO2_METAL_PHASE_ACCOUNTS),
            "Fe_kg": (
                metal_phase_kg_by_species.get("Fe", 0.0)
                if metal_status == "available"
                else None
            ),
            "product_ledger_Fe_kg": product_fe_kg,
            "species_kg": (
                dict(sorted(metal_phase_kg_by_species.items()))
                if metal_status == "available"
                else {}
            ),
        },
        "stage_1_or_metal_tap_si_impurity": {
            "stage_1_total_kg": stage1_total_kg if trace_status == "available" else None,
            "stage_1_SiO_Si_impurity_kg": (
                stage1_si_impurity_kg if trace_status == "available" else None
            ),
            "stage_1_SiO_Si_impurity_wt_pct": (
                stage1_si_impurity_wt_pct if trace_status == "available" else None
            ),
            "metal_tap_SiO_Si_impurity_kg": (
                fe_tap_si_impurity_kg if tap_status == "available" else None
            ),
            "metal_tap_SiO_Si_impurity_wt_pct": (
                fe_tap_si_impurity_wt_pct if tap_status == "available" else None
            ),
        },
        "wall_coating": _gate_margin_payload(coating_margin),
        "mass_balance": mass_balance,
        "fe_drain_vapor_partition_dependency": {
            "status": dependency_status,
            "status_reason": dependency_reason,
            "native_fe_saturation_split_count": native_split_count,
            "required_transition": "native_fe_saturation_split",
            "stage_gas_snapshot": stage_gas_snapshot,
            "native_fe_partition_basis": native_partition_basis,
            "tap_basis": {
                "status": tap_status,
                "status_reason": tap_reason,
                "account": SSO2_FE_TAP_ACCOUNT,
                "species_kg": (
                    dict(sorted(tap_kg_by_species.items()))
                    if tap_status == "available"
                    else {}
                ),
                "Fe_kg": (
                    tap_kg_by_species.get("Fe", 0.0)
                    if tap_status == "available"
                    else None
                ),
            },
        },
    }


def _certified_surface_payload(
    sim: Any,
    *,
    dose_transition_count: int,
    stage_gas_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    record = getattr(sim, "record", None)
    additives_kg = getattr(record, "additives_kg", {}) if record is not None else {}
    dose_kg = None
    if isinstance(additives_kg, Mapping):
        try:
            dose_kg = _finite_non_negative(
                additives_kg.get(SSO2_CERTIFIED_DOSE_SPECIES, 0.0),
                "certified Na dose",
            )
        except ValueError:
            dose_kg = None
    return {
        "source": SSO2_CERTIFIED_SURFACE_SOURCE,
        "certification_assertion": OWNER_CERTIFICATION_ASSERTION,
        "dose_species": SSO2_CERTIFIED_DOSE_SPECIES,
        "dose_kg": dose_kg,
        "dose_transition": "c3_na_shuttle_reduction",
        "dose_transition_count": dose_transition_count,
        "dose_calibration_temperature_C": SSO2_CERTIFIED_DOSE_CALIBRATION_T_C,
        "declared_pO2_mbar": SSO2_CERTIFIED_PO2_MBAR,
        "declared_pN2_mbar": SSO2_CERTIFIED_PN2_MBAR,
        "declared_p_total_mbar": SSO2_CERTIFIED_TOTAL_PRESSURE_MBAR,
        "pO2_mbar": stage_gas_snapshot.get("pO2_mbar"),
        "pN2_mbar": stage_gas_snapshot.get("pN2_mbar"),
        "p_total_mbar": stage_gas_snapshot.get("p_total_mbar"),
        "stage_gas_snapshot": dict(stage_gas_snapshot),
    }


def sso2_owner_recipe_objective_reader(
    run_execution: Any,
    *,
    constraints: PhysicsConstraintSet | None = None,
) -> tuple[float, dict[str, Any]]:
    evidence = sso2_owner_recipe_evidence(run_execution, constraints=constraints)
    reader: dict[str, Any] = {
        "reader": SSO2_OWNER_RECIPE_ID,
        "status": "available",
        "status_reason": "",
        "score_components": {},
        "consumed_fields": (
            "delivered_stream_purity.margin",
            "stage_3.Fe_kg",
            "stage_3.Fe_wt_pct",
            "fe_tap.Fe_kg",
            "wall_coating.margin",
            "wall_coating.status",
            "mass_balance.max_abs_error_pct",
        ),
        "evidence": evidence,
    }

    delivered = evidence.get("delivered_stream_purity", {})
    stage3 = evidence.get("stage_3", {})
    fe_tap = evidence.get("fe_tap", {})
    coating = evidence.get("wall_coating", {})
    mass_balance = evidence.get("mass_balance", {})

    fail_reason = _sso2_reader_fail_reason(
        evidence=evidence,
        delivered=delivered,
        stage3=stage3,
        fe_tap=fe_tap,
        coating=coating,
        mass_balance=mass_balance,
    )
    if fail_reason is not None:
        reader["status"] = fail_reason[0]
        reader["status_reason"] = fail_reason[1]
        reader["score"] = 0.0
        return 0.0, reader

    purity_fraction = _finite_number(stage3["purity_fraction"], "stage_3.purity_fraction")
    stage3_fe_kg = _finite_non_negative(stage3["Fe_kg"], "stage_3.Fe_kg")
    fe_tap_kg = _finite_non_negative(fe_tap["Fe_kg"], "fe_tap.Fe_kg")
    denominator = fe_tap_kg + stage3_fe_kg
    if denominator <= _EPS:
        reader["status"] = "missing_fe_tap_evidence"
        reader["status_reason"] = "Fe tap plus Stage 3 Fe evidence is zero"
        reader["score"] = 0.0
        return 0.0, reader

    fe_partition_score = fe_tap_kg / denominator
    score = _clamp01(purity_fraction) * _clamp01(fe_partition_score)
    reader["score_components"] = {
        "stage_3_purity_fraction": purity_fraction,
        "stage_3_fe_kg": stage3_fe_kg,
        "stage_3_fe_wt_pct": _finite_number(stage3["Fe_wt_pct"], "stage_3.Fe_wt_pct"),
        "fe_tap_Fe_kg": fe_tap_kg,
        "fe_partition_score": fe_partition_score,
        "delivered_stream_purity_margin": _finite_number(
            delivered["margin"],
            "delivered_stream_purity.margin",
        ),
        "wall_coating_margin": coating["margin"],
        "wall_coating_status": str(coating.get("status", "")),
        "mass_balance_max_abs_error_pct": _finite_number(
            mass_balance["max_abs_error_pct"],
            "mass_balance.max_abs_error_pct",
        ),
    }
    if stage3_fe_kg > _EPS:
        reader["status"] = "stage_3_fe_contamination_penalized"
        reader["status_reason"] = "Stage 3 Fe contamination reduces purity and Fe tap partition score"
    reader["score"] = score
    return score, reader


def _sso2_reader_fail_reason(
    *,
    evidence: Mapping[str, Any],
    delivered: Mapping[str, Any],
    stage3: Mapping[str, Any],
    fe_tap: Mapping[str, Any],
    coating: Mapping[str, Any],
    mass_balance: Mapping[str, Any],
) -> tuple[str, str] | None:
    status = str(evidence.get("status", "missing_sso2_evidence"))
    if status not in {"available", "stage_stream_purity_failed"}:
        return status, str(evidence.get("status_reason", "") or "SSO-2 evidence unavailable")
    if stage3.get("Fe_kg") is None or stage3.get("Fe_wt_pct") is None:
        return "missing_stage_3_fe_evidence", "Stage 3 Fe kg/wt% evidence is missing"
    try:
        _finite_non_negative(stage3["Fe_kg"], "stage_3.Fe_kg")
        _finite_non_negative(stage3["Fe_wt_pct"], "stage_3.Fe_wt_pct")
        _finite_non_negative(stage3["purity_fraction"], "stage_3.purity_fraction")
    except (KeyError, ValueError) as exc:
        return "invalid_stage_3_fe_evidence", str(exc)
    if delivered.get("margin") is None:
        return "missing_stage_3_purity_evidence", "delivered_stream_purity margin is missing"
    try:
        _finite_number(delivered["margin"], "delivered_stream_purity.margin")
    except (KeyError, ValueError) as exc:
        return "invalid_stage_3_purity_evidence", str(exc)
    if not bool(delivered.get("feasible", False)):
        return "stage_stream_purity_failed", str(
            delivered.get("detail", "") or "delivered_stream_purity failed"
        )
    if fe_tap.get("Fe_kg") is None:
        return "missing_fe_tap_evidence", str(
            fe_tap.get("status_reason", "") or "Fe tap evidence is missing"
        )
    try:
        _finite_non_negative(fe_tap["Fe_kg"], "fe_tap.Fe_kg")
    except (KeyError, ValueError) as exc:
        return "invalid_fe_tap_evidence", str(exc)
    if not bool(coating.get("feasible", False)):
        return "wall_coating_failed", str(
            coating.get("status_reason", "") or coating.get("detail", "")
        )
    if coating.get("margin") is None:
        return "missing_wall_coating_evidence", "wall/coating margin is missing"
    if mass_balance.get("status") != "available":
        return str(mass_balance.get("status", "missing_mass_balance_trace")), (
            "mass-balance closure evidence is missing"
        )
    if mass_balance.get("max_abs_error_pct") is None:
        return "missing_mass_balance_trace", "mass-balance closure value is missing"
    try:
        max_error = _finite_number(
            mass_balance["max_abs_error_pct"],
            "mass_balance.max_abs_error_pct",
        )
    except ValueError as exc:
        return "invalid_mass_balance_trace", str(exc)
    if max_error > SSO2_MASS_BALANCE_TOLERANCE_PCT:
        return (
            "mass_balance_open",
            (
                f"mass balance error {max_error:g}% exceeds "
                f"{SSO2_MASS_BALANCE_TOLERANCE_PCT:g}%"
            ),
        )
    return None


def _stage_species_kg_from_trace(trace: Any) -> tuple[dict[tuple[int, str], float], str, str]:
    if trace is None:
        return {}, "missing_stage_purity_trace", "trace is missing"
    snapshots = getattr(trace, "snapshots", None)
    deltas = getattr(trace, "condensed_by_stage_species_delta", None)
    if not _is_sequence(snapshots) or not _is_sequence(deltas):
        return (
            {},
            "missing_stage_purity_trace",
            "trace snapshots or condensed_by_stage_species_delta missing",
        )
    if len(deltas) != len(snapshots):
        return (
            {},
            "missing_stage_purity_trace",
            "condensed delta count does not match snapshots",
        )
    totals: dict[tuple[int, str], float] = {}
    for tick in deltas:
        if not isinstance(tick, Mapping):
            return {}, "invalid_stage_purity_trace", "condensed tick is not a mapping"
        for key, kg in tick.items():
            if not isinstance(key, tuple) or len(key) != 2:
                return {}, "invalid_stage_purity_trace", "stage/species key is not a 2-tuple"
            stage, species = int(key[0]), str(key[1])
            try:
                amount = _finite_non_negative(kg, "condensed kg")
            except ValueError as exc:
                return {}, "invalid_stage_purity_trace", str(exc)
            if amount > _EPS:
                totals[(stage, species)] = totals.get((stage, species), 0.0) + amount
    return totals, "available", ""


def _ledger_account_kg(ledger: Any, account: str) -> tuple[dict[str, float], str, str]:
    if ledger is None:
        return {}, "missing_fe_tap_evidence", "atom ledger is missing"
    kg_by_account = getattr(ledger, "kg_by_account", None)
    if not callable(kg_by_account):
        return {}, "missing_fe_tap_evidence", "atom ledger has no kg_by_account reader"
    try:
        raw = kg_by_account(account)
    except TypeError:
        all_accounts = kg_by_account()
        raw = all_accounts.get(account, {}) if isinstance(all_accounts, Mapping) else {}
    except Exception as exc:  # noqa: BLE001 - report surface must fail closed
        return {}, "missing_fe_tap_evidence", str(exc)
    if raw is None:
        return {}, "missing_fe_tap_evidence", f"{account} kg reader returned no evidence"
    if not isinstance(raw, Mapping):
        return {}, "missing_fe_tap_evidence", f"{account} kg reader is not a mapping"
    if not raw:
        return {}, "missing_fe_tap_evidence", f"{account} kg evidence is absent"
    species_kg: dict[str, float] = {}
    for species, kg in raw.items():
        try:
            species_kg[str(species)] = _finite_non_negative(kg, f"{account}[{species}]")
        except ValueError as exc:
            return {}, "missing_fe_tap_evidence", str(exc)
    return species_kg, "available", ""


def _ledger_accounts_kg(
    ledger: Any,
    accounts: Sequence[str],
) -> tuple[dict[str, float], str, str]:
    totals: dict[str, float] = {}
    reasons: list[str] = []
    for account in accounts:
        species_kg, status, reason = _ledger_account_kg(ledger, str(account))
        if status != "available":
            reasons.append(reason)
            continue
        for species, kg in species_kg.items():
            totals[species] = totals.get(species, 0.0) + kg
    if totals:
        return totals, "available", ""
    return {}, "missing_fe_tap_evidence", "; ".join(reasons)


def _transition_count(ledger: Any, name: str) -> int:
    transitions = getattr(ledger, "transitions", ())
    if not _is_sequence(transitions):
        return 0
    return sum(1 for transition in transitions if getattr(transition, "name", "") == name)


def _execution_snapshots(run_execution: Any) -> tuple[Any, ...]:
    snapshots = getattr(run_execution, "snapshots", None)
    if _is_sequence(snapshots):
        return tuple(snapshots)
    trace = getattr(run_execution, "trace", None)
    trace_snapshots = getattr(trace, "snapshots", None) if trace is not None else None
    if _is_sequence(trace_snapshots):
        return tuple(trace_snapshots)
    return ()


def _stage_gas_snapshot_payload(run_execution: Any) -> dict[str, Any]:
    candidates = [
        snapshot
        for snapshot in _execution_snapshots(run_execution)
        if isinstance(getattr(snapshot, "c2a_staged_gas", None), Mapping)
        and getattr(snapshot, "c2a_staged_gas")
    ]
    selectors = (
        lambda snapshot: (
            _snapshot_has_native_partition(snapshot)
            and _stage_gas_matches_owner(getattr(snapshot, "c2a_staged_gas", {}))
        ),
        lambda snapshot: _stage_gas_matches_owner(
            getattr(snapshot, "c2a_staged_gas", {})
        ),
        lambda snapshot: True,
    )
    for selector in selectors:
        for snapshot in reversed(candidates):
            if selector(snapshot):
                raw = getattr(snapshot, "c2a_staged_gas", {})
                return _stage_gas_payload_from_raw(raw, snapshot=snapshot)
    return {
        "status": "missing_stage_gas_snapshot",
        "status_reason": "no c2a_staged_gas snapshot observed",
        "source": "run_execution.snapshot.c2a_staged_gas",
    }


def _snapshot_has_native_partition(snapshot: Any) -> bool:
    split = getattr(snapshot, "fe_redox_split", None)
    if not isinstance(split, Mapping):
        return False
    partition = split.get("native_fe_partition")
    return isinstance(partition, Mapping) and bool(partition)


def _stage_gas_matches_owner(raw: Any) -> bool:
    if not isinstance(raw, Mapping):
        return False
    try:
        return (
            math.isclose(
                _finite_non_negative(raw.get("pO2_mbar"), "stage_gas.pO2_mbar"),
                SSO2_CERTIFIED_PO2_MBAR,
                rel_tol=0.0,
                abs_tol=1.0e-12,
            )
            and math.isclose(
                _finite_non_negative(raw.get("pN2_mbar"), "stage_gas.pN2_mbar"),
                SSO2_CERTIFIED_PN2_MBAR,
                rel_tol=0.0,
                abs_tol=1.0e-9,
            )
        )
    except ValueError:
        return False


def _stage_gas_payload_from_raw(
    raw: Mapping[str, Any],
    *,
    snapshot: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "available",
        "status_reason": "",
        "source": "run_execution.snapshot.c2a_staged_gas",
    }
    if getattr(snapshot, "hour", None) is not None:
        payload["hour"] = getattr(snapshot, "hour")
    for key in ("stage_name", "gas_cover_mode", "atmosphere", "pn2_band_action"):
        if raw.get(key) is not None:
            payload[key] = str(raw.get(key))
    for key in (
        "pO2_mbar",
        "pN2_mbar",
        "p_total_mbar",
        "requested_p_total_mbar",
    ):
        try:
            payload[key] = _finite_non_negative(raw.get(key), f"stage_gas.{key}")
        except ValueError as exc:
            return {
                "status": "invalid_stage_gas_snapshot",
                "status_reason": str(exc),
                "source": "run_execution.snapshot.c2a_staged_gas",
            }
    return payload


def _native_fe_partition_basis(run_execution: Any, sim: Any) -> dict[str, Any]:
    raw: Mapping[str, Any] | None = None
    source = "run_execution.snapshot.fe_redox_split.native_fe_partition"
    for snapshot in reversed(_execution_snapshots(run_execution)):
        split = getattr(snapshot, "fe_redox_split", None)
        if not isinstance(split, Mapping):
            continue
        candidate = split.get("native_fe_partition")
        if isinstance(candidate, Mapping) and candidate:
            raw = candidate
            break
    if raw is None:
        candidate = getattr(sim, "_last_native_fe_partition_diagnostic", None)
        if isinstance(candidate, Mapping) and candidate:
            raw = candidate
            source = "sim._last_native_fe_partition_diagnostic"
    if raw is None:
        return {
            "status": "missing_fe_drain_vapor_partition",
            "status_reason": "native_fe_partition basis missing from snapshots",
            "source": source,
        }

    payload: dict[str, Any] = {
        "status": "available",
        "status_reason": "",
        "source": source,
    }
    numeric_keys = (
        "native_fe_pool_mol",
        "native_fe_tap_mol",
        "native_fe_vapor_mol",
        "native_fe_vapor_escape_fraction_of_pool",
        "native_fe_uncondensed_mol",
        "native_fe_uncondensed_fraction_of_pool",
        "native_fe_vapor_capacity_mol_hr",
        "native_fe_vapor_capacity_kg_hr",
        "ordinary_melt_fe_residual_capacity_mol_hr",
        "P_reference_Antoine_Pa",
        "P_eq_Pa",
        "P_bulk_Pa",
        "activity_factor",
        "temperature_K",
        "overhead_pressure_pa",
        "alpha_Fe",
    )
    required = ("native_fe_pool_mol", "native_fe_tap_mol", "native_fe_vapor_mol")
    for key in numeric_keys:
        if key not in raw:
            if key in required:
                return {
                    "status": "missing_fe_drain_vapor_partition",
                    "status_reason": f"native_fe_partition.{key} missing",
                    "source": source,
                }
            continue
        try:
            payload[key] = _finite_non_negative(raw.get(key), f"native_fe_partition.{key}")
        except ValueError as exc:
            return {
                "status": "invalid_fe_drain_vapor_partition",
                "status_reason": str(exc),
                "source": source,
            }
    for key in (
        "capacity_allocation_rule",
        "native_pool_activity_argument",
        "overhead_pressure_source",
        "carrier_gas",
        "alpha_source",
        "source_label",
    ):
        if raw.get(key) is not None:
            payload[key] = str(raw.get(key))
    return payload


def _product_species_kg(sim: Any, species: str) -> float | None:
    product_ledger = getattr(sim, "product_ledger", None)
    if not callable(product_ledger):
        return None
    try:
        products = product_ledger()
    except Exception:  # noqa: BLE001 - optional report field
        return None
    if not isinstance(products, Mapping):
        return None
    try:
        return _finite_non_negative(products.get(species, 0.0), f"product[{species}]")
    except ValueError:
        # Optional report field: a corrupt/negative/non-finite product kg must not
        # crash the evidence surface — fail closed to None like the other errors above.
        return None


def _mass_balance_closure(run_execution: Any, trace: Any) -> dict[str, Any]:
    snapshots = getattr(run_execution, "snapshots", None)
    if not _is_sequence(snapshots) and trace is not None:
        snapshots = getattr(trace, "snapshots", None)
    if not _is_sequence(snapshots) or not snapshots:
        return {"status": "missing_mass_balance_trace", "max_abs_error_pct": None}
    try:
        errors = [
            abs(_finite_number(getattr(snapshot, "mass_balance_error_pct", 0.0), "mass balance"))
            for snapshot in snapshots
        ]
    except ValueError:
        # A non-finite mass-balance value is missing/invalid evidence, not a pass:
        # fail closed with an explicit status rather than raising out of the surface.
        return {"status": "invalid_mass_balance_trace", "max_abs_error_pct": None}
    return {"status": "available", "max_abs_error_pct": max(errors)}


def _species_values(
    species_kg: Mapping[str, float],
    species_names: Sequence[str],
    available: bool,
) -> dict[str, float | None]:
    return {
        species: species_kg.get(species, 0.0) if available else None
        for species in species_names
    }


def _species_mol_values(
    run_execution: Any,
    species_kg: Mapping[str, float],
    species_names: Sequence[str],
    available: bool,
) -> dict[str, float | None]:
    if not available:
        return {species: None for species in species_names}
    return {
        species: _kg_to_mol(run_execution, species, species_kg.get(species, 0.0))
        for species in species_names
    }


def _kg_to_mol(run_execution: Any, species: str, kg: float) -> float:
    sim = getattr(run_execution, "simulator", run_execution)
    registry = getattr(sim, "species_formula_registry", None)
    formula = resolve_species_formula(species, registry)
    return _finite_non_negative(kg, species) / formula.molar_mass_kg_per_mol()


def _gate_margin_payload(margin: GateMargin) -> dict[str, Any]:
    return {
        "gate": margin.gate,
        "feasible": margin.feasible,
        "margin": _json_number(margin.margin),
        "observed": _json_number(margin.observed),
        "detail": margin.detail,
        "status": margin.status,
        "status_reason": margin.status_reason,
        "threshold": _threshold_payload(margin.threshold),
    }


def _threshold_payload(threshold: Any) -> dict[str, Any]:
    return {
        "id": threshold.id,
        "value": threshold.value,
        "units": threshold.units,
        "source": threshold.source,
        "source_ref": threshold.source_ref,
        "tolerance": threshold.tolerance,
    }


def _wt_pct(part: float, total: float) -> float | None:
    if total <= _EPS:
        return None
    return part / total * 100.0


def _is_sequence(value: Any) -> bool:
    return isinstance(value, (tuple, list)) and not isinstance(value, (str, bytes))


def _finite_non_negative(value: Any, label: str) -> float:
    number = _finite_number(value, label)
    if number < -_EPS:
        raise ValueError(f"{label} must be non-negative")
    return max(0.0, number)


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _json_number(value: float) -> float | str:
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return float(value)


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))
