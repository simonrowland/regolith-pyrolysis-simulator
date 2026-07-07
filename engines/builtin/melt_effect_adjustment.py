"""Residual-contaminant strip → adjust → warn layer (chunks H2/H3 / A2-adj).

Pure, stateless, no ledger writes. The adjustment annotates MELTS results with
provenance; it never silently retunes certified values.
"""

from __future__ import annotations

from copy import deepcopy
import math
import re
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from engines.domain_reason import OutOfDomainReason, reason_value
from engines.alphamelts.domain import (
    AlphaMELTSDomainGate,
    _is_non_oxide_species_name,
)
from engines.magemin.domain import MAGEMinDomainGate
from simulator.state import OXIDE_SPECIES

_OXIDE_SET = frozenset(OXIDE_SPECIES)


@dataclass(frozen=True)
class PropertyThreshold:
    metric: str
    warning: float
    notice: float
    absolute_warning_floor: float | None = None
    absolute_notice_floor: float | None = None
    basis: str = "absolute"


PROPERTY_THRESHOLD_TABLE: dict[str, PropertyThreshold] = {
    "liquidus": PropertyThreshold(
        metric="delta_T_frac_of_T_in_C",
        warning=2.0,
        notice=0.5,
        absolute_warning_floor=25.0,
        absolute_notice_floor=7.0,
        basis="celsius_relative_percent_with_floor",
    ),
    "redox": PropertyThreshold(
        metric="delta_log10_fO2",
        warning=1.0,
        notice=0.3,
        basis="absolute_log10_fO2",
    ),
    "phase": PropertyThreshold(
        metric="phase_topology_or_delta_absolute_fraction",
        warning=0.02,
        notice=0.005,
        basis="topological_or_absolute_mass_fraction",
    ),
    "bulk_sum_closure": PropertyThreshold(
        metric="dropped_component_mass_fraction",
        warning=0.02,
        notice=0.005,
        basis="absolute_mass_fraction",
    ),
}

EFFECT_TABLE_VERSION = "2026-06-14-refine2-newcompute-v3"
PHASE_PRESENCE_FLOOR_FRACTION = 0.001
MAGEMIN_IG_IGAD_BULK_SUM_DROPPED_OXIDES = ("CoO", "MnO", "NiO", "P2O5")
_MAGEMIN_BULK_SUM_DATABASES = frozenset({"ig", "igad"})
_VERDICT_B_HARD_FAIL_BACKEND_STATUSES = frozenset(
    {"unavailable", "out_of_domain", "not_converged"}
)
_BACKEND_STATUS_PRECEDENCE = ("unavailable", "out_of_domain", "not_converged")

# Per-contaminant effect rows sourced from CONTAMINANT-WARNING-DOC + evidence-E5.
# Intervals are literature-imported, NOT simulator-measured.
EFFECT_ROWS: dict[str, dict[str, Any]] = {
    "cl_halide": {
        "contaminant_group": "Cl/NaCl/KCl",
        "species_aliases": ("Cl", "NaCl", "KCl", "CaCl2", "MgCl2"),
        "properties": {
            "liquidus": {
                "mode": "delta_T_per_wt_pct",
                "coefficient_C_per_wt_pct": -100.0,
                "grounded": True,
                "source": "Filiberto & Treiman 2009; LPSC 2011 #2064",
            },
        },
    },
    "fluoride": {
        "contaminant_group": "F/NaF",
        "species_aliases": ("F", "NaF", "KF", "CaF2", "MgF2"),
        "properties": {
            "liquidus": {
                "mode": "delta_T_interval_per_wt_pct",
                "interval_C_per_wt_pct": (-200.0, -50.0),
                "grounded": False,
                "source": "Filiberto et al. 2010 EOS; LPSC 2011 #2064",
            },
        },
    },
    "sulfide": {
        "contaminant_group": "S/FeS/CaS",
        "species_aliases": (
            "S",
            "S2",
            "FeS",
            "FeS_troilite",
            "troilite",
            "pyrrhotite",
            "FeS2",
            "CaS",
            "oldhamite",
            "MgS",
            "MnS",
            "NiS",
        ),
        "properties": {
            "phase": {
                "mode": "delta_fraction_interval_per_wt_pct",
                "interval_per_wt_pct": (0.05, 0.20),
                "grounded": False,
                "source": "Jugo et al. 2010 Nat. Geosci. 3:521-525 (SCSS)",
            },
        },
    },
    "sulfate_proxy": {
        "contaminant_group": "SO3/sulfate carrier",
        "species_aliases": ("SO3", "SO2"),
        "properties": {
            "phase": {
                "mode": "delta_fraction_interval_per_wt_pct",
                "interval_per_wt_pct": (0.02, 0.10),
                "grounded": False,
                "source": "Jugo SCSS; sulfate clearance routing",
            },
        },
    },
    "residual_carbon": {
        "contaminant_group": "residual C",
        "species_aliases": ("C", "graphite", "carbonaceous_organic"),
        "properties": {
            "redox": {
                "mode": "delta_log10_fO2_interval_per_wt_pct",
                "interval_per_wt_pct": (0.10, 0.50),
                "grounded": False,
                "source": "Brooker et al. 2014; Sephton 2004",
            },
        },
    },
    "p2o5": {
        "contaminant_group": "P2O5",
        "species_aliases": ("P2O5",),
        "stripped": False,
        "properties": {
            "phase": {
                "mode": "phase_topology_presence",
                "phase_aliases": (
                    "apatite",
                    "fluorapatite",
                    "chlorapatite",
                    "hydroxyapatite",
                ),
                "modeled_engines": ("alphamelts",),
                "grounded": True,
                "source": "Watson 1979; AlphaMELTS phase assemblage read",
            },
            "liquidus": {
                "mode": "delta_T_interval_per_wt_pct",
                "interval_C_per_wt_pct": (-15.0, -5.0),
                "grounded": False,
                "source": "Watson 1979; Harrison 1981",
            },
        },
    },
}

NON_OXIDE_ANALYTICAL_MODEL_VERSION = "2026-07-06-t139-nonoxide-warn-v1"
_ANALYTICAL_EVIDENCE_CLASS = "internal-analytical"

