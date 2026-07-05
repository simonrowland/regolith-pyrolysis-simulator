"""UNCERTIFIED structural melt-oxide activity diagnostics.

This module is intentionally diagnostic-only. It computes NBO/T, optical
basicity, a coarse liquidus flag, and provisional reference activity
coefficients for later comparison against literature and engine sweeps. It
does not provide authoritative vapor-pressure activities.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


# Tunable parameter block. Every empirical coefficient below is provisional and
# deliberately local to this file so the MinerU/literature sweep can refine it.
#
# Stoichiometric cation/O counts are oxide formula coefficients. Lambda values
# follow Duffy & Ingram optical basicity conventions as tabulated for oxide
# components by Mills and slag/glass handbooks; tracked trace oxides are marked
# with local proxy notes because they are not calibration drivers here.
_OXIDE_COMPONENTS: dict[str, dict[str, Any]] = {
    "SiO2": {
        "oxygen": 2.0,
        "cations": {"Si": 1.0},
        "lambda": 0.48,  # Duffy/Ingram-Mills standard acidic-network former.
    },
    "Al2O3": {
        "oxygen": 3.0,
        "cations": {"Al": 2.0},
        "lambda": 0.60,  # Duffy/Ingram-Mills amphoteric oxide table value.
    },
    "Na2O": {
        "oxygen": 1.0,
        "cations": {"Na": 2.0},
        "lambda": 1.15,  # Duffy/Ingram-Mills alkali oxide table value.
    },
    "K2O": {
        "oxygen": 1.0,
        "cations": {"K": 2.0},
        "lambda": 1.40,  # Duffy/Ingram-Mills alkali oxide table value.
    },
    "CaO": {
        "oxygen": 1.0,
        "cations": {"Ca": 1.0},
        "lambda": 1.00,  # Duffy/Ingram-Mills alkaline-earth oxide value.
    },
    "MgO": {
        "oxygen": 1.0,
        "cations": {"Mg": 1.0},
        "lambda": 0.78,  # Duffy/Ingram-Mills alkaline-earth oxide value.
    },
    "FeO": {
        "oxygen": 1.0,
        "cations": {"Fe": 1.0},
        "lambda": 1.00,  # Mills slag-model proxy; FeO is not fitted here.
    },
    "Fe2O3": {
        "oxygen": 3.0,
        "cations": {"Fe": 2.0},
        "lambda": 0.78,  # Mills ferric-oxide proxy; diagnostic only.
    },
    "TiO2": {
        "oxygen": 2.0,
        "cations": {"Ti": 1.0},
        "lambda": 0.61,  # Duffy/Ingram-Mills transition-oxide value.
    },
    "Cr2O3": {
        "oxygen": 3.0,
        "cations": {"Cr": 2.0},
        "lambda": 0.65,  # Local Mills-style Cr2O3 proxy; UNCERTIFIED trace term.
    },
    "MnO": {
        "oxygen": 1.0,
        "cations": {"Mn": 1.0},
        "lambda": 1.00,  # Local Mills-style MnO proxy; UNCERTIFIED trace term.
    },
    "P2O5": {
        "oxygen": 5.0,
        "cations": {"P": 2.0},
        "lambda": 0.40,  # Local Duffy-style acidic oxide proxy; trace only.
    },
    "NiO": {
        "oxygen": 1.0,
        "cations": {"Ni": 1.0},
        "lambda": 1.00,  # Local Mills-style NiO proxy; UNCERTIFIED trace term.
    },
    "CoO": {
        "oxygen": 1.0,
        "cations": {"Co": 1.0},
        "lambda": 1.00,  # Local Mills-style CoO proxy; UNCERTIFIED trace term.
    },
}

_SINGLE_CATION_EQUIVALENTS: dict[str, tuple[str, float]] = {
    "SiO2": ("SiO2", 1.0),
    "TiO2": ("TiO2", 1.0),
    "Al2O3": ("AlO1.5", 2.0),
    "FeO": ("FeO", 1.0),
    "Fe2O3": ("FeO1.5", 2.0),
    "MgO": ("MgO", 1.0),
    "CaO": ("CaO", 1.0),
    "Na2O": ("NaO0.5", 2.0),
    "K2O": ("KO0.5", 2.0),
    "Cr2O3": ("CrO1.5", 2.0),
    "MnO": ("MnO", 1.0),
    "P2O5": ("PO2.5", 2.0),
    "NiO": ("NiO", 1.0),
    "CoO": ("CoO", 1.0),
}

_FORMULA_UNIT_ALIASES: dict[str, tuple[str, float]] = {
    "NaO0.5": ("Na2O", 0.5),
    "KO0.5": ("K2O", 0.5),
    "AlO1.5": ("Al2O3", 0.5),
    "FeO1.5": ("Fe2O3", 0.5),
    "CrO1.5": ("Cr2O3", 0.5),
    "PO2.5": ("P2O5", 0.5),
}

_REFERENCE_STRUCTURAL_STATE = {
    # Derived from the Sossi-Fegley 2018 lunar basalt 12022 proxy composition
    # in tests/chemistry/corpus_fixtures.py and the NBO/T + Lambda formulas in
    # this module; used only as the DeMaria anchor coordinate.
    "nbo_t": 1.143864967345075,
    "optical_basicity": 0.6148157641396143,
}

_GAMMA_MODEL = {
    "NaO0.5": {
        # DeMaria-inverted lunar-basalt anchor from local volatility grounding:
        # gamma_NaO0.5 ~= 4.5e-3 at 1500 K.
        "anchor_gamma_at_1500K": 4.5e-3,
        # DeMaria-inverted 1300->1500 K slope:
        # log10(4.5e-3 / 1.8e-4) / 200 K.
        "temperature_slope_dex_per_K": math.log10(4.5e-3 / 1.8e-4) / 200.0,
        # Provisional structural response: positive so gamma rises as the melt
        # becomes more basic/depolymerized; seeded until binary fits land.
        "lambda_slope_dex": 8.0,
        # Provisional NBO/T response; lower weight than Lambda to avoid double
        # counting before Toop-Samis/MQM calibration.
        "nbo_t_slope_dex": 0.35,
    },
    "KO0.5": {
        # Provisional K anchor tied to the same DeMaria/Wolf trend family;
        # K lacks a clean inverted gamma table in the current grounding note.
        "anchor_gamma_at_1500K": 6.0e-3,
        # Same thermal slope as Na until K-specific KEMS inversion lands.
        "temperature_slope_dex_per_K": math.log10(4.5e-3 / 1.8e-4) / 200.0,
        # Provisional structural response; K is at least as modifier-like as Na.
        "lambda_slope_dex": 8.0,
        # Provisional NBO/T response shared with Na.
        "nbo_t_slope_dex": 0.35,
    },
    "CaO": {
        # Major-oxide nonideality is milder than alkalis in the grounding note;
        # 0.45 is a reference-only basalt-scale starting point.
        "anchor_gamma_at_1500K": 0.45,
        # Small positive T slope: major-oxide gamma tends toward unity as T rises.
        "temperature_slope_dex_per_K": 5.0e-4,
        # Weak provisional basicity response for CaO.
        "lambda_slope_dex": 1.0,
        # Weak provisional depolymerization response for CaO.
        "nbo_t_slope_dex": 0.10,
    },
    "MgO": {
        # Major-oxide nonideality is milder than alkalis; MgO starts closer to
        # unity than alkalis but remains diagnostic-only.
        "anchor_gamma_at_1500K": 0.60,
        # Small positive T slope: major-oxide gamma tends toward unity as T rises.
        "temperature_slope_dex_per_K": 5.0e-4,
        # Weak provisional basicity response for MgO.
        "lambda_slope_dex": 0.8,
        # Weak provisional depolymerization response for MgO.
        "nbo_t_slope_dex": 0.10,
    },
}

_LIQUIDUS_MODEL = {
    # Sossi & Fegley 2018 OCR/compiled corpus: lunar basalt 12022 liquidus
    # ~= 1300 C = 1573 K. This is the only hard anchor in the fallback.
    "anchor_temperature_K": 1573.0,
    # Sossi 12022 proxy formula-unit mole fractions derived from the local
    # fixture; they place the liquidus correlation at the anchor composition.
    "anchor_x_sio2": 0.4765126754480923,
    "anchor_x_al2o3": 0.0851862831590699,
    "anchor_x_alkali": 0.004835284857031333,
    "anchor_x_basic_modifier": 0.4176332580741079,
    # Provisional slopes: Si/Al raise liquidus; alkali and basic modifiers
    # lower it. Chosen conservative, monotone, and anchored to 12022 until a
    # phase engine or calibrated liquidus regression is wired.
    "sio2_slope_K_per_mole_fraction": 800.0,
    "al2o3_slope_K_per_mole_fraction": 250.0,
    "alkali_slope_K_per_mole_fraction": -700.0,
    "basic_modifier_slope_K_per_mole_fraction": -120.0,
    # Guard rails for a fallback estimate, not physical phase-equilibrium bounds.
    "min_temperature_K": 950.0,
    "max_temperature_K": 2300.0,
    # Explicit wide error bar: the flag is the deliverable, not a calibrated Tliq.
    "uncertainty_K": 150.0,
}

_GAMMA_COMPARISON_ANCHORS = {
    # Existing CMS constant-gamma landing noted by the grounding synthesis:
    # gamma_NaO0.5 ~= 1e-3 at 1673 K. Comparison only, not authority.
    "cms_constant_gamma_NaO0.5_1673K": 1.0e-3,
    # DeMaria-inverted lower-T lunar-basalt anchor used to derive T slope.
    "demaria_lunar_basalt_gamma_NaO0.5_1300K": 1.8e-4,
    # DeMaria-inverted upper-T lunar-basalt anchor used as model intercept.
    "demaria_lunar_basalt_gamma_NaO0.5_1500K": 4.5e-3,
}


@dataclass(frozen=True)
class StructuralActivityFeatures:
    """Plain structural features used by the provisional gamma surface."""

    nbo_t: float | None
    nbo_t_raw: float | None
    optical_basicity: float | None
    oxygen_mol: float
    tetrahedral_cations_mol: float
    charge_balanced_al_mol: float
    al_charge_capacity_mol: float
    single_cation_mole_fractions: dict[str, float]
    formula_unit_mole_fractions: dict[str, float]
    unsupported_species: tuple[str, ...]


def _positive_float(value: Any) -> float:
    try:
        candidate = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(candidate) or candidate <= 0.0:
        return 0.0
    return candidate


def normalize_formula_unit_moles(
    oxide_mol_by_species: Mapping[str, float],
) -> tuple[dict[str, float], tuple[str, ...]]:
    """Return positive oxide formula-unit moles plus ignored species names."""

    formula_mol: dict[str, float] = {}
    unsupported: list[str] = []
    for raw_species, raw_mol in dict(oxide_mol_by_species or {}).items():
        species = str(raw_species)
        mol = _positive_float(raw_mol)
        if mol <= 0.0:
            continue
        if species in _OXIDE_COMPONENTS:
            formula_species = species
            factor = 1.0
        elif species in _FORMULA_UNIT_ALIASES:
            formula_species, factor = _FORMULA_UNIT_ALIASES[species]
        else:
            unsupported.append(species)
            continue
        formula_mol[formula_species] = (
            formula_mol.get(formula_species, 0.0) + mol * factor
        )
    return formula_mol, tuple(sorted(unsupported))


def _mole_fractions(values: Mapping[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(v)) for v in values.values())
    if total <= 0.0:
        return {}
    return {
        str(k): float(v) / total
        for k, v in sorted(values.items())
        if float(v) > 0.0
    }


def _single_cation_moles(
    formula_unit_mol: Mapping[str, float],
) -> dict[str, float]:
    single: dict[str, float] = {}
    for oxide, mol in formula_unit_mol.items():
        if oxide not in _SINGLE_CATION_EQUIVALENTS:
            continue
        species, factor = _SINGLE_CATION_EQUIVALENTS[oxide]
        single[species] = single.get(species, 0.0) + float(mol) * factor
    return single


def structural_activity_features(
    oxide_mol_by_species: Mapping[str, float],
) -> StructuralActivityFeatures:
    """Compute NBO/T and optical basicity from mol-native oxide inventory.

    NBO/T follows the Mysen & Richet network-former budget form:
    ``NBO/T = (2 * O_total - 4 * T_cations) / T_cations``. Tetrahedral
    cations are Si plus charge-balanced Al; AlO4 charge is balanced by Na/K
    or by one-half Ca/Mg per Al.
    """

    formula_mol, unsupported = normalize_formula_unit_moles(oxide_mol_by_species)
    single_cation_mol = _single_cation_moles(formula_mol)
    single_cation_x = _mole_fractions(single_cation_mol)
    formula_x = _mole_fractions(formula_mol)

    oxygen_mol = 0.0
    optical_weighted = 0.0
    optical_oxygen = 0.0
    cations: dict[str, float] = {}
    for oxide, mol in formula_mol.items():
        component = _OXIDE_COMPONENTS[oxide]
        oxygen = float(component["oxygen"]) * mol
        oxygen_mol += oxygen
        optical_weighted += oxygen * float(component["lambda"])
        optical_oxygen += oxygen
        for element, count in dict(component["cations"]).items():
            cations[element] = cations.get(element, 0.0) + float(count) * mol

    al_mol = cations.get("Al", 0.0)
    al_charge_capacity_mol = (
        cations.get("Na", 0.0)
        + cations.get("K", 0.0)
        + 2.0 * cations.get("Ca", 0.0)
        + 2.0 * cations.get("Mg", 0.0)
    )
    charge_balanced_al_mol = min(al_mol, al_charge_capacity_mol)
    tetrahedral_cations_mol = cations.get("Si", 0.0) + charge_balanced_al_mol

    nbo_t_raw: float | None
    nbo_t: float | None
    if tetrahedral_cations_mol > 0.0:
        nbo_t_raw = (
            (2.0 * oxygen_mol - 4.0 * tetrahedral_cations_mol)
            / tetrahedral_cations_mol
        )
        nbo_t = max(0.0, nbo_t_raw)
    else:
        nbo_t_raw = None
        nbo_t = None

    optical_basicity = (
        optical_weighted / optical_oxygen
        if optical_oxygen > 0.0
        else None
    )

    return StructuralActivityFeatures(
        nbo_t=nbo_t,
        nbo_t_raw=nbo_t_raw,
        optical_basicity=optical_basicity,
        oxygen_mol=oxygen_mol,
        tetrahedral_cations_mol=tetrahedral_cations_mol,
        charge_balanced_al_mol=charge_balanced_al_mol,
        al_charge_capacity_mol=al_charge_capacity_mol,
        single_cation_mole_fractions=single_cation_x,
        formula_unit_mole_fractions=formula_x,
        unsupported_species=unsupported,
    )


def reference_activity_coefficients(
    *,
    nbo_t: float | None,
    optical_basicity: float | None,
    temperature_K: float,
) -> dict[str, float]:
    """Return provisional log-linear structural gamma_MOx values."""

    if nbo_t is None or optical_basicity is None:
        return {}
    gamma: dict[str, float] = {}
    for species, params in _GAMMA_MODEL.items():
        log10_gamma = math.log10(float(params["anchor_gamma_at_1500K"]))
        log10_gamma += float(params["temperature_slope_dex_per_K"]) * (
            float(temperature_K) - 1500.0
        )
        log10_gamma += float(params["lambda_slope_dex"]) * (
            float(optical_basicity)
            - _REFERENCE_STRUCTURAL_STATE["optical_basicity"]
        )
        log10_gamma += float(params["nbo_t_slope_dex"]) * (
            float(nbo_t) - _REFERENCE_STRUCTURAL_STATE["nbo_t"]
        )
        gamma[species] = min(1.0, max(1.0e-12, 10.0 ** log10_gamma))
    return gamma


def estimate_liquidus_flag(
    *,
    formula_unit_mole_fractions: Mapping[str, float],
    temperature_K: float,
) -> dict[str, Any]:
    """Return a coarse liquidus estimate and sub-liquidus flag."""

    x_sio2 = float(formula_unit_mole_fractions.get("SiO2", 0.0))
    x_al2o3 = float(formula_unit_mole_fractions.get("Al2O3", 0.0))
    x_alkali = float(formula_unit_mole_fractions.get("Na2O", 0.0)) + float(
        formula_unit_mole_fractions.get("K2O", 0.0)
    )
    x_basic = (
        float(formula_unit_mole_fractions.get("FeO", 0.0))
        + float(formula_unit_mole_fractions.get("MgO", 0.0))
        + float(formula_unit_mole_fractions.get("CaO", 0.0))
    )
    estimated_K = float(_LIQUIDUS_MODEL["anchor_temperature_K"])
    estimated_K += float(_LIQUIDUS_MODEL["sio2_slope_K_per_mole_fraction"]) * (
        x_sio2 - float(_LIQUIDUS_MODEL["anchor_x_sio2"])
    )
    estimated_K += float(_LIQUIDUS_MODEL["al2o3_slope_K_per_mole_fraction"]) * (
        x_al2o3 - float(_LIQUIDUS_MODEL["anchor_x_al2o3"])
    )
    estimated_K += float(_LIQUIDUS_MODEL["alkali_slope_K_per_mole_fraction"]) * (
        x_alkali - float(_LIQUIDUS_MODEL["anchor_x_alkali"])
    )
    estimated_K += float(
        _LIQUIDUS_MODEL["basic_modifier_slope_K_per_mole_fraction"]
    ) * (x_basic - float(_LIQUIDUS_MODEL["anchor_x_basic_modifier"]))
    estimated_K = min(
        float(_LIQUIDUS_MODEL["max_temperature_K"]),
        max(float(_LIQUIDUS_MODEL["min_temperature_K"]), estimated_K),
    )
    return {
        "temperature_K": float(temperature_K),
        "estimated_liquidus_K": estimated_K,
        "uncertainty_K": float(_LIQUIDUS_MODEL["uncertainty_K"]),
        "sub_liquidus": float(temperature_K) < estimated_K,
        "model": "anchored_linear_12022_uncertified_v0",
        "status": "UNCERTIFIED_PARAMETERIZED_ESTIMATE",
    }


def structural_activity_diagnostic(
    oxide_mol_by_species: Mapping[str, float],
    *,
    temperature_K: float,
) -> dict[str, Any]:
    """Build the run diagnostic payload for structural gamma tuning."""

    features = structural_activity_features(oxide_mol_by_species)
    gamma = reference_activity_coefficients(
        nbo_t=features.nbo_t,
        optical_basicity=features.optical_basicity,
        temperature_K=temperature_K,
    )
    reference_activity = {
        species: gamma_value
        * features.single_cation_mole_fractions.get(species, 0.0)
        for species, gamma_value in gamma.items()
    }
    liquidus = estimate_liquidus_flag(
        formula_unit_mole_fractions=features.formula_unit_mole_fractions,
        temperature_K=temperature_K,
    )
    return {
        "diagnostic_only": True,
        "tier": "UNCERTIFIED",
        "model": "structural_gamma_log_linear_v0",
        "intended_consumer": (
            "future vapor-path gating decision and structural-gamma "
            "tuning harness"
        ),
        "nbo_t": features.nbo_t,
        "nbo_t_raw": features.nbo_t_raw,
        "optical_basicity": features.optical_basicity,
        "oxygen_mol": features.oxygen_mol,
        "tetrahedral_cations_mol": features.tetrahedral_cations_mol,
        "al_charge_balance": {
            "charge_balanced_al_mol": features.charge_balanced_al_mol,
            "charge_capacity_mol": features.al_charge_capacity_mol,
            "capacity_sources": "Na + K + 2*Ca + 2*Mg",
        },
        "single_cation_mole_fractions": features.single_cation_mole_fractions,
        "formula_unit_mole_fractions": features.formula_unit_mole_fractions,
        "liquidus": liquidus,
        "reference_gamma_MOx": gamma,
        "reference_activity_MOx": reference_activity,
        "comparison_anchors": dict(_GAMMA_COMPARISON_ANCHORS),
        "unsupported_species": list(features.unsupported_species),
        "provenance": [
            "NBO/T: Mysen & Richet network-former oxygen budget",
            "optical_basicity: Duffy & Ingram / Mills oxide Lambda table",
            "gamma anchors: DeMaria 1971 re-pin in local volatility grounding",
            "liquidus anchor: Sossi & Fegley 2018 12022 liquidus ~=1573 K",
        ],
    }
