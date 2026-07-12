#!/usr/bin/env python3
"""Precompute a feedstock-anchored AlphaMELTS composition/temperature grid."""

from __future__ import annotations

import argparse
import base64
import dataclasses
import json
import math
import multiprocessing
import os
import random
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence
from unittest import mock

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.grid_pregrind_writer import (  # noqa: E402
    ALPHAMELTS_DIAGNOSTIC_OUTPUT_FIELDS,
    FINDER_INPUT_FIELDS,
    GENERIC_OUTPUT_FIELDS,
    GridCacheWriter,
    canonical_input_vector,
    canonical_json,
    utc_now,
)
from engines.alphamelts.domain import AlphaMELTSDomainGate  # noqa: E402
from engines.domain_reason import OutOfDomainReason  # noqa: E402
# simulator.fe_redox is imported LAZILY (see kress91_partition_parameters and
# kress91_partitioned_composition_mol): Kress partitioning runs only at
# --prepare-only key-generation time on the laptop's epoch tree. Studio drain
# hosts execute pre-partitioned keys against public 3a0e64c, whose fe_redox
# lacks the epoch's calibration constants — a module-level import would break
# the drain path for machinery it never uses.


MELTS_OXIDE_BASIS = (
    "SiO2",
    "TiO2",
    "Al2O3",
    "FeO",
    "Fe2O3",
    "MgO",
    "CaO",
    "Na2O",
    "K2O",
    "Cr2O3",
    "MnO",
    "P2O5",
    "NiO",
    "CoO",
)

MAJOR_OXIDES = (
    "SiO2",
    "TiO2",
    "Al2O3",
    "FeO",
    "MgO",
    "CaO",
    "Na2O",
    "K2O",
)

FIXED_TRACE_OXIDES = ("MnO", "NiO", "CoO", "P2O5")
DEFAULT_INTENDED_FO2_GRID = (
    -12.0,
    -11.0,
    -10.0,
    -9.0,
    -8.0,
    -7.0,
    -6.0,
    -5.0,
)
ENGINE_PRESSURE_BAR = 1.0
KRESS91_PARTITION_VERSION = "REF-001-kress-carmichael-1991"
RAW_PAYLOAD_FORMAT = (
    "alphamelts-subprocess-capture-v3-isothermal-fO2-properties"
)


def kress91_partition_parameters():
    """Prepare-path-only metadata; lazily imports the epoch fe_redox constants."""
    from simulator.fe_redox import (
        KRESS91_INV_T_COEFFICIENT_K,
        KRESS91_LIQUID_CALIBRATION_MAX_T_C,
        KRESS91_LIQUID_CALIBRATION_MIN_T_C,
        KRESS91_LN_FO2_COEFFICIENT,
        KRESS91_MOL_FRACTION_OXIDES,
    )

    return {
        "implementation": "simulator.fe_redox:kress91_split",
        "version": KRESS91_PARTITION_VERSION,
        "reference": "Kress & Carmichael 1991, doi:10.1007/BF00307328",
        "ln_fO2_coefficient": KRESS91_LN_FO2_COEFFICIENT,
        "inverse_temperature_coefficient_K": KRESS91_INV_T_COEFFICIENT_K,
        "mole_fraction_oxides": KRESS91_MOL_FRACTION_OXIDES,
        "liquid_calibration_min_C": KRESS91_LIQUID_CALIBRATION_MIN_T_C,
        "liquid_calibration_max_C": KRESS91_LIQUID_CALIBRATION_MAX_T_C,
        "authority_gate": {
            "policy": (
                "partition_T_C=max(requested_T_C, known_liquidus_T_C, "
                "liquid_calibration_min_C)"
            ),
            "liquidus_source": "unavailable_during_pregrind_key_generation",
            "non_authoritative": (
                "compute_with_liquidus_unverified_or_extrapolation_provenance"
            ),
            "per_point_provenance": (
                "deterministic from grid key temperature_C and this policy; "
                "requested/applied temperature and band fields are exposed by "
                "kress91_partition_authority_record"
            ),
            "typed_refusal": "invalid_non_finite_control_only",
        },
        "pressure_bar": ENGINE_PRESSURE_BAR,
    }

DEFAULT_FEEDSTOCK_ANCHORS = (
    "lunar_mare_low_ti",
    "lunar_mare_high_ti",
    "lunar_highland",
    "mars_basalt",
    "ci_carbonaceous_chondrite",
)

DEFAULT_DB = REPO_ROOT / "docs-private/recipe-db/grind-alphamelts-expedited.db"
DEFAULT_STATUS = (
    REPO_ROOT / "docs-private/recipe-db/grind-alphamelts-expedited.status.json"
)
DEFAULT_FEEDSTOCKS = REPO_ROOT / "data/feedstocks.yaml"
DEFAULT_SEED = 20260710
DEFAULT_BATCH_MASS_KG = 100.0


_STOP_REQUESTED = False
_WORKER_BACKEND: Any = None
_WORKER_MODULE: Any = None
_WORKER_ENGINE_VERSION = "unavailable"
_WORKER_INIT_ERROR: str | None = None


@dataclasses.dataclass(frozen=True)
class GridPoint:
    ordinal: int
    temperature_C: float
    intended_fO2_log: float
    pressure_bar: float
    composition_wt_pct: dict[str, float]


@dataclasses.dataclass(frozen=True)
class WorkerJob:
    grid_key_id: int
    shuffle_rank: int
    inputs: dict[str, Any]


def _request_stop(signum: int, frame: Any) -> None:
    del signum, frame
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


def normalize_composition(composition: Mapping[str, float]) -> dict[str, float]:
    positive = {
        str(species): float(value)
        for species, value in composition.items()
        if float(value) > 0.0
    }
    total = sum(positive.values())
    if total <= 0.0:
        raise ValueError("composition must contain positive oxide mass")
    return {
        species: value * 100.0 / total
        for species, value in sorted(positive.items())
    }


def load_feedstock_box(
    path: str | os.PathLike[str],
    *,
    anchors: Sequence[str] = DEFAULT_FEEDSTOCK_ANCHORS,
    step_pct: float = 10.0,
    margin_pct: float = 5.0,
) -> dict[str, tuple[float, float]]:
    data = yaml.safe_load(Path(path).read_text())
    minima = {oxide: 100.0 for oxide in MAJOR_OXIDES}
    maxima = {oxide: 0.0 for oxide in MAJOR_OXIDES}
    for anchor in anchors:
        if anchor not in data:
            raise KeyError(f"feedstock anchor missing: {anchor}")
        entry = data[anchor]
        nominal = {
            oxide: float((entry.get("composition_wt_pct") or {}).get(oxide, 0.0))
            for oxide in MAJOR_OXIDES
        }
        ranges = entry.get("composition_ranges") or {}
        low = {
            oxide: float(ranges.get(oxide, (nominal[oxide], nominal[oxide]))[0])
            for oxide in MAJOR_OXIDES
        }
        high = {
            oxide: float(ranges.get(oxide, (nominal[oxide], nominal[oxide]))[1])
            for oxide in MAJOR_OXIDES
        }
        vectors = [nominal]
        for target_oxide in MAJOR_OXIDES:
            vectors.append(
                {
                    oxide: low[oxide] if oxide == target_oxide else high[oxide]
                    for oxide in MAJOR_OXIDES
                }
            )
            vectors.append(
                {
                    oxide: high[oxide] if oxide == target_oxide else low[oxide]
                    for oxide in MAJOR_OXIDES
                }
            )
        # Premise: normalized wt_i = 100*m_i/sum(m_j). The minimum uses the
        # target oxide's low bound with every denominator term high; the
        # maximum reverses those extremes. Units remain wt% because the ratio
        # is dimensionless * 100. Sanity: all-low/all-high can cancel a wide
        # FeO or MgO range, while these cross-extremes cannot hide it.
        for vector in vectors:
            normalized = normalize_composition(vector)
            for oxide in MAJOR_OXIDES:
                value = normalized.get(oxide, 0.0)
                minima[oxide] = min(minima[oxide], value)
                maxima[oxide] = max(maxima[oxide], value)

    bounds: dict[str, tuple[float, float]] = {}
    for oxide in MAJOR_OXIDES:
        lower = max(0.0, minima[oxide] - margin_pct)
        upper = min(100.0, maxima[oxide] + margin_pct)
        aligned_lower = math.ceil((lower - 1e-12) / step_pct) * step_pct
        aligned_upper = math.floor((upper + 1e-12) / step_pct) * step_pct
        bounds[oxide] = (aligned_lower, aligned_upper)
    return bounds