# WARN-tier model registry. It is diagnostic-only: no ledger authority, no
# certification authority, and no Stage-0 bucket changes.
WARN_TIER_ANALYTICAL_MODELS: dict[str, dict[str, Any]] = {
    "cl_halide": {
        "model_class": "halide_salt_volatility",
        "tier": "WARN",
        "evidence_class": _ANALYTICAL_EVIDENCE_CLASS,
        "engine_coverage": {
            "alphamelts": "none_for_halide_salts",
            "magemin_ig_igad": "none_for_halide_salts",
            "vaporock": "none_for_halide_salts",
        },
        "certification": {
            "eligible": False,
            "reason": "internal-analytical halide/salt model; denylisted from certification",
        },
        "ledger_authority": False,
        "stage0_contract": "inventory_bucket_unchanged; stripped before melt engine",
        "speciation": {
            "default_forms": ("NaCl", "KCl", "CaCl2", "MgCl2"),
            "routing": "terminal.stage0_chloride_salt_phase or evaporation",
        },
        "forms": {
            "liquidus_delta": {
                "equation": "delta_T_C = -100.0 * Cl_wt_pct",
                "coefficient_C_per_wt_pct": -100.0,
                "validity": "Mars/basaltic dissolved Cl warning anchor; not a universal liquidus law",
                "error": "WARN-tier point; keep as diagnostic until per-composition engine sweep",
                "citation": "Filiberto & Treiman 2009 Chemical Geology; LPSC 2011 abstract 2064",
            },
            "vapor_pressure": {
                "NaCl": {
                    "equation": "log10(P_Pa) = A - B / (T_K + C)",
                    "A": 10.07184,
                    "B": 8388.497,
                    "C": -82.638,
                    "valid_range_K": (1138.0, 1738.0),
                    "boiling_point_C": 1465.0,
                    "error": "source-equation fit; normal boiling point check ~1 atm",
                    "citation": "Stull 1947 DOI 10.1021/ie50448a022; NIST Chemistry WebBook SRD 69 C7647145",
                },
                "KCl": {
                    "equation": "log10(P_Pa) = A - B / (T_K + C)",
                    "A": 10.185900,
                    "B": 8774.500,
                    "C": 0.0,
                    "valid_range_K": (1094.0, 1680.0),
                    "boiling_point_C": 1420.0,
                    "error": "source-equation fit; runtime row remains separate",
                    "citation": "Stull 1947 DOI 10.1021/ie50448a022; NIST Chemistry WebBook SRD 69 C7447407",
                },
            },
        },
    },
    "fluoride": {
        "model_class": "fluoride_salt_interval",
        "tier": "WARN",
        "evidence_class": _ANALYTICAL_EVIDENCE_CLASS,
        "engine_coverage": {
            "alphamelts": "none_for_fluoride_salts",
            "magemin_ig_igad": "none_for_fluoride_salts",
            "vaporock": "none_for_fluoride_salts",
        },
        "certification": {
            "eligible": False,
            "reason": "interval-only fluoride row; no certified vapor-pressure coefficient",
        },
        "ledger_authority": False,
        "stage0_contract": "inventory_bucket_unchanged; stripped before melt engine",
        "speciation": {
            "default_forms": ("NaF", "KF", "CaF2", "MgF2"),
            "routing": "terminal.stage0_chloride_salt_phase or refractory rump by carrier",
        },
        "forms": {
            "liquidus_interval": {
                "equation": "delta_T_C in [-200, -50] * F_wt_pct",
                "interval_C_per_wt_pct": (-200.0, -50.0),
                "validity": "basaltic halogen liquidus warning interval",
                "error": "interval half-width drives WARN-tier flag",
                "citation": "Filiberto et al. 2010 EOS; LPSC 2011 abstract 2064",
            },
            "vapor_pressure": {
                "NaF": {
                    "equation": "no certified point",
                    "valid_range_K": (1200.0, 2000.0),
                    "boiling_point_C": 1704.0,
                    "error": "interval_required; runtime Antoine absent by design",
                    "citation": "CRC boiling point carried in data/vapor_pressures.yaml",
                },
            },
        },
    },
    "sulfide": {
        "model_class": "sulfide_matte_speciation",
        "tier": "WARN",
        "evidence_class": _ANALYTICAL_EVIDENCE_CLASS,
        "engine_coverage": {
            "alphamelts": "none_for_raw_sulfide_matte",
            "magemin_ig_igad": "none_for_raw_sulfide_matte",
            "vaporock": "none_for_sulfide_matte",
        },
        "certification": {
            "eligible": False,
            "reason": "pure-FeS/JANAF and SCSS fallbacks are diagnostics, not melt-engine authority",
        },
        "ledger_authority": False,
        "stage0_contract": "sulfide_matte bucket unchanged; no implicit oxide conversion",
        "speciation": {
            "default_forms": ("FeS_like", "NiS_like", "CaS_like", "MgMnS_like", "FeS2_like"),
            "routing": "terminal.stage0_sulfide_matte; FeS2 excess sulfur as Sx_vapor_unspeciated diagnostic",
            "matte_mass_balance": {
                "FeS_molar_mass_g_mol": 87.910,
                "S_mass_fraction_in_FeS": 0.36475,
                "Fe_mass_fraction_in_FeS": 0.63525,
                "kg_FeS_per_kg_excess_S": 2.7416,
            },
        },
        "forms": {
            "phase_interval": {
                "equation": "delta_phase_fraction in [0.05, 0.20] * sulfide_wt_pct",
                "interval_per_wt_pct": (0.05, 0.20),
                "validity": "SCSS fallback only; prefer PySulfSat when installed and in range",
                "error": "composition/redox/T/P dependent; no certification",
                "citation": "Wieser & Gleeson 2023 DOI 10.30909/vol.06.01.107127; O'Neill & Mavrogenes 2002 DOI 10.1093/petrology/43.6.1049",
            },
            "FeS_solid_vapor": {
                "equation": "log10(P_FeS_bar) = A - B / T_K",
                "A": 7.79795,
                "B": 23413.3,
                "valid_range_K": (900.0, 1400.0),
                "error": "fit residual <=0.018 dex; use +/-0.3 dex for mixed matte activity",
                "citation": "NIST-JANAF SRD 13 DOI 10.18434/T42S31 FeS(cr) Fe-023 + FeS(g) Fe-026",
            },
            "FeS_liquid_vapor": {
                "equation": "log10(P_FeS_bar) = A - B / T_K",
                "A": 5.89363,
                "B": 20615.4,
                "valid_range_K": (1500.0, 2600.0),
                "error": "fit residual <=0.030 dex; use +/-0.3 dex for mixed matte activity",
                "citation": "NIST-JANAF SRD 13 DOI 10.18434/T42S31 FeS(l) Fe-024 + FeS(g) Fe-026",
            },
            "FeS_decomposition": {
                "equation": "log10(K_bar_1p5) = A - B / T_K for FeS(l) -> Fe(g) + 0.5 S2(g)",
                "A": 8.61298,
                "B": 26706.4,
                "valid_range_K": (1500.0, 2600.0),
                "error": "fit residual <=0.024 dex; gas speciation/activity dependent",
                "citation": "NIST-JANAF SRD 13 DOI 10.18434/T42S31 FeS(l), Fe(g), S2(g)",
            },
            "FeS_roast": {
                "reaction": "FeS + 1.5 O2 -> FeO + SO2",
                "deltaG_kJ_per_mol": {
                    900: -405.7,
                    1000: -398.0,
                    1100: -390.3,
                    1200: -382.6,
                    1300: -375.0,
                    1400: -367.2,
                    1500: -358.7,
                    1600: -348.8,
                },
                "valid_range_K": (900.0, 1600.0),
                "error": "thermodynamics grounded; kinetics/onset unmodeled",
                "citation": "NIST-JANAF SRD 13 DOI 10.18434/T42S31 FeS, FeO, SO2, O2 tables",
            },
        },
    },
    "sulfate_proxy": {
        "model_class": "sulfate_process_loss_proxy",
        "tier": "WARN",
        "evidence_class": _ANALYTICAL_EVIDENCE_CLASS,
        "engine_coverage": {
            "alphamelts": "none_for_raw_sulfate_foulants",
            "magemin_ig_igad": "none_for_raw_sulfate_foulants",
            "vaporock": "none_for_sulfate_foulants",
        },
        "certification": {
            "eligible": False,
            "reason": "sulfate decomposition proxy; not a melt-engine phase claim",
        },
        "ledger_authority": False,
        "stage0_contract": "sulfate decomposition routing unchanged",
        "speciation": {
            "default_forms": ("SO3", "SO2", "CaSO4", "MgSO4", "FeSO4"),
            "routing": "terminal.offgas plus oxide/rump product per Stage 0 registry",
        },
        "forms": {
            "phase_interval": {
                "equation": "delta_phase_fraction in [0.02, 0.10] * sulfate_wt_pct",
                "interval_per_wt_pct": (0.02, 0.10),
                "validity": "process-clearance warning proxy",
                "error": "interval half-width drives WARN-tier flag",
                "citation": "Jugo sulfur capacity framing; sulfate clearance routing",
            },
        },
    },
    "residual_carbon": {
        "model_class": "elemental_carbon_redox_partition",
        "tier": "WARN",
        "evidence_class": _ANALYTICAL_EVIDENCE_CLASS,
        "engine_coverage": {
            "alphamelts": "none_for_elemental_or_organic_carbon",
            "magemin_ig_igad": "none_for_elemental_or_organic_carbon",
            "vaporock": "none_for_graphite_carbon",
        },
        "certification": {
            "eligible": False,
            "reason": "carbon redox/partition diagnostics are internal-analytical and interval-bounded",
        },
        "ledger_authority": False,
        "stage0_contract": "refractory_carbon/trapped_gasses buckets unchanged",
        "speciation": {
            "default_forms": ("C", "graphite", "carbonaceous_organic", "CO", "CO2"),
            "routing": "partition_carbon; graphite/organic carbon stays out of cleaned_melt",
        },
        "forms": {
            "redox_interval": {
                "equation": "delta_log10_fO2 in [0.10, 0.50] * C_wt_pct",
                "interval_per_wt_pct": (0.10, 0.50),
                "validity": "residual graphite/organic-C redox warning interval",
                "error": "interval half-width drives WARN-tier flag",
                "citation": "Brooker et al. 2014; Sephton et al. 2004 DOI 10.1016/j.gca.2003.08.019",
            },
            "cco_buffer": {
                "equation": "log10(fO2/bar) = -21803/T_K + 4.325 + 0.171*(P_bar - 1)/T_K",
                "valid_range_K": (1273.15, 1873.15),
                "error": "CCO reference only; EMOG/EMOD graphite-saturation bounds stay interval-only",
                "citation": "Jakobsson & Oskarsson 1994 DOI 10.1016/0016-7037(94)90442-1; Stagno & Frost 2010 EPSL 300:72-84",
            },
            "graphite_vapor": {
                "equation": "negative result; graphite vapor pressure negligible below ~2500 C for Stage-0 use",
                "validity": "do not use vaporization as default carbon-removal route",
                "error": "qualitative pressure-level bound",
                "citation": "NIST-JANAF carbon graphite thermochemistry; S-E5-4 negative vapor result",
            },
            "burnout_kinetics": {
                "equation": "Avrami-Erofeev n=1.5 scenario-only char burnout",
                "Ea_kJ_per_mol_range": (59.6, 110.66),
                "temperature_validity_C": (400.0, 700.0),
                "error": "air/char scenario only, not default certification",
                "citation": "Guo et al. 2018 RSC Advances low-rank char combustion kinetics",
            },
        },
    },
    "p2o5": {
        "model_class": "phosphate_topology_process_loss",
        "tier": "WARN",
        "evidence_class": _ANALYTICAL_EVIDENCE_CLASS,
        "engine_coverage": {
            "alphamelts": "partial_for_phosphate_phase_topology",
            "magemin_ig_igad": "drops_P2O5_from_bulk_basis",
            "vaporock": "none_for_P_species",
        },
        "certification": {
            "eligible": False,
            "reason": "phase/process-loss diagnostics only; no certified liquidus coefficient",
        },
        "ledger_authority": False,
        "stage0_contract": "P2O5 remains cleaned_melt oxide; do not route as non-oxide",
        "speciation": {
            "default_forms": ("P2O5", "apatite_surrogate", "merrillite_surrogate"),
            "routing": "cleaned_melt phosphate/apatite topology warning",
        },
        "forms": {
            "sweep_band": {
                "P2O5_wt_pct": (0.0, 2.0),
                "Mars_nominal_P2O5_wt_pct": 0.85,
                "apatite_concern_band_wt_pct": (0.5, 1.5),
                "phase_presence_floor_wt_pct": 0.1,
                "validity": "Mars basalt family, CaO 5-9 wt%",
                "error": "topology warning only; liquidus coefficient refused",
                "citation": "Watson 1979 DOI 10.1029/GL006i012p00937; Harrison 1981 EPSL 51:322",
            },
            "CaO_stoichiometry": {
                "beta_TCP_CaO_per_P2O5": 1.1852,
                "apatite_CaO_per_P2O5": 1.3169,
                "merrillite_CaO_per_P2O5": 1.0159,
                "error": "stoichiometry exact; phase surrogate only",
                "citation": "S-E5-2 stoichiometric derivation from CaO/P2O5 molar masses",
            },
            "vacuum_process_loss": {
                "P2O5_retention_vs_Al2O3": (0.57, 0.69),
                "mean_retention": 0.65,
                "sigma_retention": 0.07,
                "validity": "JSC-1 high vacuum, 1425-1580 C, short solar-furnace exposures",
                "error": "process-loss envelope only; not a vapor-pressure coefficient",
                "citation": "Sauerborn 2004 DLR solar-furnace thesis Table 5.3 / Fig. 5.31-5.32",
            },
        },
    },
}


