#!/usr/bin/env python3
"""Build the SSO-R redox validation map for pre-grind recipe selection."""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simulator.core import PyrolysisSimulator
from simulator.runner import build_per_hour_summary
from simulator.state import Atmosphere, CampaignPhase


SCHEMA_VERSION = "sso-r-validation-map-v2"
GOLDEN_SCHEMA_VERSION = "sso-r-validation-map-golden-v2"
FEEDSTOCK = "lunar_mare_low_ti"
BATCH_KG = 1000.0
DOSE_SPECIES = "Na"
DOSE_CALIBRATION_T_C = 1150.0
SAMPLE_TIME_H = 1.0
GRID_SCOPE_FULL = "1512-full"
GRID_SCOPE_SMOKE = "36-smoke"
MAP_TEMPERATURES_C = (1400.0, 1450.0, 1500.0, 1550.0, 1600.0, 1650.0, 1700.0, 1750.0, 1800.0)
PO2_MBAR = (1.0e-6, 1.0e-4, 1.0e-2, 1.0e-1, 1.0)
PN2_MBAR = (5.0, 10.0, 15.0)
DOSE_FRACTIONS = (0.0, 0.025, 0.05, 0.10, 0.25, 0.50, 0.75, 1.0)
MASS_BALANCE_LIMIT_PCT = 5.0e-12
OWNER_RECIPE_MIN_SIO_KG_HR = 1.0e-3
OWNER_RECIPE_MAX_ESCAPE_FRACTION = 1.0e-3
ANCHOR_REFERENCE_SOURCE = (
    "docs-private/research/2026-07-01-sso-r-scope/findings.md lines 107 and 259; "
    "CH1-CLOSEOUT.md lines 23-26 and 107-118 record the ch1/ch1c redox-state provenance"
)
ANCHOR_REFERENCE_BY_FO2 = {
    -9.0: {
        "native_fe_frac": 0.012467999513236896,
        "abs_tolerance": 5.0e-6,
        "note": "recorded forced-reduction native_frac_before at 1600 C",
    },
    -9.5: {
        "native_fe_frac": 0.44,
        "abs_tolerance": 5.0e-4,
        "note": "recorded manual fO2 curve native fraction, 44.0 percent at 1600 C",
    },
}
ROW_UNITS = {
    "temperature_C": "degC",
    "requested_pO2_mbar": "mbar",
    "requested_pN2_mbar": "mbar",
    "total_pressure_mbar": "mbar",
    "dose_kg": "kg Na loaded",
    "dose_consumed_kg": "kg Na consumed",
    "dose_feo_reduced_mol": "mol FeO reduced by Na dose",
    "post_exchange_fO2_log_diagnostic": "log10 fO2, post-exchange diagnostic only",
    "post_exchange_delta_IW_diagnostic": "log10 fO2 minus IW, post-exchange diagnostic only",
    "native_fe_pool_mol": "mol Fe in native-Fe pool",
    "native_fe_tap_mol": "mol Fe routed to tap",
    "native_fe_vapor_mol": "mol Fe routed to vapor",
    "native_fe_vapor_escape_fraction_of_pool": "native_fe_vapor_mol/native_fe_pool_mol",
    "Fe_vapor_kg_hr": "kg/hr",
    "SiO_flux_kg_hr": "kg/hr",
    "stage_3_Fe_kg": "kg",
    "stage_3_total_kg": "kg",
    "stage_3_Fe_wt_pct": "wt pct",
    "stage_3_SiO2_capture_kg": "kg",
    "oxygen_reservoir_exchange_o2_mol": "mol O2 equivalent",
    "mass_balance_error_pct": "percent",
}


@dataclass(frozen=True)
class GasPoint:
    pO2_mbar: float
    pN2_mbar: float
    gas_regime: str

    @property
    def total_pressure_mbar(self) -> float:
        return self.pO2_mbar + self.pN2_mbar


@dataclass(frozen=True)
class DoseCalibration:
    species: str
    calibration_temperature_C: float
    base_feo_mol: float
    transition_name: str
    transition_reason: str
    transition_reagent_debit_mol: float
    transition_feo_debit_mol: float
    reagent_mol_per_feo_mol: float
    full_feo_equiv_dose_kg: float
    source: str


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_data() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        _load_yaml(REPO_ROOT / "data" / "setpoints.yaml"),
        _load_yaml(REPO_ROOT / "data" / "feedstocks.yaml"),
        _load_yaml(REPO_ROOT / "data" / "vapor_pressures.yaml"),
    )


def _build_sim(
    setpoints: Mapping[str, Any],
    feedstocks: Mapping[str, Any],
    vapor_pressures: Mapping[str, Any],
    *,
    dose_kg: float = 0.0,
) -> PyrolysisSimulator:
    sim = PyrolysisSimulator(None, setpoints, feedstocks, vapor_pressures)
    additives = {DOSE_SPECIES: dose_kg} if dose_kg > 0.0 else {}
    sim.load_batch(FEEDSTOCK, mass_kg=BATCH_KG, additives_kg=additives)
    return sim


def _molar_mass_kg_per_mol(sim: PyrolysisSimulator, species: str) -> float:
    formula = sim.species_formula_registry[species]
    return float(formula.molar_mass_kg_per_mol())


