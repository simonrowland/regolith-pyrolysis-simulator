"""SQLite storage for the expedited AlphaMELTS grid grinder."""

from __future__ import annotations

import dataclasses
import datetime as dt
import functools
import hashlib
import json
import math
import os
import socket
import sqlite3
import struct
import time
import urllib.parse
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from simulator.accounting.formulas import load_species_formulas
from simulator.fidelity_vocabulary import EvidenceClass
from simulator.melt_regime import MeltRegime


SCHEMA_VARIANT = "alphamelts-expedited-v1"
GRID_REALIZATION_REVISION = "v2-kress-composition-space"
CACHE_V2_SCHEMA_VERSION = "cache-v2-grind-source-v1"
CACHE_V2_FAILURE_MESSAGE_MAX_LENGTH = 512

CACHE_V2_SUBPROCESS_PHASE_DICTIONARY = (
    "liquid",
    "olivine",
    "orthopyroxene",
    "clinopyroxene",
    "spinel",
    "plagioclase",
    "feldspar",
    # Native alphaMELTS 2 Phase_main_tbl labels observed in the epoch-2
    # subprocess corpus; keep the emitted spelling rather than aliasing it.
    "ortho-oxide",
    "alkali-feldspar",
    "quartz",
    "tridymite",
    "cristobalite",
    "rhm-oxide",
    "ilmenite",
    "magnetite",
    "hematite",
    "garnet",
    "melilite",
    "nepheline",
    "leucite",
    "kalsilite",
    "perovskite",
    "whitlockite",
    "apatite",
    "corundum",
    "metal",
    "alloy-solid",
    "alloy-liquid",
    "sulfide-liquid",
    "fluid",
)

# Grounded ThermoEngine MELTSv1.0.2 phase vocabulary.  The native labels come
# from ``MELTSmodel.get_phase_names()`` (the same source counted by
# ``ThermoEngineTransport._equilibrate_in_process`` in
# engines/alphamelts/thermoengine.py).  The canonical labels are the exact
# output of the t-331 ``_thermoengine_generic_result.canonical_phase`` contract
# in scripts/grid_pregrind.py; simulator/melt_backend/thermoengine.py passes the
# native assemblage labels through to that boundary.  Keeping both values here
# makes the provenance of every dictionary entry reviewable instead of
# presenting an unexplained hand-written union.
CACHE_V2_THERMOENGINE_PHASE_LABELS = (
    ("actinolite", "Actinolite"),
    ("aegirine", "Aegirine"),
    ("aenigmatite", "Aenigmatite"),
    ("akermanite", "Akermanite"),
    ("andalusite", "Andalusite"),
    ("anthophyllite", "Anthophyllite"),
    ("apatite", "Apatite"),
    ("augite", "Augite"),
    ("biotite", "Biotite"),
    ("chromite", "Chromite"),
    ("coesite", "Coesite"),
    ("corundum", "Corundum"),
    ("cristobalite", "Cristobalite"),
    ("cummingtonite", "Cummingtonite"),
    ("fayalite", "Fayalite"),
    ("forsterite", "Forsterite"),
    ("garnet", "Garnet"),
    ("gehlenite", "Gehlenite"),
    ("hematite", "Hematite"),
    ("hornblende", "Hornblende"),
    ("ilmenite", "Ilmenite"),
    ("ilmenite ss", "Ilmenite ss"),
    ("kalsilite", "Kalsilite"),
    ("kalsilite ss", "Kalsilite ss"),
    ("kyanite", "Kyanite"),
    ("leucite", "Leucite"),
    ("lime", "Lime"),
    ("liquid", "Liquid"),
    ("liquid alloy", "Liquid Alloy"),
    ("magnetite", "Magnetite"),
    ("melilite", "Melilite"),
    ("muscovite", "Muscovite"),
    ("nepheline", "Nepheline"),
    ("nepheline ss", "Nepheline ss"),
    ("olivine", "Olivine"),
    ("orthooxide", "OrthoOxide"),
    ("orthopyroxene", "Orthopyroxene"),
    ("panunzite", "Panunzite"),
    ("periclase", "Periclase"),
    ("perovskite", "Perovskite"),
    ("phlogopite", "Phlogopite"),
    ("pigeonite", "Pigeonite"),
    ("plagioclase", "Plagioclase"),
    ("quartz", "Quartz"),
    ("rutile", "Rutile"),
    ("sanidine", "Sanidine"),
    ("sillimanite", "Sillimanite"),
    ("solid alloy", "Solid Alloy"),
    ("sphene", "Sphene"),
    ("spinel", "Spinel"),
    ("titanaugite", "Titanaugite"),
    ("tridymite", "Tridymite"),
    ("water", "Water"),
    ("whitlockite", "Whitlockite"),
)

CACHE_V2_PHASE_DICTIONARY = tuple(
    dict.fromkeys(
        (
            *CACHE_V2_SUBPROCESS_PHASE_DICTIONARY,
            *(canonical for canonical, _native in CACHE_V2_THERMOENGINE_PHASE_LABELS),
        )
    )
)

# ThermoEngine MELTSv1.0.2 liquid solution endmembers. These are activity
# labels, not bulk oxide components; keeping a separate dictionary prevents a
# distiller from silently interpreting Fe2SiO4 activity as FeO activity.
CACHE_V2_THERMOENGINE_LIQUID_ENDMEMBERS = (
    "SiO2",
    "TiO2",
    "Al2O3",
    "Fe2O3",
    "MgCr2O4",
    "Fe2SiO4",
    "MnSi0.5O2",
    "Mg2SiO4",
    "NiSi0.5O2",
    "CoSi0.5O2",
    "CaSiO3",
    "Na2SiO3",
    "KAlSiO4",
    "Ca3(PO4)2",
    "H2O",
)

class CacheV2GridBackend(str, Enum):
    SUBPROCESS = "subprocess"
    THERMOENGINE = "thermoengine"


class CacheV2ConfidenceTier(str, Enum):
    GROUNDED = "grounded"
    MODELED = "modeled"
    INDICATIVE = "indicative"


class CacheV2Notice(str, Enum):
    NONE = "none"


# Distillation dictionaries are enumerated from their defining enums. Regime,
# evidence class, tier, and notice have no grind-source row carriers, so their
# definition validation remains a manifest/config check. Backend is also used
# by the point-level engine_mode containment and writer no-blend gate.
CACHE_V2_FLAG_DICTIONARIES = {
    "regime": tuple(item.value for item in MeltRegime),
    "evidence_class": tuple(item.value for item in EvidenceClass),
    "backend": tuple(item.value for item in CacheV2GridBackend),
    "tier": tuple(item.value for item in CacheV2ConfidenceTier),
    "notice": tuple(item.value for item in CacheV2Notice),
}

CACHE_V2_CLAMP_EXTRAPOLATION_BITS = {
    "0": "temperature_clamped",
    "1": "pressure_clamped",
    "2": "fo2_clamped",
    "3": "composition_clamped",
    "4": "temperature_extrapolated",
    "5": "pressure_extrapolated",
    "6": "fo2_extrapolated",
    "7": "composition_extrapolated",
}

COMPONENT_FIELDS = (
    "SiO2",
    "TiO2",
    "Al2O3",
    "Fe2O3",
    "Cr2O3",
    "FeO",
    "MnO",
    "MgO",
    "NiO",
    "CoO",
    "CaO",
    "Na2O",
    "K2O",
    "P2O5",
)

# Formula-bearing volatile products that can be emitted independently of a
# feedstock/catalog key. Keep this registry explicit: H2S is a legitimate
# future volatile even though it is not yet a species_catalog feedstock id.
CACHE_V2_VOLATILE_SPECIES = (
    "H2O",
    "CO2",
    "CO",
    "CH4",
    "NH3",
    "HCN",
    "SO2",
    "H2S",
)

CACHE_V2_QUANTIZED_INPUTS = (
    *(
        {
            "field": f"component_{component}_mol",
            "units": "mol",
            "representation": "ieee754-binary64",
            "rounding": "none; raw native value",
        }
        for component in COMPONENT_FIELDS
    ),
    {
        "field": "temperature_C",
        "units": "degC",
        "representation": "ieee754-binary64",
        "rounding": "none; raw native value",
    },
    {
        "field": "pressure_bar",
        "units": "bar",
        "representation": "ieee754-binary64",
        "rounding": "none; raw native value",
    },
    {
        "field": "fO2_log",
        "units": "log10(fO2/bar)",
        "representation": "ieee754-binary64",
        "rounding": "none; raw native value",
    },
    {
        "field": "fO2_offset",
        "units": "log10 buffer offset",
        "representation": "nullable ieee754-binary64",
        "rounding": "none; raw native value",
    },
    {
        "field": "Fe3Fet_Liq",
        "units": "mol fraction",
        "representation": "nullable ieee754-binary64",
        "rounding": "none; raw native value",
    },
    {
        "field": "model",
        "units": "enum",
        "representation": "UTF-8 exact string",
        "rounding": "not applicable",
    },
    {
        "field": "subprocess_run_mode",
        "units": "enum",
        "representation": "UTF-8 exact string",
        "rounding": "not applicable",
    },
    {
        "field": "redox_buffer",
        "units": "enum",
        "representation": "nullable UTF-8 exact string",
        "rounding": "not applicable",
    },
    *(
        {
            "field": field,
            "units": units,
            "representation": representation,
            "rounding": "none; raw native value",
        }
        for field, units, representation in (
            ("finder_min_T_C", "degC", "nullable ieee754-binary64"),
            ("finder_max_T_C", "degC", "nullable ieee754-binary64"),
            ("finder_scan_step_C", "degC", "nullable ieee754-binary64"),
            ("finder_tolerance_C", "degC", "nullable ieee754-binary64"),
            (
                "finder_solid_epsilon",
                "mass fraction",
                "nullable ieee754-binary64",
            ),
            (
                "finder_liquid_epsilon",
                "mass fraction",
                "nullable ieee754-binary64",
            ),
            (
                "finder_monotonicity_tolerance",
                "mass fraction",
                "nullable ieee754-binary64",
            ),
            (
                "finder_monotone_smoothing_max",
                "mass fraction",
                "nullable ieee754-binary64",
            ),
            (
                "finder_max_bisection_iterations",
                "count",
                "nullable unsigned integer",
            ),
        )
    ),
)

COMMON_INPUT_FIELDS = (
    "temperature_C",
    "composition_kg",
    "fO2_log",
    "pressure_bar",
    "composition_mol",
    "composition_mol_by_account",
    "species_formula_registry",
)

ALPHAMELTS_CONFIG_FIELDS = (
    "mode",
    "subprocess_run_mode",
    "redox_buffer",
    "fO2_offset",
    "Fe3Fet_Liq",
    "model",
    "timeout_s",
    "require_petthermotools",
    "thermoengine_health_timeout_s",
)

FINDER_INPUT_FIELDS = (
    "finder_min_T_C",
    "finder_max_T_C",
    "finder_scan_step_C",
    "finder_tolerance_C",
    "finder_solid_epsilon",
    "finder_liquid_epsilon",
    "finder_monotonicity_tolerance",
    "finder_monotone_smoothing_max",
    "finder_max_bisection_iterations",
)

INPUT_FIELDS = COMMON_INPUT_FIELDS + ALPHAMELTS_CONFIG_FIELDS + FINDER_INPUT_FIELDS
POINT_PROVENANCE_FIELDS = (
    "kress91_partition_provenance",
    "kress91_fixed_ferric_fraction",
)
# t-299 added kress91_fixed_ferric_fraction; legacy input vectors (and legacy
# stored batches replayed by the harvest tooling) predate it, so its presence is
# OPTIONAL. It is a provenance field, not part of the hashed INPUT_FIELDS
# identity (canonical_input_vector hashes INPUT_FIELDS only), so absence cannot
# alter any cache key; row build treats an absent field as None.
OPTIONAL_PROVENANCE_FIELDS = frozenset({"kress91_fixed_ferric_fraction"})

GENERIC_OUTPUT_FIELDS = (
    "temperature_C",
    "requested_temperature_C",
    "pressure_bar",
    "phases_present",
    "phase_masses_kg",
    "phase_species_mol",
    "phase_species_kg",
    "phase_instances",
    "phase_compositions",
    "liquid_fraction",
    "phase_assemblage_available",
    "liquid_composition_wt_pct",
    "liquid_viscosity_Pa_s",
    "liquid_density_kg_m3",
    "system_enthalpy",
    "system_entropy",
    "system_volume",
    "system_heat_capacity_Cp",
    "system_dVdP",
    "system_dVdT",
    "system_fO2_delta_QFM",
    "system_solid_density_rhos",
    "system_phi",
    "system_chisqr",
    "phase_thermo",
    "chem_potentials",
    "phase_affinities",
    "solid_composition_wt_pct",
    "bulk_composition_wt_pct",
    "vapor_pressures_Pa",
    "vapor_pressures_source",
    "activity_coefficients",
    "fO2_log",
    "warnings",
    "ledger_transition",
    "status",
    "sulfur_saturation",
    "liquidus_T_C",
    "diagnostics",
)