class CertifiedPointRefusedError(ValueError):
    """Raised when a caller requests a certified point on an ungrounded row."""


@dataclass(frozen=True)
class StrippedMassProvenance:
    species: str
    kg: float
    wt_pct_of_total: float
    reason: str


@dataclass(frozen=True)
class StripResult:
    oxide_kg: dict[str, float]
    stripped_kg: dict[str, float]
    total_kg: float
    oxide_wt_pct: dict[str, float]
    provenance: tuple[StrippedMassProvenance, ...]
    stripped_mass_kg: float


@dataclass(frozen=True)
class PropertyPerturbation:
    property: str
    contaminant: str
    effect_row: str
    source: str
    residual_wt_pct: float
    perturbation_before: float | None
    perturbation_after: float | None
    metric: str
    grounded: bool
    correctable: bool
    raw_value: float | None = None
    adjusted_value: float | None = None
    interval: tuple[float, float] | None = None
    metric_basis: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PropertyFlag:
    property: str
    level: str
    contaminant: str
    effect_row: str
    perturbation_before: float | None
    perturbation_after: float | None
    metric: str
    grounded: bool
    correctable: bool
    residual_wt_pct: float
    hour: int
    active: bool = True
    cleared: bool = False
    clear_hour: int | None = None
    noise_floor_status: str = "proposed"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MeltEffectAdjustmentResult:
    effect_table_version: str
    T_in_C: float
    engine: str
    perturbations: tuple[PropertyPerturbation, ...]
    raw_liquidus_C: float | None
    adjusted_liquidus_C: float | None
    adjusted_liquidus_interval_C: tuple[float, float] | None = None
    adjusted_liquidus_provenance: tuple[dict[str, Any], ...] = ()
    adjusted_liquidus_interval_provenance: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerdictAResult:
    flags: tuple[PropertyFlag, ...]
    step_resolved: tuple[dict[str, Any], ...]
    warn_only: bool = True


@dataclass(frozen=True)
class BackendStatusSignal:
    status: str
    reason: str | None = None

    def __str__(self) -> str:
        return self.status


@dataclass(frozen=True)
class VerdictBResult:
    backend_status: str
    backend_status_reason: str | None
    layer_a_state: str
    offending_species: tuple[str, ...]
    stripped_domain_valid: bool
    hard_gate_failed: bool
    stripped_oxide_wt_pct: dict[str, float]
    stripped_mass_provenance: tuple[StrippedMassProvenance, ...]
    domain_warnings: tuple[str, ...]
    engine: str
    contaminant_present_never_crash: bool = True


def is_strippable_non_oxide_residual(species: str) -> bool:
    """True for Cl/F/S/elemental-C residuals; P2O5 and valid oxides stay."""
    key = str(species).strip()
    if key in _OXIDE_SET:
        return False
    if key in {"C", "graphite"}:
        return True
    return _is_non_oxide_species_name(key)


def _species_effect_row(species: str) -> str | None:
    for row_key, row in EFFECT_ROWS.items():
        if row.get("stripped") is False:
            continue
        aliases = row.get("species_aliases", ())
        if species in aliases or species.lower() in {a.lower() for a in aliases}:
            return row_key
    if _is_non_oxide_residual_name(species):
        return "cl_halide" if "Cl" in species or species in {"NaCl", "KCl"} else None
    return None


def _is_non_oxide_residual_name(species: str) -> bool:
    return is_strippable_non_oxide_residual(species)


def strip_non_oxide_residuals(
    cleaned_melt_kg: Mapping[str, float],
) -> StripResult:
    """Strip non-oxide residuals; record provenance; do NOT renormalize oxides."""
    oxide_kg: dict[str, float] = {}
    stripped_kg: dict[str, float] = {}
    total_kg = 0.0

    for species, kg_raw in cleaned_melt_kg.items():
        kg = float(kg_raw or 0.0)
        if kg <= 1e-15:
            continue
        total_kg += kg
        if is_strippable_non_oxide_residual(species):
            stripped_kg[species] = stripped_kg.get(species, 0.0) + kg
        else:
            oxide_kg[species] = oxide_kg.get(species, 0.0) + kg

    provenance: list[StrippedMassProvenance] = []
    stripped_mass = 0.0
    for species, kg in sorted(stripped_kg.items()):
        stripped_mass += kg
        wt = (kg / total_kg * 100.0) if total_kg > 0.0 else 0.0
        provenance.append(
            StrippedMassProvenance(
                species=species,
                kg=kg,
                wt_pct_of_total=wt,
                reason="non_oxide_residual_stripped_before_engine",
            )
        )

    oxide_wt_pct: dict[str, float] = {}
    if total_kg > 0.0:
        for species, kg in sorted(oxide_kg.items()):
            oxide_wt_pct[species] = (kg / total_kg) * 100.0

    return StripResult(
        oxide_kg=dict(oxide_kg),
        stripped_kg=dict(stripped_kg),
        total_kg=total_kg,
        oxide_wt_pct=oxide_wt_pct,
        provenance=tuple(provenance),
        stripped_mass_kg=stripped_mass,
    )


def _oxide_ratios_for_domain_check(
    oxide_wt_pct: Mapping[str, float],
) -> dict[str, float]:
    """Return a 100 wt% oxide-ratio copy for domain checks only."""
    oxide_total = 0.0
    parsed: dict[str, float] = {}
    for species, raw_wt in oxide_wt_pct.items():
        try:
            wt = float(raw_wt)
        except (TypeError, ValueError):
            return dict(oxide_wt_pct)
        if wt != wt or wt in (float("inf"), float("-inf")):
            return dict(oxide_wt_pct)
        parsed[str(species)] = wt
        if wt > 0.0:
            oxide_total += wt
    if oxide_total <= 0.0:
        return dict(oxide_wt_pct)
    return {
        species: (wt / oxide_total * 100.0) if wt > 0.0 else wt
        for species, wt in parsed.items()
    }