def calibrate_dose(
    setpoints: Mapping[str, Any] | None = None,
    feedstocks: Mapping[str, Any] | None = None,
    vapor_pressures: Mapping[str, Any] | None = None,
) -> DoseCalibration:
    if setpoints is None or feedstocks is None or vapor_pressures is None:
        setpoints, feedstocks, vapor_pressures = _load_data()
    sim = _build_sim(
        setpoints,
        feedstocks,
        vapor_pressures,
        dose_kg=1000.0,
    )
    base_feo_mol = float(
        sim.atom_ledger.mol_by_account("process.cleaned_melt").get("FeO", 0.0)
        or 0.0
    )
    sim._init_shuttle_inventory(CampaignPhase.C3_NA)
    sim.melt.campaign = CampaignPhase.C3_NA
    sim.melt.temperature_C = DOSE_CALIBRATION_T_C
    sim.melt.campaign_hour = 1
    before = len(sim.atom_ledger.transitions)
    sim._shuttle_inject_Na(target_stage="feo_cleanup", liquid_fraction=1.0)
    transitions = sim.atom_ledger.transitions[before:]
    committed = [t for t in transitions if t.name == "c3_na_shuttle_reduction"]
    if not committed:
        raise RuntimeError("dose calibration did not produce c3_na_shuttle_reduction")
    transition = committed[-1]
    reagent_mol = sim._transition_species_mol(
        transition,
        side="debits",
        account="process.reagent_inventory",
        species=DOSE_SPECIES,
    )
    feo_mol = sim._transition_species_mol(
        transition,
        side="debits",
        account="process.cleaned_melt",
        species="FeO",
    )
    if reagent_mol <= 0.0 or feo_mol <= 0.0 or base_feo_mol <= 0.0:
        raise RuntimeError("dose calibration transition had no FeO/reagent debit")
    ratio = reagent_mol / feo_mol
    full_dose_kg = base_feo_mol * ratio * _molar_mass_kg_per_mol(sim, DOSE_SPECIES)
    return DoseCalibration(
        species=DOSE_SPECIES,
        calibration_temperature_C=DOSE_CALIBRATION_T_C,
        base_feo_mol=base_feo_mol,
        transition_name=transition.name,
        transition_reason=transition.reason,
        transition_reagent_debit_mol=reagent_mol,
        transition_feo_debit_mol=feo_mol,
        reagent_mol_per_feo_mol=ratio,
        full_feo_equiv_dose_kg=full_dose_kg,
        source="committed c3_na_shuttle_reduction at valid FeO cleanup temperature",
    )


def gas_grid() -> list[GasPoint]:
    rows = [GasPoint(pO2_mbar=0.0, pN2_mbar=0.0, gas_regime="vacuum")]
    rows.extend(
        GasPoint(pO2_mbar=pO2, pN2_mbar=0.0, gas_regime="managed_o2_no_n2")
        for pO2 in PO2_MBAR
    )
    rows.extend(
        GasPoint(pO2_mbar=pO2, pN2_mbar=pN2, gas_regime="n2_carrier")
        for pN2 in PN2_MBAR
        for pO2 in PO2_MBAR
    )
    return rows


def _smoke_gases() -> tuple[GasPoint, ...]:
    return (
        GasPoint(0.0, 0.0, "vacuum"),
        GasPoint(1.0e-6, 0.0, "managed_o2_no_n2"),
        GasPoint(1.0e-6, 10.0, "n2_carrier"),
        GasPoint(1.0, 10.0, "n2_carrier"),
    )


def _grid_axes(smoke: bool) -> tuple[tuple[float, ...], tuple[GasPoint, ...], tuple[float, ...]]:
    if smoke:
        return (1600.0, 1650.0, 1700.0), _smoke_gases(), (0.0, 0.50, 1.0)
    return MAP_TEMPERATURES_C, tuple(gas_grid()), DOSE_FRACTIONS


def grid_scope(smoke: bool) -> str:
    return GRID_SCOPE_SMOKE if smoke else GRID_SCOPE_FULL


def expected_grid_count(*, smoke: bool) -> int:
    temperatures, gases, doses = _grid_axes(smoke)
    return len(temperatures) * len(gases) * len(doses)


def _requested_grid(smoke: bool) -> list[tuple[float, GasPoint, float]]:
    temperatures, gases, doses = _grid_axes(smoke)
    return [
        (temperature_C, gas, dose_fraction)
        for temperature_C in temperatures
        for gas in gases
        for dose_fraction in doses
    ]


def grid_spec(*, smoke: bool) -> dict[str, Any]:
    temperatures, gases, doses = _grid_axes(smoke)
    return {
        "scope": grid_scope(smoke),
        "temperature_C": list(temperatures),
        "gas_points": [
            {
                "pO2_mbar": gas.pO2_mbar,
                "pN2_mbar": gas.pN2_mbar,
                "gas_regime": gas.gas_regime,
            }
            for gas in gases
        ],
        "pO2_mbar": sorted({gas.pO2_mbar for gas in gases}),
        "pN2_mbar": sorted({gas.pN2_mbar for gas in gases}),
        "dose_fraction_of_full_FeO_equiv": list(doses),
        "expected_row_count": expected_grid_count(smoke=smoke),
    }


def full_grid() -> list[tuple[float, GasPoint, float]]:
    return _requested_grid(False)


def smoke_grid() -> list[tuple[float, GasPoint, float]]:
    return _requested_grid(True)


def _configure_gas_state(sim: PyrolysisSimulator, gas: GasPoint) -> None:
    sim.melt.pO2_mbar = float(gas.pO2_mbar)
    sim.melt.p_total_mbar = float(gas.total_pressure_mbar)
    # The current transport helper supports neutral carrier properties
    # (N2/Ar/CO2), not O2. For no-N2 rows, N2 is only the transport-property
    # basis; total pressure remains the requested O2-only pressure.
    sim.melt.background_gas_species = "N2"
    if gas.gas_regime == "vacuum":
        sim.melt.atmosphere = Atmosphere.HARD_VACUUM
        sim.melt.p_total_mbar = 0.0
        sim.overhead.composition = {}
        sim.overhead.pressure_mbar = 0.0
        return
    if gas.pN2_mbar > 0.0:
        sim.melt.atmosphere = Atmosphere.PN2_SWEEP
        sim.overhead.composition = {
            "N2": float(gas.pN2_mbar),
            "O2": float(gas.pO2_mbar),
        }
    else:
        sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
        sim.overhead.composition = {"O2": float(gas.pO2_mbar)}
    sim.overhead.pressure_mbar = float(gas.total_pressure_mbar)