def generate_simplex_grid(
    bounds: Mapping[str, tuple[float, float]],
    *,
    step_pct: float = 10.0,
) -> list[dict[str, float]]:
    basis = tuple(bounds)
    total_units = round(100.0 / step_pct)
    if not math.isclose(total_units * step_pct, 100.0, abs_tol=1e-12):
        raise ValueError("step_pct must divide 100 exactly")
    lower_units = {
        oxide: math.ceil((bounds[oxide][0] - 1e-12) / step_pct)
        for oxide in basis
    }
    upper_units = {
        oxide: math.floor((bounds[oxide][1] + 1e-12) / step_pct)
        for oxide in basis
    }
    points: list[dict[str, float]] = []
    seen: set[tuple[float, ...]] = set()
    units: list[int] = []

    def visit(index: int, remaining: int) -> None:
        oxide = basis[index]
        if index == len(basis) - 1:
            if lower_units[oxide] <= remaining <= upper_units[oxide]:
                values = [*units, remaining]
                raw = {
                    species: value * step_pct
                    for species, value in zip(basis, values)
                    if value > 0
                }
                normalized = normalize_composition(raw)
                key = tuple(normalized.get(name, 0.0) for name in basis)
                if key not in seen:
                    seen.add(key)
                    points.append(normalized)
            return
        tail = basis[index + 1 :]
        tail_min = sum(lower_units[name] for name in tail)
        tail_max = sum(upper_units[name] for name in tail)
        first = max(lower_units[oxide], remaining - tail_max)
        last = min(upper_units[oxide], remaining - tail_min)
        for value in range(first, last + 1):
            units.append(value)
            visit(index + 1, remaining - value)
            units.pop()

    visit(0, total_units)
    return points