def residual_wt_pct_by_species(
    cleaned_melt_kg: Mapping[str, float],
) -> dict[str, float]:
    total = sum(float(v or 0.0) for v in cleaned_melt_kg.values())
    if total <= 0.0:
        return {}
    out: dict[str, float] = {}
    for species, kg in cleaned_melt_kg.items():
        mass = float(kg or 0.0)
        if mass <= 1e-15:
            continue
        if is_strippable_non_oxide_residual(species) or species == "P2O5":
            out[species] = (mass / total) * 100.0
    return out


def _match_effect_row(species: str) -> tuple[str, dict[str, Any]] | None:
    for row_key, row in EFFECT_ROWS.items():
        aliases = row.get("species_aliases", ())
        if species in aliases:
            return row_key, row
        lowered = {a.lower() for a in aliases}
        if species.lower() in lowered:
            return row_key, row
    if species in {"Cl", "NaCl", "KCl"}:
        return "cl_halide", EFFECT_ROWS["cl_halide"]
    if species in {"C", "graphite", "carbonaceous_organic"}:
        return "residual_carbon", EFFECT_ROWS["residual_carbon"]
    if "F" in re.findall(r"[A-Z][a-z]?", species) and species not in _OXIDE_SET:
        return "fluoride", EFFECT_ROWS["fluoride"]
    if species in {
        "S",
        "S2",
        "FeS",
        "FeS_troilite",
        "troilite",
        "pyrrhotite",
        "FeS2",
        "CaS",
        "oldhamite",
        "MgS",
        "MnS",
        "NiS",
    }:
        return "sulfide", EFFECT_ROWS["sulfide"]
    if species in {"SO3", "SO2"}:
        return "sulfate_proxy", EFFECT_ROWS["sulfate_proxy"]
    return None


def _selected_model_forms(
    row_key: str,
    species: str,
    model: Mapping[str, Any],
) -> dict[str, Any]:
    forms = model.get("forms", {}) or {}
    if row_key == "cl_halide":
        vapor_forms = forms.get("vapor_pressure", {})
        if species in vapor_forms:
            return {
                "liquidus_delta": deepcopy(forms.get("liquidus_delta", {})),
                "vapor_pressure": {species: deepcopy(vapor_forms[species])},
            }
    if row_key == "fluoride":
        vapor_forms = forms.get("vapor_pressure", {})
        if species in vapor_forms:
            return {
                "liquidus_interval": deepcopy(forms.get("liquidus_interval", {})),
                "vapor_pressure": {species: deepcopy(vapor_forms[species])},
            }
    return deepcopy(dict(forms))


def _evaluate_analytical_forms(
    forms: Mapping[str, Any],
    *,
    T_K: float | None,
) -> dict[str, Any]:
    if T_K is None:
        return {}
    try:
        temperature = float(T_K)
    except (TypeError, ValueError):
        return {}
    if not math.isfinite(temperature) or temperature <= 0.0:
        return {}

    evaluations: dict[str, Any] = {}
    vapor_forms = forms.get("vapor_pressure")
    if isinstance(vapor_forms, Mapping):
        vapor_eval: dict[str, Any] = {}
        for species, form in vapor_forms.items():
            if not isinstance(form, Mapping):
                continue
            if form.get("equation") != "log10(P_Pa) = A - B / (T_K + C)":
                continue
            A = float(form["A"])
            B = float(form["B"])
            C = float(form["C"])
            log10_P = A - B / (temperature + C)
            vapor_eval[str(species)] = {
                "T_K": temperature,
                "log10_P_Pa": log10_P,
                "P_Pa": 10.0**log10_P,
                "inside_valid_range": _inside_range(
                    temperature,
                    form.get("valid_range_K"),
                ),
            }
        if vapor_eval:
            evaluations["vapor_pressure"] = vapor_eval

    for key in ("FeS_solid_vapor", "FeS_liquid_vapor", "FeS_decomposition"):
        form = forms.get(key)
        if not isinstance(form, Mapping):
            continue
        if "A" not in form or "B" not in form:
            continue
        value = float(form["A"]) - float(form["B"]) / temperature
        output_key = (
            "log10_K_bar_1p5"
            if key == "FeS_decomposition"
            else "log10_P_FeS_bar"
        )
        evaluations[key] = {
            "T_K": temperature,
            output_key: value,
            "inside_valid_range": _inside_range(
                temperature,
                form.get("valid_range_K"),
            ),
        }

    cco = forms.get("cco_buffer")
    if isinstance(cco, Mapping):
        # Keep this as an explicit diagnostic evaluation of the stored formula;
        # callers that need the canonical redox helper still use
        # engines.builtin.cco_redox_buffer.
        log10_fO2 = -21803.0 / temperature + 4.325
        evaluations["cco_buffer"] = {
            "T_K": temperature,
            "pressure_bar": 1.0,
            "log10_fO2_bar": log10_fO2,
            "inside_valid_range": _inside_range(
                temperature,
                cco.get("valid_range_K"),
            ),
        }

    return evaluations


def _inside_range(value: float, bounds: Any) -> bool | None:
    if not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
        return None
    low = float(bounds[0])
    high = float(bounds[1])
    return low <= value <= high


def _analytical_model_metadata(
    row_key: str,
    species: str,
    *,
    T_K: float | None = None,
) -> dict[str, Any]:
    model = WARN_TIER_ANALYTICAL_MODELS.get(row_key)
    if model is None:
        return {}
    selected_forms = _selected_model_forms(row_key, species, model)
    metadata = {
        "analytical_model_version": NON_OXIDE_ANALYTICAL_MODEL_VERSION,
        "analytical_model_class": model["model_class"],
        "analytical_tier": model["tier"],
        "evidence_class": model["evidence_class"],
        "engine_coverage": deepcopy(model["engine_coverage"]),
        "certification": deepcopy(model["certification"]),
        "ledger_authority": bool(model["ledger_authority"]),
        "stage0_contract": model["stage0_contract"],
        "speciation": deepcopy(model["speciation"]),
        "forms": selected_forms,
    }
    evaluations = _evaluate_analytical_forms(selected_forms, T_K=T_K)
    if evaluations:
        metadata["evaluated_at_T_K"] = evaluations
    return metadata


def warn_tier_analytical_diagnostic(
    species: str,
    *,
    T_K: float | None = None,
) -> dict[str, Any] | None:
    """Return the internal-analytical WARN-tier model for a residual species."""
    matched = _match_effect_row(species)
    if matched is None:
        return None
    row_key, _row = matched
    return _analytical_model_metadata(row_key, species, T_K=T_K)


def _engine_coverage_absent_warning(
    row_key: str,
    species: str,
    engine: str,
) -> str | None:
    model = WARN_TIER_ANALYTICAL_MODELS.get(row_key)
    if model is None:
        return None
    coverage = model.get("engine_coverage", {}) or {}
    if row_key == "p2o5" and "alphamelts" in str(engine).lower():
        return None
    if not coverage:
        return None
    return (
        "nonoxide_engine_coverage_absent: "
        f"{species} handled by {model['model_class']} "
        f"({NON_OXIDE_ANALYTICAL_MODEL_VERSION}); "
        "WARN-tier internal-analytical, no ledger authority, no certification"
    )


def _with_analytical_model_metadata(
    pert: PropertyPerturbation | None,
    metadata: Mapping[str, Any],
) -> PropertyPerturbation | None:
    if pert is None or not metadata:
        return pert
    merged = dict(metadata)
    merged.update(dict(pert.metadata))
    return replace(pert, metadata=merged)


def _liquidus_perturbation_pct(delta_T_C: float, T_in_C: float) -> float:
    if T_in_C <= 0.0:
        return abs(delta_T_C)
    return abs(delta_T_C) / T_in_C * 100.0


def _interval_half_width(low: float, high: float) -> float:
    return abs(float(high) - float(low)) / 2.0


def _lookup_result_value(result: Mapping[str, Any] | Any | None, key: str) -> Any:
    if result is None:
        return None
    if isinstance(result, Mapping):
        if key in result:
            return result[key]
        for nested_key in ("diagnostic", "backend_diagnostics"):
            nested = result.get(nested_key)
            if isinstance(nested, Mapping) and key in nested:
                return nested[key]
    return getattr(result, key, None)


def _finite_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return None
    return numeric


def _phase_modes_as_fractions(
    melts_result: Mapping[str, Any] | Any | None,
) -> dict[str, float]:
    modes = _lookup_result_value(melts_result, "phase_modes_wt_pct")
    if modes is None:
        modes = _lookup_result_value(melts_result, "phase_modes_pct")
    if isinstance(modes, Mapping) and modes:
        out: dict[str, float] = {}
        for name, value in modes.items():
            numeric = _finite_float(value)
            if numeric is not None and numeric > 0.0:
                out[str(name)] = numeric / 100.0
        return out

    masses = _lookup_result_value(melts_result, "phase_masses_kg")
    if isinstance(masses, Mapping) and masses:
        positive: dict[str, float] = {}
        for name, value in masses.items():
            numeric = _finite_float(value)
            if numeric is not None and numeric > 0.0:
                positive[str(name)] = numeric
        total = sum(positive.values())
        if total > 0.0:
            return {name: mass / total for name, mass in positive.items()}
    return {}