def _transport_property_basis(gas: GasPoint) -> str:
    if gas.gas_regime == "vacuum":
        return "none"
    if gas.gas_regime == "managed_o2_no_n2":
        return "N2 transport-property placeholder; overhead gas is O2-only"
    return "N2 carrier"


def _apply_dose(
    sim: PyrolysisSimulator,
    *,
    dose_kg: float,
) -> dict[str, Any]:
    if dose_kg <= 0.0:
        return {
            "dose_transition_name": "",
            "dose_transition_reason": "",
            "dose_consumed_kg": 0.0,
            "dose_feo_reduced_mol": 0.0,
            "dose_redox_label": "",
        }
    sim._init_shuttle_inventory(CampaignPhase.C3_NA)
    sim.melt.campaign = CampaignPhase.C3_NA
    sim.melt.temperature_C = DOSE_CALIBRATION_T_C
    sim.melt.campaign_hour = 1
    before = len(sim.atom_ledger.transitions)
    sim._shuttle_inject_Na(target_stage="feo_cleanup", liquid_fraction=1.0)
    committed = [
        t
        for t in sim.atom_ledger.transitions[before:]
        if t.name == "c3_na_shuttle_reduction"
    ]
    if not committed:
        refusal = dict(getattr(sim, "_last_shuttle_refusal_diagnostic", {}) or {})
        return {
            "dose_transition_name": "",
            "dose_transition_reason": "",
            "dose_consumed_kg": 0.0,
            "dose_feo_reduced_mol": 0.0,
            "dose_redox_label": "",
            "dose_refusal": refusal,
        }
    transition = committed[-1]
    consumed_mol = sim._transition_species_mol(
        transition,
        side="debits",
        account="process.reagent_inventory",
        species=DOSE_SPECIES,
    )
    feo_reduced_mol = sim._transition_species_mol(
        transition,
        side="debits",
        account="process.cleaned_melt",
        species="FeO",
    )
    return {
        "dose_transition_name": transition.name,
        "dose_transition_reason": transition.reason,
        "dose_consumed_kg": consumed_mol * _molar_mass_kg_per_mol(sim, DOSE_SPECIES),
        "dose_feo_reduced_mol": feo_reduced_mol,
        "dose_redox_label": "redox_source:c3_na_shuttle_reduction",
    }


def _positive(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number) or number <= 0.0:
        return 0.0
    return number