THERMOENGINE_OUTPUT_FIELDS = (
    "liquid_activities",
    "system_dVdP_m3_bar",
    "system_dVdT_m3_K",
    "solver_status",
    "solver_converged",
    "solver_iterations",
    "solver_iterations_available",
    "fO2_solve_count",
    "phase_universe_size",
)

ALPHAMELTS_DIAGNOSTIC_OUTPUT_FIELDS = (
    "activity_coefficients",
    "applied_fe3fet",
    "backend_diagnostics",
    "backend_status",
    "backend_status_reason",
    "backend_warnings",
    "engine_version",
    "fO2_log",
    "fe_redox_policy",
    "intrinsic_fO2_log",
    "liquid_composition_wt_pct",
    "liquid_fraction",
    "liquid_fraction_path",
    "liquidus_T_C",
    "liquidus_T_K",
    "mode",
    "phase_masses_kg",
    "phase_modes_wt_pct",
    "phases_present",
    "solidus_T_C",
)

FINDER_OUTPUT_FIELDS = (
    "liquidus_T_C",
    "liquidus_T_K",
    "solidus_T_C",
    "liquid_fraction",
    "status",
    "warnings",
    "diagnostics",
    "iterations",
    "samples",
    "sample_temperature_C",
    "sample_frac_M",
    "curve_source",
    "curve_solidus_T_C",
    "curve_liquidus_T_C",
    "curve_path_temperature_C",
    "curve_path_liquid_fraction",
)

assert len(INPUT_FIELDS) == 25
assert (
    len(GENERIC_OUTPUT_FIELDS)
    + len(THERMOENGINE_OUTPUT_FIELDS)
    + len(ALPHAMELTS_DIAGNOSTIC_OUTPUT_FIELDS)
    + len(FINDER_OUTPUT_FIELDS)
) == 84


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite float cannot be cached: {value!r}")
        return 0.0 if value == 0.0 else value
    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value):
        return {
            field.name: to_jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key): to_jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    raise TypeError(f"unsupported cache value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        to_jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _load_corpus_version() -> str:
    path = Path(__file__).resolve().parents[1] / "data" / "corpus_version.yaml"
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load corpus version from {path}: {exc}") from exc
    value = payload.get("corpus_version") if isinstance(payload, Mapping) else None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"corpus version missing from {path}")
    return value.strip()


@functools.cache
def _cache_v2_species_dictionary() -> tuple[str, ...]:
    """Enumerate every project-authoritative formula/vapor species label."""
    root = Path(__file__).resolve().parents[1]
    catalog_path = root / "data" / "species_catalog.yaml"
    vapor_path = root / "data" / "vapor_pressures.yaml"
    try:
        formula_species = tuple(load_species_formulas(catalog_path))
        vapor_payload = yaml.safe_load(vapor_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise ValueError(f"cannot enumerate cache_v2 species registries: {exc}") from exc
    if not isinstance(vapor_payload, Mapping):
        raise ValueError(f"cache_v2 vapor species registry must be a mapping: {vapor_path}")
    vapor_species: list[str] = []
    for section in ("metals", "oxide_vapors", "foulant_vapor"):
        entries = vapor_payload.get(section)
        if not isinstance(entries, Mapping):
            raise ValueError(
                f"cache_v2 vapor species registry section {section!r} "
                f"must be a mapping: {vapor_path}"
            )
        vapor_species.extend(str(value) for value in entries)
    return tuple(
        dict.fromkeys(
            (
                *COMPONENT_FIELDS,
                *formula_species,
                *vapor_species,
                *CACHE_V2_VOLATILE_SPECIES,
            )
        )
    )


def _cache_v2_dictionary(
    values: Sequence[str], *, unknown_policy: str = "refuse"
) -> dict[str, Any]:
    ordered = [str(value) for value in values]
    payload = canonical_json(ordered)
    return {
        "values": ordered,
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "unknown_value_policy": unknown_policy,
    }


def _cache_v2_output_spec(
    namespace: str, field: str
) -> dict[str, Any]:
    name = f"{namespace}.{field}"
    units = "dimensionless"
    basis = "AlphaMELTS solved state"
    encoding = "canonical-json"
    if field.endswith("_T_C") or field.endswith("temperature_C"):
        units = "degC"
        encoding = "ieee754-binary64"
    elif field.endswith("_T_K"):
        units = "K"
        encoding = "ieee754-binary64"
    elif "pressure_bar" in field:
        units = "bar"
        encoding = "ieee754-binary64"
    elif field.endswith("_kg") or field == "phase_masses_kg":
        units = "kg"
        basis = "physical batch mass"
    elif field == "phase_species_mol":
        units = "mol"
        basis = "physical phase-instance mass and parsed formula/endmember"
    elif field == "phase_species_kg":
        units = "kg"
        basis = "physical phase-instance mass"
    elif (
        "composition_wt_pct" in field
        or "phase_modes_wt_pct" in field
        or field == "phase_compositions"
    ):
        units = "wt_pct"
        basis = "named phase or solved bulk composition"
    elif "fraction" in field or field in {"system_phi", "sample_frac_M"}:
        units = "fraction"
        encoding = "ieee754-binary64 or canonical-json array"
    elif "viscosity_Pa_s" in field:
        units = "Pa*s"
        encoding = "ieee754-binary64"
    elif "density" in field or field == "system_solid_density_rhos":
        units = "kg/m3"
        encoding = "ieee754-binary64"
    elif field == "system_enthalpy":
        units = "J"
        encoding = "ieee754-binary64"
        basis = "AlphaMELTS solver system amount"
    elif field == "system_entropy":
        units = "J/K"
        encoding = "ieee754-binary64"
        basis = "AlphaMELTS solver system amount"
    elif field == "system_volume":
        units = "m3"
        encoding = "ieee754-binary64"
        basis = "AlphaMELTS solver system amount; converted from table cm3"
    elif field == "system_heat_capacity_Cp":
        units = "J/K"
        encoding = "ieee754-binary64"
        basis = "AlphaMELTS solver system amount"
    elif field == "system_dVdP":
        units = "AlphaMELTS System_main dVdP*10^6 as printed"
        encoding = "ieee754-binary64"
        basis = "unscaled table value for AlphaMELTS solver system amount"
    elif field == "system_dVdT":
        units = "AlphaMELTS System_main dVdT*10^6 as printed"
        encoding = "ieee754-binary64"
        basis = "unscaled table value for AlphaMELTS solver system amount"
    elif field == "system_fO2_delta_QFM" or field == "fO2_log":
        units = "log10"
        encoding = "ieee754-binary64"
    elif field == "vapor_pressures_Pa":
        units = "Pa"
        basis = "post-equilibrium project-authoritative vapor projection"
    elif field == "vapor_pressures_source":
        units = "source_label"
        basis = "one source label per vapor_pressures_Pa species key"
    elif field == "activity_coefficients":
        units = "dimensionless"
        basis = "AlphaMELTS solved liquid activity basis"
    elif field == "phase_instances":
        units = "mixed_nested"
        basis = "one row per AlphaMELTS Phase_main phase instance"
    elif field == "phase_thermo":
        units = "mixed_nested"
        basis = "AlphaMELTS solver phase amount before physical-mass rescale"
    elif field == "chem_potentials":
        units = "J/mol"
        basis = "ThermoEngine chemical_potential_J_mol component basis"
    elif field == "phase_affinities":
        units = "J"
        basis = "ThermoEngine affinity_J phase basis"
    elif field == "liquid_activities":
        units = "dimensionless"
        basis = "ThermoEngine solved liquid endmember activity"
    elif field == "system_dVdP_m3_bar":
        units = "m3/bar"
        encoding = "ieee754-binary64"
        basis = "ThermoEngine solver system amount"
    elif field == "system_dVdT_m3_K":
        units = "m3/K"
        encoding = "ieee754-binary64"
        basis = "ThermoEngine solver system amount"
    elif field in {"solver_converged", "solver_iterations_available"}:
        units = "boolean"
        encoding = "sqlite-integer-0-or-1"
        basis = "ThermoEngine public solver result"
    elif field in {"solver_iterations", "fO2_solve_count", "phase_universe_size"}:
        units = "count"
        encoding = "nullable-integer"
        basis = "ThermoEngine public solver result"
    elif field == "solver_status":
        units = "status_text"
        basis = "ThermoEngine public solver result"
    elif field in {
        "samples",
        "diagnostics",
        "backend_diagnostics",
        "liquid_fraction_path",
        "sulfur_saturation",
        "ledger_transition",
    }:
        units = "mixed_nested_or_not_applicable"
    elif field in {"liquidus_T_C", "solidus_T_C"}:
        units = "degC"
        encoding = "ieee754-binary64"
    elif field in {"liquidus_T_K", "solidus_T_K"}:
        units = "K"
        encoding = "ieee754-binary64"
    elif field in {"iterations", "status", "warnings", "diagnostics"}:
        units = "not_applicable"
    spec: dict[str, Any] = {
        "field": name,
        "units": units,
        "reference_basis": basis,
        "encoding": encoding,
    }
    if field == "phase_instances":
        spec["nested_units"] = {
            "solver_basis_mass_kg": "kg",
            "physical_mass_kg": "kg",
            "reference_mass_kg": "kg",
            "enthalpy_J": "J",
            "entropy_J_K": "J/K",
            "volume_m3": "m3",
            "heat_capacity_J_K": "J/K",
            "density_kg_m3": "kg/m3",
            "composition_wt_pct": "wt_pct",
            "reference_basis": "alphamelts_solver_phase_amount",
        }
    elif field in {"phase_species_mol", "phase_species_kg"}:
        spec["nested_key_encoding"] = {
            "outer": "exact phase instance_id",
            "inner_dictionary_values": "species dictionary index",
            "inner_formula_tokens": (
                "exact UTF-8 only when identical to the corresponding "
                "phase_instances.formula_or_endmember_token"
            ),
            "unknown": "refuse",
        }
    elif field == "phase_thermo":
        spec["nested_units"] = {
            "enthalpy_J": "J",
            "entropy_J_K": "J/K",
            "volume_m3": "m3",
            "heat_capacity_J_K": "J/K",
            "density_kg_m3": "kg/m3",
            "reference_mass_kg": "kg",
            "reference_basis": "alphamelts_solver_phase_amount",
        }
    elif field == "chem_potentials":
        spec["nested_units"] = {
            "components": "J/mol",
            "units": "J/mol",
            "source_basis": "chemical_potential_J_mol",
        }
    elif field == "phase_affinities":
        spec["nested_units"] = {
            "affinity_J": "J",
            "state": "enum",
            "phase_scope": "enum",
        }
    elif field == "liquid_activities":
        spec["nested_key_encoding"] = {
            "dictionary": "thermoengine_liquid_endmember",
            "unknown": "refuse",
        }
    return spec


def _cache_v2_output_contract() -> list[dict[str, Any]]:
    contract = [
        *(_cache_v2_output_spec("generic", field) for field in GENERIC_OUTPUT_FIELDS),
        *(
            _cache_v2_output_spec("thermoengine", field)
            for field in THERMOENGINE_OUTPUT_FIELDS
        ),
        *(
            _cache_v2_output_spec("alphamelts", field)
            for field in ALPHAMELTS_DIAGNOSTIC_OUTPUT_FIELDS
        ),
        *(_cache_v2_output_spec("finder", field) for field in FINDER_OUTPUT_FIELDS),
    ]
    assert len(contract) == 84
    return contract


def cache_v2_identity_manifest() -> dict[str, Any]:
    species = _cache_v2_species_dictionary()
    dictionaries = {
        "phase": _cache_v2_dictionary(CACHE_V2_PHASE_DICTIONARY),
        "species": _cache_v2_dictionary(species),
        "thermoengine_liquid_endmember": _cache_v2_dictionary(
            CACHE_V2_THERMOENGINE_LIQUID_ENDMEMBERS
        ),
        **{
            name: _cache_v2_dictionary(values)
            for name, values in CACHE_V2_FLAG_DICTIONARIES.items()
        },
    }
    return {
        "schema_version": CACHE_V2_SCHEMA_VERSION,
        "corpus_version": _load_corpus_version(),
        "identity": {
            "fields": [
                "engine_name",
                "engine_version",
                "quantized_inputs",
            ],
            "cache_lever": "corpus_version",
            "optimizer_identity_included": False,
        },
        "quantized_inputs": list(CACHE_V2_QUANTIZED_INPUTS),
        "numeric_policy": {
            "authoritative_storage": "raw native values",
            "canonical_negative_zero": "+0.0",
            "non_finite": "refuse",
            "rounding_rule": "no decimal quantization or rounding",
        },
        "key_hash": {
            "algorithm": "sha256",
            "canonical_bytes": "cache-v2-canonical-f64-key-v1",
            "field_order": [
                item["field"] for item in CACHE_V2_QUANTIZED_INPUTS
            ],
            "float_encoding": "IEEE-754 binary64 big-endian; -0 normalized to +0",
            "nullable_encoding": "one-byte presence tag then encoded value",
            "string_encoding": "one-byte presence tag, uint32-be byte length, UTF-8 bytes",
            "identity_note": (
                "hash component for quantized_inputs only; never a sole cache identity"
            ),
            "join_identity": ["engine_name", "engine_version", "key_hash"],
        },
        "dictionaries": dictionaries,
        "dictionary_sources": {
            "phase": (
                "alphaMELTS 2.3.1 MELTSv1.0.2 Phase_main_tbl.txt and "
                "assemblage labels from captured subprocess output and the "
                "bundled executable vocabulary, union ThermoEngine "
                "MELTSv1.0.2 MELTSmodel.get_phase_names(), canonicalized by "
                "scripts.grid_pregrind._thermoengine_generic_result"
            ),
            "species": (
                "simulator.accounting.formulas.load_species_formulas over "
                "data/species_catalog.yaml union data/vapor_pressures.yaml "
                "metals/oxide_vapors/foulant_vapor keys union the in-code "
                "CACHE_V2_VOLATILE_SPECIES product registry"
            ),
            "thermoengine_liquid_endmember": (
                "ThermoEngine MELTSv1.0.2 Liq endmember_names"
            ),
            "regime": "simulator.melt_regime.MeltRegime",
            "evidence_class": "simulator.fidelity_vocabulary.EvidenceClass",
            "backend": "scripts.grid_pregrind_writer.CacheV2GridBackend",
            "tier": "scripts.grid_pregrind_writer.CacheV2ConfidenceTier",
            "notice": "scripts.grid_pregrind_writer.CacheV2Notice",
        },
        "dictionary_policy": {
            "unknown_phase": "typed per-point cache_v2_unknown_phase failure",
            "unknown_species": "typed per-point cache_v2_unknown_species failure",
            "unknown_thermoengine_liquid_endmember": (
                "typed per-point cache_v2_unknown_thermoengine_liquid_endmember failure"
            ),
            "overflow": "no silent overflow; schema-version break required",
            "phase_formula_tokens": (
                "exact UTF-8 tokens are carried by phase_instances and may key "
                "phase_species only when the same token is declared on that instance; "
                "they are not species-dictionary indices"
            ),
        },
        "flags": {
            "dictionaries": ["regime", "evidence_class", "backend", "tier", "notice"],
            "clamp_extrapolation_bits": CACHE_V2_CLAMP_EXTRAPOLATION_BITS,
            "unknown_bit_policy": "refuse",
        },
        "outputs": _cache_v2_output_contract(),
    }


def _cache_v2_quantized_values(inputs: Mapping[str, Any]) -> dict[str, Any]:
    composition_mol = inputs.get("composition_mol") or {}
    values = {
        f"component_{component}_mol": composition_mol.get(component)
        for component in COMPONENT_FIELDS
    }
    values.update(
        {
            "temperature_C": inputs.get("temperature_C"),
            "pressure_bar": inputs.get("pressure_bar"),
            "fO2_log": inputs.get("fO2_log"),
            "fO2_offset": inputs.get("fO2_offset"),
            "Fe3Fet_Liq": inputs.get("Fe3Fet_Liq"),
            "model": inputs.get("model"),
            "subprocess_run_mode": inputs.get("subprocess_run_mode"),
            "redox_buffer": inputs.get("redox_buffer"),
            **{
                field: inputs.get(field)
                for field in FINDER_INPUT_FIELDS
            },
        }
    )
    return values


def _canonical_f64_key_bytes(values: Mapping[str, Any]) -> bytes:
    payload = bytearray(b"cache-v2-canonical-f64-key-v1\0")
    missing = [
        item["field"]
        for item in CACHE_V2_QUANTIZED_INPUTS
        if item["field"] not in values
    ]
    if missing:
        raise ValueError(f"cache_v2 key fields missing: {missing}")
    for item in CACHE_V2_QUANTIZED_INPUTS:
        field = item["field"]
        representation = item["representation"]
        value = values[field]
        nullable = representation.startswith("nullable ")
        if value is None:
            if not nullable:
                raise ValueError(f"cache_v2 key field {field!r} may not be null")
            payload.extend(b"\x00")
            continue
        payload.extend(b"\x01")
        if "UTF-8" in representation:
            encoded = str(value).encode("utf-8")
            payload.extend(struct.pack(">I", len(encoded)))
            payload.extend(encoded)
            continue
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(
                f"cache_v2 key field {field!r} must be finite: {value!r}"
            )
        if number == 0.0:
            number = 0.0
        payload.extend(struct.pack(">d", number))
    return bytes(payload)


def cache_v2_key_hash(inputs: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_f64_key_bytes(_cache_v2_quantized_values(inputs))
    ).hexdigest()


def cache_v2_key_hash_from_grid_row(row: Mapping[str, Any]) -> str:
    values = {
        item["field"]: row[
            "fe3fet_ratio" if item["field"] == "Fe3Fet_Liq" else item["field"]
        ]
        for item in CACHE_V2_QUANTIZED_INPUTS
    }
    return hashlib.sha256(_canonical_f64_key_bytes(values)).hexdigest()


def _immutable_cache_v2_metadata() -> dict[str, str]:
    manifest = canonical_json(cache_v2_identity_manifest())
    return {
        "cache_v2_schema_version": CACHE_V2_SCHEMA_VERSION,
        "corpus_version": _load_corpus_version(),
        "cache_v2_identity_manifest": manifest,
        "cache_v2_identity_manifest_sha256": hashlib.sha256(
            manifest.encode("utf-8")
        ).hexdigest(),
    }


def _cache_v2_descriptive_manifest_compatible(
    database_manifest: str | None,
    writer_manifest: str,
) -> bool:
    """Allow legacy descriptive dictionaries without relaxing cache identity."""
    try:
        database_payload = json.loads(database_manifest or "")
        writer_payload = json.loads(writer_manifest)
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(database_payload, dict) or not isinstance(writer_payload, dict):
        return False
    descriptive_fields = {
        "dictionaries",
        "dictionary_sources",
        "dictionary_policy",
    }
    return {
        key: value
        for key, value in database_payload.items()
        if key not in descriptive_fields
    } == {
        key: value
        for key, value in writer_payload.items()
        if key not in descriptive_fields
    }


def canonical_input_vector(inputs: Mapping[str, Any]) -> str:
    allowed_fields = INPUT_FIELDS + POINT_PROVENANCE_FIELDS
    missing = [
        name
        for name in allowed_fields
        if name not in inputs and name not in OPTIONAL_PROVENANCE_FIELDS
    ]
    extra = sorted(set(inputs) - set(allowed_fields))
    if missing or extra:
        raise ValueError(f"input vector mismatch: missing={missing}, extra={extra}")
    return canonical_json({name: inputs[name] for name in INPUT_FIELDS})


def expedited_key(inputs: Mapping[str, Any]) -> str:
    vector = canonical_input_vector(inputs)
    return hashlib.sha256(vector.encode("utf-8")).hexdigest()


def _float(value: Any) -> float | None:
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"non-finite float cannot be cached: {value!r}")
    return result