def _normal_phase_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def _phase_alias_match(name: str, aliases: tuple[str, ...]) -> bool:
    normalized = _normal_phase_name(name)
    return any(_normal_phase_name(alias) in normalized for alias in aliases)


def _engine_matches_any(engine: str, names: tuple[str, ...]) -> bool:
    engine_key = str(engine).lower()
    return any(name.lower() in engine_key for name in names)


def _phase_topology_perturbation(
    *,
    species: str,
    wt_pct: float,
    prop_cfg: Mapping[str, Any],
    row_key: str,
    contaminant_group: str,
    melts_result: Mapping[str, Any] | Any | None,
    engine: str,
) -> PropertyPerturbation | None:
    modeled_engines = tuple(str(e) for e in prop_cfg.get("modeled_engines", ()))
    if modeled_engines and not _engine_matches_any(engine, modeled_engines):
        return None

    aliases = tuple(str(a) for a in prop_cfg.get("phase_aliases", ()))
    phase_modes = _phase_modes_as_fractions(melts_result)
    matches = {
        phase: fraction
        for phase, fraction in phase_modes.items()
        if _phase_alias_match(phase, aliases)
    }
    if not matches:
        return None

    phase, fraction = max(matches.items(), key=lambda item: item[1])
    if fraction < PHASE_PRESENCE_FLOOR_FRACTION:
        return None

    return PropertyPerturbation(
        property="phase",
        contaminant=species,
        effect_row=row_key,
        source=str(prop_cfg.get("source", "")),
        residual_wt_pct=float(wt_pct),
        perturbation_before=float(fraction),
        perturbation_after=0.0,
        metric="phase_topology_presence",
        grounded=bool(prop_cfg.get("grounded", False)),
        correctable=True,
        raw_value=float(fraction),
        adjusted_value=0.0,
        metric_basis=PHASE_PRESENCE_FLOOR_FRACTION,
        metadata={
            "contaminant_group": contaminant_group,
            "phase": phase,
            "phase_fraction": float(fraction),
            "phase_wt_pct": float(fraction) * 100.0,
            "phase_presence_floor_wt_pct": PHASE_PRESENCE_FLOOR_FRACTION * 100.0,
        },
    )


def _magemin_database_from_engine_or_result(
    engine: str,
    melts_result: Mapping[str, Any] | Any | None,
) -> str | None:
    for key in ("magemin_database", "database", "db"):
        value = _lookup_result_value(melts_result, key)
        if value is not None:
            return str(value).lower().strip()

    engine_key = str(engine).lower().replace("-", "_")
    for database in ("igad", "ig", "mp", "mb", "um", "ume", "mtl"):
        if engine_key == database or engine_key.endswith(f"_{database}"):
            return database
    if "magemin" in engine_key:
        return "ig"
    return None


def _magemin_ig_igad_bulk_sum_applies(
    engine: str,
    melts_result: Mapping[str, Any] | Any | None,
) -> bool:
    engine_key = str(engine).lower()
    if "magemin" not in engine_key and engine_key not in _MAGEMIN_BULK_SUM_DATABASES:
        return False
    database = _magemin_database_from_engine_or_result(engine, melts_result)
    return database in _MAGEMIN_BULK_SUM_DATABASES


def _bulk_sum_closure_perturbation(
    *,
    cleaned_oxide_wt_pct: Mapping[str, float] | None,
    engine: str,
    melts_result: Mapping[str, Any] | Any | None,
) -> PropertyPerturbation | None:
    if not _magemin_ig_igad_bulk_sum_applies(engine, melts_result):
        return None
    if not cleaned_oxide_wt_pct:
        return None

    dropped: dict[str, float] = {}
    for oxide in MAGEMIN_IG_IGAD_BULK_SUM_DROPPED_OXIDES:
        value = _finite_float(cleaned_oxide_wt_pct.get(oxide))
        if value is not None and value > 0.0:
            dropped[oxide] = value
    dropped_wt_pct = sum(dropped.values())
    if dropped_wt_pct <= 1e-12:
        return None

    dropped_fraction = dropped_wt_pct / 100.0
    return PropertyPerturbation(
        property="bulk_sum_closure",
        contaminant="MAGEMin ig/igad dropped oxides",
        effect_row="magemin_ig_igad_bulk_sum_closure",
        source="simulator/melt_backend/magemin.py::_DB_BULK_ORDERS",
        residual_wt_pct=float(dropped_wt_pct),
        perturbation_before=float(dropped_fraction),
        perturbation_after=0.0,
        metric="dropped_component_mass_fraction",
        grounded=True,
        correctable=False,
        raw_value=float(dropped_fraction),
        adjusted_value=0.0,
        metadata={
            "dropped_oxides_wt_pct": dict(sorted(dropped.items())),
            "dropped_wt_pct": float(dropped_wt_pct),
            "database": _magemin_database_from_engine_or_result(engine, melts_result),
        },
    )


def _compute_property_perturbation(
    *,
    property_name: str,
    species: str,
    wt_pct: float,
    prop_cfg: Mapping[str, Any],
    row_key: str,
    contaminant_group: str,
    T_in_C: float,
    melts_result: Mapping[str, Any] | Any | None,
    engine: str,
) -> PropertyPerturbation | None:
    mode = str(prop_cfg.get("mode", ""))
    grounded = bool(prop_cfg.get("grounded", False))
    source = str(prop_cfg.get("source", ""))
    analytical_metadata = _analytical_model_metadata(
        row_key,
        species,
        T_K=float(T_in_C) + 273.15,
    )

    if mode == "phase_topology_presence":
        return _with_analytical_model_metadata(
            _phase_topology_perturbation(
                species=species,
                wt_pct=wt_pct,
                prop_cfg=prop_cfg,
                row_key=row_key,
                contaminant_group=contaminant_group,
                melts_result=melts_result,
                engine=engine,
            ),
            analytical_metadata,
        )

    if mode == "delta_T_per_wt_pct":
        coeff = float(prop_cfg["coefficient_C_per_wt_pct"])
        delta_T = coeff * wt_pct
        before = _liquidus_perturbation_pct(delta_T, T_in_C)
        after = 0.0
        return _with_analytical_model_metadata(
            PropertyPerturbation(
                property=property_name,
                contaminant=species,
                effect_row=row_key,
                source=source,
                residual_wt_pct=wt_pct,
                perturbation_before=before,
                perturbation_after=after,
                metric="delta_T_frac_of_T_in_C",
                grounded=grounded,
                correctable=grounded,
                raw_value=delta_T,
                adjusted_value=0.0,
                metric_basis=float(T_in_C),
            ),
            analytical_metadata,
        )

    if mode == "delta_T_interval_per_wt_pct":
        low_c, high_c = prop_cfg["interval_C_per_wt_pct"]
        delta_low = float(low_c) * wt_pct
        delta_high = float(high_c) * wt_pct
        before = max(
            _liquidus_perturbation_pct(delta_low, T_in_C),
            _liquidus_perturbation_pct(delta_high, T_in_C),
        )
        width = abs(
            _liquidus_perturbation_pct(delta_high, T_in_C)
            - _liquidus_perturbation_pct(delta_low, T_in_C)
        )
        after = width / 2.0
        return _with_analytical_model_metadata(
            PropertyPerturbation(
                property=property_name,
                contaminant=species,
                effect_row=row_key,
                source=source,
                residual_wt_pct=wt_pct,
                perturbation_before=before,
                perturbation_after=after,
                metric="delta_T_frac_of_T_in_C",
                grounded=False,
                correctable=False,
                raw_value=None,
                adjusted_value=None,
                interval=(delta_low, delta_high),
                metric_basis=float(T_in_C),
            ),
            analytical_metadata,
        )

    if mode == "delta_fraction_interval_per_wt_pct":
        low_f, high_f = prop_cfg["interval_per_wt_pct"]
        before = max(abs(float(low_f) * wt_pct), abs(float(high_f) * wt_pct))
        after = _interval_half_width(float(low_f) * wt_pct, float(high_f) * wt_pct)
        return _with_analytical_model_metadata(
            PropertyPerturbation(
                property=property_name,
                contaminant=species,
                effect_row=row_key,
                source=source,
                residual_wt_pct=wt_pct,
                perturbation_before=before,
                perturbation_after=after,
                metric="delta_absolute_fraction",
                grounded=False,
                correctable=False,
                interval=(float(low_f) * wt_pct, float(high_f) * wt_pct),
            ),
            analytical_metadata,
        )

    if mode == "delta_log10_fO2_interval_per_wt_pct":
        low_l, high_l = prop_cfg["interval_per_wt_pct"]
        before = max(abs(float(low_l) * wt_pct), abs(float(high_l) * wt_pct))
        after = _interval_half_width(float(low_l) * wt_pct, float(high_l) * wt_pct)
        return _with_analytical_model_metadata(
            PropertyPerturbation(
                property=property_name,
                contaminant=species,
                effect_row=row_key,
                source=source,
                residual_wt_pct=wt_pct,
                perturbation_before=before,
                perturbation_after=after,
                metric="delta_log10_fO2",
                grounded=False,
                correctable=False,
                interval=(float(low_l) * wt_pct, float(high_l) * wt_pct),
            ),
            analytical_metadata,
        )

    raise ValueError(f"unsupported effect mode {mode!r} for {property_name}")