def expand_composition_axes(
    path: str | os.PathLike[str],
    major_points: Sequence[Mapping[str, float]],
    *,
    anchors: Sequence[str] = DEFAULT_FEEDSTOCK_ANCHORS,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    data = yaml.safe_load(Path(path).read_text())
    nominal_vectors: list[dict[str, float]] = []
    cr_candidates: list[float] = []
    for anchor in anchors:
        entry = data[anchor]
        nominal = {
            oxide: float((entry.get("composition_wt_pct") or {}).get(oxide, 0.0))
            for oxide in MELTS_OXIDE_BASIS
        }
        nominal_normalized = normalize_composition(nominal)
        nominal_vectors.append(nominal_normalized)
        ranges = entry.get("composition_ranges") or {}
        for vector in (
            nominal,
            {
                oxide: float(ranges.get(oxide, (nominal[oxide], nominal[oxide]))[0])
                for oxide in MELTS_OXIDE_BASIS
            },
            {
                oxide: float(ranges.get(oxide, (nominal[oxide], nominal[oxide]))[1])
                for oxide in MELTS_OXIDE_BASIS
            },
        ):
            cr_candidates.append(normalize_composition(vector).get("Cr2O3", 0.0))

    fixed_trace = {
        oxide: sum(vector.get(oxide, 0.0) for vector in nominal_vectors)
        / len(nominal_vectors)
        for oxide in FIXED_TRACE_OXIDES
    }
    cr_nominal = sum(vector.get("Cr2O3", 0.0) for vector in nominal_vectors) / len(
        nominal_vectors
    )
    cr_levels = sorted({min(cr_candidates), cr_nominal, max(cr_candidates)})
    points: list[dict[str, float]] = []
    seen: set[str] = set()
    for cr_level in cr_levels:
        reserved = cr_level + sum(fixed_trace.values())
        scale = (100.0 - reserved) / 100.0
        for major in major_points:
            composition = {oxide: 0.0 for oxide in MELTS_OXIDE_BASIS}
            for oxide in MAJOR_OXIDES:
                composition[oxide] = float(major.get(oxide, 0.0)) * scale
            composition["Cr2O3"] = cr_level
            composition.update(fixed_trace)
            composition["SiO2"] += 100.0 - sum(composition.values())
            key = canonical_json(composition)
            if key not in seen:
                seen.add(key)
                points.append(composition)
    return points, {
        "major_oxides": MAJOR_OXIDES,
        "fixed_trace_wt_pct": fixed_trace,
        "cr2o3_levels_wt_pct": cr_levels,
        "fe_basis": "FeO_total represented by FeO with Fe2O3 fixed at zero",
    }


def _inclusive_range(start: float, stop: float, step: float) -> list[float]:
    if step <= 0:
        raise ValueError("temperature step must be positive")
    values: list[float] = []
    index = 0
    while True:
        value = start + index * step
        if value > stop + 1e-12:
            break
        values.append(float(value))
        index += 1
    return values


def temperature_grid(
    minimum_C: float = 800.0,
    maximum_C: float = 1800.0,
    *,
    coarse_step_C: float = 50.0,
    dense_minimum_C: float = 1100.0,
    dense_maximum_C: float = 1400.0,
    dense_step_C: float = 25.0,
) -> list[float]:
    if minimum_C > maximum_C:
        raise ValueError("temperature minimum exceeds maximum")
    values = set(_inclusive_range(minimum_C, maximum_C, coarse_step_C))
    dense_start = max(minimum_C, dense_minimum_C)
    dense_stop = min(maximum_C, dense_maximum_C)
    if dense_start <= dense_stop:
        values.update(_inclusive_range(dense_start, dense_stop, dense_step_C))
    return sorted(values)


def build_grid_points(
    compositions: Sequence[dict[str, float]],
    temperatures_C: Sequence[float],
    intended_fO2_logs: Sequence[float] = DEFAULT_INTENDED_FO2_GRID,
    *,
    seed: int,
    limit: int | None = None,
    filter_stats: MutableMapping[str, int] | None = None,
) -> tuple[int, list[GridPoint]]:
    pairs = [
        (composition_index, temperature_C, intended_fO2_log)
        for composition_index in range(len(compositions))
        for temperature_C in temperatures_C
        # fO2 is now an engine constraint, not Fe-partition provenance only;
        # retain the full axis even when the input composition has no iron.
        for intended_fO2_log in intended_fO2_logs
    ]
    unfiltered_total = len(pairs)
    eligible_pairs = []
    filtered_silicate_window = 0
    eligible_kress91_computed = 0
    eligible_kress91_floor_adjusted = 0
    eligible_kress91_non_authoritative = 0
    eligible_kress91_extrapolated = 0
    eligible_kress91_iron_free = 0
    for composition_index, temperature_C, intended_fO2_log in pairs:
        candidate = GridPoint(
            ordinal=-1,
            temperature_C=float(temperature_C),
            intended_fO2_log=float(intended_fO2_log),
            pressure_bar=ENGINE_PRESSURE_BAR,
            composition_wt_pct=dict(compositions[composition_index]),
        )
        if alphamelts_queue_domain_reason(candidate) == (
            OutOfDomainReason.SILICATE_WINDOW.value
        ):
            filtered_silicate_window += 1
            continue
        eligible_pairs.append(
            (composition_index, temperature_C, intended_fO2_log)
        )
        has_iron = any(
            float(candidate.composition_wt_pct.get(species, 0.0) or 0.0) > 0.0
            for species in ("FeO", "Fe2O3")
        )
        if not has_iron:
            eligible_kress91_iron_free += 1
            continue
        record = kress91_partition_authority_record(
            temperature_C=candidate.temperature_C
        )
        eligible_kress91_computed += 1
        eligible_kress91_floor_adjusted += int(bool(record["adjusted"]))
        eligible_kress91_non_authoritative += int(
            not bool(record["authoritative"])
        )
        eligible_kress91_extrapolated += int(bool(record["extrapolation"]))
    random.Random(seed).shuffle(eligible_pairs)
    total = len(eligible_pairs)
    if filter_stats is not None:
        filter_stats.update(
            {
                "unfiltered_grid_points": unfiltered_total,
                "filtered_silicate_window_points": filtered_silicate_window,
                "eligible_kress91_computed_points": eligible_kress91_computed,
                "eligible_kress91_floor_adjusted_points": (
                    eligible_kress91_floor_adjusted
                ),
                "eligible_kress91_non_authoritative_points": (
                    eligible_kress91_non_authoritative
                ),
                "eligible_kress91_extrapolated_points": (
                    eligible_kress91_extrapolated
                ),
                "eligible_kress91_iron_free_points": eligible_kress91_iron_free,
                "eligible_grid_points": total,
            }
        )
    if limit is not None:
        eligible_pairs = eligible_pairs[: max(0, limit)]
    return total, [
        GridPoint(
            ordinal=ordinal,
            temperature_C=float(temperature_C),
            intended_fO2_log=float(intended_fO2_log),
            pressure_bar=ENGINE_PRESSURE_BAR,
            composition_wt_pct=dict(compositions[composition_index]),
        )
        for ordinal, (
            composition_index,
            temperature_C,
            intended_fO2_log,
        ) in enumerate(eligible_pairs)
    ]


def composition_wt_pct_to_mol(
    composition_wt_pct: Mapping[str, float],
    *,
    batch_mass_kg: float = DEFAULT_BATCH_MASS_KG,
) -> dict[str, float]:
    from simulator.accounting.formulas import resolve_species_formula

    result: dict[str, float] = {}
    for species, wt_pct in composition_wt_pct.items():
        mass_kg = batch_mass_kg * float(wt_pct) / 100.0
        molar_mass = resolve_species_formula(species).molar_mass_kg_per_mol()
        result[str(species)] = mass_kg / molar_mass
    return dict(sorted(result.items()))


def kress91_partitioned_composition_mol(
    composition_wt_pct: Mapping[str, float],
    *,
    temperature_C: float,
    intended_fO2_log: float,
    pressure_bar: float = ENGINE_PRESSURE_BAR,
    batch_mass_kg: float = DEFAULT_BATCH_MASS_KG,
    liquidus_T_C: float | None = None,
) -> dict[str, float]:
    baseline_mol = composition_wt_pct_to_mol(
        composition_wt_pct, batch_mass_kg=batch_mass_kg
    )
    total_fe_mol = baseline_mol.get("FeO", 0.0) + (
        2.0 * baseline_mol.get("Fe2O3", 0.0)
    )
    if total_fe_mol <= 0.0:
        return baseline_mol

    authority = kress91_partition_authority_record(
        temperature_C=temperature_C,
        liquidus_T_C=liquidus_T_C,
    )

    from simulator.fe_redox import kress91_split, melt_mol_fractions_for_kress91

    mol_fractions = melt_mol_fractions_for_kress91(composition_wt_pct)
    split = kress91_split(
        fO2_log=float(intended_fO2_log),
        mol_fractions=mol_fractions,
        T_K=float(authority["partition_temperature_C"]) + 273.15,
        pressure_bar=float(pressure_bar),
    )
    ferric_fraction = float(split["fe3"])

    # Premise: the backbone's FeO + Fe2O3 inventory defines total Fe atoms.
    # Kress91 supplies ln(Fe2O3/FeO) = f(T, fO2, melt composition), exposed by
    # kress91_split as dimensionless Fe3+/sum(Fe). Therefore n(Fe2O3) =
    # 0.5*ferric_fraction*n(Fe_total) and n(FeO) = (1-ferric_fraction)*
    # n(Fe_total). Units: dimensionless * mol Fe -> mol oxide; the identity
    # 2*n(Fe2O3)+n(FeO)=n(Fe_total) closes exactly. Sanity: the authority
    # record distinguishes floor adjustment and extrapolation from certified
    # use while this Fe-atom identity remains invariant in every branch.
    partitioned = dict(baseline_mol)
    partitioned["Fe2O3"] = 0.5 * ferric_fraction * total_fe_mol
    partitioned["FeO"] = (1.0 - ferric_fraction) * total_fe_mol
    return dict(sorted(partitioned.items()))


def kress91_partition_authority_record(
    *,
    temperature_C: float,
    liquidus_T_C: float | None = None,
) -> dict[str, Any]:
    """Return the liquid-regime action and authority provenance."""
    from simulator.fe_redox import (
        KRESS91_LIQUID_CALIBRATION_MIN_T_C,
        kress91_temperature_band_case,
    )

    requested = float(temperature_C)
    if not math.isfinite(requested):
        raise ValueError("Kress91 requested temperature must be finite")
    liquidus = None if liquidus_T_C is None else float(liquidus_T_C)
    if liquidus is not None and not math.isfinite(liquidus):
        raise ValueError("Kress91 liquidus temperature must be finite when provided")
    gate = max(
        KRESS91_LIQUID_CALIBRATION_MIN_T_C,
        liquidus if liquidus is not None else KRESS91_LIQUID_CALIBRATION_MIN_T_C,
    )
    partition_temperature = max(requested, gate)
    band = kress91_temperature_band_case(partition_temperature)
    liquidus_verified = liquidus is not None
    authoritative = bool(band["authoritative"]) and liquidus_verified
    # Premise: Kress91 is a liquid-silicate relation, so the usable partition
    # temperature is max(requested T, actual liquidus when known, 1200 C
    # calibration floor). Units remain degC. Sanity: an 800 C grid point
    # partitions at 1200 C instead of silently extrapolating or disappearing;
    # absent liquidus evidence keeps the result explicitly non-authoritative.
    return {
        "requested_temperature_C": requested,
        "partition_temperature_C": partition_temperature,
        "applied_temperature_C": partition_temperature,
        "gate_temperature_C": gate,
        "liquidus_temperature_C": liquidus,
        "action": "apply_kress91_partition",
        "action_reason": (
            "adjusted_to_liquid_authority_gate"
            if partition_temperature != requested
            else "computed_at_requested_temperature"
        ),
        "adjusted": partition_temperature != requested,
        "liquidus_verified": liquidus_verified,
        "authority_source": (
            "liquidus_plus_calibrated_floor"
            if liquidus is not None
            else "calibrated_floor_only_liquidus_unavailable"
        ),
        "temperature_band_case": band["case"],
        "temperature_band_status": band["status"],
        "temperature_band_source": band["source"],
        "temperature_band_authoritative": band["authoritative"],
        "authoritative": authoritative,
        "extrapolation": band["extrapolation"],
        "high_uncertainty": band["high_uncertainty"],
    }


def alphamelts_queue_domain_reason(point: GridPoint) -> str | None:
    """Return the canonical AlphaMELTS domain reason for a generated point."""
    from simulator.accounting.formulas import resolve_species_formula

    composition_mol = kress91_partitioned_composition_mol(
        point.composition_wt_pct,
        temperature_C=point.temperature_C,
        intended_fO2_log=point.intended_fO2_log,
        pressure_bar=point.pressure_bar,
    )
    composition_kg = {
        species: float(mol) * resolve_species_formula(species).molar_mass_kg_per_mol()
        for species, mol in composition_mol.items()
        if float(mol) > 0.0
    }
    composition_wt_pct = normalize_composition(composition_kg)
    _valid, _warnings, reason = AlphaMELTSDomainGate.validate_with_reason(
        composition_wt_pct
    )
    return reason


def backend_config(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "mode": "subprocess",
        "fO2_buffer": None,
        "fO2_offset": None,
        "Fe3Fet_Liq": None,
        "model": args.model,
        "timeout_s": args.timeout_s,
        "require_petthermotools": False,
        "thermoengine_health_timeout_s": args.thermoengine_health_timeout_s,
    }


def point_inputs(point: GridPoint, args: argparse.Namespace) -> dict[str, Any]:
    partition_provenance = kress91_partition_authority_record(
        temperature_C=point.temperature_C,
    )
    composition_mol = kress91_partitioned_composition_mol(
        point.composition_wt_pct,
        temperature_C=point.temperature_C,
        intended_fO2_log=point.intended_fO2_log,
        pressure_bar=point.pressure_bar,
    )
    values: dict[str, Any] = {
        "temperature_C": point.temperature_C,
        "kress91_partition_provenance": partition_provenance,
        "composition_kg": None,
        "fO2_log": point.intended_fO2_log,
        "pressure_bar": point.pressure_bar,
        "composition_mol": composition_mol,
        "composition_mol_by_account": {
            "process.cleaned_melt": composition_mol
        },
        "species_formula_registry": None,
        "mode": "subprocess",
        "subprocess_run_mode": "isothermal",
        "redox_buffer": None,
        "fO2_offset": None,
        "Fe3Fet_Liq": None,
        "model": args.model,
        "timeout_s": args.timeout_s,
        "require_petthermotools": False,
        "thermoengine_health_timeout_s": args.thermoengine_health_timeout_s,
    }
    values.update({name: None for name in FINDER_INPUT_FIELDS})
    canonical_input_vector(values)
    return values


def probe_engine(config: Mapping[str, Any]) -> dict[str, Any]:
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend

    try:
        backend = AlphaMELTSBackend()
        available = bool(backend.initialize(dict(config)))
        return {
            "available": available and backend.is_available(),
            "mode": getattr(backend, "_mode", None),
            "engine_version": backend.get_engine_version(),
            "model": getattr(backend, "_model", None),
        }
    except Exception as exc:
        return {
            "available": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def _worker_initialize(config: Mapping[str, Any]) -> None:
    global _WORKER_BACKEND, _WORKER_MODULE, _WORKER_ENGINE_VERSION, _WORKER_INIT_ERROR
    try:
        import simulator.melt_backend.alphamelts as alphamelts_module

        backend = alphamelts_module.AlphaMELTSBackend()
        available = backend.initialize(dict(config))
        if not available or not backend.is_available():
            raise RuntimeError("AlphaMELTS subprocess transport unavailable")
        _WORKER_BACKEND = backend
        _WORKER_MODULE = alphamelts_module
        _WORKER_ENGINE_VERSION = backend.get_engine_version()
        _WORKER_INIT_ERROR = None
    except Exception as exc:
        _WORKER_BACKEND = None
        _WORKER_MODULE = None
        _WORKER_ENGINE_VERSION = "unavailable"
        _WORKER_INIT_ERROR = f"{type(exc).__name__}: {exc}"


def _raw_stream(value: Any) -> Any:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return {
            "encoding": "base64",
            "data": base64.b64encode(value).decode("ascii"),
        }
    return str(value)


def _generic_result(result: Any) -> dict[str, Any]:
    return {name: getattr(result, name) for name in GENERIC_OUTPUT_FIELDS}


def _alpha_result(result: Any, backend: Any, engine_version: str) -> dict[str, Any]:
    diagnostics = dict(getattr(result, "diagnostics", {}) or {})
    values = {
        "activity_coefficients": result.activity_coefficients,
        "applied_fe3fet": diagnostics.get("applied_fe3fet"),
        "backend_diagnostics": diagnostics,
        "backend_status": result.status,
        "backend_status_reason": (
            diagnostics.get("backend_status_reason")
            or diagnostics.get("backend_failure_reason_code")
        ),
        "backend_warnings": result.warnings,
        "engine_version": engine_version,
        "fO2_log": result.fO2_log,
        "fe_redox_policy": diagnostics.get("fe_redox_policy"),
        "intrinsic_fO2_log": diagnostics.get("intrinsic_fO2_log"),
        "liquid_composition_wt_pct": result.liquid_composition_wt_pct,
        "liquid_fraction": result.liquid_fraction,
        "liquid_fraction_path": diagnostics.get("liquid_fraction_path"),
        "liquidus_T_C": result.liquidus_T_C,
        "liquidus_T_K": diagnostics.get("liquidus_T_K"),
        "mode": getattr(backend, "_mode", None),
        "phase_masses_kg": result.phase_masses_kg,
        "phase_modes_wt_pct": diagnostics.get("phase_modes_wt_pct"),
        "phases_present": result.phases_present,
        "solidus_T_C": diagnostics.get("solidus_T_C"),
    }
    if set(values) != set(ALPHAMELTS_DIAGNOSTIC_OUTPUT_FIELDS):
        raise RuntimeError("AlphaMELTS output field inventory drift")
    return values


def _status_kind(status: str, reason: str | None = None) -> str:
    if reason == "subprocess_died":
        return "failure"
    if status == "ok":
        return "success"
    if status in {"out_of_domain", "not_converged"}:
        return "refusal"
    return "failure"


def _refusal_reason(result: Any) -> str | None:
    diagnostics = dict(getattr(result, "diagnostics", {}) or {})
    reason = (
        diagnostics.get("backend_status_reason")
        or diagnostics.get("backend_failure_reason_code")
        or diagnostics.get("reason")
    )
    if reason is not None:
        return str(reason)
    warnings = list(getattr(result, "warnings", []) or [])
    return str(warnings[0]) if result.status != "ok" and warnings else None


def _worker_failure_output(
    exc: BaseException,
    *,
    started: float,
    captures: Sequence[Mapping[str, Any]],
    native_input: Mapping[str, Any] | None,
    run_mode: str | None = None,
    applied_timeout_s: float | None = None,
) -> dict[str, Any]:
    reason = (
        getattr(exc, "backend_failure_reason_code", None)
        or getattr(exc, "backend_status_reason", None)
    )
    status = "timeout" if reason == "timeout" else "error"
    if reason == "missing_binary":
        status = "unavailable"
    raw = {
        "format": RAW_PAYLOAD_FORMAT,
        "engine_invoked": bool(captures),
        "fO2_constraint": (
            dict(native_input.get("fO2_constraint") or {})
            if native_input is not None
            else None
        ),
        "captures": list(captures),
        "exception": {
            "type": type(exc).__name__,
            "message": str(exc),
            "backend_failure_reason_code": reason,
            "backend_failure_category": getattr(
                exc, "backend_failure_category", None
            ),
        },
    }
    return {
        "status": status,
        "status_kind": "failure",
        "refusal_reason": str(reason or type(exc).__name__),
        "raw_payload": canonical_json(raw),
        "raw_payload_format": RAW_PAYLOAD_FORMAT,
        "timing_s": time.monotonic() - started,
        "engine_version": _WORKER_ENGINE_VERSION,
        "engine_mode": "subprocess",
        "engine_model": str(getattr(_WORKER_BACKEND, "_model", "unknown")),
        "run_mode": run_mode,
        "applied_timeout_s": applied_timeout_s,
        "native_input": native_input,
        "generic": {},
        "alphamelts": {
            "backend_status": status,
            "backend_status_reason": str(reason or type(exc).__name__),
            "backend_diagnostics": raw["exception"],
            "backend_warnings": [str(exc)],
            "engine_version": _WORKER_ENGINE_VERSION,
            "mode": "subprocess",
        },
        "finder": {},
        "created_at": utc_now(),
        "host": socket.gethostname(),
    }


def _job_runtime_settings(job: WorkerJob) -> tuple[float, str]:
    try:
        timeout_s = float(job.inputs["timeout_s"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("queued AlphaMELTS job has invalid timeout_s") from exc
    if not math.isfinite(timeout_s) or timeout_s <= 0.0:
        raise ValueError(
            f"queued AlphaMELTS job timeout_s must be positive and finite: {timeout_s!r}"
        )
    run_mode = str(job.inputs.get("subprocess_run_mode") or "")
    if run_mode != "isothermal":
        raise ValueError(
            "queued AlphaMELTS job subprocess_run_mode must be 'isothermal': "
            f"{run_mode!r}"
        )
    return timeout_s, run_mode


def _record_job_runtime(
    job: WorkerJob,
    output: MutableMapping[str, Any],
) -> None:
    timeout_s, run_mode = _job_runtime_settings(job)
    output_run_mode = output.get("run_mode")
    if output_run_mode is not None and str(output_run_mode) != run_mode:
        raise RuntimeError(
            f"worker run-mode mismatch: queued={run_mode!r}, applied={output_run_mode!r}"
        )
    output_timeout = output.get("applied_timeout_s")
    if output_timeout is not None and float(output_timeout) != timeout_s:
        raise RuntimeError(
            "worker timeout mismatch: "
            f"queued={timeout_s!r}, applied={output_timeout!r}"
        )
    output["run_mode"] = run_mode
    output["applied_timeout_s"] = timeout_s


def _run_point(job: WorkerJob) -> tuple[int, dict[str, Any]]:
    started = time.monotonic()
    captures: list[dict[str, Any]] = []
    native_input: dict[str, Any] | None = None
    try:
        applied_timeout_s, run_mode = _job_runtime_settings(job)
    except Exception as exc:
        return job.grid_key_id, _worker_failure_output(
            exc,
            started=started,
            captures=captures,
            native_input=native_input,
        )
    if _WORKER_BACKEND is None or _WORKER_MODULE is None:
        exc = RuntimeError(_WORKER_INIT_ERROR or "AlphaMELTS worker unavailable")
        return job.grid_key_id, _worker_failure_output(
            exc,
            started=started,
            captures=captures,
            native_input=native_input,
            run_mode=run_mode,
            applied_timeout_s=applied_timeout_s,
        )

    backend = _WORKER_BACKEND
    backend._timeout_s = applied_timeout_s
    module = _WORKER_MODULE
    original_equilibrate_subprocess = backend._equilibrate_subprocess
    original_run = module.subprocess.run

    def capture_run(*args: Any, **kwargs: Any) -> Any:
        command = args[0] if args else kwargs.get("args")
        event: dict[str, Any] = {
            "argv": list(command) if isinstance(command, (list, tuple)) else command,
            "stdin": _raw_stream(kwargs.get("input")),
            "timeout": kwargs.get("timeout"),
        }
        cwd = kwargs.get("cwd")
        input_file = Path(cwd) / "input.melts" if cwd is not None else None
        if input_file is not None and input_file.exists():
            event["input_file"] = input_file.read_text()
        try:
            completed = original_run(*args, **kwargs)
        except Exception as exc:
            event.update(
                {
                    "exception_type": type(exc).__name__,
                    "exception": str(exc),
                    "stdout": _raw_stream(getattr(exc, "stdout", None)),
                    "stderr": _raw_stream(getattr(exc, "stderr", None)),
                }
            )
            captures.append(event)
            raise
        event.update(
            {
                "returncode": completed.returncode,
                "stdout": _raw_stream(completed.stdout),
                "stderr": _raw_stream(completed.stderr),
            }
        )
        if cwd is not None:
            output_files = {
                output_path.name: output_path.read_text(errors="replace")
                for output_path in sorted(Path(cwd).glob("*_tbl.txt"))
            }
            if output_files:
                event["output_files"] = output_files
        captures.append(event)
        return completed

    def observe_subprocess(
        temperature_C: float,
        composition_wt_pct: Mapping[str, float],
        fO2_log: float,
        pressure_bar: float,
        warnings: Sequence[str] | None = None,
        *,
        diagnostics: Mapping[str, Any] | None = None,
        run_mode: Any,
    ) -> Any:
        nonlocal native_input
        native_input = {
            "temperature_C": float(temperature_C),
            "composition_wt_pct": dict(composition_wt_pct),
            "fO2_constraint": {
                "path": "Absolute",
                "offset": float(fO2_log),
            },
            "adapter_fO2_log_argument": float(fO2_log),
            "pressure_bar": float(pressure_bar),
            "run_mode": run_mode.value if hasattr(run_mode, "value") else str(run_mode),
            "applied_timeout_s": applied_timeout_s,
            "warnings": list(warnings or []),
            "diagnostics": dict(diagnostics or {}),
        }
        with mock.patch.object(module.subprocess, "run", side_effect=capture_run):
            return original_equilibrate_subprocess(
                temperature_C,
                composition_wt_pct,
                fO2_log,
                pressure_bar,
                warnings,
                diagnostics=diagnostics,
                run_mode=run_mode,
            )

    try:
        with mock.patch.object(
            backend, "_equilibrate_subprocess", side_effect=observe_subprocess
        ):
            result = backend.equilibrate(
                temperature_C=job.inputs["temperature_C"],
                composition_kg=job.inputs["composition_kg"],
                fO2_log=job.inputs["fO2_log"],
                pressure_bar=job.inputs["pressure_bar"],
                composition_mol=job.inputs["composition_mol"],
                composition_mol_by_account=job.inputs[
                    "composition_mol_by_account"
                ],
                species_formula_registry=job.inputs["species_formula_registry"],
                subprocess_run_mode=run_mode,
            )
        if result.ledger_transition is not None:
            raise RuntimeError(
                "AlphaMELTS returned forbidden diagnostic ledger_transition"
            )
        raw = {
            "format": RAW_PAYLOAD_FORMAT,
            "engine_invoked": bool(captures),
            "fO2_constraint": {
                "path": "Absolute",
                "offset": float(job.inputs["fO2_log"]),
            },
            "captures": captures,
        }
        generic_output = _generic_result(result)
        alpha_output = _alpha_result(result, backend, _WORKER_ENGINE_VERSION)
        reason = _refusal_reason(result)
        output = {
            "status": result.status,
            "status_kind": _status_kind(result.status, reason),
            "refusal_reason": reason,
            "raw_payload": canonical_json(raw),
            "raw_payload_format": RAW_PAYLOAD_FORMAT,
            "timing_s": time.monotonic() - started,
            "engine_version": _WORKER_ENGINE_VERSION,
            "engine_mode": str(getattr(backend, "_mode", "subprocess")),
            "engine_model": str(getattr(backend, "_model", "unknown")),
            "run_mode": run_mode,
            "applied_timeout_s": applied_timeout_s,
            "native_input": native_input,
            "generic": generic_output,
            "alphamelts": alpha_output,
            "finder": {},
            "created_at": utc_now(),
            "host": socket.gethostname(),
        }
        return job.grid_key_id, output
    except Exception as exc:
        return job.grid_key_id, _worker_failure_output(
            exc,
            started=started,
            captures=captures,
            native_input=native_input,
            run_mode=run_mode,
            applied_timeout_s=applied_timeout_s,
        )


def write_status(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)


def _heartbeat(
    *,
    path: Path,
    state: str,
    started: float,
    grid_total: int,
    selected_total: int,
    existing: int,
    completed: int,
    inserted: int,
    kinds: Mapping[str, int],
    workers: int,
    seed: int,
    database: Path,
) -> None:
    elapsed = max(time.monotonic() - started, 1e-12)
    rate = completed / elapsed
    remaining = max(0, selected_total - existing - completed)
    payload = {
        "state": state,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "updated_at": utc_now(),
        "database": str(database),
        "grid_seed": seed,
        "grid_total_points": grid_total,
        "selected_points": selected_total,
        "resume_skipped": existing,
        "rows_done": existing + completed,
        "rows_inserted": inserted,
        "rows_success": int(kinds.get("success", 0)),
        "rows_refusal": int(kinds.get("refusal", 0)),
        "rows_failure": int(kinds.get("failure", 0)),
        "rate_rows_s": rate,
        "eta_s": remaining / rate if rate > 0.0 else None,
        "workers": workers,
    }
    write_status(path, payload)


def run_cycle(
    args: argparse.Namespace,
    writer: GridCacheWriter,
    *,
    batch_id: int,
    grid_total: int,
    shard: int | None,
    seed: int | None = None,
) -> dict[str, int]:
    started = time.monotonic()
    run_seed = args.seed if seed is None else int(seed)
    selected_grid_key_ids = getattr(args, "_selected_grid_key_ids", None)
    queue = writer.queue_counts(
        batch_id=batch_id,
        shard=shard,
        rank_limit=args.limit,
        grid_key_ids=selected_grid_key_ids,
    )
    existing = queue["done"]
    print(
        f"grid_total_points={grid_total} selected_points={queue['total']} "
        f"resume_skipped={existing} pending={queue['remaining']}",
        flush=True,
    )

    completed = 0
    inserted = 0
    kinds = {"success": 0, "refusal": 0, "failure": 0}
    _heartbeat(
        path=args.status_json,
        state="running" if queue["remaining"] else "complete",
        started=started,
        grid_total=grid_total,
        selected_total=queue["total"],
        existing=existing,
        completed=completed,
        inserted=inserted,
        kinds=kinds,
        workers=args.workers,
        seed=run_seed,
        database=args.db,
    )
    if not queue["remaining"]:
        return {
            "existing": existing,
            "completed": 0,
            "inserted": 0,
            **kinds,
        }

    next_heartbeat = time.monotonic() + args.heartbeat_s
    context = multiprocessing.get_context("spawn")

    def pending_jobs() -> Iterable[WorkerJob]:
        after_rank = -1
        while True:
            rows = writer.pending_rows(
                batch_id=batch_id,
                shard=shard,
                rank_limit=args.limit,
                after_shuffle_rank=after_rank,
                fetch_limit=1000,
                grid_key_ids=selected_grid_key_ids,
            )
            if not rows:
                return
            for row in rows:
                after_rank = int(row["shuffle_rank"])
                yield WorkerJob(
                    grid_key_id=int(row["grid_key_id"]),
                    shuffle_rank=after_rank,
                    inputs=dict(row["inputs"]),
                )

    iterator = iter(pending_jobs())
    active: list[tuple[Any, WorkerJob, float]] = []
    pool = context.Pool(
        processes=args.workers,
        initializer=_worker_initialize,
        initargs=(backend_config(args),),
    )
    try:
        def fill() -> None:
            while not _STOP_REQUESTED and len(active) < args.workers:
                try:
                    job = next(iterator)
                except StopIteration:
                    return
                active.append((
                    pool.apply_async(_run_point, (job,)),
                    job,
                    time.monotonic(),
                ))

        fill()
        while active:
            ready = [item for item in active if item[0].ready()]
            if not ready:
                time.sleep(0.1)
            for async_result, fallback_job, submitted_at in ready:
                active.remove((async_result, fallback_job, submitted_at))
                try:
                    grid_key_id, output = async_result.get()
                except Exception as exc:
                    grid_key_id = fallback_job.grid_key_id
                    output = _worker_failure_output(
                        exc,
                        started=submitted_at,
                        captures=[],
                        native_input=None,
                    )
                job = fallback_job
                if grid_key_id != job.grid_key_id:
                    raise RuntimeError(
                        "worker grid-key mismatch: "
                        f"expected {job.grid_key_id}, got {grid_key_id}"
                    )
                _record_job_runtime(job, output)
                if writer.write_result(job.grid_key_id, output):
                    inserted += 1
                completed += 1
                kind = str(output["status_kind"])
                kinds[kind] = kinds.get(kind, 0) + 1
                if completed % args.commit_every == 0:
                    writer.commit()
                fill()
            if time.monotonic() >= next_heartbeat:
                _heartbeat(
                    path=args.status_json,
                    state="stopping" if _STOP_REQUESTED else "running",
                    started=started,
                    grid_total=grid_total,
                    selected_total=queue["total"],
                    existing=existing,
                    completed=completed,
                    inserted=inserted,
                    kinds=kinds,
                    workers=args.workers,
                    seed=run_seed,
                    database=args.db,
                )
                next_heartbeat = time.monotonic() + args.heartbeat_s
    except BaseException:
        pool.terminate()
        raise
    else:
        pool.close()
    finally:
        pool.join()
    writer.commit()
    final_state = "stopped" if _STOP_REQUESTED else "complete"
    _heartbeat(
        path=args.status_json,
        state=final_state,
        started=started,
        grid_total=grid_total,
        selected_total=queue["total"],
        existing=existing,
        completed=completed,
        inserted=inserted,
        kinds=kinds,
        workers=args.workers,
        seed=run_seed,
        database=args.db,
    )
    return {
        "existing": existing,
        "completed": completed,
        "inserted": inserted,
        **kinds,
    }


def default_workers() -> int:
    return max(1, math.floor((os.cpu_count() or 1) * 0.8))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--db", type=Path, default=DEFAULT_DB)
    result.add_argument("--status-json", type=Path, default=DEFAULT_STATUS)
    result.add_argument("--feedstocks", type=Path, default=DEFAULT_FEEDSTOCKS)
    result.add_argument("--workers", type=int, default=default_workers())
    result.add_argument("--limit", type=int)
    result.add_argument("--seed", type=int, default=DEFAULT_SEED)
    result.add_argument(
        "--batch-label", default="fixed-backbone-v3-window-filtered"
    )
    result.add_argument("--shard", choices=("all", "0", "1", "2"), default="all")
    result.add_argument(
        "--engine-epoch",
        type=int,
        default=2,
        help=(
            "output epoch (default: 2, the explicit isothermal/fO2/property "
            "contract; epoch 1 retains legacy liquidus-mode rows)"
        ),
    )
    result.add_argument("--composition-step-pct", type=float, default=10.0)
    result.add_argument("--composition-margin-pct", type=float, default=5.0)
    result.add_argument("--temperature-min-C", type=float, default=800.0)
    result.add_argument("--temperature-max-C", type=float, default=1800.0)
    result.add_argument(
        "--fo2-grid",
        default=",".join(str(value) for value in DEFAULT_INTENDED_FO2_GRID),
        help=(
            "absolute log10(fO2/bar) levels imposed on alphaMELTS and used "
            "to pre-partition Fe2O3/FeO with Kress91"
        ),
    )
    result.add_argument("--model", default="MELTSv1.0.2")
    result.add_argument("--timeout-s", type=float, default=20.0)
    result.add_argument("--thermoengine-health-timeout-s", type=float, default=8.0)
    result.add_argument("--commit-every", type=int, default=25)
    result.add_argument("--heartbeat-s", type=float, default=60.0)
    result.add_argument("--loop", action="store_true")
    result.add_argument("--rescan-s", type=float, default=3600.0)
    result.add_argument("--probe-only", action="store_true")
    result.add_argument("--prepare-only", action="store_true")
    result.add_argument(
        "--drain-only",
        action="store_true",
        help="drain an existing prepared queue without generating or materializing keys",
    )
    result.add_argument(
        "--keys",
        help=(
            "comma-separated grid-key ids or expedited-key prefixes; use id:N or "
            "key:PREFIX to disambiguate numeric selectors"
        ),
    )
    result.add_argument(
        "--retry-failed",
        metavar="REASON",
        help="rerun existing keys with this refusal reason into --engine-epoch",
    )
    result.add_argument("--retry-source-epoch", type=int, default=1)
    result.add_argument("--retry-limit", type=int, default=12)
    result.add_argument("--estimate-s-per-point", type=float, default=2.0)
    return result


def _validate_args(args: argparse.Namespace) -> None:
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    if args.limit is not None and args.limit < 0:
        raise SystemExit("--limit must be >= 0")
    if args.commit_every < 1:
        raise SystemExit("--commit-every must be >= 1")
    if args.timeout_s <= 0.0:
        raise SystemExit("--timeout-s must be positive")
    if args.estimate_s_per_point <= 0.0:
        raise SystemExit("--estimate-s-per-point must be positive")
    if args.keys and args.retry_failed:
        raise SystemExit("--keys and --retry-failed are mutually exclusive")
    if (args.keys or args.retry_failed) and args.limit is not None:
        raise SystemExit("--limit cannot be combined with retry selectors")
    if args.retry_source_epoch < 1:
        raise SystemExit("--retry-source-epoch must be >= 1")
    if args.retry_limit < 1:
        raise SystemExit("--retry-limit must be >= 1")
    if args.retry_failed and args.engine_epoch == args.retry_source_epoch:
        raise SystemExit(
            "--engine-epoch must differ from --retry-source-epoch to preserve cache rows"
        )
    if args.drain_only and (args.prepare_only or args.probe_only):
        raise SystemExit(
            "--drain-only is mutually exclusive with --prepare-only and --probe-only"
        )
    if args.drain_only and (args.keys or args.retry_failed):
        raise SystemExit(
            "--drain-only cannot be combined with --keys or --retry-failed"
        )


def _axis_values(raw: str, *, positive: bool = False) -> list[float]:
    values = list(dict.fromkeys(float(item.strip()) for item in raw.split(",") if item.strip()))
    if not values:
        raise SystemExit("grid axis must contain at least one value")
    if positive and any(value <= 0.0 for value in values):
        raise SystemExit("pressure grid values must be positive")
    return values


def run_selected_retry(args: argparse.Namespace) -> int:
    if not args.db.exists():
        raise SystemExit(f"retry database does not exist: {args.db}")
    selectors = tuple(
        item.strip() for item in str(args.keys or "").split(",") if item.strip()
    )
    with GridCacheWriter(
        args.db, engine_epoch=args.engine_epoch, existing_only=True
    ) as writer:
        grid_key_ids = writer.select_grid_key_ids(
            selectors=selectors,
            refusal_reason=args.retry_failed,
            source_epoch=args.retry_source_epoch,
            limit=args.retry_limit if args.retry_failed else None,
        )
        if not grid_key_ids:
            raise SystemExit("retry selector matched no grid keys")
        args._selected_grid_key_ids = tuple(grid_key_ids)
        print(
            "retry_selection="
            + json.dumps(
                {
                    "grid_key_ids": grid_key_ids,
                    "source_epoch": args.retry_source_epoch,
                    "target_epoch": args.engine_epoch,
                    "refusal_reason": args.retry_failed,
                    "workers": args.workers,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        probe = probe_engine(backend_config(args))
        print(f"engine_probe={json.dumps(probe, sort_keys=True)}", flush=True)
        if not probe.get("available"):
            print(
                "BLOCKED: AlphaMELTS engine probe failed: "
                f"{json.dumps(probe, sort_keys=True)}",
                flush=True,
            )
            return 2
        summaries = []
        shard = None if args.shard == "all" else int(args.shard)
        for batch in writer.batches():
            queue = writer.queue_counts(
                batch_id=batch["batch_id"],
                shard=shard,
                grid_key_ids=grid_key_ids,
            )
            if not queue["total"]:
                continue
            summaries.append(
                run_cycle(
                    args,
                    writer,
                    batch_id=batch["batch_id"],
                    grid_total=queue["total"],
                    shard=shard,
                )
            )
        histogram = writer.selected_result_histogram(grid_key_ids)
        print(
            f"retry_summary={json.dumps(summaries, sort_keys=True)} "
            f"retry_histogram={json.dumps(histogram, sort_keys=True)}",
            flush=True,
        )
    return 0


def run_drain_only(args: argparse.Namespace) -> int:
    shard = None if args.shard == "all" else int(args.shard)
    try:
        writer = GridCacheWriter(
            args.db,
            engine_epoch=args.engine_epoch,
            existing_only=True,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(f"DRAIN-ONLY REFUSED: {exc}") from exc

    with writer:
        try:
            manifest = writer.drain_manifest(
                shard=shard,
                rank_limit=args.limit,
            )
        except ValueError as exc:
            raise SystemExit(f"DRAIN-ONLY REFUSED: {exc}") from exc

        batch_params_refs = [
            {
                "batch_id": batch["batch_id"],
                "label": batch["label"],
                "grid_seed": batch["seed"],
                "params_source": batch["params_source"],
                "params": batch["params"],
                "kress91_partition": batch["kress91_partition"],
            }
            for batch in manifest["batches"]
        ]
        print(
            "drain_manifest="
            + json.dumps(
                {
                    "shard": manifest["shard"],
                    "materialized_keys": manifest["materialized_keys"],
                    "batches": [
                        {
                            "batch_id": batch["batch_id"],
                            "label": batch["label"],
                            "grid_seed": batch["seed"],
                            "params_source": batch["params_source"],
                            "kress91_partition": batch["kress91_partition"],
                            "queue": batch["queue"],
                        }
                        for batch in manifest["batches"]
                    ],
                },
                sort_keys=True,
            ),
            flush=True,
        )

        probe = probe_engine(backend_config(args))
        print(f"engine_probe={json.dumps(probe, sort_keys=True)}", flush=True)
        drain_run = {
            "mode": "drain-only",
            "started_at": utc_now(),
            "host": socket.gethostname(),
            "workers": args.workers,
            "engine_epoch": args.engine_epoch,
            "engine_probe": probe,
            "database": str(args.db),
            "shard": manifest["shard"],
            "rank_limit": args.limit,
            "batch_params_refs": batch_params_refs,
        }
        writer.set_run_metadata(
            {
                "last_run_mode": "drain-only",
                "last_drain_run": drain_run,
            }
        )
        if not probe.get("available"):
            print(
                "BLOCKED: AlphaMELTS engine probe failed: "
                f"{json.dumps(probe, sort_keys=True)}",
                flush=True,
            )
            return 2

        while not _STOP_REQUESTED:
            summaries = []
            for batch in manifest["batches"]:
                batch_queue = writer.queue_counts(
                    batch_id=batch["batch_id"],
                    shard=shard,
                    rank_limit=args.limit,
                )
                summaries.append(
                    run_cycle(
                        args,
                        writer,
                        batch_id=batch["batch_id"],
                        grid_total=batch_queue["total"],
                        shard=shard,
                        seed=batch["seed"],
                    )
                )
            counts = writer.counts()
            print(
                f"cycle_summary={json.dumps(summaries, sort_keys=True)} "
                f"database_counts={json.dumps(counts, sort_keys=True)}",
                flush=True,
            )
            sample = writer.sample_row()
            print(f"sample_row={json.dumps(sample, sort_keys=True)}", flush=True)
            if not args.loop:
                break
            write_status(
                args.status_json,
                {
                    "state": "idle",
                    "mode": "drain-only",
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "updated_at": utc_now(),
                    "database": str(args.db),
                    "engine_epoch": args.engine_epoch,
                    "shard": manifest["shard"],
                    "database_counts": counts,
                    "batch_params_refs": batch_params_refs,
                    "rescan_s": args.rescan_s,
                },
            )
            deadline = time.monotonic() + args.rescan_s
            while not _STOP_REQUESTED and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    _validate_args(args)
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    if args.probe_only:
        probe = probe_engine(backend_config(args))
        print(f"engine_probe={json.dumps(probe, sort_keys=True)}", flush=True)
        return 0 if probe.get("available") else 2

    if args.keys or args.retry_failed:
        return run_selected_retry(args)

    if args.drain_only:
        return run_drain_only(args)

    if not args.prepare_only:
        try:
            existing_batches = GridCacheWriter.has_batch_definitions(args.db)
        except ValueError as exc:
            raise SystemExit(f"GRID PREPARE REFUSED: {exc}") from exc
        if existing_batches:
            raise SystemExit(
                "GRID PREPARE REFUSED: database already contains stored batch "
                "grid_seed/params; use --drain-only to drain the prepared queue"
            )

    bounds = load_feedstock_box(
        args.feedstocks,
        step_pct=args.composition_step_pct,
        margin_pct=args.composition_margin_pct,
    )
    major_compositions = generate_simplex_grid(
        bounds, step_pct=args.composition_step_pct
    )
    compositions, composition_spec = expand_composition_axes(
        args.feedstocks, major_compositions
    )
    temperatures = temperature_grid(
        args.temperature_min_C, args.temperature_max_C
    )
    intended_fO2_logs = _axis_values(args.fo2_grid)
    filter_stats: dict[str, int] = {}
    grid_total, points = build_grid_points(
        compositions,
        temperatures,
        intended_fO2_logs,
        seed=args.seed,
        limit=args.limit,
        filter_stats=filter_stats,
    )
    kress91_provenance_counts = {
        name: filter_stats[name]
        for name in (
            "eligible_kress91_computed_points",
            "eligible_kress91_floor_adjusted_points",
            "eligible_kress91_non_authoritative_points",
            "eligible_kress91_extrapolated_points",
            "eligible_kress91_iron_free_points",
        )
    }
    kress91_partition_by_temperature_C = {
        str(float(temperature_C)): kress91_partition_authority_record(
            temperature_C=float(temperature_C)
        )
        for temperature_C in temperatures
    }
    iron_bearing_compositions = sum(
        1
        for composition in compositions
        if (
            float(composition.get("FeO", 0.0))
            + float(composition.get("Fe2O3", 0.0))
        )
        > 0.0
    )
    iron_free_compositions = len(compositions) - iron_bearing_compositions
    cartesian_grid_points = (
        len(compositions) * len(temperatures) * len(intended_fO2_logs)
    )
    budget = {
        "major_simplex_points": len(major_compositions),
        "cr2o3_levels": len(composition_spec["cr2o3_levels_wt_pct"]),
        "composition_points": len(compositions),
        "iron_bearing_composition_points": iron_bearing_compositions,
        "iron_free_composition_points": iron_free_compositions,
        "temperature_points": len(temperatures),
        "intended_fo2_partition_points": len(intended_fO2_logs),
        "pressure_points": 1,
        "pressure_bar": ENGINE_PRESSURE_BAR,
        "cartesian_grid_points": cartesian_grid_points,
        "deduplicated_nonidentity_points": (
            cartesian_grid_points - filter_stats["unfiltered_grid_points"]
        ),
        "pre_filter_grid_points": filter_stats["unfiltered_grid_points"],
        "filtered_silicate_window_points": filter_stats[
            "filtered_silicate_window_points"
        ],
        **kress91_provenance_counts,
        "full_grid_points": grid_total,
        "shard_points_modulo_3": {
            str(shard): (grid_total + 2 - shard) // 3 for shard in range(3)
        },
        "materialized_this_invocation": len(points),
        "estimated_engine_hours": grid_total * args.estimate_s_per_point / 3600.0,
        "estimated_wall_hours_at_workers": (
            grid_total * args.estimate_s_per_point / args.workers / 3600.0
        ),
        "estimated_wall_hours_per_three_way_shard": (
            grid_total * args.estimate_s_per_point / 3.0 / args.workers / 3600.0
        ),
        "workers": args.workers,
        "estimate_s_per_point": args.estimate_s_per_point,
        "seed": args.seed,
    }
    print(f"budget_table={json.dumps(budget, sort_keys=True)}", flush=True)

    with GridCacheWriter(args.db, engine_epoch=args.engine_epoch) as writer:
        shard = None if args.shard == "all" else int(args.shard)
        if shard is not None:
            writer.seed_id_block(shard)
        batch_params = {
            "feedstock_anchors": DEFAULT_FEEDSTOCK_ANCHORS,
            "composition_step_pct": args.composition_step_pct,
            "composition_margin_pct": args.composition_margin_pct,
            "composition_spec": composition_spec,
            "temperature_grid_C": temperatures,
            "intended_fO2_log_grid": intended_fO2_logs,
            "kress91_partition": kress91_partition_parameters(),
            "kress91_partition_by_temperature_C": (
                kress91_partition_by_temperature_C
            ),
            "pressure_bar_grid": [ENGINE_PRESSURE_BAR],
            "engine_fO2_constraint": (
                "absolute log10(fO2/bar) from each grid point"
            ),
            "pre_filter_grid_points": filter_stats["unfiltered_grid_points"],
            "filtered_silicate_window_points": filter_stats[
                "filtered_silicate_window_points"
            ],
            **kress91_provenance_counts,
            "full_grid_points": grid_total,
            "cartesian_grid_points": cartesian_grid_points,
            "deduplicated_nonidentity_points": (
                cartesian_grid_points - filter_stats["unfiltered_grid_points"]
            ),
            "shard_count": 3,
        }
        batch_id = writer.ensure_batch(
            label=args.batch_label,
            kind="fixed",
            seed=args.seed,
            params=batch_params,
        )
        print(
            "batch_window_filter="
            + json.dumps(
                {
                    "batch_id": batch_id,
                    "batch_label": args.batch_label,
                    **filter_stats,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        materialized = 0
        for index, point in enumerate(points, 1):
            point_shard = point.ordinal % 3
            if shard is not None and point_shard != shard:
                continue
            if writer.materialize_key(
                point_inputs(point, args),
                batch_id=batch_id,
                shuffle_rank=point.ordinal,
                shard=point_shard,
                intended_fO2_log=point.intended_fO2_log,
            ):
                materialized += 1
            if index % 1000 == 0:
                writer.commit()
        writer.commit()
        queue = writer.queue_counts(
            batch_id=batch_id,
            shard=shard,
            rank_limit=args.limit,
        )
        print(
            f"queue_materialized={materialized} "
            f"queue_counts={json.dumps(queue, sort_keys=True)} shard={args.shard}",
            flush=True,
        )
        writer.set_run_metadata(
            {
                "schema_variant": "alphamelts-expedited-v1",
                "grid_seed": args.seed,
                "feedstock_anchors": DEFAULT_FEEDSTOCK_ANCHORS,
                "composition_step_pct": args.composition_step_pct,
                "composition_margin_pct": args.composition_margin_pct,
                "temperature_grid_C": temperatures,
                "intended_fO2_log_grid": intended_fO2_logs,
                "kress91_partition": kress91_partition_parameters(),
                "kress91_partition_by_temperature_C": (
                    kress91_partition_by_temperature_C
                ),
                "pressure_bar_grid": [ENGINE_PRESSURE_BAR],
                "engine_fO2_constraint": (
                    "absolute log10(fO2/bar) from each grid point"
                ),
                "pre_filter_grid_points": filter_stats["unfiltered_grid_points"],
                "filtered_silicate_window_points": filter_stats[
                    "filtered_silicate_window_points"
                ],
                **kress91_provenance_counts,
                "grid_total_points": grid_total,
                "cartesian_grid_points": cartesian_grid_points,
                "deduplicated_nonidentity_points": (
                    cartesian_grid_points - filter_stats["unfiltered_grid_points"]
                ),
                "engine_epoch": args.engine_epoch,
                "shard": args.shard,
            }
        )
        if args.prepare_only:
            return 0

        probe = probe_engine(backend_config(args))
        print(f"engine_probe={json.dumps(probe, sort_keys=True)}", flush=True)
        if not probe.get("available"):
            print(
                f"BLOCKED: AlphaMELTS engine probe failed: "
                f"{json.dumps(probe, sort_keys=True)}",
                flush=True,
            )
            return 2
        writer.set_run_metadata(
            {"engine_version_at_start": probe.get("engine_version")}
        )
        while not _STOP_REQUESTED:
            summaries = []
            for batch in writer.batches():
                batch_queue = writer.queue_counts(
                    batch_id=batch["batch_id"],
                    shard=shard,
                    rank_limit=args.limit,
                )
                summaries.append(
                    run_cycle(
                        args,
                        writer,
                        batch_id=batch["batch_id"],
                        grid_total=batch_queue["total"],
                        shard=shard,
                    )
                )
            counts = writer.counts()
            print(
                f"cycle_summary={json.dumps(summaries, sort_keys=True)} "
                f"database_counts={json.dumps(counts, sort_keys=True)}",
                flush=True,
            )
            sample = writer.sample_row()
            print(f"sample_row={json.dumps(sample, sort_keys=True)}", flush=True)
            if not args.loop:
                break
            write_status(
                args.status_json,
                {
                    "state": "idle",
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "updated_at": utc_now(),
                    "database": str(args.db),
                    "grid_seed": args.seed,
                    "grid_total_points": grid_total,
                    "database_counts": counts,
                    "batches": writer.batches(),
                    "rescan_s": args.rescan_s,
                },
            )
            deadline = time.monotonic() + args.rescan_s
            while not _STOP_REQUESTED and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