def _repr(value: Any) -> str | None:
    number = _float(value)
    return None if number is None else repr(number)


def _json(value: Any) -> str | None:
    return None if value is None else canonical_json(value)


def _execute_sql_script_in_transaction(
    connection: sqlite3.Connection, script: str
) -> None:
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("incomplete SQLite schema statement")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS id_block_registry (
    block_base INTEGER PRIMARY KEY,
    source_label TEXT NOT NULL UNIQUE
);
INSERT OR IGNORE INTO id_block_registry(block_base, source_label)
    VALUES (0, 'laptop-dev-smoke');
INSERT OR IGNORE INTO id_block_registry(block_base, source_label)
    VALUES (1000000000, 'studio1');
INSERT OR IGNORE INTO id_block_registry(block_base, source_label)
    VALUES (2000000000, 'studio2');
INSERT OR IGNORE INTO id_block_registry(block_base, source_label)
    VALUES (3000000000, 'studio3');

CREATE TABLE IF NOT EXISTS batches (
    batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('fixed', 'sobol')),
    seed INTEGER NOT NULL,
    params_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    generator_host TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grid_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expedited_key TEXT NOT NULL UNIQUE,
    key_hash TEXT UNIQUE,
    canonical_vector TEXT NOT NULL UNIQUE,
    batch_id INTEGER NOT NULL REFERENCES batches(batch_id),
    shuffle_rank INTEGER NOT NULL,
    shard INTEGER NOT NULL CHECK(shard IN (0, 1, 2)),
    artifact_kind TEXT NOT NULL DEFAULT 'equilibrium',
    temperature_C REAL NOT NULL,
    temperature_C_repr TEXT NOT NULL,
    composition_kg_json TEXT,
    -- Provenance only: excluded from canonical_vector and expedited_key.
    intended_fO2_log REAL,
    intended_fO2_log_repr TEXT,
    kress91_fixed_ferric_fraction REAL,
    kress91_fixed_ferric_fraction_repr TEXT,
    kress91_partition_provenance_json TEXT,
    fO2_log REAL NOT NULL,
    fO2_log_repr TEXT NOT NULL,
    pressure_bar REAL NOT NULL,
    pressure_bar_repr TEXT NOT NULL,
    composition_mol_json TEXT NOT NULL,
    composition_mol_by_account_json TEXT NOT NULL,
    composition_total_mol REAL NOT NULL,
    composition_total_mol_repr TEXT NOT NULL,
    component_SiO2_mol REAL NOT NULL,
    component_SiO2_mol_repr TEXT NOT NULL,
    component_TiO2_mol REAL NOT NULL,
    component_TiO2_mol_repr TEXT NOT NULL,
    component_Al2O3_mol REAL NOT NULL,
    component_Al2O3_mol_repr TEXT NOT NULL,
    component_Fe2O3_mol REAL NOT NULL,
    component_Fe2O3_mol_repr TEXT NOT NULL,
    component_Cr2O3_mol REAL NOT NULL,
    component_Cr2O3_mol_repr TEXT NOT NULL,
    component_FeO_mol REAL NOT NULL,
    component_FeO_mol_repr TEXT NOT NULL,
    component_MnO_mol REAL NOT NULL,
    component_MnO_mol_repr TEXT NOT NULL,
    component_MgO_mol REAL NOT NULL,
    component_MgO_mol_repr TEXT NOT NULL,
    component_NiO_mol REAL NOT NULL,
    component_NiO_mol_repr TEXT NOT NULL,
    component_CoO_mol REAL NOT NULL,
    component_CoO_mol_repr TEXT NOT NULL,
    component_CaO_mol REAL NOT NULL,
    component_CaO_mol_repr TEXT NOT NULL,
    component_Na2O_mol REAL NOT NULL,
    component_Na2O_mol_repr TEXT NOT NULL,
    component_K2O_mol REAL NOT NULL,
    component_K2O_mol_repr TEXT NOT NULL,
    component_P2O5_mol REAL NOT NULL,
    component_P2O5_mol_repr TEXT NOT NULL,
    species_formula_registry_json TEXT,
    species_formula_registry_digest TEXT,
    mode TEXT,
    subprocess_run_mode TEXT,
    redox_buffer TEXT,
    fO2_offset REAL,
    fO2_offset_repr TEXT,
    fe3fet_ratio REAL,
    fe3fet_ratio_repr TEXT,
    model TEXT,
    timeout_s REAL NOT NULL,
    timeout_s_repr TEXT NOT NULL,
    require_petthermotools INTEGER NOT NULL,
    thermoengine_health_timeout_s REAL NOT NULL,
    thermoengine_health_timeout_s_repr TEXT NOT NULL,
    finder_min_T_C REAL,
    finder_max_T_C REAL,
    finder_scan_step_C REAL,
    finder_tolerance_C REAL,
    finder_solid_epsilon REAL,
    finder_liquid_epsilon REAL,
    finder_monotonicity_tolerance REAL,
    finder_monotone_smoothing_max REAL,
    finder_max_bisection_iterations INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(batch_id, shuffle_rank)
);

CREATE TABLE IF NOT EXISTS grid_key_claims (
    grid_key_id INTEGER NOT NULL REFERENCES grid_keys(id),
    engine_epoch INTEGER NOT NULL,
    claim_owner TEXT NOT NULL,
    claimed_at_epoch REAL NOT NULL,
    expires_at_epoch REAL NOT NULL,
    PRIMARY KEY(grid_key_id, engine_epoch)
);