def _unmodeled_residual_perturbation(
    species: str,
    wt_pct: float,
) -> PropertyPerturbation:
    return PropertyPerturbation(
        property="noise_floor",
        contaminant=species,
        effect_row="unmodeled_residual",
        source="no matched contaminant effect row",
        residual_wt_pct=float(wt_pct),
        perturbation_before=None,
        perturbation_after=None,
        metric="noise_floor_ungrounded",
        grounded=False,
        correctable=False,
    )


def request_certified_point(
    row_key: str,
    property_name: str,
    *,
    wt_pct: float = 1.0,
) -> float:
    """Fail loud when an ungrounded effect row has no certified point."""
    row = EFFECT_ROWS[row_key]
    prop_cfg = row["properties"][property_name]
    if not prop_cfg.get("grounded", False):
        raise CertifiedPointRefusedError(
            f"certified-point refused for ungrounded effect "
            f"{row_key}.{property_name} (interval only; wt%={wt_pct})"
        )
    mode = prop_cfg["mode"]
    if mode == "delta_T_per_wt_pct":
        return float(prop_cfg["coefficient_C_per_wt_pct"]) * wt_pct
    raise CertifiedPointRefusedError(
        f"no certified-point path for {row_key}.{property_name} mode={mode!r}"
    )


def melt_effect_adjustment(
    residual_by_species_wt_pct: Mapping[str, float],
    melts_result: Mapping[str, Any] | None,
    engine: str,
    *,
    T_in_C: float,
    cleaned_oxide_wt_pct: Mapping[str, float] | None = None,
) -> MeltEffectAdjustmentResult:
    """Per-residual analytical correction with separate raw vs adjusted fields."""
    perturbations: list[PropertyPerturbation] = []
    liquidus_delta = 0.0
    liquidus_prov: list[dict[str, Any]] = []
    liquidus_interval_low = 0.0
    liquidus_interval_high = 0.0
    liquidus_interval_prov: list[dict[str, Any]] = []
    warnings: list[str] = []

    raw_liquidus = None
    if melts_result is not None:
        raw_liquidus = _lookup_result_value(melts_result, "liquidus_T_C")
        if raw_liquidus is not None:
            raw_liquidus = float(raw_liquidus)

    bulk_sum = _bulk_sum_closure_perturbation(
        cleaned_oxide_wt_pct=(
            cleaned_oxide_wt_pct
            if cleaned_oxide_wt_pct is not None
            else residual_by_species_wt_pct
        ),
        engine=engine,
        melts_result=melts_result,
    )
    if bulk_sum is not None:
        perturbations.append(bulk_sum)

    for species, wt_pct in sorted(residual_by_species_wt_pct.items()):
        if wt_pct <= 1e-12:
            continue
        matched = _match_effect_row(species)
        if matched is None:
            perturbations.append(
                _unmodeled_residual_perturbation(species, float(wt_pct))
            )
            warnings.append(
                f"noise_floor_ungrounded: no effect row for residual {species} "
                f"at {wt_pct:.4g} wt%"
            )
            continue
        row_key, row = matched
        coverage_warning = _engine_coverage_absent_warning(row_key, species, engine)
        if coverage_warning is not None and coverage_warning not in warnings:
            warnings.append(coverage_warning)
        for prop_name, prop_cfg in row.get("properties", {}).items():
            pert = _compute_property_perturbation(
                property_name=prop_name,
                species=species,
                wt_pct=float(wt_pct),
                prop_cfg=prop_cfg,
                row_key=row_key,
                contaminant_group=str(row.get("contaminant_group", "")),
                T_in_C=T_in_C,
                melts_result=melts_result,
                engine=engine,
            )
            if pert is None:
                continue
            perturbations.append(pert)
            if prop_name == "liquidus" and pert.grounded and pert.raw_value is not None:
                liquidus_delta += float(pert.raw_value)
                liquidus_prov.append({
                    "contaminant": species,
                    "effect_row": row_key,
                    "source": pert.source,
                    "delta_T_C": pert.raw_value,
                    "grounded": pert.grounded,
                })
            if prop_name == "liquidus" and not pert.grounded and pert.interval is not None:
                delta_low, delta_high = pert.interval
                liquidus_interval_low += float(delta_low)
                liquidus_interval_high += float(delta_high)
                liquidus_interval_prov.append({
                    "contaminant": species,
                    "effect_row": row_key,
                    "source": pert.source,
                    "interval_delta_T_C": pert.interval,
                    "grounded": False,
                })
            if not pert.grounded:
                warnings.append(
                    f"noise_floor_ungrounded: {species} {prop_name} effect "
                    f"interval half-width drives flag (row={row_key})"
                )

    adjusted_liquidus = None
    if raw_liquidus is not None:
        adjusted_liquidus = raw_liquidus + liquidus_delta
    adjusted_liquidus_interval = None
    if raw_liquidus is not None and liquidus_interval_prov:
        adjusted_liquidus_interval = (
            raw_liquidus + liquidus_interval_low,
            raw_liquidus + liquidus_interval_high,
        )

    return MeltEffectAdjustmentResult(
        effect_table_version=EFFECT_TABLE_VERSION,
        T_in_C=float(T_in_C),
        engine=str(engine),
        perturbations=tuple(perturbations),
        raw_liquidus_C=raw_liquidus,
        adjusted_liquidus_C=adjusted_liquidus,
        adjusted_liquidus_interval_C=adjusted_liquidus_interval,
        adjusted_liquidus_provenance=tuple(liquidus_prov),
        adjusted_liquidus_interval_provenance=tuple(liquidus_interval_prov),
        warnings=tuple(warnings),
    )


def _property_thresholds(property_name: str, metric: str) -> PropertyThreshold:
    for name, threshold in PROPERTY_THRESHOLD_TABLE.items():
        if property_name == name or metric == threshold.metric:
            return threshold
    return PropertyThreshold(metric=metric, warning=2.0, notice=0.5)


def _liquidus_absolute_before_after(
    pert: PropertyPerturbation,
) -> tuple[float | None, float | None]:
    if pert.raw_value is not None:
        return abs(float(pert.raw_value)), abs(float(pert.adjusted_value or 0.0))
    if pert.interval is not None:
        low, high = pert.interval
        return max(abs(float(low)), abs(float(high))), _interval_half_width(low, high)
    return None, None


def _meets_threshold(
    pert: PropertyPerturbation,
    threshold: PropertyThreshold,
    level: str,
    *,
    after_only: bool,
) -> bool:
    limit = threshold.warning if level == "warning" else threshold.notice
    values: list[float] = []
    if not after_only and pert.perturbation_before is not None:
        values.append(float(pert.perturbation_before))
    if pert.perturbation_after is not None:
        values.append(float(pert.perturbation_after))

    if threshold.absolute_warning_floor is None or pert.property != "liquidus":
        return any(value >= limit for value in values)

    basis = float(pert.metric_basis or 0.0)
    floor = (
        threshold.absolute_warning_floor
        if level == "warning"
        else threshold.absolute_notice_floor
    )
    if floor is None:
        return any(value >= limit for value in values)

    absolute_limit = max((basis * limit / 100.0) if basis > 0.0 else 0.0, floor)
    before_abs, after_abs = _liquidus_absolute_before_after(pert)
    absolute_values: list[float] = []
    if not after_only and before_abs is not None:
        absolute_values.append(before_abs)
    if after_abs is not None:
        absolute_values.append(after_abs)
    if absolute_values:
        return any(value >= absolute_limit for value in absolute_values)
    return any(value >= limit for value in values)