def run_row(
    temperature_C: float,
    gas: GasPoint,
    dose_fraction: float,
    calibration: DoseCalibration,
    setpoints: Mapping[str, Any],
    feedstocks: Mapping[str, Any],
    vapor_pressures: Mapping[str, Any],
    *,
    grid_scope_label: str = "",
) -> dict[str, Any]:
    dose_kg = float(dose_fraction) * calibration.full_feo_equiv_dose_kg
    sim = _build_sim(
        setpoints,
        feedstocks,
        vapor_pressures,
        dose_kg=dose_kg,
    )
    dose_result = _apply_dose(sim, dose_kg=dose_kg)

    sim.melt.campaign = CampaignPhase.C2A_STAGED
    sim.melt.temperature_C = float(temperature_C)
    sim.melt.target_temperature_C = float(temperature_C)
    _configure_gas_state(sim, gas)
    T_K = float(temperature_C) + 273.15
    sim._re_reference_melt_fO2_to_temperature(T_K)
    exchange = sim._apply_oxygen_reservoir_exchange()
    first_respeciation = sim._apply_fe_redox_respeciation()
    split = sim._apply_native_fe_saturation_split(sample_time_h=SAMPLE_TIME_H)
    equilibrium = sim._get_equilibrium()
    evap_flux = sim._calculate_evaporation(equilibrium)
    evap_flux = sim._apply_analytic_evaporation_depletion(evap_flux)
    if evap_flux.total_kg_hr > 0.0:
        sim._configure_condensation_operating_conditions(evap_flux)
        sim._apply_lab_surface_temperatures(sample_time_h=SAMPLE_TIME_H)
        sim._route_to_condensation(evap_flux)
    sim._update_melt_composition(evap_flux)
    second_respeciation = sim._apply_fe_redox_respeciation()

    snapshot = sim._make_snapshot()
    snapshot.evap_flux = evap_flux
    snapshot.fe_redox_split = sim._compute_fe_redox_split_diagnostic()
    snapshot.redox_source_breakdown = sim._redox_source_breakdown_diagnostic()
    summary = build_per_hour_summary(sim, snapshot, include_fe_redox_split=True)
    redox = dict(summary.get("redox_source_breakdown", {}) or {})
    ferric_divergence = dict(
        redox.get("ferric_divergence", {})
        or snapshot.oxygen_reservoir.get("ferric_divergence", {})
        or sim._ledger_ferric_fraction_diagnostic()
    )
    partition = dict(snapshot.fe_redox_split.get("native_fe_partition", {}) or {})
    stage3 = dict(summary.get("stage_3_capture", {}) or {})
    melt_mol = sim.atom_ledger.mol_by_account("process.cleaned_melt")
    drain_mol = sim.atom_ledger.mol_by_account("terminal.drain_tap_material")
    vapor_species = dict(summary.get("vapor_species_kg_hr", {}) or {})
    source_terms = {
        str(k): float(v)
        for k, v in dict(redox.get("terms_mol_o2_equiv_by_label", {}) or {}).items()
    }
    material_divergence = (
        bool(ferric_divergence.get("warning"))
        or str(ferric_divergence.get("status", "")) == "warning"
    )
    row = {
        "schema_version": SCHEMA_VERSION,
        "grid_scope": grid_scope_label,
        "sample_time_h": SAMPLE_TIME_H,
        "feedstock": FEEDSTOCK,
        "batch_kg": BATCH_KG,
        "temperature_C": float(temperature_C),
        "requested_pO2_mbar": float(gas.pO2_mbar),
        "requested_pN2_mbar": float(gas.pN2_mbar),
        "total_pressure_mbar": float(gas.total_pressure_mbar),
        "gas_regime": gas.gas_regime,
        "transport_property_basis": _transport_property_basis(gas),
        "map_scope_note": (
            "single-hour point diagnostic; not a full campaign trajectory"
        ),
        "dose_species": DOSE_SPECIES,
        "dose_kg": dose_kg,
        "dose_consumed_kg": float(dose_result.get("dose_consumed_kg", 0.0) or 0.0),
        "dose_fraction_of_full_FeO_equiv": float(dose_fraction),
        "dose_calibration_temperature_C": DOSE_CALIBRATION_T_C,
        "dose_transition_name": str(dose_result.get("dose_transition_name", "")),
        "dose_transition_reason": str(dose_result.get("dose_transition_reason", "")),
        "dose_feo_reduced_mol": float(dose_result.get("dose_feo_reduced_mol", 0.0) or 0.0),
        "post_exchange_fO2_log_diagnostic": float(
            sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
        ),
        "post_exchange_delta_IW_diagnostic": float(snapshot.fe_redox_split.get("fO2_log", 0.0) or 0.0)
        - float(snapshot.fe_redox_split.get("iw_log", 0.0) or 0.0),
        "fO2_diagnostic_status": (
            "diagnostic_only_not_manual_anchor_scale; do not use as certification value"
        ),
        "redox_source_net_mol_o2_equiv": float(
            redox.get("net_mol_o2_equiv", 0.0) or 0.0
        ),
        "redox_source_terms_mol_o2_equiv_by_label": source_terms,
        "redox_source_reader": "runner.build_per_hour_summary.redox_source_breakdown",
        "native_fe_event_type": "native_fe_partitioned_saturation",
        "native_fe_event_source_label": (
            "redox_source:native_fe_saturation_split"
            if "redox_source:native_fe_saturation_split" in source_terms
            else ""
        ),
        "native_fe_pool_mol": _positive(partition.get("native_fe_pool_mol")),
        "native_fe_tap_mol": _positive(partition.get("native_fe_tap_mol")),
        "native_fe_vapor_mol": _positive(partition.get("native_fe_vapor_mol")),
        "native_fe_vapor_escape_fraction_of_pool": _positive(
            partition.get("native_fe_vapor_escape_fraction_of_pool")
        ),
        "native_fe_vapor_escape_fraction_denominator": "native_fe_pool_mol",
        "native_fe_uncondensed_mol": _positive(
            partition.get("native_fe_uncondensed_mol")
        ),
        "native_fe_uncondensed_fraction_of_pool": _positive(
            partition.get("native_fe_uncondensed_fraction_of_pool")
        ),
        "native_fe_transport_cap_mol_hr": _positive(
            partition.get("native_fe_vapor_capacity_mol_hr")
        ),
        "retained_FeO_mol": _positive(melt_mol.get("FeO")),
        "retained_Fe2O3_mol": _positive(melt_mol.get("Fe2O3")),
        "retained_native_Fe_mol": _positive(drain_mol.get("Fe")),
        "ferric_divergence": ferric_divergence,
        "ferric_divergence_material": material_divergence,
        "Fe_vapor_kg_hr": _positive(vapor_species.get("Fe")),
        "SiO_flux_kg_hr": _positive(vapor_species.get("SiO")),
        "stage_3_Fe_kg": _positive(stage3.get("Fe_kg")),
        "stage_3_total_kg": _positive(stage3.get("total_kg")),
        "stage_3_Fe_wt_pct": _positive(stage3.get("Fe_wt_pct")),
        "stage_3_SiO2_capture_kg": _positive(sim.train.stages[3].collected_kg.get("SiO2", 0.0))
        if len(sim.train.stages) > 3
        else 0.0,
        "oxygen_reservoir_exchange_direction": str(exchange.exchange_direction),
        "oxygen_reservoir_exchange_o2_mol": float(exchange.exchange_o2_mol),
        "first_respeciation_status": str(first_respeciation.get("status", "")),
        "first_respeciation_reason": str(first_respeciation.get("reason", "")),
        "second_respeciation_status": str(second_respeciation.get("status", "")),
        "second_respeciation_reason": str(second_respeciation.get("reason", "")),
        "mass_balance_error_pct": float(snapshot.mass_balance_error_pct or 0.0),
    }
    row["row_passes_base_integrity"] = (
        abs(row["mass_balance_error_pct"]) <= MASS_BALANCE_LIMIT_PCT
        and not row["ferric_divergence_material"]
    )
    return row