CREATE TABLE IF NOT EXISTS alphamelts_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grid_key_id INTEGER NOT NULL REFERENCES grid_keys(id),
    expedited_key TEXT NOT NULL,
    engine_epoch INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    status_kind TEXT NOT NULL,
    refusal_reason TEXT,
    failure_reason_code TEXT,
    failure_message TEXT,
    raw_payload TEXT NOT NULL,
    raw_payload_format TEXT NOT NULL,
    timing_s REAL NOT NULL,
    timing_s_repr TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    engine_mode TEXT NOT NULL,
    engine_model TEXT NOT NULL,
    run_mode TEXT,
    applied_timeout_s REAL,
    applied_timeout_s_repr TEXT,
    native_input_json TEXT,
    created_at TEXT NOT NULL,
    host TEXT NOT NULL,
    source_host TEXT,
    source_row_id INTEGER,

    generic_temperature_C REAL,
    generic_temperature_C_repr TEXT,
    generic_requested_temperature_C REAL,
    generic_requested_temperature_C_repr TEXT,
    generic_pressure_bar REAL,
    generic_pressure_bar_repr TEXT,
    generic_phases_present_json TEXT,
    generic_phase_masses_kg_json TEXT,
    generic_phase_species_mol_json TEXT,
    generic_phase_species_kg_json TEXT,
    -- Presence-optional for legacy rows written before per-instance capture.
    generic_phase_instances_json TEXT,
    generic_phase_compositions_json TEXT,
    generic_liquid_fraction REAL,
    generic_liquid_fraction_repr TEXT,
    generic_phase_assemblage_available INTEGER,
    generic_liquid_composition_wt_pct_json TEXT,
    generic_liquid_viscosity_Pa_s REAL,
    generic_liquid_viscosity_Pa_s_repr TEXT,
    generic_liquid_density_kg_m3 REAL,
    generic_liquid_density_kg_m3_repr TEXT,
    generic_system_enthalpy REAL,
    generic_system_enthalpy_repr TEXT,
    generic_system_entropy REAL,
    generic_system_entropy_repr TEXT,
    generic_system_volume REAL,
    generic_system_volume_repr TEXT,
    generic_system_heat_capacity_Cp REAL,
    generic_system_heat_capacity_Cp_repr TEXT,
    generic_system_dVdP REAL,
    generic_system_dVdP_repr TEXT,
    generic_system_dVdT REAL,
    generic_system_dVdT_repr TEXT,
    generic_system_fO2_delta_QFM REAL,
    generic_system_fO2_delta_QFM_repr TEXT,
    generic_system_solid_density_rhos REAL,
    generic_system_solid_density_rhos_repr TEXT,
    generic_system_phi REAL,
    generic_system_phi_repr TEXT,
    generic_system_chisqr REAL,
    generic_system_chisqr_repr TEXT,
    generic_phase_thermo_json TEXT,
    generic_chem_potentials_json TEXT,
    generic_phase_affinities_json TEXT,
    generic_solid_composition_wt_pct_json TEXT,
    generic_bulk_composition_wt_pct_json TEXT,
    generic_vapor_pressures_Pa_json TEXT,
    generic_vapor_pressures_source_json TEXT,
    generic_activity_coefficients_json TEXT,
    generic_fO2_log REAL,
    generic_fO2_log_repr TEXT,
    generic_warnings_json TEXT,
    generic_ledger_transition_json TEXT,
    generic_status TEXT,
    generic_sulfur_saturation_json TEXT,
    generic_liquidus_T_C REAL,
    generic_liquidus_T_C_repr TEXT,
    generic_diagnostics_json TEXT,

    -- ThermoEngine-only fields. NULL is required for subprocess rows and
    -- presence-optional for databases created before the TE field freeze.
    te_liquid_activities_json TEXT,
    te_system_dVdP_m3_bar REAL,
    te_system_dVdP_m3_bar_repr TEXT,
    te_system_dVdT_m3_K REAL,
    te_system_dVdT_m3_K_repr TEXT,
    te_solver_status TEXT,
    te_solver_converged INTEGER,
    te_solver_iterations INTEGER,
    te_solver_iterations_available INTEGER,
    te_fO2_solve_count INTEGER,
    te_phase_universe_size INTEGER,

    alpha_activity_coefficients_json TEXT,
    alpha_applied_fe3fet REAL,
    alpha_applied_fe3fet_repr TEXT,
    alpha_backend_diagnostics_json TEXT,
    alpha_backend_status TEXT,
    alpha_backend_status_reason TEXT,
    alpha_backend_warnings_json TEXT,
    alpha_engine_version TEXT,
    alpha_fO2_log REAL,
    alpha_fO2_log_repr TEXT,
    alpha_fe_redox_policy TEXT,
    alpha_intrinsic_fO2_log REAL,
    alpha_intrinsic_fO2_log_repr TEXT,
    alpha_liquid_composition_wt_pct_json TEXT,
    alpha_liquid_fraction REAL,
    alpha_liquid_fraction_repr TEXT,
    alpha_liquid_fraction_path_json TEXT,
    alpha_liquidus_T_C REAL,
    alpha_liquidus_T_C_repr TEXT,
    alpha_liquidus_T_K REAL,
    alpha_liquidus_T_K_repr TEXT,
    alpha_mode TEXT,
    alpha_phase_masses_kg_json TEXT,
    alpha_phase_modes_wt_pct_json TEXT,
    alpha_phases_present_json TEXT,
    alpha_solidus_T_C REAL,
    alpha_solidus_T_C_repr TEXT,

    finder_liquidus_T_C REAL,
    finder_liquidus_T_K REAL,
    finder_solidus_T_C REAL,
    finder_liquid_fraction REAL,
    finder_status TEXT,
    finder_warnings_json TEXT,
    finder_diagnostics_json TEXT,
    finder_iterations INTEGER,
    finder_samples_json TEXT,
    finder_sample_temperature_C_json TEXT,
    finder_sample_frac_M_json TEXT,
    curve_source TEXT,
    curve_solidus_T_C REAL,
    curve_liquidus_T_C REAL,
    curve_path_temperature_C_json TEXT,
    curve_path_liquid_fraction_json TEXT,

    UNIQUE(expedited_key, engine_epoch)
);

CREATE INDEX IF NOT EXISTS idx_alphamelts_outputs_input
    ON alphamelts_outputs(grid_key_id, engine_epoch);
CREATE INDEX IF NOT EXISTS idx_alphamelts_outputs_status
    ON alphamelts_outputs(status, engine_epoch);
CREATE INDEX IF NOT EXISTS idx_grid_keys_drain
    ON grid_keys(batch_id, shard, shuffle_rank);
CREATE INDEX IF NOT EXISTS idx_grid_key_claims_expiry
    ON grid_key_claims(expires_at_epoch);
