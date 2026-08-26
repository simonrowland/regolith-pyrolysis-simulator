"""UNCERTIFIED structural melt-oxide activity diagnostics.

This module is intentionally diagnostic-only. It computes NBO/T, optical
basicity, a coarse liquidus flag, and provisional reference activity
coefficients for later comparison against literature and engine sweeps. It
does not provide authoritative vapor-pressure activities.

Public temperature paths require finite T > 0 K. Inventory amounts that
are non-finite, non-numeric, or below-dust-floor negative raise
ValueError from normalize_formula_unit_moles rather than being dropped.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from simulator.scalar_boundary import is_declared_real_scalar


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

# Orthosilicate M2SiO4 is the last composition whose tetrahedral-network
# description is intact. Past this ceiling the log-linear gamma surface is
# out of domain (see structural_gamma_domain_verdict).
NBO_T_ORTHOSILICATE_CEILING = 4.0
# Inherited diagnostic display envelope, applied in log10 space so 10**x
# is not evaluated on an overflowing argument (t-717).
#
# Premise: the previous contract was min(1.0, max(1e-12, 10**x)). That is
# a display clamp, not an overflow identity. 10**x overflows a C double
# only for x > log10(DBL_MAX) ≈ 308.2547.
# Algebra: comparing x to log10(envelope) first makes the pow a no-op
# outside (-12, 0) and finite inside it. x >= 0 still returns 1.0 even
# when 10**x is a finite number above 1.
# Unit check: log10(gamma) is dimensionless dex; 10**dex = gamma.
# Sanity, this module at the 12022 intercept: 1600 K MgO raw
# 10**(0.0005*100) = 1.1220184543; this clamp returns 1.0. Sossi &
# Fegley 2018 Table 2 MgO ≈0.25-4 at 1873 K includes gamma>1; the cap
# maps that above-unity band to 1.0 on this function's path.
_LOG10_GAMMA_DISPLAY_MIN = -12.0  # gamma = 1e-12
_LOG10_GAMMA_DISPLAY_MAX = 0.0  # gamma = 1.0
# Signed ledger dust on a depleted parent can be ~1e-15 mol (see
# melt_activity.single_cation_mole_fractions). This module is called on
# the same account_view; treat [-dust, 0] as absent.
_INVENTORY_NUMERICAL_DUST_MOL = 1.0e-12

_GAMMA_MODEL = {
    "NaO0.5": {
        # DeMaria-inverted lunar-basalt anchor from local volatility grounding:
        # gamma_NaO0.5 ~= 4.5e-3 at 1500 K.
        "anchor_gamma_at_1500K": 4.5e-3,
        # Two-point DeMaria slope from 1300 K (1.8e-4) and 1500 K (4.5e-3):
        # log10(4.5e-3 / 1.8e-4) / 200 K. This function applies that slope
        # at whatever finite temperature_K it receives.
        # structural_gamma_domain_verdict checks NBO/T and optical basicity
        # only (that function's body has no T or phase test). Sossi &
        # Fegley 2018 printed p. 412-413: 12022 liquidus ≈1573 K and the
        # DeMaria Na/K gamma endpoints are 1300 and 1500 K, sub-liquidus.
        "temperature_slope_dex_per_K": math.log10(4.5e-3 / 1.8e-4) / 200.0,
        # Optical-basicity response calibrated against the CMS/Abdelouhab series
        # (COMPILED-ACTIVITY-KEMS.md Axis 3): log10(gamma) ~ 4.5 +/- 1.0 per
        # Lambda unit, scatter +/-0.8 dex. E-glass/Al-rich outliers break a
        # universal single-Lambda fit; UNCERTIFIED.
        "lambda_slope_dex": 4.5,
        # NBO/T response calibrated against the Zaitsev/Charles/Neudorf binary
        # alkali-silicate ladder (COMPILED-ACTIVITY-KEMS.md Axis 1):
        # log10(gamma_NaO0.5) ~ (-8.5 +/- 1.5) + (2.8 +/- 0.6) * NBO/T at
        # ~1473 K; scatter +/-1.5 dex across EMF/KEMS/transpiration families.
        #
        # On this function's log-linear surface both structural terms are
        # always added, so the Na factor is their product:
        #   10**(lambda_slope * dLambda + nbo_t_slope * dNBO/T)
        #   = 10**(lambda_slope * dLambda) * 10**(nbo_t_slope * dNBO/T).
        # Premise: Lambda and NBO/T are correlated composition axes; the
        # tabulated slopes are fitted marginals, not a joint Toop-Samis/MQM
        # calibration. Adding both therefore double-counts shared structure.
        # Unit check: slopes are dex per Lambda and dex per NBO/T; the
        # product of two 10**dex factors is dimensionless gamma.
        # Sanity (this function, 1500 K, 12022 intercept): dLambda=0.05 and
        # dNBO/T=0.5 give 10**(4.5*0.05)=1.678804, 10**(2.8*0.5)=25.118864,
        # product 42.169650; live Na gamma / 4.5e-3 equals that product.
        # This function has no one-axis mode. UNCERTIFIED.
        "nbo_t_slope_dex": 2.8,
    },
    "KO0.5": {
        # DeMaria-inverted lunar-basalt K anchor, Sossi & Fegley 2018 (OCR
        # source.md line ~350, Fig. 5): gamma_KO0.5 = 3.5e-5 at 1500 K.
        # Replaces the provisional 6.0e-3 (was ~170x high vs the primary).
        "anchor_gamma_at_1500K": 3.5e-5,
        # Two-point K slope from the same primary: 3.5e-5 @1500 K vs 7.2e-5
        # @1300 K — gamma RISES on cooling (opposite sign to Na, weak).
        # log10(3.5e-5 / 7.2e-5) / 200 ~= -1.57e-3 dex/K. Sign-ambiguous in
        # the literature (K3/K4 lanes disagree); UNCERTIFIED. Same as Na:
        # this function applies the slope at any finite T it is given;
        # structural_gamma_domain_verdict does not test T or phase.
        "temperature_slope_dex_per_K": math.log10(3.5e-5 / 7.2e-5) / 200.0,
        # Same calibrated structural axes as Na (K binary ladder parallels Na
        # with slightly lower gamma at fixed NBO/T; Axis 1 slope 3.0 +/- 0.6).
        "lambda_slope_dex": 4.5,
        "nbo_t_slope_dex": 3.0,
    },
    "CaO": {
        # Sossi & Fegley 2018 Table 2 (printed p. 409), Beckett 2002 CMAS
        # row: CaO gamma ≈0.001-0.15 at 1873 K, 0.55 < basicity < 0.65.
        # The table text says use as a guide; values vary with composition,
        # T, and fO2. This module stores 1.2e-2 as anchor_gamma_at_1500K,
        # 373 K below the table isotherm. The 5.0e-4 dex/K T slope below
        # is local and provisional, not a Table 2 fit.
        # structural_gamma_domain_verdict does not test CMAS membership
        # or the 0.55-0.65 basicity band.
        "anchor_gamma_at_1500K": 1.2e-2,
        # Local provisional T slope, not a Table 2 derivative. Positive so
        # unclamped gamma increases with T; the display cap at 1 still
        # applies (see reference_activity_coefficients).
        "temperature_slope_dex_per_K": 5.0e-4,
        # Weak provisional basicity response for CaO.
        "lambda_slope_dex": 1.0,
        # Weak provisional depolymerization response for CaO.
        "nbo_t_slope_dex": 0.10,
    },
    "MgO": {
        # Sossi & Fegley 2018 Table 2 (printed p. 409), Beckett 2002 CMAS
        # row: MgO gamma ≈0.25-4 at 1873 K, 0.55 < basicity < 0.65, so the
        # table band straddles unity. This module stores 1.0 as
        # anchor_gamma_at_1500K (373 K below that row) with the same local
        # 5.0e-4 dex/K T slope. The inherited display cap at gamma=1 maps
        # any above-unity MgO result back to 1.0 on this function's path.
        "anchor_gamma_at_1500K": 1.0,
        # Local provisional T slope, not a Table 2 derivative. Positive so
        # unclamped gamma increases with T; the display cap at 1 still
        # applies (see reference_activity_coefficients).
        "temperature_slope_dex_per_K": 5.0e-4,
        # Weak provisional basicity response for MgO.
        "lambda_slope_dex": 0.8,
        # Weak provisional depolymerization response for MgO.
        "nbo_t_slope_dex": 0.10,
    },
}

_LIQUIDUS_MODEL = {
    # Sossi & Fegley 2018 printed p. 412: lunar basalt 12022 liquidus
    # ≈ 1300 C = 1573 K (Green et al. 1971). estimate_liquidus_flag uses
    # that as the intercept of an affine plane. Four composition slopes
    # are asserted, not identified: one point cannot determine four
    # independent coefficients.
    "anchor_temperature_K": 1573.0,
    # Sossi 12022 proxy formula-unit mole fractions derived from the local
    # fixture; they place the liquidus correlation at the anchor composition.
    "anchor_x_sio2": 0.4765126754480923,
    "anchor_x_al2o3": 0.0851862831590699,
    "anchor_x_alkali": 0.004835284857031333,
    "anchor_x_basic_modifier": 0.4176332580741079,
    # Provisional slopes: Si/Al raise the plane; alkali and basic
    # modifiers lower it. They were chosen to be conservative and
    # monotone about 12022. estimate_liquidus_flag applies this one
    # global plane to every mapping it is given, including empty and
    # single-oxide compositions, then clamps to [950, 2300] K. That
    # function has no melt-class, composition-distance, or phase-field
    # gate.
    "sio2_slope_K_per_mole_fraction": 800.0,
    "al2o3_slope_K_per_mole_fraction": 250.0,
    "alkali_slope_K_per_mole_fraction": -700.0,
    "basic_modifier_slope_K_per_mole_fraction": -120.0,
    # Guard rails for a fallback estimate, not physical phase-equilibrium bounds.
    "min_temperature_K": 950.0,
    "max_temperature_K": 2300.0,
    # Explicit wide error bar. sub_liquidus is T_K < estimated_K on this
    # plane, not a calibrated phase-boundary crossing.
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
    """Map non-positive / non-numeric values to 0.0.

    Inventory admission for ``normalize_formula_unit_moles`` is
    ``_require_finite_nonnegative_inventory_mol``, not this helper.
    """

    try:
        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise TypeError
        candidate = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(candidate) or candidate <= 0.0:
        return 0.0
    return candidate


def _require_finite_positive_temperature_K(value: Any) -> float:
    """Refuse non-numeric, non-finite, or non-positive kelvin.

    Scoped to the three functions in this module that take
    ``temperature_K``. Finite T > 0 K, including values outside the
    1300-1500 K alkali-anchor interval, still pass.
    """

    if not is_declared_real_scalar(value, allow_numeric_str=True):
        raise ValueError(
            f"temperature_K must be a finite number > 0 K, got {value!r}"
        )
    temperature_K = float(value)
    if not math.isfinite(temperature_K) or temperature_K <= 0.0:
        raise ValueError(
            f"temperature_K must be a finite number > 0 K, got {value!r}"
        )
    return temperature_K


def _require_finite_nonnegative_inventory_mol(species: str, value: Any) -> float:
    """Refuse non-numeric, non-finite, or below-dust-floor negative moles."""

    if not is_declared_real_scalar(value, allow_numeric_str=True):
        raise ValueError(
            f"melt inventory for {species!r} must be finite and non-negative"
        )
    mol = float(value)
    if not math.isfinite(mol):
        raise ValueError(
            f"melt inventory for {species!r} must be finite and non-negative"
        )
    if mol < 0.0:
        if mol >= -_INVENTORY_NUMERICAL_DUST_MOL:
            return 0.0
        raise ValueError(
            f"melt inventory for {species!r} must be finite and non-negative"
        )
    return mol


def normalize_formula_unit_moles(
    oxide_mol_by_species: Mapping[str, float],
) -> tuple[dict[str, float], tuple[str, ...]]:
    """Return positive oxide formula-unit moles plus ignored species names.

    Ignored names are species not in ``_OXIDE_COMPONENTS`` or
    ``_FORMULA_UNIT_ALIASES`` whose amounts are finite and non-negative.
    Non-finite, non-numeric, or below-dust-floor negative amounts raise
    ``ValueError`` rather than being dropped. Finite zero, and signed dust
    in ``[-1e-12, 0]``, is treated as absent. An empty mapping still
    returns an empty basis.
    """

    formula_mol: dict[str, float] = {}
    unsupported: list[str] = []
    for raw_species, raw_mol in dict(oxide_mol_by_species or {}).items():
        species = str(raw_species)
        mol = _require_finite_nonnegative_inventory_mol(species, raw_mol)
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


def structural_gamma_domain_verdict(
    nbo_t: float | None,
    optical_basicity: float | None,
) -> tuple[str, str]:
    """Return ``('ok', '')`` or ``('out_of_domain', typed reason)``.

    Derivation
    ----------
    1. Premise: Mysen & Richet NBO/T = (2 O - 4 T) / T counts non-bridging
       oxygen per tetrahedral network-former. It describes a silicate
       *network*, not free-oxide melt with a vanishing T budget.
    2. Algebra: NBO/T = 4  =>  2O - 4T = 4T  =>  O = 4T. Each tetrahedral
       cation is then an isolated SiO4 (orthosilicate M2SiO4). For
       T_cations -> 0 the coordinate diverges; this module already stores
       nbo_t = None at T = 0.
    3. Premise: the log-linear gamma surface is a first-order expansion
       about lunar-basalt NBO/T ~ 1.14 (DeMaria/12022). The KO0.5 NBO/T
       slope is 3.0 dex per NBO/T unit, so at NBO/T ~ 105 (2 wt% SiO2 in
       CaO) unconstrained log10(gamma_KO0.5) ~ 308.5, and 10**x overflows
       a C double (IEEE-754 binary64 max ~ 1.80e308, log10 ~ 308.25).
    4. Domain statement: NBO/T > 4 is dilute_network_former_out_of_domain.
       Returning a clamped finite gamma there would fabricate an activity
       coefficient the expansion does not support. nbo_t is None is the
       same reason (no tetrahedral network at all).
    5. Unit check: NBO/T is mol/mol, dimensionless. Ceiling 4 is
       dimensionless.
    6. Sanity: silica NBO/T = 0; sodium disilicate NBO/T = 1; Ca2SiO4
       (2 CaO + 1 SiO2) NBO/T = 4, in-domain inclusive; 2 wt% SiO2 in CaO
       has NBO/T ~ 105, out of domain.

    This function does not test temperature or phase. Those checks, if
    any, live in the callers that take ``temperature_K``.
    """

    if nbo_t is None:
        return (
            "out_of_domain",
            "dilute_network_former_out_of_domain: tetrahedral "
            "network-former cations are absent so NBO/T is undefined",
        )
    nbo = float(nbo_t)
    if not math.isfinite(nbo):
        return (
            "out_of_domain",
            "dilute_network_former_out_of_domain: NBO/T is non-finite",
        )
    if nbo > NBO_T_ORTHOSILICATE_CEILING:
        return (
            "out_of_domain",
            "dilute_network_former_out_of_domain: "
            f"NBO/T={nbo:.6g} exceeds orthosilicate ceiling "
            f"{NBO_T_ORTHOSILICATE_CEILING:g} (Mysen tetrahedral-network "
            "budget; isolated SiO4 at NBO/T=4)",
        )
    if optical_basicity is None or not math.isfinite(float(optical_basicity)):
        return (
            "out_of_domain",
            "optical_basicity_undefined: no finite Duffy-Ingram Lambda "
            "for the log-linear gamma surface",
        )
    return "ok", ""


def reference_activity_coefficients(
    *,
    nbo_t: float | None,
    optical_basicity: float | None,
    temperature_K: float,
) -> dict[str, float]:
    """Return provisional log-linear structural gamma_MOx values.

    Out of the NBO/T domain the surface returns empty rather than a
    clamped number. In-domain, the inherited display envelope [1e-12, 1]
    is applied in log10 space so ``10**x`` is not attempted on an
    overflowing argument.

    Derivation (in-domain log-space envelope)
    -----------------------------------------
    1. Premise: the previous contract was ``min(1.0, max(1e-12, 10**x))``.
       That is a display clamp, not an overflow-derived physical ceiling.
       Overflow of ``10**x`` requires x > log10(DBL_MAX) ≈ 308.2547.
    2. Algebra: comparing x to log10(envelope) first makes the pow a
       no-op outside (-12, 0) and finite inside it. Values with x >= 0
       are returned as 1.0 even when ``10**x`` is a finite number above 1.
    3. Unit check: log10(gamma) is dimensionless dex; 10**dex = gamma
       dimensionless.
    4. Sanity, this function at the 12022 intercept:
       - 1500 K: every delta is 0 so gamma_NaO0.5 = 4.5e-3 (inside the
         envelope, unclamped).
       - 1600 K: MgO raw ``10**(0.0005*100)`` = 1.1220184543; this clamp
         returns 1.0.

    On this function's path both Lambda and NBO/T terms are always
    added (see ``_GAMMA_MODEL`` NaO0.5). ``temperature_K`` must be finite
    and > 0 K; this function has no 1300-1500 K calibration-window gate.
    """

    temperature_K = _require_finite_positive_temperature_K(temperature_K)
    status, _reason = structural_gamma_domain_verdict(nbo_t, optical_basicity)
    if status != "ok":
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
        if not math.isfinite(log10_gamma):
            # Non-finite log is not a finite gamma. Skip the species
            # rather than raise OverflowError or invent a number.
            continue
        if log10_gamma >= _LOG10_GAMMA_DISPLAY_MAX:
            gamma[species] = 1.0
        elif log10_gamma <= _LOG10_GAMMA_DISPLAY_MIN:
            gamma[species] = 1.0e-12
        else:
            gamma[species] = 10.0 ** log10_gamma
    return gamma


def estimate_liquidus_flag(
    *,
    formula_unit_mole_fractions: Mapping[str, float],
    temperature_K: float,
) -> dict[str, Any]:
    """Return a coarse liquidus estimate and sub-liquidus flag.

    On this function's path the estimate is the 12022-anchored affine
    plane, clamped to the rails in ``_LIQUIDUS_MODEL``. Missing oxide
    keys contribute 0 mole fraction. ``temperature_K`` must be finite
    and > 0 K. ``sub_liquidus`` is ``T_K < estimated_K`` on that plane.
    """

    temperature_K = _require_finite_positive_temperature_K(temperature_K)
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
        "temperature_K": temperature_K,
        "estimated_liquidus_K": estimated_K,
        "uncertainty_K": float(_LIQUIDUS_MODEL["uncertainty_K"]),
        "sub_liquidus": temperature_K < estimated_K,
        "model": "anchored_linear_12022_uncertified_v0",
        "status": "UNCERTIFIED_PARAMETERIZED_ESTIMATE",
    }


def structural_activity_diagnostic(
    oxide_mol_by_species: Mapping[str, float],
    *,
    temperature_K: float,
) -> dict[str, Any]:
    """Build the run diagnostic payload for structural gamma tuning.

    ``temperature_K`` must be finite and > 0 K. Known-oxide (and alias)
    amounts that are non-finite, non-numeric, or below-dust-floor
    negative raise ``ValueError`` via ``normalize_formula_unit_moles``.
    """

    temperature_K = _require_finite_positive_temperature_K(temperature_K)
    features = structural_activity_features(oxide_mol_by_species)
    gamma_status, gamma_reason = structural_gamma_domain_verdict(
        features.nbo_t, features.optical_basicity
    )
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
        "reference_gamma_status": gamma_status,
        "reference_gamma_reason": gamma_reason,
        "nbo_t_orthosilicate_ceiling": NBO_T_ORTHOSILICATE_CEILING,
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