def manual_fO2_anchors(
    setpoints: Mapping[str, Any] | None = None,
    feedstocks: Mapping[str, Any] | None = None,
    vapor_pressures: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if setpoints is None or feedstocks is None or vapor_pressures is None:
        setpoints, feedstocks, vapor_pressures = _load_data()
    anchors = []
    for fO2_log in (-9.0, -9.5):
        reference = ANCHOR_REFERENCE_BY_FO2[fO2_log]
        sim = _build_sim(setpoints, feedstocks, vapor_pressures)
        sim.melt.temperature_C = 1600.0
        sim.melt.p_total_mbar = 10.0
        sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = fO2_log
        sim.melt.oxygen_reservoir.reference_T_K = 1600.0 + 273.15
        sim._sync_oxygen_reservoir_mirror()
        split = sim._compute_fe_redox_split_diagnostic()
        anchors.append({
            "mode": "manual_fO2_diagnostic_anchor",
            "temperature_C": 1600.0,
            "feedstock": FEEDSTOCK,
            "fO2_log": fO2_log,
            "native_fe_frac": float(split.get("native_fe_frac", 0.0) or 0.0),
            "reference_native_fe_frac": reference["native_fe_frac"],
            "reference_abs_tolerance": reference["abs_tolerance"],
            "reference_source": ANCHOR_REFERENCE_SOURCE,
            "reference_note": reference["note"],
            "diagnostic_only": True,
        })
    return anchors


def _group_rows(
    rows: Iterable[Mapping[str, Any]],
    keys: tuple[str, ...],
) -> dict[tuple[Any, ...], list[Mapping[str, Any]]]:
    grouped: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(tuple(row[k] for k in keys), []).append(row)
    return grouped


def _nonincreasing(values: list[float], *, tolerance: float = 1.0e-12) -> bool:
    return all(values[i + 1] <= values[i] + tolerance for i in range(len(values) - 1))


def _nondecreasing(values: list[float], *, tolerance: float = 1.0e-12) -> bool:
    return all(values[i + 1] + tolerance >= values[i] for i in range(len(values) - 1))


def evaluate_assertions(
    rows: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    *,
    expected_rows: int,
    grid_scope_label: str,
) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        assertions.append({"name": name, "passed": bool(passed), "detail": detail})

    add(
        "grid_count",
        len(rows) == expected_rows,
        f"rows={len(rows)} expected={expected_rows} scope={grid_scope_label}",
    )
    max_mass = max((abs(float(r["mass_balance_error_pct"])) for r in rows), default=0.0)
    add(
        "mass_balance",
        max_mass <= MASS_BALANCE_LIMIT_PCT,
        f"max_abs_pct={max_mass:.6g} limit={MASS_BALANCE_LIMIT_PCT:.6g}",
    )
    divergence_rows = [
        r for r in rows if bool(r.get("ferric_divergence_material", False))
    ]
    passing_divergence_rows = [
        r for r in divergence_rows if bool(r.get("row_passes_base_integrity", False))
    ]
    add(
        "ferric_divergence_fail_loud",
        not passing_divergence_rows,
        (
            f"material_divergence_rows={len(divergence_rows)} "
            f"passing_with_divergence={len(passing_divergence_rows)}"
        ),
    )
    anchor_by_fO2 = {float(a["fO2_log"]): a for a in anchors}
    for fO2_log in (-9.0, -9.5):
        anchor = anchor_by_fO2[fO2_log]
        observed = float(anchor["native_fe_frac"])
        reference = float(anchor["reference_native_fe_frac"])
        tolerance = float(anchor["reference_abs_tolerance"])
        add(
            f"manual_fO2_anchor_{fO2_log}",
            abs(observed - reference) <= tolerance,
            (
                f"native_fe_frac={observed:.12g} reference={reference:.12g} "
                f"abs_tolerance={tolerance:.6g} source={anchor['reference_source']}"
            ),
        )

    # Non-vacuity guard: a monotonicity assertion with zero qualifying
    # slices never exercised its constraint. On the full grid that is a
    # harness defect (fail loud); on the smoke grid an unexercised axis is
    # expected (single-point axes) and must be LABELED, not silently green.
    is_full_scope = grid_scope_label == GRID_SCOPE_FULL

    def monotonicity_pass(failures: int, qualifying_slices: int) -> bool:
        if qualifying_slices == 0:
            return not is_full_scope
        return failures == 0

    def slice_note(qualifying_slices: int) -> str:
        if qualifying_slices == 0 and not is_full_scope:
            return " not_exercised_on_smoke_grid"
        return ""

    pO2_sio_failures = 0
    pO2_native_response_slices = 0
    pO2_qualifying_slices = 0
    for key, group in _group_rows(
        rows,
        ("temperature_C", "requested_pN2_mbar", "dose_fraction_of_full_FeO_equiv"),
    ).items():
        managed = sorted(
            [r for r in group if r["requested_pO2_mbar"] > 0.0],
            key=lambda r: r["requested_pO2_mbar"],
        )
        if len(managed) < 2:
            continue
        pO2_qualifying_slices += 1
        native_values = [float(r["native_fe_pool_mol"]) + float(r["native_fe_tap_mol"]) for r in managed]
        sio_values = [float(r["SiO_flux_kg_hr"]) for r in managed]
        if not _nonincreasing(sio_values):
            pO2_sio_failures += 1
        if not _nonincreasing(native_values):
            pO2_native_response_slices += 1
    add(
        "pO2_SiO_suppression_monotonicity",
        monotonicity_pass(pO2_sio_failures, pO2_qualifying_slices),
        (
            f"sio_nonincreasing_failures={pO2_sio_failures} "
            f"native_response_nonmonotone_reported={pO2_native_response_slices} "
            f"qualifying_slices={pO2_qualifying_slices}"
            f"{slice_note(pO2_qualifying_slices)}"
        ),
    )

    dose_reduction_failures = 0
    dose_consumption_failures = 0
    dose_native_response_slices = 0
    dose_qualifying_slices = 0
    for key, group in _group_rows(rows, ("temperature_C", "requested_pO2_mbar", "requested_pN2_mbar")).items():
        ordered = sorted(group, key=lambda r: r["dose_fraction_of_full_FeO_equiv"])
        if len(ordered) < 2:
            continue
        dose_qualifying_slices += 1
        native_values = [float(r["native_fe_pool_mol"]) + float(r["native_fe_tap_mol"]) for r in ordered]
        dose_reduced = [float(r["dose_feo_reduced_mol"]) for r in ordered]
        dose_consumed = [float(r["dose_consumed_kg"]) for r in ordered]
        if not _nondecreasing(dose_reduced):
            dose_reduction_failures += 1
        if not _nondecreasing(dose_consumed):
            dose_consumption_failures += 1
        if not _nondecreasing(native_values):
            dose_native_response_slices += 1
    add(
        "dose_reduction_monotonicity",
        monotonicity_pass(
            dose_reduction_failures + dose_consumption_failures,
            dose_qualifying_slices,
        ),
        (
            f"feo_reduced_failures={dose_reduction_failures} "
            f"consumed_kg_failures={dose_consumption_failures} "
            f"native_response_nonmonotone_reported={dose_native_response_slices} "
            f"qualifying_slices={dose_qualifying_slices}"
            f"{slice_note(dose_qualifying_slices)}"
        ),
    )

    pn2_failures = 0
    pn2_qualifying_slices = 0
    for key, group in _group_rows(rows, ("temperature_C", "requested_pO2_mbar", "dose_fraction_of_full_FeO_equiv")).items():
        carrier = sorted(
            [r for r in group if r["requested_pN2_mbar"] in PN2_MBAR],
            key=lambda r: r["requested_pN2_mbar"],
        )
        if len(carrier) != len(PN2_MBAR):
            continue
        pn2_qualifying_slices += 1
        escape = [float(r["native_fe_vapor_escape_fraction_of_pool"]) for r in carrier]
        if not _nonincreasing(escape):
            pn2_failures += 1
    add(
        "pN2_escape_monotonicity",
        monotonicity_pass(pn2_failures, pn2_qualifying_slices),
        (
            f"failing_slices={pn2_failures} "
            f"qualifying_slices={pn2_qualifying_slices}"
            f"{slice_note(pn2_qualifying_slices)}"
        ),
    )

    owner = [
        r for r in rows
        if float(r["temperature_C"]) == 1650.0
        and math.isclose(float(r["requested_pO2_mbar"]), 1.0e-6)
        and math.isclose(float(r["requested_pN2_mbar"]), 10.0)
        and math.isclose(float(r["dose_fraction_of_full_FeO_equiv"]), 1.0)
    ]
    if owner:
        row = owner[0]
        owner_pass = (
            row["native_fe_pool_mol"] > 0.0
            and row["native_fe_tap_mol"] > row["native_fe_vapor_mol"]
            and row["native_fe_vapor_escape_fraction_of_pool"] < OWNER_RECIPE_MAX_ESCAPE_FRACTION
            and row["stage_3_total_kg"] > 0.0
            and row["SiO_flux_kg_hr"] >= OWNER_RECIPE_MIN_SIO_KG_HR
            and row["row_passes_base_integrity"]
        )
        add(
            "owner_pN2_recipe_point",
            owner_pass,
            (
                "native_pool={native_fe_pool_mol:.6g} tap={native_fe_tap_mol:.6g} "
                "vapor={native_fe_vapor_mol:.6g} escape={native_fe_vapor_escape_fraction_of_pool:.6g} "
                "stage3_Fe_wt={stage_3_Fe_wt_pct:.6g} SiO={SiO_flux_kg_hr:.6g}"
            ).format(**row),
        )
    else:
        add("owner_pN2_recipe_point", False, "owner row missing")

    passing_target_rows = [
        r for r in rows
        if r["row_passes_base_integrity"]
        and r["native_fe_pool_mol"] > 0.0
        and r["native_fe_tap_mol"] > r["native_fe_vapor_mol"]
        and r["native_fe_vapor_escape_fraction_of_pool"] < OWNER_RECIPE_MAX_ESCAPE_FRACTION
        and r["SiO_flux_kg_hr"] >= OWNER_RECIPE_MIN_SIO_KG_HR
    ]
    first_T = min((float(r["temperature_C"]) for r in passing_target_rows), default=None)
    add(
        "grind_ready_target_window",
        first_T is not None and first_T >= 1600.0,
        f"first_passing_T_C={first_T}",
    )
    return assertions


def run_validation_map(*, smoke: bool = False) -> dict[str, Any]:
    warnings.filterwarnings("ignore", category=UserWarning, module="simulator.melt_backend.vaporock")
    setpoints, feedstocks, vapor_pressures = _load_data()
    calibration = calibrate_dose(setpoints, feedstocks, vapor_pressures)
    scope = grid_scope(smoke)
    grid = smoke_grid() if smoke else full_grid()
    rows = [
        run_row(
            T,
            gas,
            dose,
            calibration,
            setpoints,
            feedstocks,
            vapor_pressures,
            grid_scope_label=scope,
        )
        for T, gas, dose in grid
    ]
    anchors = manual_fO2_anchors(setpoints, feedstocks, vapor_pressures)
    assertions = evaluate_assertions(
        rows,
        anchors,
        expected_rows=expected_grid_count(smoke=smoke),
        grid_scope_label=scope,
    )
    grid_payload = grid_spec(smoke=smoke)
    grid_payload["row_count"] = len(rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "commit": _git_commit(),
        "feedstock": FEEDSTOCK,
        "batch_kg": BATCH_KG,
        "smoke": smoke,
        "grid_scope": scope,
        "map_scope": "single-hour point diagnostic; not a full campaign trajectory",
        "sample_time_h": SAMPLE_TIME_H,
        "grid": grid_payload,
        "units": ROW_UNITS,
        "dose_calibration": calibration.__dict__,
        "manual_fO2_anchors": anchors,
        "assertions": assertions,
        "rows": rows,
    }


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _json_default(value: Any) -> Any:
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return value


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field, "")) for field in fields})