"""


class GridCacheWriter:
    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        engine_epoch: int = 1,
        existing_only: bool = False,
        backend_name: str | None = None,
    ):
        self.path = Path(path)
        self.engine_epoch = int(engine_epoch)
        self.backend_name = None if backend_name is None else str(backend_name)
        self.claim_owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"
        if self.engine_epoch < 1:
            raise ValueError("engine_epoch must be >= 1")
        if existing_only and not self.path.is_file():
            raise FileNotFoundError(f"database does not exist: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        database = str(self.path)
        if existing_only:
            database = (
                "file:"
                + urllib.parse.quote(str(self.path.resolve()), safe="/")
                + "?mode=rw"
            )
        try:
            self.connection = sqlite3.connect(
                database, timeout=30.0, uri=existing_only
            )
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"cannot open grid cache {self.path}: {exc}") from exc
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=30000")
        try:
            # One handle owns classification, immutable validation, migration,
            # and fresh creation. BEGIN IMMEDIATE serializes concurrent creators
            # and path replacement cannot change the already-open file handle.
            self.connection.execute("BEGIN IMMEDIATE")
            tables = {
                str(row[0])
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name != 'sqlite_sequence'"
                )
            }
            fresh_database = not tables
            if existing_only and fresh_database:
                raise ValueError(
                    f"existing database is not a grid cache: {self.path}"
                )
            if fresh_database:
                _execute_sql_script_in_transaction(self.connection, SCHEMA_SQL)
            else:
                # This explicit grid-cache signature is what distinguishes a
                # presence-optional legacy DB from a partial/crashed creation.
                self._validate_existing_database()
                self._upgrade_compatible_cache_v2_descriptive_manifest()

            # Additive migrations happen only after the same-connection guard.
            self._ensure_v2_provenance_columns()
            self._ensure_runmode_output_columns()
            self._ensure_claim_table()
            self._set_metadata("schema_variant", SCHEMA_VARIANT)
            self._set_metadata(
                "expedited_key_note",
                "variant-local bookkeeping only; recompute reviewed canonical_state_bytes "
                "from typed full-precision inputs; never transplant this hash",
            )
            self._set_metadata("schema_output_field_count", "84")
            self._set_metadata("schema_input_field_count", "25")
            self._set_metadata("grid_realization_revision", GRID_REALIZATION_REVISION)
            self._set_metadata("database_id", str(uuid.uuid4()), overwrite=False)
            self._set_metadata("created_at", utc_now(), overwrite=False)
            if fresh_database:
                for key, value in _immutable_cache_v2_metadata().items():
                    self._set_metadata(key, value, overwrite=False)
            self.connection.commit()
        except Exception as exc:
            if self.connection.in_transaction:
                self.connection.rollback()
            self.connection.close()
            if isinstance(exc, sqlite3.DatabaseError):
                raise ValueError(
                    f"cannot validate existing grid cache {self.path}: {exc}"
                ) from exc
            raise
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        try:
            self._assert_backend_not_blended(self.backend_name)
        except Exception:
            self.connection.close()
            raise

    @staticmethod
    def _engine_mode_for_backend(backend_name: str) -> str:
        try:
            return CacheV2GridBackend(backend_name).value
        except ValueError as exc:
            raise ValueError(f"unsupported grid backend: {backend_name!r}") from exc

    def _assert_backend_not_blended(self, backend_name: str | None) -> None:
        if backend_name is None:
            return
        expected_mode = self._engine_mode_for_backend(backend_name)
        existing_modes = {
            str(row[0])
            for row in self.connection.execute(
                "SELECT DISTINCT engine_mode FROM alphamelts_outputs"
            )
        }
        if existing_modes and existing_modes != {expected_mode}:
            raise ValueError(
                "grid cache engine blend refused: "
                f"requested backend={backend_name!r} engine_mode={expected_mode!r}; "
                f"database engine_mode values={sorted(existing_modes)!r}. "
                "Use a dedicated database for each engine."
            )

    def _validate_existing_database(self) -> None:
        self._validate_connection(self.connection)

    def _upgrade_compatible_cache_v2_descriptive_manifest(self) -> None:
        """Refresh descriptive dictionaries before this writer can append rows."""
        immutable = _immutable_cache_v2_metadata()
        rows = {
            str(row[0]): str(row[1])
            for row in self.connection.execute(
                "SELECT key, value FROM metadata WHERE key IN (?, ?)",
                (
                    "cache_v2_identity_manifest",
                    "cache_v2_identity_manifest_sha256",
                ),
            )
        }
        stored_manifest = rows.get("cache_v2_identity_manifest")
        stored_sha256 = rows.get("cache_v2_identity_manifest_sha256")
        if stored_manifest is None or stored_sha256 is None:
            return
        if stored_sha256 != hashlib.sha256(
            stored_manifest.encode("utf-8")
        ).hexdigest():
            return
        current_manifest = immutable["cache_v2_identity_manifest"]
        if stored_manifest == current_manifest:
            return
        if not _cache_v2_descriptive_manifest_compatible(
            stored_manifest,
            current_manifest,
        ):
            return
        self._set_metadata("cache_v2_identity_manifest", current_manifest)
        self._set_metadata(
            "cache_v2_identity_manifest_sha256",
            immutable["cache_v2_identity_manifest_sha256"],
        )

    @staticmethod
    def _validate_connection(connection: sqlite3.Connection) -> None:
        required_tables = {
            "metadata",
            "id_block_registry",
            "batches",
            "grid_keys",
            "alphamelts_outputs",
        }
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = sorted(required_tables - tables)
        if missing:
            raise ValueError(
                "existing database is not a grid cache; missing tables: "
                + ", ".join(missing)
            )
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_variant'"
        ).fetchone()
        if row is None or str(row[0]) != SCHEMA_VARIANT:
            value = None if row is None else str(row[0])
            raise ValueError(
                f"schema variant mismatch: database={value!r}, "
                f"writer={SCHEMA_VARIANT!r}"
            )
        GridCacheWriter._validate_immutable_cache_v2_metadata(connection)
        provenance_columns = set(table_columns(connection, "grid_keys"))
        missing_columns = {
            "intended_fO2_log",
            "intended_fO2_log_repr",
        } - provenance_columns
        if missing_columns:
            raise ValueError(
                "existing database lacks v2 provenance columns: "
                + ", ".join(sorted(missing_columns))
            )

    @staticmethod
    def _validate_immutable_cache_v2_metadata(
        connection: sqlite3.Connection,
    ) -> None:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "metadata" not in tables:
            return
        immutable = _immutable_cache_v2_metadata()
        rows = {
            str(row[0]): str(row[1])
            for row in connection.execute(
                "SELECT key, value FROM metadata WHERE key IN (?, ?, ?, ?)",
                tuple(immutable),
            )
        }
        if rows:
            missing_metadata = sorted(set(immutable) - set(rows))
            stored_manifest = rows.get("cache_v2_identity_manifest")
            stored_manifest_sha256 = rows.get("cache_v2_identity_manifest_sha256")
            stored_manifest_hash_valid = (
                stored_manifest is not None
                and stored_manifest_sha256
                == hashlib.sha256(stored_manifest.encode("utf-8")).hexdigest()
            )
            descriptive_manifest_compatible = (
                stored_manifest_hash_valid
                and _cache_v2_descriptive_manifest_compatible(
                    stored_manifest,
                    immutable["cache_v2_identity_manifest"],
                )
            )
            mismatches = {
                key: {"database": rows.get(key), "writer": value}
                for key, value in immutable.items()
                if rows.get(key) != value
                and not (
                    descriptive_manifest_compatible
                    and key
                    in {
                        "cache_v2_identity_manifest",
                        "cache_v2_identity_manifest_sha256",
                    }
                )
            }
            if missing_metadata or mismatches:
                raise ValueError(
                    "cache_v2 immutable metadata mismatch: "
                    f"missing={missing_metadata!r}, mismatches={mismatches!r}"
                )

    @classmethod
    def has_batch_definitions(cls, path: str | os.PathLike[str]) -> bool:
        database_path = Path(path)
        if not database_path.is_file() or database_path.stat().st_size == 0:
            return False
        database = (
            "file:"
            + urllib.parse.quote(str(database_path.resolve()), safe="/")
            + "?mode=ro"
        )
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(database, uri=True)
            table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'batches'"
            ).fetchone()
            if table is None:
                return False
            return (
                connection.execute(
                    "SELECT 1 FROM batches "
                    "WHERE seed IS NOT NULL AND params_json IS NOT NULL LIMIT 1"
                ).fetchone()
                is not None
            )
        except sqlite3.DatabaseError as exc:
            raise ValueError(
                f"cannot inspect existing database {database_path}: {exc}"
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    def _ensure_v2_provenance_columns(self) -> None:
        columns = set(table_columns(self.connection, "grid_keys"))
        additions = {
            "key_hash": "TEXT",
            "intended_fO2_log": "REAL",
            "intended_fO2_log_repr": "TEXT",
            "kress91_fixed_ferric_fraction": "REAL",
            "kress91_fixed_ferric_fraction_repr": "TEXT",
            "kress91_partition_provenance_json": "TEXT",
        }
        for name, column_type in additions.items():
            if name not in columns:
                self.connection.execute(
                    f'ALTER TABLE grid_keys ADD COLUMN "{name}" {column_type}'
                )
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_grid_keys_cache_v2_key_hash "
            "ON grid_keys(key_hash) WHERE key_hash IS NOT NULL"
        )

    def _ensure_runmode_output_columns(self) -> None:
        additions = {
            "grid_keys": {
                "subprocess_run_mode": "TEXT",
            },
            "alphamelts_outputs": {
                "failure_reason_code": "TEXT",
                "failure_message": "TEXT",
                "generic_requested_temperature_C": "REAL",
                "generic_requested_temperature_C_repr": "TEXT",
                "generic_liquid_density_kg_m3": "REAL",
                "generic_liquid_density_kg_m3_repr": "TEXT",
                "generic_system_enthalpy": "REAL",
                "generic_system_enthalpy_repr": "TEXT",
                "generic_system_entropy": "REAL",
                "generic_system_entropy_repr": "TEXT",
                "generic_system_volume": "REAL",
                "generic_system_volume_repr": "TEXT",
                "generic_system_heat_capacity_Cp": "REAL",
                "generic_system_heat_capacity_Cp_repr": "TEXT",
                "generic_system_dVdP": "REAL",
                "generic_system_dVdP_repr": "TEXT",
                "generic_system_dVdT": "REAL",
                "generic_system_dVdT_repr": "TEXT",
                "generic_system_fO2_delta_QFM": "REAL",
                "generic_system_fO2_delta_QFM_repr": "TEXT",
                "generic_system_solid_density_rhos": "REAL",
                "generic_system_solid_density_rhos_repr": "TEXT",
                "generic_system_phi": "REAL",
                "generic_system_phi_repr": "TEXT",
                "generic_system_chisqr": "REAL",
                "generic_system_chisqr_repr": "TEXT",
                "generic_phase_thermo_json": "TEXT",
                "generic_chem_potentials_json": "TEXT",
                "generic_phase_affinities_json": "TEXT",
                "generic_phase_instances_json": "TEXT",
                "generic_solid_composition_wt_pct_json": "TEXT",
                "generic_bulk_composition_wt_pct_json": "TEXT",
                "te_liquid_activities_json": "TEXT",
                "te_system_dVdP_m3_bar": "REAL",
                "te_system_dVdP_m3_bar_repr": "TEXT",
                "te_system_dVdT_m3_K": "REAL",
                "te_system_dVdT_m3_K_repr": "TEXT",
                "te_solver_status": "TEXT",
                "te_solver_converged": "INTEGER",
                "te_solver_iterations": "INTEGER",
                "te_solver_iterations_available": "INTEGER",
                "te_fO2_solve_count": "INTEGER",
                "te_phase_universe_size": "INTEGER",
                "run_mode": "TEXT",
                "applied_timeout_s": "REAL",
                "applied_timeout_s_repr": "TEXT",
            },
        }
        for table, columns_to_add in additions.items():
            columns = set(table_columns(self.connection, table))
            for name, column_type in columns_to_add.items():
                if name not in columns:
                    self.connection.execute(
                        f'ALTER TABLE "{table}" ADD COLUMN '
                        f'"{name}" {column_type}'
                    )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_alphamelts_outputs_failure_reason "
            "ON alphamelts_outputs(failure_reason_code, engine_epoch)"
        )

    def _ensure_claim_table(self) -> None:
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS grid_key_claims (
                grid_key_id INTEGER NOT NULL REFERENCES grid_keys(id),
                engine_epoch INTEGER NOT NULL,
                claim_owner TEXT NOT NULL,
                claimed_at_epoch REAL NOT NULL,
                expires_at_epoch REAL NOT NULL,
                PRIMARY KEY(grid_key_id, engine_epoch)
            )"""
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_grid_key_claims_expiry "
            "ON grid_key_claims(expires_at_epoch)"
        )

    def _begin_write_section(self, savepoint_name: str) -> str | None:
        if self.connection.in_transaction:
            self.connection.execute(f"SAVEPOINT {savepoint_name}")
            return savepoint_name
        self.connection.execute("BEGIN IMMEDIATE")
        return None

    def _finish_write_section(self, savepoint_name: str | None) -> None:
        if savepoint_name is None:
            self.connection.commit()
        else:
            self.connection.execute(f"RELEASE SAVEPOINT {savepoint_name}")

    def _rollback_write_section(self, savepoint_name: str | None) -> None:
        if savepoint_name is None:
            self.connection.rollback()
        else:
            self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
            self.connection.execute(f"RELEASE SAVEPOINT {savepoint_name}")

    def _set_metadata(self, key: str, value: str, *, overwrite: bool = True) -> None:
        existing = self.connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        if existing is not None and key == "schema_variant" and existing[0] != value:
            raise ValueError(
                f"schema variant mismatch: database={existing[0]!r}, writer={value!r}"
            )
        if existing is None:
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES (?, ?)", (key, value)
            )
        elif overwrite:
            self.connection.execute(
                "UPDATE metadata SET value = ? WHERE key = ?", (value, key)
            )

    def set_run_metadata(self, values: Mapping[str, Any]) -> None:
        for key, value in values.items():
            if str(key) == "schema_variant":
                self._set_metadata("schema_variant", str(value))
            else:
                self._set_metadata(str(key), canonical_json(value))
        self.connection.commit()

    def ensure_batch(
        self,
        *,
        label: str,
        kind: str,
        seed: int,
        params: Mapping[str, Any],
        generator_host: str | None = None,
    ) -> int:
        if kind not in {"fixed", "sobol"}:
            raise ValueError(f"unsupported batch kind: {kind}")
        params_json = canonical_json(params)
        self.connection.execute(
            "INSERT OR IGNORE INTO batches(label, kind, seed, params_json, "
            "created_at, generator_host) VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(label),
                kind,
                int(seed),
                params_json,
                utc_now(),
                generator_host or socket.gethostname(),
            ),
        )
        row = self.connection.execute(
            "SELECT batch_id, kind, seed, params_json FROM batches WHERE label = ?",
            (str(label),),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"failed to materialize batch {label!r}")
        if (
            row["kind"] != kind
            or int(row["seed"]) != int(seed)
            or row["params_json"] != params_json
        ):
            raise ValueError(f"batch definition drift for {label!r}")
        return int(row["batch_id"])

    def seed_id_block(self, shard: int) -> None:
        if shard not in {0, 1, 2}:
            raise ValueError("shard must be 0, 1, or 2")
        for table in ("grid_keys", "alphamelts_outputs"):
            count = int(
                self.connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            if count:
                continue
            base = (int(shard) + 1) * 1_000_000_000
            self.connection.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
            self.connection.execute(
                "INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)",
                (table, base - 1),
            )

    def materialize_key(
        self,
        inputs: Mapping[str, Any],
        *,
        batch_id: int,
        shuffle_rank: int,
        shard: int,
        intended_fO2_log: float | None = None,
    ) -> bool:
        values = self._grid_key_values(
            inputs,
            batch_id=batch_id,
            shuffle_rank=shuffle_rank,
            shard=shard,
            intended_fO2_log=intended_fO2_log,
        )
        cursor = self._insert_or_ignore("grid_keys", values)
        if cursor.rowcount == 0:
            vector = values["canonical_vector"]
            row = self.connection.execute(
                "SELECT canonical_vector, key_hash, batch_id, shuffle_rank, shard, "
                "kress91_partition_provenance_json, "
                "kress91_fixed_ferric_fraction "
                "FROM grid_keys WHERE expedited_key = ?",
                (values["expedited_key"],),
            ).fetchone()
            if row is None or row["canonical_vector"] != vector:
                raise RuntimeError(
                    f"expedited-key collision for {values['expedited_key']}"
                )
            if row["key_hash"] not in {None, values["key_hash"]}:
                raise RuntimeError(
                    f"cache-v2 key-hash drift for {values['expedited_key']}"
                )
            if row["key_hash"] is None:
                self.connection.execute(
                    "UPDATE grid_keys SET key_hash = ? WHERE expedited_key = ?",
                    (values["key_hash"], values["expedited_key"]),
                )
            existing_provenance = row["kress91_partition_provenance_json"]
            point_provenance = values["kress91_partition_provenance_json"]
            if existing_provenance is None:
                self.connection.execute(
                    "UPDATE grid_keys "
                    "SET kress91_partition_provenance_json = ?, "
                    "kress91_fixed_ferric_fraction = ?, "
                    "kress91_fixed_ferric_fraction_repr = ? "
                    "WHERE expedited_key = ?",
                    (
                        point_provenance,
                        values["kress91_fixed_ferric_fraction"],
                        values["kress91_fixed_ferric_fraction_repr"],
                        values["expedited_key"],
                    ),
                )
            elif existing_provenance != point_provenance:
                raise ValueError(
                    "Kress91 point provenance drift for expedited key "
                    f"{values['expedited_key']}"
                )
            elif (
                row["kress91_fixed_ferric_fraction"] is None
                and values["kress91_fixed_ferric_fraction"] is not None
            ):
                self.connection.execute(
                    "UPDATE grid_keys SET kress91_fixed_ferric_fraction = ?, "
                    "kress91_fixed_ferric_fraction_repr = ? "
                    "WHERE expedited_key = ?",
                    (
                        values["kress91_fixed_ferric_fraction"],
                        values["kress91_fixed_ferric_fraction_repr"],
                        values["expedited_key"],
                    ),
                )
        return cursor.rowcount == 1

    def pending_rows(
        self,
        *,
        batch_id: int,
        shard: int | None = None,
        rank_limit: int | None = None,
        after_shuffle_rank: int = -1,
        fetch_limit: int | None = None,
        grid_key_ids: Sequence[int] | None = None,
    ) -> list[dict[str, Any]]:
        now = time.time()
        if grid_key_ids is not None and not grid_key_ids:
            return []
        if fetch_limit is not None and int(fetch_limit) <= 0:
            return []
        write_section = self._begin_write_section("grid_key_claim_pending")
        try:
            self.connection.execute(
                "DELETE FROM grid_key_claims WHERE expires_at_epoch <= ?",
                (now,),
            )
            clauses = [
                "g.batch_id = ?",
                "o.id IS NULL",
                "c.grid_key_id IS NULL",
                "g.shuffle_rank > ?",
            ]
            parameters: list[Any] = [
                self.engine_epoch,
                self.engine_epoch,
                int(batch_id),
                int(after_shuffle_rank),
            ]
            if shard is not None:
                clauses.append("g.shard = ?")
                parameters.append(int(shard))
            if rank_limit is not None:
                clauses.append("g.shuffle_rank < ?")
                parameters.append(int(rank_limit))
            if grid_key_ids is not None:
                placeholders = ",".join("?" for _ in grid_key_ids)
                clauses.append(f"g.id IN ({placeholders})")
                parameters.extend(int(value) for value in grid_key_ids)
            query = (
                "SELECT g.id, g.expedited_key, g.canonical_vector, "
                "g.kress91_partition_provenance_json, "
                "g.kress91_fixed_ferric_fraction, g.intended_fO2_log, "
                "g.shuffle_rank, g.shard, g.timeout_s FROM grid_keys g "
                "LEFT JOIN alphamelts_outputs o ON o.expedited_key = g.expedited_key "
                "AND o.engine_epoch = ? "
                "LEFT JOIN grid_key_claims c ON c.grid_key_id = g.id "
                "AND c.engine_epoch = ? WHERE "
                + " AND ".join(clauses)
                + " ORDER BY g.shuffle_rank"
            )
            if fetch_limit is not None:
                # The drain caller asks in pages, but claims must follow actual
                # worker capacity rather than reserving an entire page at once.
                query += " LIMIT 1"
            rows = self.connection.execute(query, tuple(parameters)).fetchall()
            if not rows:
                self._finish_write_section(write_section)
                return []
            for row in rows:
                lease_s = max(3600.0, float(row["timeout_s"]) * 2.0 + 300.0)
                self.connection.execute(
                    "INSERT INTO grid_key_claims("
                    "grid_key_id, engine_epoch, claim_owner, "
                    "claimed_at_epoch, expires_at_epoch"
                    ") VALUES (?, ?, ?, ?, ?)",
                    (
                        int(row["id"]),
                        self.engine_epoch,
                        self.claim_owner,
                        now,
                        now + lease_s,
                    ),
                )
            self._finish_write_section(write_section)
        except BaseException:
            self._rollback_write_section(write_section)
            raise
        return [
            {
                "grid_key_id": int(row["id"]),
                "expedited_key": str(row["expedited_key"]),
                "inputs": {
                    **json.loads(row["canonical_vector"]),
                    # Provenance is intentionally outside canonical key identity,
                    # but the drain must compare it with the persisted engine input.
                    "intended_fO2_log": row["intended_fO2_log"],
                    "kress91_fixed_ferric_fraction": row[
                        "kress91_fixed_ferric_fraction"
                    ],
                    "kress91_partition_provenance": (
                        json.loads(row["kress91_partition_provenance_json"])
                        if row["kress91_partition_provenance_json"] is not None
                        else None
                    ),
                },
                "shuffle_rank": int(row["shuffle_rank"]),
                "shard": int(row["shard"]),
            }
            for row in rows
        ]

    def queue_counts(
        self,
        *,
        batch_id: int,
        shard: int | None = None,
        rank_limit: int | None = None,
        grid_key_ids: Sequence[int] | None = None,
    ) -> dict[str, int]:
        extra = "" if shard is None else " AND g.shard = ?"
        parameters: list[Any] = [self.engine_epoch, int(batch_id)]
        if shard is not None:
            parameters.append(int(shard))
        if rank_limit is not None:
            extra += " AND g.shuffle_rank < ?"
            parameters.append(int(rank_limit))
        if grid_key_ids is not None:
            if not grid_key_ids:
                return {"total": 0, "done": 0, "remaining": 0}
            placeholders = ",".join("?" for _ in grid_key_ids)
            extra += f" AND g.id IN ({placeholders})"
            parameters.extend(int(value) for value in grid_key_ids)
        row = self.connection.execute(
            "SELECT COUNT(*) AS total, COUNT(o.id) AS done "
            "FROM grid_keys g LEFT JOIN alphamelts_outputs o "
            "ON o.expedited_key = g.expedited_key AND o.engine_epoch = ? "
            "WHERE g.batch_id = ?" + extra,
            tuple(parameters),
        ).fetchone()
        total = int(row["total"])
        done = int(row["done"])
        return {"total": total, "done": done, "remaining": total - done}

    def select_grid_key_ids(
        self,
        *,
        selectors: Sequence[str] = (),
        refusal_reason: str | None = None,
        source_epoch: int = 1,
        limit: int | None = None,
    ) -> list[int]:
        clauses: list[str] = []
        parameters: list[Any] = []
        joins = ""
        if selectors:
            selector_clauses = []
            for selector in selectors:
                text = str(selector).strip()
                if not text:
                    continue
                if text.startswith("id:"):
                    row_id = text.removeprefix("id:")
                    if not row_id.isdigit():
                        raise ValueError(f"invalid grid-key id selector: {text!r}")
                    selector_clauses.append("g.id = ?")
                    parameters.append(int(row_id))
                elif text.startswith("key:"):
                    key_prefix = text.removeprefix("key:")
                    if not key_prefix:
                        raise ValueError("expedited-key prefix cannot be empty")
                    selector_clauses.append("g.expedited_key LIKE ?")
                    parameters.append(f"{key_prefix}%")
                elif text.isdigit() and self.connection.execute(
                    "SELECT 1 FROM grid_keys WHERE id = ?", (int(text),)
                ).fetchone() is not None:
                    selector_clauses.append("g.id = ?")
                    parameters.append(int(text))
                else:
                    selector_clauses.append("g.expedited_key LIKE ?")
                    parameters.append(f"{text}%")
            if selector_clauses:
                clauses.append("(" + " OR ".join(selector_clauses) + ")")
        if refusal_reason is not None:
            joins = (
                " JOIN alphamelts_outputs source_output "
                "ON source_output.expedited_key = g.expedited_key"
            )
            clauses.extend(
                [
                    "source_output.engine_epoch = ?",
                    "source_output.refusal_reason = ?",
                ]
            )
            parameters.extend([int(source_epoch), str(refusal_reason)])
        if not clauses:
            raise ValueError("at least one key selector or refusal reason is required")
        query = (
            "SELECT DISTINCT g.id FROM grid_keys g"
            + joins
            + " WHERE "
            + " AND ".join(clauses)
            + " ORDER BY g.shuffle_rank, g.id"
        )
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(int(limit))
        return [int(row[0]) for row in self.connection.execute(query, parameters)]

    def selected_result_histogram(
        self, grid_key_ids: Sequence[int]
    ) -> dict[str, dict[str, int]]:
        if not grid_key_ids:
            return {
                "status": {},
                "refusal_reason": {},
                "failure_reason_code": {},
            }
        placeholders = ",".join("?" for _ in grid_key_ids)
        parameters = (self.engine_epoch, *(int(value) for value in grid_key_ids))
        status_rows = self.connection.execute(
            "SELECT status, COUNT(*) FROM alphamelts_outputs "
            f"WHERE engine_epoch = ? AND grid_key_id IN ({placeholders}) "
            "GROUP BY status ORDER BY status",
            parameters,
        )
        reason_rows = self.connection.execute(
            "SELECT COALESCE(refusal_reason, '<none>'), COUNT(*) "
            "FROM alphamelts_outputs "
            f"WHERE engine_epoch = ? AND grid_key_id IN ({placeholders}) "
            "GROUP BY refusal_reason ORDER BY COUNT(*) DESC",
            parameters,
        )
        failure_reason_rows = self.connection.execute(
            "SELECT COALESCE(failure_reason_code, '<none>'), COUNT(*) "
            "FROM alphamelts_outputs "
            f"WHERE engine_epoch = ? AND grid_key_id IN ({placeholders}) "
            "GROUP BY failure_reason_code ORDER BY COUNT(*) DESC",
            parameters,
        )
        return {
            "status": {str(name): int(count) for name, count in status_rows},
            "refusal_reason": {
                str(name): int(count) for name, count in reason_rows
            },
            "failure_reason_code": {
                str(name): int(count) for name, count in failure_reason_rows
            },
        }

    def batches(self) -> list[dict[str, Any]]:
        return [
            {
                "batch_id": int(row["batch_id"]),
                "label": str(row["label"]),
                "kind": str(row["kind"]),
                "seed": int(row["seed"]),
                "params": json.loads(row["params_json"]),
                "created_at": str(row["created_at"]),
                "generator_host": str(row["generator_host"]),
            }
            for row in self.connection.execute(
                "SELECT batch_id, label, kind, seed, params_json, "
                "created_at, generator_host "
                "FROM batches ORDER BY batch_id"
            )
        ]

    def drain_manifest(
        self,
        *,
        shard: int | None,
        rank_limit: int | None = None,
    ) -> dict[str, Any]:
        if shard is not None and shard not in {0, 1, 2}:
            raise ValueError("drain shard must be 0, 1, or 2")
        registry = {
            int(row["block_base"]): str(row["source_label"])
            for row in self.connection.execute(
                "SELECT block_base, source_label FROM id_block_registry"
            )
        }
        expected_registry = {
            0: "laptop-dev-smoke",
            1_000_000_000: "studio1",
            2_000_000_000: "studio2",
            3_000_000_000: "studio3",
        }
        registry_drift = {
            base: {"expected": label, "actual": registry.get(base)}
            for base, label in expected_registry.items()
            if registry.get(base) != label
        }
        if registry_drift:
            raise ValueError(
                "id-block registry mismatch: " + canonical_json(registry_drift)
            )

        bad_shard_rows = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM grid_keys "
                "WHERE shuffle_rank < 0 OR shard != (shuffle_rank % 3)"
            ).fetchone()[0]
        )
        if bad_shard_rows:
            raise ValueError(
                f"id-block/shard sanity failed: {bad_shard_rows} grid keys "
                "disagree with shuffle_rank modulo 3"
            )

        total_keys = int(
            self.connection.execute("SELECT COUNT(*) FROM grid_keys").fetchone()[0]
        )
        if total_keys == 0:
            raise ValueError(
                "database has no materialized queue; run --prepare-only on the prepare host"
            )

        if shard is not None:
            lower = (shard + 1) * 1_000_000_000
            upper = lower + 1_000_000_000
            selected = self.connection.execute(
                "SELECT COUNT(*), MIN(id), MAX(id) FROM grid_keys WHERE shard = ?",
                (shard,),
            ).fetchone()
            selected_count = int(selected[0])
            if selected_count == 0:
                raise ValueError(
                    f"database has no materialized queue for shard {shard}"
                )
            if int(selected[1]) < lower or int(selected[2]) >= upper:
                raise ValueError(
                    "id-block/shard sanity failed: "
                    f"shard {shard} grid-key ids must be in [{lower}, {upper})"
                )
            for table in ("grid_keys", "alphamelts_outputs"):
                row = self.connection.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)
                ).fetchone()
                sequence = None if row is None else int(row[0])
                if sequence is None or sequence < lower - 1 or sequence >= upper:
                    raise ValueError(
                        "id-block/shard sanity failed: "
                        f"{table} sequence {sequence!r} is outside shard {shard} block"
                    )
        else:
            invalid_blocks = int(
                self.connection.execute(
                    "SELECT COUNT(*) FROM grid_keys WHERE id >= 1000000000 AND "
                    "(id >= 4000000000 OR shard != CAST(id / 1000000000 AS INTEGER) - 1)"
                ).fetchone()[0]
            )
            if invalid_blocks:
                raise ValueError(
                    f"id-block/shard sanity failed for {invalid_blocks} grid keys"
                )

        batches = {batch["batch_id"]: batch for batch in self.batches()}
        materialized = {
            int(row["batch_id"]): int(row["key_count"])
            for row in self.connection.execute(
                "SELECT batch_id, COUNT(*) AS key_count FROM grid_keys "
                + ("" if shard is None else "WHERE shard = ? ")
                + "GROUP BY batch_id",
                () if shard is None else (shard,),
            )
        }
        result_batches = []
        for batch_id in sorted(materialized):
            key_count = materialized[batch_id]
            batch = batches.get(batch_id)
            if batch is None:
                raise ValueError(
                    f"materialized queue references missing batch {batch_id}"
                )
            params = batch["params"]
            if not isinstance(params, Mapping):
                raise ValueError(f"batch {batch_id} params_json is not an object")
            kress91_partition = params.get("kress91_partition")
            if not isinstance(kress91_partition, Mapping):
                raise ValueError(
                    f"batch {batch_id} has no stored kress91_partition metadata"
                )
            queue = self.queue_counts(
                batch_id=batch_id,
                shard=shard,
                rank_limit=rank_limit,
            )
            result_batches.append(
                {
                    **batch,
                    "params_source": "batches.params_json",
                    "kress91_partition": dict(kress91_partition),
                    "materialized_keys": key_count,
                    "queue": queue,
                }
            )
        return {
            "shard": "all" if shard is None else shard,
            "materialized_keys": sum(materialized.values()),
            "batches": result_batches,
        }

    def existing_keys(self, keys: Iterable[str]) -> set[str]:
        result: set[str] = set()
        batch: list[str] = []
        for key in keys:
            batch.append(str(key))
            if len(batch) == 500:
                result.update(self._existing_key_batch(batch))
                batch.clear()
        if batch:
            result.update(self._existing_key_batch(batch))
        return result

    def _existing_key_batch(self, keys: Sequence[str]) -> set[str]:
        placeholders = ",".join("?" for _ in keys)
        rows = self.connection.execute(
            f"SELECT expedited_key FROM alphamelts_outputs "
            f"WHERE engine_epoch = ? AND expedited_key IN ({placeholders})",
            (self.engine_epoch, *keys),
        )
        return {str(row[0]) for row in rows}

    def _grid_key_values(
        self,
        inputs: Mapping[str, Any],
        *,
        batch_id: int,
        shuffle_rank: int,
        shard: int,
        intended_fO2_log: float | None,
    ) -> dict[str, Any]:
        vector = canonical_input_vector(inputs)
        key = hashlib.sha256(vector.encode("utf-8")).hexdigest()
        composition_mol = inputs["composition_mol"] or {}
        total_mol = sum(float(value) for value in composition_mol.values())
        registry_json = _json(inputs["species_formula_registry"])
        registry_digest = (
            hashlib.sha256(registry_json.encode("utf-8")).hexdigest()
            if registry_json is not None
            else None
        )
        missing_components = [
            component for component in COMPONENT_FIELDS if component not in composition_mol
        ]
        if missing_components:
            raise ValueError(
                f"full 14-component input vector required; missing={missing_components}"
            )
        values = {
            "expedited_key": key,
            "key_hash": cache_v2_key_hash(inputs),
            "canonical_vector": vector,
            "batch_id": int(batch_id),
            "shuffle_rank": int(shuffle_rank),
            "shard": int(shard),
            "temperature_C": _float(inputs["temperature_C"]),
            "temperature_C_repr": _repr(inputs["temperature_C"]),
            "composition_kg_json": _json(inputs["composition_kg"]),
            "intended_fO2_log": _float(intended_fO2_log),
            "intended_fO2_log_repr": _repr(intended_fO2_log),
            "kress91_fixed_ferric_fraction": _float(
                inputs.get("kress91_fixed_ferric_fraction")
            ),
            "kress91_fixed_ferric_fraction_repr": _repr(
                inputs.get("kress91_fixed_ferric_fraction")
            ),
            "kress91_partition_provenance_json": _json(
                inputs["kress91_partition_provenance"]
            ),
            "fO2_log": _float(inputs["fO2_log"]),
            "fO2_log_repr": _repr(inputs["fO2_log"]),
            "pressure_bar": _float(inputs["pressure_bar"]),
            "pressure_bar_repr": _repr(inputs["pressure_bar"]),
            "composition_mol_json": _json(composition_mol),
            "composition_mol_by_account_json": _json(
                inputs["composition_mol_by_account"]
            ),
            "composition_total_mol": total_mol,
            "composition_total_mol_repr": repr(total_mol),
            "species_formula_registry_json": registry_json,
            "species_formula_registry_digest": registry_digest,
            "mode": inputs["mode"],
            "subprocess_run_mode": inputs["subprocess_run_mode"],
            "redox_buffer": inputs["redox_buffer"],
            "fO2_offset": _float(inputs["fO2_offset"]),
            "fO2_offset_repr": _repr(inputs["fO2_offset"]),
            "fe3fet_ratio": _float(inputs["Fe3Fet_Liq"]),
            "fe3fet_ratio_repr": _repr(inputs["Fe3Fet_Liq"]),
            "model": inputs["model"],
            "timeout_s": _float(inputs["timeout_s"]),
            "timeout_s_repr": _repr(inputs["timeout_s"]),
            "require_petthermotools": int(bool(inputs["require_petthermotools"])),
            "thermoengine_health_timeout_s": _float(
                inputs["thermoengine_health_timeout_s"]
            ),
            "thermoengine_health_timeout_s_repr": _repr(
                inputs["thermoengine_health_timeout_s"]
            ),
            "finder_min_T_C": _float(inputs["finder_min_T_C"]),
            "finder_max_T_C": _float(inputs["finder_max_T_C"]),
            "finder_scan_step_C": _float(inputs["finder_scan_step_C"]),
            "finder_tolerance_C": _float(inputs["finder_tolerance_C"]),
            "finder_solid_epsilon": _float(inputs["finder_solid_epsilon"]),
            "finder_liquid_epsilon": _float(inputs["finder_liquid_epsilon"]),
            "finder_monotonicity_tolerance": _float(
                inputs["finder_monotonicity_tolerance"]
            ),
            "finder_monotone_smoothing_max": _float(
                inputs["finder_monotone_smoothing_max"]
            ),
            "finder_max_bisection_iterations": inputs[
                "finder_max_bisection_iterations"
            ],
            "created_at": utc_now(),
        }
        for component in COMPONENT_FIELDS:
            value = _float(composition_mol[component])
            values[f"component_{component}_mol"] = value
            values[f"component_{component}_mol_repr"] = _repr(value)
        return values

    def write_result(
        self,
        grid_key_id: int,
        output: Mapping[str, Any],
    ) -> bool:
        output = self.contain_cache_v2_unknown_dictionary(output)
        if output.get("failure_reason_code") == "cache_v2_unknown_backend":
            contained_backend = self.backend_name
            if contained_backend is None:
                raw_format = str(output.get("raw_payload_format") or "")
                contained_backend = (
                    CacheV2GridBackend.THERMOENGINE.value
                    if raw_format.startswith("thermoengine-")
                    else CacheV2GridBackend.SUBPROCESS.value
                )
            output = {**output, "engine_mode": contained_backend}
        output_mode = str(output["engine_mode"])
        if self.backend_name is not None:
            expected_mode = self._engine_mode_for_backend(self.backend_name)
            if output_mode != expected_mode:
                raise ValueError(
                    "grid cache engine blend refused: "
                    f"writer backend={self.backend_name!r} expects "
                    f"engine_mode={expected_mode!r}, got {output_mode!r}"
                )
        output_backend_name = (
            "subprocess" if output_mode == "subprocess" else output_mode
        )

        row = self.connection.execute(
            "SELECT id, expedited_key FROM grid_keys WHERE id = ?",
            (int(grid_key_id),),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"grid key is not materialized: {grid_key_id}")

        output_values = self._output_values(output)
        output_values.update(
            {
                "grid_key_id": int(row["id"]),
                "expedited_key": str(row["expedited_key"]),
                "engine_epoch": self.engine_epoch,
            }
        )
        write_section = self._begin_write_section("grid_key_claim_result")
        try:
            # The scan must share the BEGIN IMMEDIATE section with the insert:
            # otherwise opposite-engine writers can both observe an empty DB.
            self._assert_backend_not_blended(output_backend_name)
            existing = self.connection.execute(
                "SELECT 1 FROM alphamelts_outputs "
                "WHERE expedited_key = ? AND engine_epoch = ?",
                (str(row["expedited_key"]), self.engine_epoch),
            ).fetchone()
            if existing is not None:
                self.connection.execute(
                    "DELETE FROM grid_key_claims "
                    "WHERE grid_key_id = ? AND engine_epoch = ? "
                    "AND claim_owner = ?",
                    (int(grid_key_id), self.engine_epoch, self.claim_owner),
                )
                self._finish_write_section(write_section)
                return False
            claim = self.connection.execute(
                "SELECT claim_owner, expires_at_epoch FROM grid_key_claims "
                "WHERE grid_key_id = ? AND engine_epoch = ?",
                (int(grid_key_id), self.engine_epoch),
            ).fetchone()
            if claim is None or (
                str(claim["claim_owner"]) != self.claim_owner
                or float(claim["expires_at_epoch"]) <= time.time()
            ):
                raise RuntimeError(
                    f"grid key {grid_key_id} is not claimed by this writer"
                )
            cursor = self._insert_or_ignore("alphamelts_outputs", output_values)
            self.connection.execute(
                "DELETE FROM grid_key_claims "
                "WHERE grid_key_id = ? AND engine_epoch = ? AND claim_owner = ?",
                (int(grid_key_id), self.engine_epoch, self.claim_owner),
            )
            self._finish_write_section(write_section)
            return cursor.rowcount == 1
        except BaseException:
            self._rollback_write_section(write_section)
            raise

    @staticmethod
    def _cache_v2_unknown_phases(
        generic: Mapping[str, Any],
        alphamelts: Mapping[str, Any] | None = None,
    ) -> tuple[str, ...]:
        allowed_phases = set(CACHE_V2_PHASE_DICTIONARY)
        observed_phases: set[str] = set()
        observed_phases.update(
            str(value) for value in generic.get("phases_present") or ()
        )
        for field in (
            "phase_masses_kg",
            "phase_compositions",
            "phase_thermo",
            "chem_potentials",
            "phase_affinities",
        ):
            observed_phases.update(
                str(value) for value in dict(generic.get(field) or {})
            )
        for instance in generic.get("phase_instances") or ():
            phase = dict(instance).get("phase")
            if phase is not None:
                observed_phases.add(str(phase))
        alpha = dict(alphamelts or {})
        observed_phases.update(
            str(value) for value in alpha.get("phases_present") or ()
        )
        for field in ("phase_masses_kg", "phase_modes_wt_pct"):
            observed_phases.update(
                str(value) for value in dict(alpha.get(field) or {})
            )
        return tuple(sorted(observed_phases - allowed_phases))

    @staticmethod
    def _cache_v2_unknown_species(
        generic: Mapping[str, Any],
        alphamelts: Mapping[str, Any] | None = None,
    ) -> tuple[str, ...]:
        allowed_species = set(_cache_v2_species_dictionary())
        instance_formula_tokens = {
            str(dict(instance).get("instance_id")): str(
                dict(instance).get("formula_or_endmember_token")
            )
            for instance in generic.get("phase_instances") or ()
            if dict(instance).get("instance_id")
            and dict(instance).get("formula_or_endmember_token")
        }
        observed_species: set[str] = set()
        for field in (
            "liquid_composition_wt_pct",
            "solid_composition_wt_pct",
            "bulk_composition_wt_pct",
            "vapor_pressures_Pa",
            "vapor_pressures_source",
        ):
            observed_species.update(
                str(value) for value in dict(generic.get(field) or {})
            )
        for field in ("phase_species_mol", "phase_species_kg"):
            for instance_id, species_values in dict(
                generic.get(field) or {}
            ).items():
                for value in dict(species_values or {}):
                    species = str(value)
                    if species in allowed_species:
                        continue
                    if instance_formula_tokens.get(str(instance_id)) == species:
                        continue
                    observed_species.add(species)
        allowed_activity_labels = (
            allowed_species | set(CACHE_V2_THERMOENGINE_LIQUID_ENDMEMBERS)
        )
        observed_species.update(
            set(
                str(value)
                for value in dict(generic.get("activity_coefficients") or {})
            )
            - allowed_activity_labels
        )
        alpha = dict(alphamelts or {})
        observed_species.update(
            str(value)
            for value in dict(alpha.get("liquid_composition_wt_pct") or {})
            if str(value) not in allowed_species
        )
        observed_species.update(
            set(
                str(value)
                for value in dict(alpha.get("activity_coefficients") or {})
            )
            - allowed_activity_labels
        )
        return tuple(sorted(observed_species - allowed_species))

    @staticmethod
    def _cache_v2_unknown_thermoengine_liquid_endmembers(
        thermoengine: Mapping[str, Any],
    ) -> tuple[str, ...]:
        observed = {
            str(value)
            for value in dict(thermoengine.get("liquid_activities") or {})
        }
        return tuple(
            sorted(observed - set(CACHE_V2_THERMOENGINE_LIQUID_ENDMEMBERS))
        )

    @classmethod
    def _cache_v2_dictionary_gap(
        cls,
        output: Mapping[str, Any],
    ) -> tuple[str, tuple[str, ...]] | None:
        generic = dict(output.get("generic") or {})
        thermoengine = dict(output.get("thermoengine") or {})
        alphamelts = dict(output.get("alphamelts") or {})
        backend = str(output.get("engine_mode"))
        unknown_backend = (
            ()
            if backend in {item.value for item in CacheV2GridBackend}
            else (backend,)
        )
        checks = (
            ("backend", unknown_backend),
            ("phase", cls._cache_v2_unknown_phases(generic, alphamelts)),
            ("species", cls._cache_v2_unknown_species(generic, alphamelts)),
            (
                "thermoengine_liquid_endmember",
                cls._cache_v2_unknown_thermoengine_liquid_endmembers(thermoengine),
            ),
        )
        return next(((kind, labels) for kind, labels in checks if labels), None)

    @classmethod
    def contain_cache_v2_unknown_dictionary(
        cls,
        output: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Convert a dictionary gap into one fail-closed point result."""
        contained = dict(output)
        if str(contained["status"]) != "ok":
            return contained
        gap = cls._cache_v2_dictionary_gap(contained)
        if gap is None:
            return contained

        kind, unknown_values = gap
        labels = ", ".join(repr(label) for label in unknown_values)
        reason = f"cache_v2_unknown_{kind}"
        message = f"cache_v2 unknown {kind} values refused: {labels}"
        contained.update(
            {
                "status": "error",
                "status_kind": "failure",
                "refusal_reason": reason,
                "failure_reason_code": reason,
                "failure_message": message[:CACHE_V2_FAILURE_MESSAGE_MAX_LENGTH],
                # Do not persist scientific values from an output whose
                # vocabulary failed the closed dictionary contract.
                "generic": {},
                "thermoengine": {},
                "alphamelts": {},
                "finder": {},
            }
        )
        return contained

    @classmethod
    def contain_cache_v2_unknown_phase(
        cls,
        output: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Backward-compatible name for the all-dictionary containment gate."""
        return cls.contain_cache_v2_unknown_dictionary(output)

    def _output_values(self, output: Mapping[str, Any]) -> dict[str, Any]:
        output = self.contain_cache_v2_unknown_dictionary(output)
        generic = dict(output.get("generic") or {})
        thermoengine = dict(output.get("thermoengine") or {})
        alpha = dict(output.get("alphamelts") or {})
        finder = dict(output.get("finder") or {})
        if str(output["status"]) == "ok":
            self._validate_cache_v2_vapor_maps(generic)
        values = {
            "status": str(output["status"]),
            "status_kind": str(output["status_kind"]),
            "refusal_reason": output.get("refusal_reason"),
            "failure_reason_code": output.get("failure_reason_code"),
            "failure_message": output.get("failure_message"),
            "raw_payload": str(output["raw_payload"]),
            "raw_payload_format": str(output["raw_payload_format"]),
            "timing_s": _float(output["timing_s"]),
            "timing_s_repr": _repr(output["timing_s"]),
            "engine_version": str(output["engine_version"]),
            "engine_mode": str(output["engine_mode"]),
            "engine_model": str(output["engine_model"]),
            "run_mode": output.get("run_mode"),
            "applied_timeout_s": _float(output.get("applied_timeout_s")),
            "applied_timeout_s_repr": _repr(output.get("applied_timeout_s")),
            "native_input_json": _json(output.get("native_input")),
            "created_at": str(output.get("created_at") or utc_now()),
            "host": str(output.get("host") or socket.gethostname()),
            "source_host": output.get("source_host"),
            "source_row_id": output.get("source_row_id"),
            "generic_temperature_C": _float(generic.get("temperature_C")),
            "generic_temperature_C_repr": _repr(generic.get("temperature_C")),
            "generic_requested_temperature_C": _float(
                generic.get("requested_temperature_C")
            ),
            "generic_requested_temperature_C_repr": _repr(
                generic.get("requested_temperature_C")
            ),
            "generic_pressure_bar": _float(generic.get("pressure_bar")),
            "generic_pressure_bar_repr": _repr(generic.get("pressure_bar")),
            "generic_phases_present_json": _json(generic.get("phases_present")),
            "generic_phase_masses_kg_json": _json(generic.get("phase_masses_kg")),
            "generic_phase_species_mol_json": _json(generic.get("phase_species_mol")),
            "generic_phase_species_kg_json": _json(generic.get("phase_species_kg")),
            "generic_phase_instances_json": _json(generic.get("phase_instances")),
            "generic_phase_compositions_json": _json(generic.get("phase_compositions")),
            "generic_liquid_fraction": _float(generic.get("liquid_fraction")),
            "generic_liquid_fraction_repr": _repr(generic.get("liquid_fraction")),
            "generic_phase_assemblage_available": (
                None
                if generic.get("phase_assemblage_available") is None
                else int(bool(generic["phase_assemblage_available"]))
            ),
            "generic_liquid_composition_wt_pct_json": _json(
                generic.get("liquid_composition_wt_pct")
            ),
            "generic_liquid_viscosity_Pa_s": _float(
                generic.get("liquid_viscosity_Pa_s")
            ),
            "generic_liquid_viscosity_Pa_s_repr": _repr(
                generic.get("liquid_viscosity_Pa_s")
            ),
            "generic_liquid_density_kg_m3": _float(
                generic.get("liquid_density_kg_m3")
            ),
            "generic_liquid_density_kg_m3_repr": _repr(
                generic.get("liquid_density_kg_m3")
            ),
            "generic_system_enthalpy": _float(generic.get("system_enthalpy")),
            "generic_system_enthalpy_repr": _repr(generic.get("system_enthalpy")),
            "generic_system_entropy": _float(generic.get("system_entropy")),
            "generic_system_entropy_repr": _repr(generic.get("system_entropy")),
            "generic_system_volume": _float(generic.get("system_volume")),
            "generic_system_volume_repr": _repr(generic.get("system_volume")),
            "generic_system_heat_capacity_Cp": _float(
                generic.get("system_heat_capacity_Cp")
            ),
            "generic_system_heat_capacity_Cp_repr": _repr(
                generic.get("system_heat_capacity_Cp")
            ),
            "generic_system_dVdP": _float(generic.get("system_dVdP")),
            "generic_system_dVdP_repr": _repr(generic.get("system_dVdP")),
            "generic_system_dVdT": _float(generic.get("system_dVdT")),
            "generic_system_dVdT_repr": _repr(generic.get("system_dVdT")),
            "generic_system_fO2_delta_QFM": _float(
                generic.get("system_fO2_delta_QFM")
            ),
            "generic_system_fO2_delta_QFM_repr": _repr(
                generic.get("system_fO2_delta_QFM")
            ),
            "generic_system_solid_density_rhos": _float(
                generic.get("system_solid_density_rhos")
            ),
            "generic_system_solid_density_rhos_repr": _repr(
                generic.get("system_solid_density_rhos")
            ),
            "generic_system_phi": _float(generic.get("system_phi")),
            "generic_system_phi_repr": _repr(generic.get("system_phi")),
            "generic_system_chisqr": _float(generic.get("system_chisqr")),
            "generic_system_chisqr_repr": _repr(generic.get("system_chisqr")),
            "generic_phase_thermo_json": _json(generic.get("phase_thermo")),
            "generic_chem_potentials_json": _json(
                generic.get("chem_potentials")
            ),
            "generic_phase_affinities_json": _json(
                generic.get("phase_affinities")
            ),
            "generic_solid_composition_wt_pct_json": _json(
                generic.get("solid_composition_wt_pct")
            ),
            "generic_bulk_composition_wt_pct_json": _json(
                generic.get("bulk_composition_wt_pct")
            ),
            "generic_vapor_pressures_Pa_json": _json(generic.get("vapor_pressures_Pa")),
            "generic_vapor_pressures_source_json": _json(
                generic.get("vapor_pressures_source")
            ),
            "generic_activity_coefficients_json": _json(
                generic.get("activity_coefficients")
            ),
            "generic_fO2_log": _float(generic.get("fO2_log")),
            "generic_fO2_log_repr": _repr(generic.get("fO2_log")),
            "generic_warnings_json": _json(generic.get("warnings")),
            "generic_ledger_transition_json": _json(generic.get("ledger_transition")),
            "generic_status": generic.get("status"),
            "generic_sulfur_saturation_json": _json(generic.get("sulfur_saturation")),
            "generic_liquidus_T_C": _float(generic.get("liquidus_T_C")),
            "generic_liquidus_T_C_repr": _repr(generic.get("liquidus_T_C")),
            "generic_diagnostics_json": _json(generic.get("diagnostics")),
            "te_liquid_activities_json": _json(
                thermoengine.get("liquid_activities")
            ),
            "te_system_dVdP_m3_bar": _float(
                thermoengine.get("system_dVdP_m3_bar")
            ),
            "te_system_dVdP_m3_bar_repr": _repr(
                thermoengine.get("system_dVdP_m3_bar")
            ),
            "te_system_dVdT_m3_K": _float(
                thermoengine.get("system_dVdT_m3_K")
            ),
            "te_system_dVdT_m3_K_repr": _repr(
                thermoengine.get("system_dVdT_m3_K")
            ),
            "te_solver_status": thermoengine.get("solver_status"),
            "te_solver_converged": (
                None
                if thermoengine.get("solver_converged") is None
                else int(bool(thermoengine["solver_converged"]))
            ),
            "te_solver_iterations": thermoengine.get("solver_iterations"),
            "te_solver_iterations_available": (
                None
                if thermoengine.get("solver_iterations_available") is None
                else int(bool(thermoengine["solver_iterations_available"]))
            ),
            "te_fO2_solve_count": thermoengine.get("fO2_solve_count"),
            "te_phase_universe_size": thermoengine.get("phase_universe_size"),
            "alpha_activity_coefficients_json": _json(alpha.get("activity_coefficients")),
            "alpha_applied_fe3fet": _float(alpha.get("applied_fe3fet")),
            "alpha_applied_fe3fet_repr": _repr(alpha.get("applied_fe3fet")),
            "alpha_backend_diagnostics_json": _json(alpha.get("backend_diagnostics")),
            "alpha_backend_status": alpha.get("backend_status"),
            "alpha_backend_status_reason": alpha.get("backend_status_reason"),
            "alpha_backend_warnings_json": _json(alpha.get("backend_warnings")),
            "alpha_engine_version": alpha.get("engine_version"),
            "alpha_fO2_log": _float(alpha.get("fO2_log")),
            "alpha_fO2_log_repr": _repr(alpha.get("fO2_log")),
            "alpha_fe_redox_policy": alpha.get("fe_redox_policy"),
            "alpha_intrinsic_fO2_log": _float(alpha.get("intrinsic_fO2_log")),
            "alpha_intrinsic_fO2_log_repr": _repr(alpha.get("intrinsic_fO2_log")),
            "alpha_liquid_composition_wt_pct_json": _json(
                alpha.get("liquid_composition_wt_pct")
            ),
            "alpha_liquid_fraction": _float(alpha.get("liquid_fraction")),
            "alpha_liquid_fraction_repr": _repr(alpha.get("liquid_fraction")),
            "alpha_liquid_fraction_path_json": _json(alpha.get("liquid_fraction_path")),
            "alpha_liquidus_T_C": _float(alpha.get("liquidus_T_C")),
            "alpha_liquidus_T_C_repr": _repr(alpha.get("liquidus_T_C")),
            "alpha_liquidus_T_K": _float(alpha.get("liquidus_T_K")),
            "alpha_liquidus_T_K_repr": _repr(alpha.get("liquidus_T_K")),
            "alpha_mode": alpha.get("mode"),
            "alpha_phase_masses_kg_json": _json(alpha.get("phase_masses_kg")),
            "alpha_phase_modes_wt_pct_json": _json(alpha.get("phase_modes_wt_pct")),
            "alpha_phases_present_json": _json(alpha.get("phases_present")),
            "alpha_solidus_T_C": _float(alpha.get("solidus_T_C")),
            "alpha_solidus_T_C_repr": _repr(alpha.get("solidus_T_C")),
            "finder_liquidus_T_C": _float(finder.get("liquidus_T_C")),
            "finder_liquidus_T_K": _float(finder.get("liquidus_T_K")),
            "finder_solidus_T_C": _float(finder.get("solidus_T_C")),
            "finder_liquid_fraction": _float(finder.get("liquid_fraction")),
            "finder_status": finder.get("status"),
            "finder_warnings_json": _json(finder.get("warnings")),
            "finder_diagnostics_json": _json(finder.get("diagnostics")),
            "finder_iterations": finder.get("iterations"),
            "finder_samples_json": _json(finder.get("samples")),
            "finder_sample_temperature_C_json": _json(
                finder.get("sample_temperature_C")
            ),
            "finder_sample_frac_M_json": _json(finder.get("sample_frac_M")),
            "curve_source": finder.get("curve_source"),
            "curve_solidus_T_C": _float(finder.get("curve_solidus_T_C")),
            "curve_liquidus_T_C": _float(finder.get("curve_liquidus_T_C")),
            "curve_path_temperature_C_json": _json(
                finder.get("curve_path_temperature_C")
            ),
            "curve_path_liquid_fraction_json": _json(
                finder.get("curve_path_liquid_fraction")
            ),
        }
        return values

    @staticmethod
    def _validate_cache_v2_vapor_maps(
        generic: Mapping[str, Any],
    ) -> None:
        liquid_fraction = float(generic.get("liquid_fraction") or 0.0)
        pressures = dict(generic.get("vapor_pressures_Pa") or {})
        sources = dict(generic.get("vapor_pressures_source") or {})
        if liquid_fraction > 0.0 and (
            not pressures or set(pressures) != set(sources)
        ):
            raise ValueError(
                "cache_v2 positive-liquid vapor map must be non-empty and sourced"
            )

    def _insert_or_ignore(
        self, table: str, values: Mapping[str, Any]
    ) -> sqlite3.Cursor:
        columns = tuple(values)
        placeholders = ",".join("?" for _ in columns)
        quoted = ",".join(f'"{name}"' for name in columns)
        return self.connection.execute(
            f'INSERT OR IGNORE INTO "{table}" ({quoted}) VALUES ({placeholders})',
            tuple(values[name] for name in columns),
        )

    def counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT status_kind, COUNT(*) FROM alphamelts_outputs "
            "WHERE engine_epoch = ? GROUP BY status_kind",
            (self.engine_epoch,),
        )
        counts = {"success": 0, "refusal": 0, "failure": 0}
        for kind, count in rows:
            counts[str(kind)] = int(count)
        counts["total"] = sum(counts.values())
        return counts

    def sample_row(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT o.id, o.expedited_key, h.temperature_C, h.pressure_bar, "
            "h.intended_fO2_log, h.kress91_fixed_ferric_fraction, "
            "CASE WHEN o.engine_mode = 'thermoengine' THEN NULL "
            "ELSE h.fO2_log END AS adapter_fO2_log_argument, "
            "o.generic_fO2_log AS solved_fO2_log, "
            "o.status, o.status_kind, o.engine_version, "
            "o.timing_s, o.generic_phases_present_json "
            "FROM alphamelts_outputs o JOIN grid_keys h ON h.id = o.grid_key_id "
            "WHERE o.engine_epoch = ? ORDER BY o.id DESC LIMIT 1",
            (self.engine_epoch,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["phases_present"] = json.loads(
            result.pop("generic_phases_present_json") or "[]"
        )
        return result

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self._release_owned_claims()
        self.connection.commit()
        self.connection.close()

    def _release_owned_claims(self) -> None:
        self.connection.execute(
            "DELETE FROM grid_key_claims WHERE claim_owner = ?",
            (self.claim_owner,),
        )

    def __enter__(self) -> "GridCacheWriter":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if exc_type is None:
            self._release_owned_claims()
            self.connection.commit()
        else:
            self.connection.rollback()
            self._release_owned_claims()
            self.connection.commit()
        self.connection.close()


def table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