def _classify_flag(pert: PropertyPerturbation) -> str | None:
    if pert.metric == "noise_floor_ungrounded":
        return "WARNING"
    if pert.metric == "phase_topology_presence":
        if (pert.perturbation_before or 0.0) >= PHASE_PRESENCE_FLOOR_FRACTION:
            return "WARNING"
        return None
    if pert.perturbation_before is None or pert.perturbation_after is None:
        return None
    thresholds = _property_thresholds(pert.property, pert.metric)
    if _meets_threshold(pert, thresholds, "warning", after_only=False):
        return "WARNING"
    if _meets_threshold(
        pert,
        thresholds,
        "notice",
        after_only=pert.correctable,
    ):
        return "NOTICE"
    return "INFO"


def evaluate_verdict_a(
    perturbations: tuple[PropertyPerturbation, ...],
    *,
    hour: int,
    confounding_threshold_pct: float = 0.01,
    residual_wt_pct: Mapping[str, float] | None = None,
) -> tuple[PropertyFlag, ...]:
    """WARN-only property-impact flags for one timeline step."""
    flags: list[PropertyFlag] = []
    for pert in perturbations:
        if residual_wt_pct is not None and pert.property != "bulk_sum_closure":
            wt = float(residual_wt_pct.get(pert.contaminant, 0.0))
            if wt < confounding_threshold_pct:
                continue
        level = _classify_flag(pert)
        if level is None:
            continue
        noise_status = "proposed" if pert.grounded else "noise_floor_ungrounded"
        flags.append(
            PropertyFlag(
                property=pert.property,
                level=level,
                contaminant=pert.contaminant,
                effect_row=pert.effect_row,
                perturbation_before=pert.perturbation_before,
                perturbation_after=pert.perturbation_after,
                metric=pert.metric,
                grounded=pert.grounded,
                correctable=pert.correctable,
                residual_wt_pct=pert.residual_wt_pct,
                hour=hour,
                noise_floor_status=noise_status,
                metadata=dict(pert.metadata),
            )
        )
    return tuple(flags)


def _bakeoff_hour_by_species(
    timeline: tuple[Any, ...],
) -> dict[str, int]:
    """First hour a carrier is cleared by escape/decompose/burn."""
    bakeoff: dict[str, int] = {}
    for entry in timeline:
        hour = int(getattr(entry, "hour", 0))
        for group_events in (getattr(entry, "by_group", {}) or {}).values():
            for event in group_events:
                carrier = str(event.get("carrier", ""))
                disposition = str(event.get("disposition", ""))
                if disposition not in {"escaped", "decomposed", "burned"}:
                    continue
                if carrier and carrier not in bakeoff:
                    bakeoff[carrier] = hour
    return bakeoff


def _estimate_hourly_residuals(
    final_residual_wt_pct: Mapping[str, float],
    timeline: tuple[Any, ...],
) -> list[tuple[int, dict[str, float]]]:
    """Step-resolved residual fractions from disposition bakeoff events."""
    if not timeline:
        return [(0, dict(final_residual_wt_pct))]

    bakeoff = _bakeoff_hour_by_species(timeline)
    hourly: list[tuple[int, dict[str, float]]] = []

    for entry in timeline:
        hour = int(getattr(entry, "hour", 0))
        residual_at_hour: dict[str, float] = {}
        for species, final_wt in final_residual_wt_pct.items():
            clear_hour = bakeoff.get(species)
            if clear_hour is not None and hour >= clear_hour:
                residual_at_hour[species] = 0.0
            else:
                residual_at_hour[species] = float(final_wt)
        hourly.append((hour, residual_at_hour))

    return hourly


def _flag_timeline_key(flag: PropertyFlag) -> tuple[str, str, str]:
    return (flag.contaminant, flag.property, flag.effect_row)


def _timeline_flag_record(
    flag: PropertyFlag,
    *,
    cleared: bool,
    clear_hour: int | None,
) -> dict[str, Any]:
    return {
        "property": flag.property,
        "level": flag.level,
        "contaminant": flag.contaminant,
        "effect_row": flag.effect_row,
        "grounded": flag.grounded,
        "correctable": flag.correctable,
        "metric": flag.metric,
        "active": not cleared,
        "cleared": cleared,
        "clear_hour": clear_hour,
        "metadata": dict(flag.metadata),
    }


def evaluate_verdict_a_timeline(
    final_residual_wt_pct: Mapping[str, float],
    melts_result: Mapping[str, Any] | None,
    engine: str,
    *,
    T_in_C: float,
    timeline: tuple[Any, ...],
    confounding_threshold_pct: float = 0.01,
    cleaned_oxide_wt_pct: Mapping[str, float] | None = None,
) -> VerdictAResult:
    """Step-resolved WARN-only flags; clears when bakeoff drops residual."""
    hourly = _estimate_hourly_residuals(final_residual_wt_pct, timeline)
    all_flags: list[PropertyFlag] = []
    step_resolved: list[dict[str, Any]] = []
    previous_active: dict[tuple[str, str, str], PropertyFlag] = {}
    clear_hour_by_key: dict[tuple[str, str, str], int] = {}

    for hour, residual_at_hour in hourly:
        adjustment = melt_effect_adjustment(
            residual_at_hour,
            melts_result,
            engine,
            T_in_C=T_in_C,
            cleaned_oxide_wt_pct=cleaned_oxide_wt_pct,
        )
        flags = evaluate_verdict_a(
            adjustment.perturbations,
            hour=hour,
            confounding_threshold_pct=confounding_threshold_pct,
            residual_wt_pct=residual_at_hour,
        )
        active_by_key = {_flag_timeline_key(f): f for f in flags if f.level}
        flag_records = [
            _timeline_flag_record(
                flag,
                cleared=False,
                clear_hour=clear_hour_by_key.get(key),
            )
            for key, flag in active_by_key.items()
        ]
        for key, old_flag in previous_active.items():
            if key in active_by_key or key in clear_hour_by_key:
                continue
            clear_hour_by_key[key] = hour
            flag_records.append(
                _timeline_flag_record(
                    old_flag,
                    cleared=True,
                    clear_hour=hour,
                )
            )
        step_resolved.append({
            "hour": hour,
            "residual_wt_pct": dict(residual_at_hour),
            "flags": flag_records,
        })
        all_flags.extend(flags)
        previous_active = active_by_key

    return VerdictAResult(
        flags=tuple(all_flags),
        step_resolved=tuple(step_resolved),
        warn_only=True,
    )


def _domain_gate_for_engine(engine: str):
    engine_key = str(engine).lower()
    if "magemin" in engine_key or engine_key in {"ig", "igad"}:
        return MAGEMinDomainGate
    return AlphaMELTSDomainGate


def evaluate_verdict_b(
    cleaned_melt_kg: Mapping[str, float],
    backend_status: str | BackendStatusSignal,
    engine: str,
) -> VerdictBResult:
    """Hard gate on stripped silicate OOD only; contaminant-present never crashes."""
    stripped = strip_non_oxide_residuals(cleaned_melt_kg)
    gate = _domain_gate_for_engine(engine)
    # Domain validity is a silicate oxide-ratio question. This normalization is
    # only for the gate input; the returned stripped wt% and provenance keep the
    # honest post-strip sub-100 mass record.
    domain_oxide_wt_pct = _oxide_ratios_for_domain_check(stripped.oxide_wt_pct)
    if hasattr(gate, "validate_with_reason"):
        stripped_valid, domain_warnings, stripped_reason = (
            gate.validate_with_reason(domain_oxide_wt_pct)
        )
    else:
        stripped_valid, domain_warnings = gate.validate(domain_oxide_wt_pct)
        stripped_reason = None

    status_signal = _backend_status_signal(backend_status)
    status = status_signal.status
    status_reason = status_signal.reason
    hard_gate_failed = (
        status in _VERDICT_B_HARD_FAIL_BACKEND_STATUSES or not stripped_valid
    )
    if not stripped_valid and status_reason is None:
        status_reason = reason_value(stripped_reason)
    if hard_gate_failed or not stripped_valid:
        layer_a_state = "out_of_domain"
        offending_species = tuple(sorted(stripped.oxide_wt_pct))
    elif stripped.stripped_mass_kg > 0.0:
        layer_a_state = "stripped_then_in_domain"
        offending_species = tuple(sorted(stripped.stripped_kg))
    else:
        layer_a_state = "in_domain"
        offending_species = ()

    return VerdictBResult(
        backend_status=status,
        backend_status_reason=status_reason,
        layer_a_state=layer_a_state,
        offending_species=offending_species,
        stripped_domain_valid=stripped_valid,
        hard_gate_failed=hard_gate_failed,
        stripped_oxide_wt_pct=dict(stripped.oxide_wt_pct),
        stripped_mass_provenance=stripped.provenance,
        domain_warnings=tuple(domain_warnings),
        engine=str(engine),
    )