def write_json(payload: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _assertion_mark(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def write_markdown(payload: Mapping[str, Any], path: Path, *, command: str) -> None:
    rows = list(payload["rows"])
    assertions = list(payload["assertions"])
    assertions_by_name = {a["name"]: a for a in assertions}
    owner_assertion = assertions_by_name.get(
        "owner_pN2_recipe_point",
        {"passed": False, "detail": "owner assertion missing"},
    )
    grid = dict(payload["grid"])
    grid_scope_label = str(payload["grid_scope"])
    owner_rows = [
        r for r in rows
        if r["temperature_C"] in (1650.0, 1700.0)
        and math.isclose(r["requested_pO2_mbar"], 1.0e-6)
        and r["requested_pN2_mbar"] in PN2_MBAR
        and math.isclose(r["dose_fraction_of_full_FeO_equiv"], 1.0)
    ]
    pO2_slice = [
        r for r in rows
        if r["temperature_C"] in (1600.0, 1650.0, 1700.0)
        and math.isclose(r["requested_pN2_mbar"], 10.0)
        and math.isclose(r["dose_fraction_of_full_FeO_equiv"], 1.0)
        and r["requested_pO2_mbar"] in PO2_MBAR
    ]
    dose_thresholds: list[list[Any]] = []
    for gas in gas_grid():
        matching = [
            r for r in rows
            if r["temperature_C"] == 1650.0
            and math.isclose(r["requested_pO2_mbar"], gas.pO2_mbar)
            and math.isclose(r["requested_pN2_mbar"], gas.pN2_mbar)
        ]
        if not matching:
            continue
        native_rows = [r for r in matching if r["native_fe_pool_mol"] > 0.0]
        positive_native_rows = [
            r for r in native_rows
            if r["dose_fraction_of_full_FeO_equiv"] > 0.0
        ]
        first_native = min(
            native_rows,
            key=lambda r: r["dose_fraction_of_full_FeO_equiv"],
            default=None,
        )
        first_positive = min(
            positive_native_rows,
            key=lambda r: r["dose_fraction_of_full_FeO_equiv"],
            default=None,
        )
        peak = max(matching, key=lambda r: r["native_fe_pool_mol"])
        dose_thresholds.append([
            gas.gas_regime,
            f"{gas.pO2_mbar:.6g}",
            f"{gas.pN2_mbar:.6g}",
            (
                f"{first_native['dose_fraction_of_full_FeO_equiv']:.6g}"
                if first_native is not None
                else "none"
            ),
            (
                f"{first_positive['dose_fraction_of_full_FeO_equiv']:.6g}"
                if first_positive is not None
                else "none"
            ),
            f"{peak['dose_fraction_of_full_FeO_equiv']:.6g}",
            f"{peak['native_fe_pool_mol']:.6g}",
        ])
    moved_pin_rows = [
        ["existing runner/golden pins", "none changed by this harness", "no rebaseline"],
        [
            "new distilled fixture",
            "tests/goldens/sso_r_validation_map_lunar_mare_low_ti.json",
            f"{grid_scope_label} validation anchor; owner row classified current blocker if SiO target fails",
        ],
    ]
    unit_rows = [
        [field, unit]
        for field, unit in ROW_UNITS.items()
        if rows and field in rows[0]
    ]
    text = "\n\n".join([
        "Consumer: owner/pre-grind operator recipe selection.",
        "# SSO-R validation map",
        f"- Commit: `{payload['commit']}`",
        f"- Run command: `{command}`",
        f"- Grid scope: `{grid_scope_label}`",
        f"- Grid rows: `{grid['row_count']}` of requested `{grid['expected_row_count']}`",
        f"- Map scope: `{payload['map_scope']}` (`sample_time_h={payload['sample_time_h']}`)",
        f"- Feedstock/batch: `{FEEDSTOCK}`, `{BATCH_KG:g} kg`",
        f"- Dose calibration: `{payload['dose_calibration']['transition_name']}` at `{DOSE_CALIBRATION_T_C:g} C`; full FeO equivalent `{payload['dose_calibration']['full_feo_equiv_dose_kg']:.9g} kg Na`.",
        "## Recipe certification",
        (
            f"Overall owner recipe certification: **{_assertion_mark(bool(owner_assertion['passed']))}**. "
            f"Detail: `{owner_assertion['detail']}`. Fe purity/base-integrity PASS values are component checks, not recipe certification."
        ),
        "## pN2 recipe table",
        _table(
            ["T_C", "pN2_mbar", "native_pool_mol", "tap_mol", "vapor_mol", "escape_frac", "stage3_Fe_wt_pct", "SiO_kg_hr", "base_integrity"],
            [
                [
                    f"{r['temperature_C']:.0f}",
                    f"{r['requested_pN2_mbar']:.0f}",
                    f"{r['native_fe_pool_mol']:.6g}",
                    f"{r['native_fe_tap_mol']:.6g}",
                    f"{r['native_fe_vapor_mol']:.6g}",
                    f"{r['native_fe_vapor_escape_fraction_of_pool']:.6g}",
                    f"{r['stage_3_Fe_wt_pct']:.6g}",
                    f"{r['SiO_flux_kg_hr']:.6g}",
                    _assertion_mark(bool(r["row_passes_base_integrity"])),
                ]
                for r in sorted(owner_rows, key=lambda row: (row["temperature_C"], row["requested_pN2_mbar"]))
            ],
        ),
        "## pO2 SiO suppression slice",
        _table(
            ["T_C", "pO2_mbar", "native_pool_mol", "tap_mol", "SiO_kg_hr", "fO2_diag_status"],
            [
                [
                    f"{r['temperature_C']:.0f}",
                    f"{r['requested_pO2_mbar']:.6g}",
                    f"{r['native_fe_pool_mol']:.6g}",
                    f"{r['native_fe_tap_mol']:.6g}",
                    f"{r['SiO_flux_kg_hr']:.6g}",
                    r["fO2_diagnostic_status"],
                ]
                for r in sorted(pO2_slice, key=lambda row: (row["temperature_C"], row["requested_pO2_mbar"]))
            ],
        ),
        "## Dose response table",
        _table(
            [
                "gas_regime",
                "pO2_mbar",
                "pN2_mbar",
                "first_native_pool_fraction",
                "first_positive_dosed_native_fraction",
                "peak_native_pool_fraction",
                "peak_native_pool_mol",
            ],
            dose_thresholds,
        ),
        "## Assertion table",
        _table(
            ["assertion", "status", "detail"],
            [[a["name"], _assertion_mark(a["passed"]), a["detail"]] for a in assertions],
        ),
        "## Units and denominators",
        _table(["field", "unit_or_denominator"], unit_rows),
        "## Golden/rebaseline table",
        _table(["pin", "movement", "classification"], moved_pin_rows),
        "## Source-label readers",
        _table(
            ["payload", "reader"],
            [
                ["redox_source_terms_mol_o2_equiv_by_label", "runner.build_per_hour_summary redox_source_breakdown"],
                ["native_fe_event_source_label", "validation-map row native_fe_event_type/source_label"],
                ["post_exchange_fO2_log_diagnostic", "diagnostic-only row field; not a certification anchor"],
                ["stage_3_capture", "runner.build_per_hour_summary stage_3_capture"],
            ],
        ),
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def golden_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    rows = list(payload["rows"])

    def find_row(T: float, pO2: float, pN2: float, dose: float) -> dict[str, Any]:
        for row in rows:
            if (
                math.isclose(float(row["temperature_C"]), T)
                and math.isclose(float(row["requested_pO2_mbar"]), pO2)
                and math.isclose(float(row["requested_pN2_mbar"]), pN2)
                and math.isclose(float(row["dose_fraction_of_full_FeO_equiv"]), dose)
            ):
                return row
        raise KeyError((T, pO2, pN2, dose))

    owner = find_row(1650.0, 1.0e-6, 10.0, 1.0)
    pO2_slice = sorted(
        [
            row for row in rows
            if math.isclose(float(row["temperature_C"]), 1650.0)
            and math.isclose(float(row["requested_pN2_mbar"]), 10.0)
            and math.isclose(float(row["dose_fraction_of_full_FeO_equiv"]), 1.0)
            and float(row["requested_pO2_mbar"]) > 0.0
        ],
        key=lambda row: row["requested_pO2_mbar"],
    )
    dose_slice = sorted(
        [
            row for row in rows
            if math.isclose(float(row["temperature_C"]), 1650.0)
            and math.isclose(float(row["requested_pO2_mbar"]), 1.0e-6)
            and math.isclose(float(row["requested_pN2_mbar"]), 10.0)
        ],
        key=lambda row: row["dose_fraction_of_full_FeO_equiv"],
    )
    pn2_slice = sorted(
        [
            row for row in rows
            if math.isclose(float(row["temperature_C"]), 1650.0)
            and math.isclose(float(row["requested_pO2_mbar"]), 1.0e-6)
            and math.isclose(float(row["dose_fraction_of_full_FeO_equiv"]), 1.0)
            and float(row["requested_pN2_mbar"]) > 0.0
        ],
        key=lambda row: row["requested_pN2_mbar"],
    )
    owner_pass = next(
        a for a in payload["assertions"] if a["name"] == "owner_pN2_recipe_point"
    )["passed"]
    return {
        "schema_version": GOLDEN_SCHEMA_VERSION,
        "source_commit": payload["commit"],
        "grid_scope": payload["grid_scope"],
        "grid_expected_row_count": payload["grid"]["expected_row_count"],
        "grid_row_count": payload["grid"]["row_count"],
        "manual_fO2_anchors": payload["manual_fO2_anchors"],
        "owner_pn2_row": {
            "temperature_C": 1650.0,
            "pO2_mbar": 1.0e-6,
            "pN2_mbar": 10.0,
            "dose_fraction": 1.0,
            "native_fe_pool_mol": owner["native_fe_pool_mol"],
            "native_fe_tap_mol": owner["native_fe_tap_mol"],
            "native_fe_vapor_mol": owner["native_fe_vapor_mol"],
            "native_fe_vapor_escape_fraction_of_pool": owner[
                "native_fe_vapor_escape_fraction_of_pool"
            ],
            "stage_3_Fe_wt_pct": owner["stage_3_Fe_wt_pct"],
            "SiO_flux_kg_hr": owner["SiO_flux_kg_hr"],
            "owner_recipe_pass": owner_pass,
            "classification": (
                "certification_pass" if owner_pass else "current_physics_blocker"
            ),
        },
        "pO2_sio_suppression_slice": [
            {
                "pO2_mbar": row["requested_pO2_mbar"],
                "native_fe_pool_mol": row["native_fe_pool_mol"],
                "native_fe_tap_mol": row["native_fe_tap_mol"],
                "SiO_flux_kg_hr": row["SiO_flux_kg_hr"],
            }
            for row in pO2_slice
        ],
        "dose_reduction_slice": [
            {
                "dose_fraction": row["dose_fraction_of_full_FeO_equiv"],
                "dose_consumed_kg": row["dose_consumed_kg"],
                "dose_feo_reduced_mol": row["dose_feo_reduced_mol"],
                "native_fe_pool_mol": row["native_fe_pool_mol"],
            }
            for row in dose_slice
        ],
        "pN2_monotonic_slice": [
            {
                "pN2_mbar": row["requested_pN2_mbar"],
                "native_fe_vapor_escape_fraction_of_pool": row[
                    "native_fe_vapor_escape_fraction_of_pool"
                ],
            }
            for row in pn2_slice
        ],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "docs-private" / "research" / "2026-07-02-sso-r-validation-map",
    )
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--write-golden", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = run_validation_map(smoke=args.smoke)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "validation-map.csv"
    json_path = args.out_dir / "validation-map.json"
    md_path = args.out_dir / "validation-map.md"
    write_csv(payload["rows"], csv_path)
    write_json(payload, json_path)
    command = "python3 scripts/sso_r_validation_map.py --out-dir " + str(args.out_dir)
    if args.smoke:
        command += " --smoke"
    write_markdown(payload, md_path, command=command)
    if args.write_golden is not None:
        write_json(golden_payload(payload), args.write_golden)
    failing = [a for a in payload["assertions"] if not a["passed"]]
    print(
        f"scope={payload['grid_scope']} rows={len(payload['rows'])} "
        f"expected={payload['grid']['expected_row_count']} "
        f"csv={csv_path} json={json_path} md={md_path}"
    )
    if failing:
        print("failing_assertions=" + ",".join(a["name"] for a in failing))
    if args.strict and failing:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