def _backend_status_reason_from_mapping(value: Mapping[str, Any]) -> str | None:
    for key in (
        "backend_status_reason",
        "out_of_domain_reason",
        "reason_out_of_domain",
    ):
        reason = value.get(key)
        if reason is not None:
            return reason_value(reason)
    nested = value.get("backend_diagnostics")
    if isinstance(nested, Mapping):
        return _backend_status_reason_from_mapping(nested)
    nested = value.get("diagnostics")
    if isinstance(nested, Mapping):
        return _backend_status_reason_from_mapping(nested)
    return None


def _default_backend_status_reason(status: str, reason: str | None) -> str | None:
    if reason is not None:
        return reason
    if status == "unavailable":
        return OutOfDomainReason.BACKEND_UNAVAILABLE.value
    if status == "not_converged":
        return OutOfDomainReason.NOT_CONVERGED.value
    return None


def _backend_status_signal(value: Any) -> BackendStatusSignal:
    if isinstance(value, BackendStatusSignal):
        return BackendStatusSignal(
            status=str(value.status),
            reason=_default_backend_status_reason(
                str(value.status), reason_value(value.reason)
            ),
        )
    if isinstance(value, Mapping):
        status = str(
            value.get("backend_status")
            or value.get("status")
            or value.get("runtime_status")
            or "ok"
        )
        reason = _backend_status_reason_from_mapping(value)
        return BackendStatusSignal(
            status=status,
            reason=_default_backend_status_reason(status, reason),
        )
    status = str(
        getattr(
            value,
            "backend_status",
            getattr(value, "status", value),
        )
    )
    reason = reason_value(
        getattr(value, "backend_status_reason", getattr(value, "reason", None))
    )
    return BackendStatusSignal(
        status=status,
        reason=_default_backend_status_reason(status, reason),
    )


def aggregate_backend_status(
    history: Any,
    latest: str | BackendStatusSignal | Mapping[str, Any],
) -> BackendStatusSignal:
    """Mirror run_executor._aggregate_backend_status (no new equilibrium)."""
    try:
        statuses = [_backend_status_signal(s) for s in history]
    except TypeError:
        statuses = []
    latest_signal = _backend_status_signal(latest)
    statuses.append(latest_signal)
    for status in _BACKEND_STATUS_PRECEDENCE:
        matches = [signal for signal in statuses if signal.status == status]
        if matches:
            for signal in matches:
                if signal.reason is not None:
                    return signal
            return matches[0]
    return latest_signal


def build_harness_verdicts(
    *,
    cleaned_melt_kg: Mapping[str, float],
    sim: Any,
    engine: str,
    timeline: tuple[Any, ...],
    T_in_C: float,
) -> dict[str, Any]:
    """Assemble verdict (a) + verdict (b) for Stage0HarnessResult."""
    residual_wt = residual_wt_pct_by_species(cleaned_melt_kg)
    strip_result = strip_non_oxide_residuals(cleaned_melt_kg)

    melts_result: dict[str, Any] = {}
    raw_liq = getattr(sim, "_last_liquidus_T_C", None)
    diag = getattr(sim, "_last_backend_diagnostics", {}) or {}
    if raw_liq is None:
        raw_liq = diag.get("liquidus_T_C") or diag.get("liquidus_C")
    if raw_liq is not None:
        melts_result["liquidus_T_C"] = float(raw_liq)
    for key in (
        "phase_modes_wt_pct",
        "phase_modes_pct",
        "phase_masses_kg",
        "phases_present",
        "magemin_database",
        "database",
        "db",
    ):
        if key in diag:
            melts_result[key] = diag[key]

    adjustment = melt_effect_adjustment(
        residual_wt,
        melts_result or None,
        engine,
        T_in_C=T_in_C,
        cleaned_oxide_wt_pct=strip_result.oxide_wt_pct,
    )
    verdict_a = evaluate_verdict_a_timeline(
        residual_wt,
        melts_result or None,
        engine,
        T_in_C=T_in_C,
        timeline=timeline,
        cleaned_oxide_wt_pct=strip_result.oxide_wt_pct,
    )

    latest_status = str(
        getattr(
            sim,
            "_backend_selection_status",
            getattr(sim, "_last_backend_status", "ok"),
        )
    )
    status_history = list(getattr(sim, "_backend_status_history", ()) or ())
    last_ood_diag = getattr(sim, "_last_out_of_domain_diagnostics", None)
    if isinstance(last_ood_diag, Mapping):
        ood_reason = _backend_status_reason_from_mapping(last_ood_diag)
        has_out_of_domain_status = any(
            _backend_status_signal(signal).status == "out_of_domain"
            for signal in status_history
        )
        if ood_reason is not None and has_out_of_domain_status:
            status_history.append({
                "backend_status": "out_of_domain",
                "backend_status_reason": ood_reason,
            })
    latest_signal: Mapping[str, Any] = {"backend_status": latest_status}
    latest_reason = _backend_status_reason_from_mapping(diag)
    if latest_reason is not None:
        latest_signal = {
            "backend_status": latest_status,
            "backend_status_reason": latest_reason,
        }
    backend_status = aggregate_backend_status(
        status_history,
        latest_signal,
    )
    verdict_b = evaluate_verdict_b(cleaned_melt_kg, backend_status, engine)

    return {
        "verdict_a": {
            "warn_only": verdict_a.warn_only,
            "flags": [
                {
                    "property": f.property,
                    "level": f.level,
                    "contaminant": f.contaminant,
                    "effect_row": f.effect_row,
                    "perturbation_before": f.perturbation_before,
                    "perturbation_after": f.perturbation_after,
                    "metric": f.metric,
                    "grounded": f.grounded,
                    "correctable": f.correctable,
                    "residual_wt_pct": f.residual_wt_pct,
                    "hour": f.hour,
                    "active": f.active,
                    "cleared": f.cleared,
                    "clear_hour": f.clear_hour,
                    "noise_floor_status": f.noise_floor_status,
                    "metadata": dict(f.metadata),
                }
                for f in verdict_a.flags
            ],
            "step_resolved": list(verdict_a.step_resolved),
        },
        "verdict_b": {
            "backend_status": verdict_b.backend_status,
            "backend_status_reason": verdict_b.backend_status_reason,
            "layer_a_state": verdict_b.layer_a_state,
            "offending_species": list(verdict_b.offending_species),
            "stripped_domain_valid": verdict_b.stripped_domain_valid,
            "hard_gate_failed": verdict_b.hard_gate_failed,
            "stripped_oxide_wt_pct": verdict_b.stripped_oxide_wt_pct,
            "stripped_mass_provenance": [
                {
                    "species": p.species,
                    "kg": p.kg,
                    "wt_pct_of_total": p.wt_pct_of_total,
                    "reason": p.reason,
                }
                for p in verdict_b.stripped_mass_provenance
            ],
            "domain_warnings": list(verdict_b.domain_warnings),
            "engine": verdict_b.engine,
            "contaminant_present_never_crash": True,
        },
        "strip": {
            "oxide_wt_pct": dict(strip_result.oxide_wt_pct),
            "stripped_mass_kg": strip_result.stripped_mass_kg,
            "provenance": [
                {
                    "species": p.species,
                    "kg": p.kg,
                    "wt_pct_of_total": p.wt_pct_of_total,
                    "reason": p.reason,
                }
                for p in strip_result.provenance
            ],
            "renormalized": False,
        },
        "melt_effect_adjustment": {
            "effect_table_version": adjustment.effect_table_version,
            "raw_liquidus_C": adjustment.raw_liquidus_C,
            "adjusted_liquidus_C": adjustment.adjusted_liquidus_C,
            "adjusted_liquidus_interval_C": adjustment.adjusted_liquidus_interval_C,
            "adjusted_liquidus_provenance": list(
                adjustment.adjusted_liquidus_provenance
            ),
            "adjusted_liquidus_interval_provenance": list(
                adjustment.adjusted_liquidus_interval_provenance
            ),
            "perturbations": [
                {
                    "property": p.property,
                    "contaminant": p.contaminant,
                    "effect_row": p.effect_row,
                    "source": p.source,
                    "residual_wt_pct": p.residual_wt_pct,
                    "perturbation_before": p.perturbation_before,
                    "perturbation_after": p.perturbation_after,
                    "metric": p.metric,
                    "grounded": p.grounded,
                    "correctable": p.correctable,
                    "raw_value": p.raw_value,
                    f"adjusted_{p.property}": p.adjusted_value,
                    "interval": p.interval,
                    "metric_basis": p.metric_basis,
                    "metadata": dict(p.metadata),
                }
                for p in adjustment.perturbations
            ],
            "warnings": list(adjustment.warnings),
        },
    }
