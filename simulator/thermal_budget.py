"""Standalone thermal-budget diagnostics for bootstrap furnace sizing.

This module is intentionally golden-neutral: it has no AtomLedger, runner, or
fixture integration.  It exposes a callable decomposition that callers may use
to inspect heat-flow bookkeeping and the cold-skull active cooling floor.

Model scope:
- heat in is caller-supplied solar/electrical thermal input;
- sinks are feed sensible+fusion enthalpy, reaction/disproportionation
  enthalpy, product-vapor enthalpy, melt-surface radiation, and outer-wall
  radiation;
- cold-skull active extraction is the ideal/minimum steady-state heat-pipe or
  metal-conduction load after passive radiation to space.

Assumptions and uncertified refinements:
- process gas flow is never a cooling term on this furnace-sizing floor;
  pO2 and neutral sweep remain chemistry controls, not furnace-wall heat
  sinks. The benchtop power-driven pellet residual is a separate balance:
  continuum argon at Nu = 2 is a sink there, because that pellet sits in
  the printed low-pressure argon, and so is the evaporative enthalpy at
  the same trial temperature. Neither term is part of this floor;
- wall conductivity, wall thickness, wall area, solidus, outer-wall
  temperature, sky temperature, and view factor are caller-supplied unless a
  caller provides source tags;
- creep, thermal shock, and mbar forced-convection coefficient are structured
  uncertified gaps and are not part of the ideal/minimum heat-balance floor.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from engines.antoine import _read_antoine_coefficients
from engines.builtin.vapor_pressure import BUILTIN_VAPOR_BELOW_400K_LIMIT_K
from simulator.diagnostic_helpers.timeseries_concordance import (
    _interpolate,
    _normalise_curve,
)
from simulator.equipment import EquipmentDesigner, STEFAN_BOLTZMANN
from simulator.furnace_materials import load_furnace_materials
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.process_timestep import PROCESS_HOUR_S
from simulator.scalar_boundary import is_declared_real_scalar
from simulator.state import GAS_CONSTANT, MOLAR_MASS, OXIDE_TO_METAL, STOICH_RATIOS


CITED = "CITED"
ASSUMED = "ASSUMED"
UNCERTIFIED = "UNCERTIFIED"

KELVIN_OFFSET = CELSIUS_TO_KELVIN_OFFSET
MELT_EMISSIVITY = EquipmentDesigner.MELT_EMISSIVITY
THERMAL_BUDGET_VIEW_FACTOR = 1.0
THERMAL_BUDGET_WALL_CONDUCTIVITY_W_M_K = 1.5
THERMAL_BUDGET_WALL_INNER_SOLIDUS_T_C = 1050.0
KJ_PER_KWH = 3600.0
UNCERTIFIED_GAP_SPECS = (
    (
        "creep",
        "Long-duration hot-wall creep life is not certified by this heat-balance floor.",
    ),
    (
        "thermal-shock",
        "Thermal-shock cycling limits are not certified by this heat-balance floor.",
    ),
    (
        "mbar-h",
        "mbar forced-convection coefficient is not certified; gas remains a chemistry trim lever, not primary cooling.",
    ),
)


@dataclass(frozen=True)
class EnthalpyCoefficient:
    kJ_per_mol: float
    source: str


# Product-vapor latent heats.  Values are kJ/mol product vapor.
_LATENT_VAPORIZATION_KJ_PER_MOL: dict[str, EnthalpyCoefficient] = {
    # NIST-JANAF phase-change table, Chase 1998: Na(l)->Na(g), ΔvapH=97.42 kJ/mol.
    "Na": EnthalpyCoefficient(97.42, "NIST-JANAF Chase 1998 Na(l)->Na(g) ΔvapH=97.42 kJ/mol"),
    # NIST-JANAF phase-change table, Chase 1998: K(l)->K(g), ΔvapH=76.90 kJ/mol.
    "K": EnthalpyCoefficient(76.90, "NIST-JANAF Chase 1998 K(l)->K(g) ΔvapH=76.90 kJ/mol"),
    # NIST-JANAF phase-change table, Chase 1998: Mg(l)->Mg(g), ΔvapH=127.40 kJ/mol.
    "Mg": EnthalpyCoefficient(127.40, "NIST-JANAF Chase 1998 Mg(l)->Mg(g) ΔvapH=127.40 kJ/mol"),
    # NIST-JANAF phase-change table, Chase 1998: Fe(l)->Fe(g), ΔvapH=340.00 kJ/mol.
    "Fe": EnthalpyCoefficient(340.00, "NIST-JANAF Chase 1998 Fe(l)->Fe(g) ΔvapH=340.00 kJ/mol"),
    # NIST-JANAF phase-change table, Chase 1998: Ca(l)->Ca(g), ΔvapH=153.60 kJ/mol.
    "Ca": EnthalpyCoefficient(153.60, "NIST-JANAF Chase 1998 Ca(l)->Ca(g) ΔvapH=153.60 kJ/mol"),
    # NIST-JANAF phase-change table, Chase 1998: Al(l)->Al(g), ΔvapH=284.10 kJ/mol.
    "Al": EnthalpyCoefficient(284.10, "NIST-JANAF Chase 1998 Al(l)->Al(g) ΔvapH=284.10 kJ/mol"),
    # NIST-JANAF phase-change table, Chase 1998: Si(l)->Si(g), ΔvapH=359.00 kJ/mol.
    "Si": EnthalpyCoefficient(359.00, "NIST-JANAF Chase 1998 Si(l)->Si(g) ΔvapH=359.00 kJ/mol"),
    # NIST-JANAF phase-change table, Chase 1998: Ti(l)->Ti(g), ΔvapH=425.00 kJ/mol.
    "Ti": EnthalpyCoefficient(425.00, "NIST-JANAF Chase 1998 Ti(l)->Ti(g) ΔvapH=425.00 kJ/mol"),
    # NIST-JANAF phase-change table, Chase 1998: Cr(l)->Cr(g), ΔvapH=339.50 kJ/mol.
    "Cr": EnthalpyCoefficient(339.50, "NIST-JANAF Chase 1998 Cr(l)->Cr(g) ΔvapH=339.50 kJ/mol"),
    # NIST-JANAF phase-change table, Chase 1998: Mn(l)->Mn(g), ΔvapH=220.50 kJ/mol.
    "Mn": EnthalpyCoefficient(220.50, "NIST-JANAF Chase 1998 Mn(l)->Mn(g) ΔvapH=220.50 kJ/mol"),
    # Oxide vapors are NOT reduced metals: they do not go through a
    # metal latent + full oxide->element dissociation. They form directly from
    # the melt oxide in a single reaction (parent_oxide -> oxide_vapor(g) +
    # partial O2) and are handled via _OXIDE_VAPOR_REACTION_KJ_PER_MOL below.
}


def latent_vaporization_kj_per_mol(species: str) -> float:
    """Return the cited product-vapor latent heat without exposing its table."""

    coefficient = _LATENT_VAPORIZATION_KJ_PER_MOL.get(str(species))
    if coefficient is None:
        raise KeyError(f"no latent-vaporization coefficient for {species!r}")
    return float(coefficient.kJ_per_mol)


# Parent-oxide dissociation enthalpies.  Values are kJ/mol parent oxide.
#
# BASIS BOUNDARY: these are reversals of 298 K standard formation enthalpies
# for the named condensed oxide and elements in their 298 K standard states
# (Na(s)/K(s)/Mg(s) for the cross-checked rows) + O2(g).
# They are not interchangeable with ``ELLINGHAM_THERMO`` dH coefficients:
# those are
# effective intercepts from high-temperature linear dG(T) fits, per mol O2,
# whose JANAF phase basis includes high-T metal/oxide phase changes.  The
# expected Na/K/Mg separation is pinned in
# ``tests/test_thermochemical_basis_split.py``.
_OXIDE_DISSOCIATION_KJ_PER_MOL: dict[str, EnthalpyCoefficient] = {
    # NIST-JANAF Chase 1998 ΔfH° Na2O(s)=-414.22 kJ/mol; Na2O -> 2Na + 1/2O2 = +414.22.
    "Na2O": EnthalpyCoefficient(414.22, "NIST-JANAF Chase 1998 Na2O(s) ΔfH=-414.22 kJ/mol"),
    # NIST-JANAF Chase 1998 ΔfH° K2O(s)=-363.17 kJ/mol; K2O -> 2K + 1/2O2 = +363.17.
    "K2O": EnthalpyCoefficient(363.17, "NIST-JANAF Chase 1998 K2O(s) ΔfH=-363.17 kJ/mol"),
    # NIST-JANAF Chase 1998 ΔfH° MgO(s)=-601.60 kJ/mol; MgO -> Mg + 1/2O2 = +601.60.
    "MgO": EnthalpyCoefficient(601.60, "NIST-JANAF Chase 1998 MgO(s) ΔfH=-601.60 kJ/mol"),
    # NIST-JANAF Chase 1998 ΔfH° FeO(s)=-272.04 kJ/mol; FeO -> Fe + 1/2O2 = +272.04.
    "FeO": EnthalpyCoefficient(272.04, "NIST-JANAF Chase 1998 FeO(s) ΔfH=-272.04 kJ/mol"),
    # NIST-JANAF Chase 1998 ΔfH° CaO(s)=-635.09 kJ/mol; CaO -> Ca + 1/2O2 = +635.09.
    "CaO": EnthalpyCoefficient(635.09, "NIST-JANAF Chase 1998 CaO(s) ΔfH=-635.09 kJ/mol"),
    # NIST-JANAF Chase 1998 ΔfH° Al2O3(s)=-1675.69 kJ/mol; Al2O3 -> 2Al + 3/2O2 = +1675.69.
    "Al2O3": EnthalpyCoefficient(1675.69, "NIST-JANAF Chase 1998 Al2O3(s) ΔfH=-1675.69 kJ/mol"),
    # NIST-JANAF Chase 1998 ΔfH° SiO2(quartz)=-910.94 kJ/mol; SiO2 -> Si + O2 = +910.94.
    "SiO2": EnthalpyCoefficient(910.94, "NIST-JANAF Chase 1998 SiO2(quartz) ΔfH=-910.94 kJ/mol"),
    # NIST-JANAF Chase 1998 ΔfH° TiO2(rutile)=-944.75 kJ/mol; TiO2 -> Ti + O2 = +944.75.
    "TiO2": EnthalpyCoefficient(944.75, "NIST-JANAF Chase 1998 TiO2(rutile) ΔfH=-944.75 kJ/mol"),
    # NIST-JANAF Chase 1998 ΔfH° Cr2O3(s)=-1139.70 kJ/mol; Cr2O3 -> 2Cr + 3/2O2 = +1139.70.
    "Cr2O3": EnthalpyCoefficient(1139.70, "NIST-JANAF Chase 1998 Cr2O3(s) ΔfH=-1139.70 kJ/mol"),
    # NIST-JANAF Chase 1998 ΔfH° MnO(s)=-385.20 kJ/mol; MnO -> Mn + 1/2O2 = +385.20.
    "MnO": EnthalpyCoefficient(385.20, "NIST-JANAF Chase 1998 MnO(s) ΔfH=-385.20 kJ/mol"),
}


# Oxide-vapor single-reaction enthalpies.  Values are kJ/mol oxide vapor.
#
# A species that evaporates AS AN OXIDE (SiO, CrO2), not as a reduced metal,
# must not be charged metal latent PLUS full oxide->element dissociation: that
# double-counts.  For SiO the buggy path routed SiO2 all the way to elemental
# Si + O2 (910.94 kJ/mol) and then also added an SiO(condensed)->SiO(g) latent
# (337.60), ~54% high and physically wrong.  The real path is a single reaction
#   parent_oxide(melt) -> oxide_vapor(g) + partial O2,
# ΔH follows the balanced per-mol-vapor reaction below; the parent-oxide
# coefficient is species-specific (1 SiO2 per SiO, but 1/2 Cr2O3 per CrO2).
_OXIDE_VAPOR_REACTION_KJ_PER_MOL: dict[str, EnthalpyCoefficient] = {
    "Al2": EnthalpyCoefficient(
        2176.991722,
        "nasa-cea-thermo:cea_AL2_gibbs ΔfH298=+501.301722 kJ/mol + NIST-JANAF "
        "Al2O3(s) ΔfH298=-1675.69; Al2O3->Al2(g)+3/2O2 => +2176.991722",
    ),
    "Al2O2": EnthalpyCoefficient(
        1272.594413,
        "nasa-cea-thermo:cea_AL2O2_gibbs ΔfH298=-403.095587 kJ/mol + NIST-JANAF "
        "Al2O3(s) ΔfH298=-1675.69; Al2O3->Al2O2(g)+1/2O2 => +1272.594413",
    ),
    "Al2O3_gas": EnthalpyCoefficient(
        1128.799470,
        "nasa-cea-thermo:cea_AL2O3_gibbs ΔfH298=-546.890530 kJ/mol + NIST-JANAF "
        "Al2O3(s) ΔfH298=-1675.69; Al2O3(s)->Al2O3(g) => +1128.799470",
    ),
    "AlO2": EnthalpyCoefficient(
        799.187129,
        "nasa-cea-thermo:cea_ALO2_gibbs ΔfH298=-38.657871 kJ/mol + 1/2 NIST-JANAF "
        "Al2O3(s) ΔfH298=-1675.69; 1/2Al2O3+1/4O2->AlO2(g) => +799.187129",
    ),
    "Ca2": EnthalpyCoefficient(
        1611.945336,
        "nasa-cea-thermo:cea_Ca2_gibbs ΔfH298=+341.765336 kJ/mol + 2 NIST-JANAF "
        "CaO(s) ΔfH298=-635.09; 2CaO->Ca2(g)+O2 => +1611.945336",
    ),
    "CrO": EnthalpyCoefficient(
        756.431318,
        "nasa-cea-thermo:cea_CrO_gibbs ΔfH298=+186.581318 kJ/mol + 1/2 NIST-JANAF "
        "Cr2O3(s) ΔfH298=-1139.70; 1/2Cr2O3->CrO(g)+1/4O2 => +756.431318",
    ),
    # NIST-JANAF Chase 1998: ΔfH°[SiO2, α-quartz]=-910.94, ΔfH°[SiO(g)]=-100.42;
    # SiO2 -> SiO(g) + 1/2 O2 = -100.42 - (-910.94) = +810.52 kJ/mol SiO.
    "SiO": EnthalpyCoefficient(
        810.52,
        "NIST-JANAF Chase 1998: SiO2(quartz) ΔfH=-910.94 + SiO(g) ΔfH=-100.42 "
        "=> SiO2->SiO(g)+1/2 O2 = +810.52 kJ/mol SiO",
    ),
    # CrO2(g) forms from Cr2O3 by OXIDATION (Cr3+ -> Cr4+), not dissociation:
    #   1/2 Cr2O3 + 1/4 O2 -> CrO2(g), ΔH = ΔfH[CrO2(g)] - 1/2 ΔfH[Cr2O3].
    # ΔfH[Cr2O3(s)]=-1139.70 (NIST-JANAF). ΔfH[CrO2(g)]=-75.31 kJ/mol from NIST
    # Chemistry WebBook SRD 69, chromium dioxide (CAS 12018-01-8), gas-phase
    # thermochemistry, method Review, ref Chase 1998 / NIST-JANAF 4th ed
    # (J. Phys. Chem. Ref. Data Monograph 9). => ΔH = -75.31 + 569.85 = +494.54.
    # Confidence stays MODERATE / UNCERTIFIED: the WebBook/JANAF value is clean,
    # but the independent Barin and Ebbinghaus table values were NOT recovered,
    # so the known gas Cr-oxide scatter is not closed -- no certification gate may
    # treat this as ground truth (grounding: docs-private/research/
    # 2026-07-05-cro2-grounding/findings.md). Cr-bearing basalts DO evaporate a
    # trace CrO2 flux, so this MUST be present (fail-loud here crashes real runs);
    # the single-reaction form is far more correct than metal latent + full
    # Cr2O3->2Cr dissociation (which double-counts).
    "CrO2": EnthalpyCoefficient(
        494.54,
        "1/2 Cr2O3 + 1/4 O2 -> CrO2(g); ΔfH[Cr2O3(s)]=-1139.70 (NIST-JANAF) + "
        "ΔfH[CrO2(g)]=-75.31 (NIST WebBook SRD 69, CAS 12018-01-8, Chase 1998/"
        "NIST-JANAF; MODERATE/UNCERTIFIED -- Barin & Ebbinghaus not verified, gas "
        "Cr-oxide scatter not closed) => +494.54 kJ/mol CrO2",
    ),
    # CEA cea_TiO_gibbs gives ΔfH°298[TiO(g)]=+49.503615 kJ/mol and
    # NIST-JANAF gives ΔfH°298[TiO2(s)]=-944.75. For
    # TiO2->TiO(g)+1/2O2, ΔH=49.503615-(-944.75)=+994.253615 kJ/mol.
    "TiO": EnthalpyCoefficient(
        994.253615,
        "nasa-cea-thermo:cea_TiO_gibbs ΔfH298=+49.503615 kJ/mol + "
        "NIST-JANAF TiO2(s) ΔfH298=-944.75; TiO2->TiO(g)+1/2O2 => +994.253615",
    ),
    # CEA cea_TiO2_gibbs gives -305.430 kJ/mol. For TiO2(s)->TiO2(g),
    # ΔH=-305.430-(-944.75)=+639.320 kJ/mol; O2 coefficient is zero.
    "TiO2_gas": EnthalpyCoefficient(
        639.320,
        "nasa-cea-thermo:cea_TiO2_gibbs ΔfH298=-305.430 kJ/mol + "
        "NIST-JANAF TiO2(s) ΔfH298=-944.75; TiO2(s)->TiO2(g) => +639.320",
    ),
    # CEA cea_CaO_gibbs gives +38.005308 kJ/mol. For CaO(s)->CaO(g),
    # ΔH=38.005308-(-635.09)=+673.095308 kJ/mol; dimensions are kJ/mol gas.
    "CaO_gas": EnthalpyCoefficient(
        673.095308,
        "nasa-cea-thermo:cea_CaO_gibbs ΔfH298=+38.005308 kJ/mol + "
        "NIST-JANAF CaO(s) ΔfH298=-635.09; CaO(s)->CaO(g) => +673.095308",
    ),
    # One AlO consumes 1/2 Al2O3. CEA cea_ALO_gibbs gives +67.319006;
    # ΔH=67.319006-0.5*(-1675.69)=+905.164006 kJ/mol AlO. The 1/2
    # coefficient also checks one Al atom on each side.
    "AlO": EnthalpyCoefficient(
        905.164006,
        "nasa-cea-thermo:cea_ALO_gibbs ΔfH298=+67.319006 kJ/mol + "
        "NIST-JANAF Al2O3(s) ΔfH298=-1675.69; 1/2Al2O3->AlO(g)+1/4O2 "
        "=> +905.164006",
    ),
    # One Al2O consumes one Al2O3. CEA cea_AL2O_gibbs gives -148.611286;
    # ΔH=-148.611286-(-1675.69)=+1527.078714 kJ/mol Al2O.
    "Al2O": EnthalpyCoefficient(
        1527.078714,
        "nasa-cea-thermo:cea_AL2O_gibbs ΔfH298=-148.611286 kJ/mol + "
        "NIST-JANAF Al2O3(s) ΔfH298=-1675.69; Al2O3->Al2O(g)+O2 "
        "=> +1527.078714",
    ),
    # One CrO3 consumes 1/2 Cr2O3 plus 3/4 O2. CEA cea_CrO3_gibbs gives
    # -322.037084; ΔH=-322.037084-0.5*(-1139.70)=+247.812916 kJ/mol.
    # Positive but smaller than CrO2 is sensible because this is oxidation.
    "CrO3": EnthalpyCoefficient(
        247.812916,
        "nasa-cea-thermo:cea_CrO3_gibbs ΔfH298=-322.037084 kJ/mol + "
        "NIST-JANAF Cr2O3(s) ΔfH298=-1139.70; 1/2Cr2O3+3/4O2->CrO3(g) "
        "=> +247.812916",
    ),
}

# Species that evaporate as an oxide vapor (single-reaction path), not a metal.
_OXIDE_VAPOR_SPECIES: frozenset[str] = frozenset(
    {
        "SiO",
        "Al2",
        "Al2O2",
        "Al2O3_gas",
        "AlO2",
        "Ca2",
        "CrO",
        "CrO2",
        "TiO",
        "TiO2_gas",
        "CaO_gas",
        "AlO",
        "Al2O",
        "CrO3",
    }
)


def evaporation_enthalpy_budget(
    species_kg_hr: Mapping[str, float],
    *,
    vapor_pressures: Mapping[str, Any] | None = None,
    temperature_K: float | None = None,
) -> dict[str, Any]:
    """Return one-hour evaporation-enthalpy sinks for evaporated species.

    This is diagnostic-only: it reads vapor species metadata and cited
    coefficients, but does not touch AtomLedger or any kg-keyed state.

    APPROXIMATION: the metal path charges (M(l)->M(g) latent) + (MOx->M+1/2 O2
    dissociation from 298 K ΔfH). It omits the M(s)->M(l) fusion step (~1-4% of
    the per-metal total, Si worst) and all 298 K->process-T sensible heat, so it
    is a consistent low-order ESTIMATE of the evaporation enthalpy, not the full
    MOx(melt, T)->M(g)+1/2 O2 path enthalpy. Oxide vapors use a
    single cited melt-oxide->oxide-vapor(g) reaction instead of that two-leg form.
    """

    latent_by_species: dict[str, float] = {}
    dissociation_by_species: dict[str, float] = {}
    sources: dict[str, str] = {}

    for species, raw_kg_hr in sorted(species_kg_hr.items()):
        kg_hr = _non_negative(float(raw_kg_hr), f"species_kg_hr[{species!r}]")
        if kg_hr <= 0.0:
            continue

        metadata = _vapor_metadata(species, vapor_pressures)
        product_mol = kg_hr * 1000.0 / _molar_mass_g_mol(species, metadata)

        analytical_reaction_coeff = _analytical_reaction_enthalpy(
            species,
            metadata,
            temperature_K=temperature_K,
        )
        if analytical_reaction_coeff is not None:
            # Source-reaction carriers already include condensed-component
            # breakup and any O2 coproduct in one NASA-polynomial delta-H.
            # Charging a metal latent term as well would double-count.
            reaction_kWh = (
                product_mol
                * analytical_reaction_coeff.kJ_per_mol
                / KJ_PER_KWH
            )
            latent_by_species[species] = 0.0
            dissociation_by_species[species] = reaction_kWh
            sources[f"analytical_source_reaction:{species}"] = (
                analytical_reaction_coeff.source
            )
            continue

        if species in _OXIDE_VAPOR_SPECIES:
            # Oxide vapor: a single reaction parent_oxide(melt) -> oxide_vapor(g)
            # + partial O2 (1:1 in the oxide-forming cation), NOT metal latent +
            # full oxide->element dissociation.  Booked as the reaction sink with
            # zero metal-latent, per mol of oxide vapor produced.
            reaction_coeff = _required_enthalpy(
                _OXIDE_VAPOR_REACTION_KJ_PER_MOL,
                species,
                "oxide-vapor formation",
            )
            reaction_kWh = product_mol * reaction_coeff.kJ_per_mol / KJ_PER_KWH
            latent_by_species[species] = 0.0
            dissociation_by_species[species] = reaction_kWh
            sources[f"oxide_vapor_reaction:{species}"] = reaction_coeff.source
            continue

        latent_coeff = _required_enthalpy(
            _LATENT_VAPORIZATION_KJ_PER_MOL,
            species,
            "latent vaporization",
        )
        latent_kWh = product_mol * latent_coeff.kJ_per_mol / KJ_PER_KWH
        latent_by_species[species] = latent_kWh
        sources[f"latent:{species}"] = latent_coeff.source

        parent_oxide = str(metadata.get("parent_oxide") or _parent_oxide_for(species))
        if not parent_oxide:
            raise ValueError(
                f"vapor species {species!r} requires parent_oxide metadata "
                "for oxide-dissociation enthalpy"
            )
        oxide_coeff = _required_enthalpy(
            _OXIDE_DISSOCIATION_KJ_PER_MOL,
            parent_oxide,
            "oxide dissociation",
        )
        oxide_kg = kg_hr * _oxide_per_product_kg(
            species,
            parent_oxide,
            metadata,
        )
        oxide_mol = oxide_kg * 1000.0 / _molar_mass_g_mol(parent_oxide, {})
        dissociation_kWh = oxide_mol * oxide_coeff.kJ_per_mol / KJ_PER_KWH
        dissociation_by_species[species] = dissociation_kWh
        sources[f"dissociation:{species}"] = oxide_coeff.source

    latent_kWh = sum(latent_by_species.values())
    dissociation_kWh = sum(dissociation_by_species.values())
    evaporation_thermal_kWh = latent_kWh + dissociation_kWh
    return {
        "schema": "evaporation_enthalpy_budget.v0",
        "status": "diagnostic_ledger_neutral",
        "evaporation_thermal_kWh": evaporation_thermal_kWh,
        "energy_scope": "electrical_plus_known_evaporation_enthalpy",
        "furnace_heat_status": "partial",
        "latent_kWh": latent_kWh,
        "dissociation_kWh": dissociation_kWh,
        "heat_flows_kWh": {
            "evaporation_enthalpy_sink": evaporation_thermal_kWh,
            "reaction_disproportionation_enthalpy_sink": dissociation_kWh,
            "product_vapor_enthalpy_sink": latent_kWh,
            "net_unallocated": 0.0,
        },
        "latent_by_species_kWh": latent_by_species,
        "dissociation_by_species_kWh": dissociation_by_species,
        "sources": sources,
    }


def thermal_budget_decomposition(
    *,
    wall_area_m2: float,
    wall_thickness_m: float,
    wall_conductivity_W_m_K: float,
    wall_inner_solidus_T_C: float,
    wall_outer_T_C: float,
    T_sky_K: float,
    view_factor: float,
    emissivity: float = MELT_EMISSIVITY,
    heat_in_kW: float | None = None,
    feed_sensible_fusion_enthalpy_kW: float | None = None,
    reaction_disproportionation_enthalpy_kW: float | None = None,
    product_vapor_enthalpy_kW: float | None = None,
    melt_T_C: float | None = None,
    melt_surface_area_m2: float | None = None,
    source_tags: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """Return tagged heat-flow terms and cold-skull active cooling floor.

    ``cold_skull_cooling_flux_kW_min`` is the ideal/minimum active extraction
    needed to hold the inner wall at the feedstock solidus:

    ``q_to_wall = k * (T_solidus - T_outer) / L``

    ``q_radiative_available = emissivity * sigma * view_factor
    * (T_outer**4 - T_sky**4)``

    ``active = max(0, q_to_wall - q_radiative_available)``

    All temperatures in the public API are Celsius except ``T_sky_K``.  All
    powers are kW; all fluxes are kW/m2.  Returned figures carry CITED,
    ASSUMED, or UNCERTIFIED status and a source note.
    """

    tags = dict(source_tags or {})

    wall_area_m2 = _positive(wall_area_m2, "wall_area_m2")
    wall_thickness_m = _positive(wall_thickness_m, "wall_thickness_m")
    wall_conductivity_W_m_K = _non_negative(
        wall_conductivity_W_m_K, "wall_conductivity_W_m_K"
    )
    view_factor = _unit_interval(view_factor, "view_factor")
    emissivity = _unit_interval(emissivity, "emissivity")

    solidus_K = _c_to_k(wall_inner_solidus_T_C, "wall_inner_solidus_T_C")
    wall_outer_K = _c_to_k(wall_outer_T_C, "wall_outer_T_C")
    T_sky_K = _kelvin(T_sky_K, "T_sky_K")

    # Fourier's law is signed on this inner-to-outer coordinate: reversing the
    # temperature gradient reverses q instead of erasing the diagnostic.
    conductive_W_per_m2 = (
        wall_conductivity_W_m_K
        * (solidus_K - wall_outer_K)
        / wall_thickness_m
    )
    wall_heat_kW_per_m2 = conductive_W_per_m2 / 1000.0
    wall_heat_kW = wall_heat_kW_per_m2 * wall_area_m2

    outer_wall_radiative_kW_per_m2 = _radiative_flux_kW_per_m2(
        wall_outer_K,
        T_sky_K,
        emissivity=emissivity,
        view_factor=view_factor,
    )
    outer_wall_radiative_kW = outer_wall_radiative_kW_per_m2 * wall_area_m2
    cooling_flux_kW_per_m2 = max(
        0.0, wall_heat_kW_per_m2 - outer_wall_radiative_kW_per_m2
    )
    cooling_kW = cooling_flux_kW_per_m2 * wall_area_m2

    melt_surface_radiative_loss_kW = None
    if melt_T_C is not None and melt_surface_area_m2 is not None:
        melt_K = _c_to_k(melt_T_C, "melt_T_C")
        melt_surface_area_m2 = _positive(
            melt_surface_area_m2, "melt_surface_area_m2"
        )
        melt_surface_radiative_loss_kW = (
            _radiative_flux_kW_per_m2(
                melt_K,
                T_sky_K,
                emissivity=emissivity,
                view_factor=view_factor,
            )
            * melt_surface_area_m2
        )

    heat_flows_kW = {
        "heat_in": _optional_non_negative(heat_in_kW, "heat_in_kW"),
        "feed_sensible_fusion_enthalpy_sink": _optional_non_negative(
            feed_sensible_fusion_enthalpy_kW,
            "feed_sensible_fusion_enthalpy_kW",
        ),
        "reaction_disproportionation_enthalpy_sink": _optional_non_negative(
            reaction_disproportionation_enthalpy_kW,
            "reaction_disproportionation_enthalpy_kW",
        ),
        "product_vapor_enthalpy_sink": _optional_non_negative(
            product_vapor_enthalpy_kW,
            "product_vapor_enthalpy_kW",
        ),
        "melt_surface_radiative_loss": melt_surface_radiative_loss_kW,
        "outer_wall_radiative_loss": outer_wall_radiative_kW,
        "cold_skull_active_extraction_sink": cooling_kW,
    }
    heat_flows_kW["net_unallocated"] = _net_unallocated(heat_flows_kW)

    figures = {
        "stefan_boltzmann_W_m2_K4": _figure(
            STEFAN_BOLTZMANN,
            "W/(m2 K4)",
            _tag(
                tags,
                "stefan_boltzmann_W_m2_K4",
                CITED,
                "simulator.equipment.STEFAN_BOLTZMANN",
            ),
        ),
        "emissivity": _figure(
            emissivity,
            "dimensionless",
            _cited_default_or_caller_supplied_tag(
                emissivity,
                MELT_EMISSIVITY,
                "simulator.equipment.EquipmentDesigner.MELT_EMISSIVITY",
            ),
        ),
        "view_factor": _figure(
            view_factor,
            "dimensionless",
            _cited_default_or_caller_supplied_tag(
                view_factor,
                THERMAL_BUDGET_VIEW_FACTOR,
                "simulator.accounting.queries._wall_geometry_conductance_weight view_factor_from_melt default",
            ),
        ),
        "wall_area_m2": _figure(
            wall_area_m2,
            "m2",
            _tag(tags, "wall_area_m2", ASSUMED, "caller supplied"),
        ),
        "wall_thickness_m": _figure(
            wall_thickness_m,
            "m",
            _tag(tags, "wall_thickness_m", ASSUMED, "caller supplied"),
        ),
        "wall_conductivity_W_m_K": _figure(
            wall_conductivity_W_m_K,
            "W/(m K)",
            _cited_default_or_caller_supplied_tag(
                wall_conductivity_W_m_K,
                THERMAL_BUDGET_WALL_CONDUCTIVITY_W_M_K,
                "bootstrap heat-balance reference: wall conductivity 1.5 W/(m K)",
            ),
        ),
        "wall_inner_solidus_T_C": _figure(
            wall_inner_solidus_T_C,
            "degC",
            _cited_default_or_caller_supplied_tag(
                wall_inner_solidus_T_C,
                THERMAL_BUDGET_WALL_INNER_SOLIDUS_T_C,
                "bootstrap heat-balance reference: solidus about 1050 C",
            ),
        ),
        "wall_outer_T_C": _figure(
            wall_outer_T_C,
            "degC",
            _tag(tags, "wall_outer_T_C", ASSUMED, "caller supplied"),
        ),
        "T_sky_K": _figure(
            T_sky_K,
            "K",
            _tag(tags, "T_sky_K", ASSUMED, "caller supplied"),
        ),
    }

    return {
        "schema": "thermal_budget_decomposition.v0",
        "status": "diagnostic_golden_neutral",
        "figures": figures,
        "heat_flows_kW": {
            key: _tagged_heat_term(key, value, tags)
            for key, value in heat_flows_kW.items()
        },
        "cold_skull": {
            "q_to_wall_kW": wall_heat_kW,
            "q_to_wall_kW_per_m2": wall_heat_kW_per_m2,
            "outer_wall_radiative_capacity_kW": outer_wall_radiative_kW,
            "outer_wall_radiative_capacity_kW_per_m2": (
                outer_wall_radiative_kW_per_m2
            ),
            "cold_skull_cooling_flux_kW_min": cooling_kW,
            "cold_skull_cooling_flux_kW_per_m2": cooling_flux_kW_per_m2,
            "status": "NOTICE",
            "basis": (
                "ideal/minimum active metal-conduction or heat-pipe extraction; "
                "passive radiation to space credited; process gas excluded"
            ),
        },
        "uncertified_gaps": _uncertified_gaps(),
        "notices": [
            (
                "Process gas, neutral sweep, and pO2 are excluded from cooling; "
                "mbar gas flow is a chemistry lever here, not a heat sink."
            ),
            (
                "Cooling flux is a thermodynamic floor only; creep, thermal "
                "shock, and mbar forced-convection coefficient remain uncertified."
            ),
        ],
    }


def furnace_material_context(
    material_id: str,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return material max-service context and conductivity certification state."""

    catalog_is_canonical = catalog is None
    raw_catalog = load_furnace_materials() if catalog_is_canonical else catalog
    items = raw_catalog.get("furnace_materials", raw_catalog)
    material = items.get(material_id)
    if not isinstance(material, Mapping):
        raise ValueError(f"unknown furnace material: {material_id}")

    grounding = material.get("grounding")
    if not isinstance(grounding, Mapping):
        grounding = {}
    grounding_tier = str(grounding.get("tier") or "")

    conductivity = material.get("conductivity_W_m_K")
    conductivity_status = (
        CITED
        if catalog_is_canonical and conductivity is not None
        else UNCERTIFIED if conductivity is None else ASSUMED
    )
    catalog_source = (
        "data/furnace_materials.yaml"
        if catalog_is_canonical
        else "caller-supplied furnace-material catalog"
    )
    conductivity_source = (
        f"{catalog_source}:{material_id}.conductivity_W_m_K"
        if conductivity is not None
        else f"{catalog_source} has no conductivity_W_m_K field"
    )
    max_service_status = (
        CITED
        if catalog_is_canonical and material.get("max_service_T_C") is not None
        else UNCERTIFIED if material.get("max_service_T_C") is None else ASSUMED
    )
    max_service_source = f"{catalog_source}:{material_id}.max_service_T_C"
    max_service_provenance = {
        "status": max_service_status,
        "source": max_service_source,
    }
    if grounding_tier == "proxy-sintering":
        max_service_provenance.update(
            {
                "status": UNCERTIFIED,
                "source": str(grounding.get("source") or max_service_source),
                "tier": grounding_tier,
                "caveat": str(grounding.get("caveat") or ""),
            }
        )
    return {
        "material_id": material_id,
        "display_name": material.get("display_name"),
        "max_service_T_C": _figure(
            material.get("max_service_T_C"),
            "degC",
            max_service_provenance,
        ),
        "conductivity_W_m_K": _figure(
            conductivity,
            "W/(m K)",
            {"status": conductivity_status, "source": conductivity_source},
        ),
        "grounding": dict(grounding),
        "source_note": material.get("source_note"),
    }


def _radiative_flux_kW_per_m2(
    hot_K: float,
    cold_K: float,
    *,
    emissivity: float,
    view_factor: float,
) -> float:
    # Net Stefan-Boltzmann exchange is signed; a hotter environment is a heat
    # source (negative loss), not a zero-loss condition.
    return (
        emissivity
        * STEFAN_BOLTZMANN
        * view_factor
        * (hot_K**4 - cold_K**4)
        / 1000.0
    )


def _net_unallocated(heat_flows_kW: Mapping[str, float | None]) -> float | None:
    heat_in = heat_flows_kW["heat_in"]
    sinks = [
        value
        for key, value in heat_flows_kW.items()
        if key != "heat_in" and key != "net_unallocated"
    ]
    if heat_in is None or any(value is None for value in sinks):
        return None
    return heat_in - sum(value for value in sinks if value is not None)


def _tagged_heat_term(
    key: str,
    value: float | None,
    tags: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    if value is None:
        return _figure(
            value,
            "kW",
            {"status": UNCERTIFIED, "source": "not supplied"},
        )
    return _figure(value, "kW", _tag(tags, key, ASSUMED, "caller supplied"))


def _vapor_metadata(
    species: str,
    vapor_pressures: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not vapor_pressures:
        return {}
    for section in ("metals", "oxide_vapors"):
        section_data = vapor_pressures.get(section, {})
        if isinstance(section_data, Mapping) and species in section_data:
            raw = section_data.get(species, {})
            if not isinstance(raw, Mapping):
                raise ValueError(f"vapor metadata for {species!r} must be a mapping")
            return dict(raw)
    raw = vapor_pressures.get(species)
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}


def _analytical_reaction_enthalpy(
    species: str,
    metadata: Mapping[str, Any],
    *,
    temperature_K: float | None,
) -> EnthalpyCoefficient | None:
    """Return NASA-polynomial delta-H per mole vapor for a source reaction."""

    model = metadata.get("reference_pressure_model")
    if isinstance(model, Mapping) and isinstance(
        model.get("gas_exchange_reaction"), Mapping
    ):
        return _composite_exchange_reaction_enthalpy(
            species,
            metadata,
            model,
            temperature_K=temperature_K,
        )

    if species == "P2O5_gas":
        return _direct_phase_transfer_reaction_enthalpy(
            species,
            model,
            temperature_K=temperature_K,
        )

    if species not in {"PO", "PO2", "P2", "P4", "P4O6", "P4O10"}:
        # b-133 lands a new thermal coefficient path only for its six CEA
        # phosphorus carriers. Existing analytical families retain their
        # pre-b-133 latent/dissociation accounting until separately reviewed.
        return None

    if not isinstance(model, Mapping):
        return None
    family = str(model.get("evaluator_family") or "")
    thermo = model.get("species_thermo")
    if family not in {"nasa_cea_7", "nasa_cea_9"} or not isinstance(
        thermo, Mapping
    ):
        return None
    if temperature_K is None:
        raise ValueError(
            f"temperature_K is required for analytical source-reaction enthalpy of {species!r}"
        )
    T_K = float(temperature_K)
    if not math.isfinite(T_K) or T_K <= 0.0:
        raise ValueError("temperature_K must be finite and positive")

    parent = str(metadata.get("parent_oxide") or "")
    parent_mol = float(metadata.get("source_oxide_mol_per_vapor_mol", 0.0))
    oxygen_mol = float(metadata.get("source_O2_mol_per_vapor_mol", 0.0))
    if not parent or parent_mol <= 0.0 or oxygen_mol < 0.0:
        raise ValueError(
            f"analytical source-reaction stoichiometry unavailable for {species!r}"
        )
    required = {parent, species}
    if oxygen_mol > 0.0:
        required.add("O2")
    missing = sorted(required - set(str(key) for key in thermo))
    if missing:
        raise ValueError(
            f"analytical source-reaction thermo unavailable for {species!r}: {missing}"
        )

    # Reuse the catalog's landed NASA7/NASA9 parser so pressure and energy
    # diagnostics evaluate the same CEA coefficient convention.
    from simulator.vapour_rail.catalog import _polynomial_from_thermo_record

    polynomials = {
        formula: _polynomial_from_thermo_record(
            name=f"thermal_budget:{species}:{formula}",
            family=family,
            record=thermo[formula],
        )
        for formula in required
    }
    delta_h_J_per_mol_vapor = (
        polynomials[species].evaluate(T_K).h_J_per_mol
        + (
            oxygen_mol * polynomials["O2"].evaluate(T_K).h_J_per_mol
            if oxygen_mol > 0.0
            else 0.0
        )
        - parent_mol * polynomials[parent].evaluate(T_K).h_J_per_mol
    )
    if not math.isfinite(delta_h_J_per_mol_vapor):
        raise ValueError(
            f"analytical source-reaction enthalpy is non-finite for {species!r}"
        )
    parent_record = thermo[parent]
    parent_reference = str(parent_record.get("cea_name") or parent)
    phase_status = ""
    if "P4O10(L)" in parent_reference:
        phase_status = (
            "; metastable P4O10(L)-derived component reference, so signed "
            "reaction heat is status-bearing and may be negative"
        )
    return EnthalpyCoefficient(
        delta_h_J_per_mol_vapor / 1000.0,
        (
            f"NASA CEA {family} delta-H(T={T_K:.2f} K) for the catalog "
            f"{parent}(melt) source reaction; analytical/status-bearing, "
            f"cannot certify{phase_status}"
        ),
    )


def _composite_exchange_reaction_enthalpy(
    species: str,
    metadata: Mapping[str, Any],
    model: Mapping[str, Any],
    *,
    temperature_K: float | None,
) -> EnthalpyCoefficient:
    """Compose base-reaction and gas-exchange delta-H per mole target vapor."""

    if temperature_K is None:
        raise ValueError(
            f"temperature_K is required for analytical source-reaction enthalpy of {species!r}"
        )
    T_K = float(temperature_K)
    if not math.isfinite(T_K) or T_K <= 0.0:
        raise ValueError("temperature_K must be finite and positive")

    base_model = model.get("base_reference_pressure_model")
    if not isinstance(base_model, Mapping) or str(
        base_model.get("evaluator_family") or ""
    ) != "antoine":
        raise ValueError(
            f"analytical composite base reaction unavailable for {species!r}"
        )
    coefficients = base_model.get("coefficients")
    if not isinstance(coefficients, Mapping):
        raise ValueError(
            f"analytical composite base coefficients unavailable for {species!r}"
        )
    _A, B, C = _read_antoine_coefficients(base_model)
    if B <= 0.0 or T_K + C <= 0.0:
        raise ValueError(
            f"analytical composite base coefficients invalid for {species!r}"
        )
    # Premise: log10(P_base/Pa)=A-B/(T+C) is the landed unit-activity
    # standard-reaction term. Van't Hoff gives
    # DeltaH_base = R*T^2*d(ln K)/dT = R*ln(10)*B*T^2/(T+C)^2.
    # Units: J/mol base vapor. Sanity: B>0 yields an endothermic base release.
    base_delta_h_J_per_mol = (
        GAS_CONSTANT * math.log(10.0) * B * T_K**2 / (T_K + C) ** 2
    )

    family = str(model.get("evaluator_family") or "")
    thermo = model.get("species_thermo")
    exchange = model.get("gas_exchange_reaction")
    if family not in {"nasa_cea_7", "nasa_cea_9"} or not isinstance(
        thermo, Mapping
    ) or not isinstance(exchange, Mapping):
        raise ValueError(
            f"analytical gas-exchange thermo unavailable for {species!r}"
        )

    from simulator.vapour_rail.catalog import (
        _polynomial_from_thermo_record,
        _resolve_thermo_record,
    )

    target_formula = str(metadata.get("formula") or species)
    base_formula = str(model.get("base_vapor_formula") or "")
    target_nu = 0.0
    base_reactant_nu = 0.0
    exchange_delta_h_J_per_mol = 0.0
    for sign, side in ((-1.0, "reactants"), (1.0, "products")):
        participants = exchange.get(side)
        if not isinstance(participants, list):
            raise ValueError(
                f"analytical gas-exchange reaction is incomplete for {species!r}"
            )
        for participant in participants:
            if not isinstance(participant, Mapping):
                raise ValueError(
                    f"analytical gas-exchange participant is invalid for {species!r}"
                )
            formula = str(participant.get("formula") or "")
            amount = float(participant.get("stoichiometry", 0.0))
            record = thermo.get(formula)
            if not formula or amount <= 0.0 or not isinstance(record, Mapping):
                raise ValueError(
                    f"analytical gas-exchange thermo unavailable for {species!r}:{formula!r}"
                )
            resolved = _resolve_thermo_record(
                record, field=f"thermal_budget:{species}:{formula}"
            )
            polynomial = _polynomial_from_thermo_record(
                name=f"thermal_budget:{species}:{formula}",
                family=family,
                record=resolved,
            )
            exchange_delta_h_J_per_mol += (
                sign * amount * polynomial.evaluate(T_K).h_J_per_mol
            )
            if sign > 0.0 and formula == target_formula:
                target_nu += amount
            if sign < 0.0 and formula == base_formula:
                base_reactant_nu += amount
    if target_nu <= 0.0 or base_reactant_nu <= 0.0:
        raise ValueError(
            f"analytical gas-exchange stoichiometry unavailable for {species!r}"
        )

    delta_h_J_per_mol_vapor = (
        base_reactant_nu * base_delta_h_J_per_mol
        + exchange_delta_h_J_per_mol
    ) / target_nu
    if not math.isfinite(delta_h_J_per_mol_vapor):
        raise ValueError(
            f"analytical source-reaction enthalpy is non-finite for {species!r}"
        )
    return EnthalpyCoefficient(
        delta_h_J_per_mol_vapor / 1000.0,
        (
            f"Landed base standard-reaction Antoine van't Hoff delta-H plus "
            f"NASA CEA {family} gas-exchange delta-H(T={T_K:.2f} K); "
            "analytical/status-bearing, cannot certify"
        ),
    )


def _direct_phase_transfer_reaction_enthalpy(
    species: str,
    model: Any,
    *,
    temperature_K: float | None,
) -> EnthalpyCoefficient:
    """Evaluate the landed P2O5(L) -> P2O5(g) CEA phase-transfer delta-H."""

    if temperature_K is None:
        raise ValueError(
            f"temperature_K is required for analytical source-reaction enthalpy of {species!r}"
        )
    T_K = float(temperature_K)
    if not math.isfinite(T_K) or T_K <= 0.0:
        raise ValueError("temperature_K must be finite and positive")
    if not isinstance(model, Mapping):
        raise ValueError(f"analytical phase-transfer model unavailable for {species!r}")
    family = str(model.get("evaluator_family") or "")
    thermo = model.get("species_thermo")
    if family not in {"nasa_cea_7", "nasa_cea_9"} or not isinstance(
        thermo, Mapping
    ):
        raise ValueError(f"analytical phase-transfer thermo unavailable for {species!r}")

    from simulator.vapour_rail.catalog import (
        _polynomial_from_thermo_record,
        _resolve_thermo_record,
    )

    polynomials = {}
    for formula in ("P2O5(L)", "P2O5"):
        record = thermo.get(formula)
        if not isinstance(record, Mapping):
            raise ValueError(
                f"analytical phase-transfer thermo unavailable for {species!r}:{formula}"
            )
        polynomials[formula] = _polynomial_from_thermo_record(
            name=f"thermal_budget:{species}:{formula}",
            family=family,
            record=_resolve_thermo_record(
                record, field=f"thermal_budget:{species}:{formula}"
            ),
        )
    delta_h_J_per_mol = (
        polynomials["P2O5"].evaluate(T_K).h_J_per_mol
        - polynomials["P2O5(L)"].evaluate(T_K).h_J_per_mol
    )
    return EnthalpyCoefficient(
        delta_h_J_per_mol / 1000.0,
        (
            f"NASA CEA {family} delta-H(T={T_K:.2f} K) for "
            "P2O5(L)->P2O5(g); analytical/status-bearing, cannot certify"
        ),
    )


def _molar_mass_g_mol(species: str, metadata: Mapping[str, Any]) -> float:
    raw_value = metadata.get("molar_mass_g_mol")
    if raw_value is None:
        raw_value = MOLAR_MASS.get(species)
    if raw_value is None:
        raise ValueError(f"missing molar mass for {species!r}")
    value = float(raw_value)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"invalid molar mass for {species!r}: {raw_value!r}")
    return value


def _parent_oxide_for(species: str) -> str:
    for oxide, (metal, _n_metal, _n_oxygen) in OXIDE_TO_METAL.items():
        if species == metal:
            return oxide
    return ""


def _oxide_per_product_kg(
    species: str,
    parent_oxide: str,
    metadata: Mapping[str, Any],
) -> float:
    explicit = metadata.get("stoich_oxide_per_vapor")
    if explicit is not None:
        value = float(explicit)
        if math.isfinite(value) and value > 0.0:
            return value
        raise ValueError(
            f"invalid stoich_oxide_per_vapor for {species!r}: {explicit!r}"
        )

    fallback = STOICH_RATIOS.get(parent_oxide)
    implied_species = OXIDE_TO_METAL.get(parent_oxide, ("", 0, 0))[0]
    if fallback and implied_species == species and fallback[0] > 0.0:
        return 1.0 / fallback[0]

    raise ValueError(
        f"vapor species {species!r} from {parent_oxide!r} requires "
        "stoich_oxide_per_vapor metadata for oxide-dissociation enthalpy"
    )


def _required_enthalpy(
    table: Mapping[str, EnthalpyCoefficient],
    key: str,
    label: str,
) -> EnthalpyCoefficient:
    coefficient = table.get(key)
    if coefficient is None:
        raise ValueError(f"missing cited {label} enthalpy coefficient for {key!r}")
    if not math.isfinite(coefficient.kJ_per_mol):
        raise ValueError(f"non-finite {label} enthalpy coefficient for {key!r}")
    return coefficient


def _tag(
    tags: Mapping[str, Mapping[str, str]],
    key: str,
    default_status: str,
    default_source: str,
) -> dict[str, str]:
    tag = tags.get(key, {})
    return {
        "status": str(tag.get("status", default_status)),
        "source": str(tag.get("source", default_source)),
    }


def _cited_default_or_caller_supplied_tag(
    value: float,
    cited_value: float,
    cited_source: str,
) -> dict[str, str]:
    if math.isclose(value, cited_value, rel_tol=1e-9):
        return {"status": CITED, "source": cited_source}

    return {"status": ASSUMED, "source": "caller supplied"}


def _uncertified_gaps() -> list[dict[str, str]]:
    return [
        {"name": name, "status": UNCERTIFIED, "reason": reason}
        for name, reason in UNCERTIFIED_GAP_SPECS
    ]


def _figure(value: Any, unit: str, tag: Mapping[str, Any]) -> dict[str, Any]:
    figure = {
        "value": value,
        "unit": unit,
        "status": tag["status"],
        "source": tag["source"],
    }
    for key, extra_value in tag.items():
        if key not in figure:
            figure[str(key)] = extra_value
    return figure


def _positive(value: float, name: str) -> float:
    number = _finite(value, name)
    if number <= 0.0:
        raise ValueError(f"{name} must be > 0")
    return number


def _non_negative(value: float, name: str) -> float:
    number = _finite(value, name)
    if number < 0.0:
        raise ValueError(f"{name} must be >= 0")
    return number


def _optional_non_negative(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    return _non_negative(value, name)


def _unit_interval(value: float, name: str) -> float:
    number = _finite(value, name)
    if number < 0.0 or number > 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return number


def _c_to_k(value_C: float, name: str) -> float:
    return _kelvin(_finite(value_C, name) + KELVIN_OFFSET, name)


def _kelvin(value_K: float, name: str) -> float:
    number = _finite(value_K, name)
    if number < 0.0:
        raise ValueError(f"{name} must be >= 0 K")
    return number


def _finite(value: float, name: str) -> float:
    try:
        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise TypeError
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


# A run deck opts into the power-driven melt temperature by carrying this
# key on its lab schedule. Absent, the schedule's melt_temperature_C
# series stays the target. The key is not part of the schedule digest.
FURNACE_POWER_DRIVER_KEY = "furnace_power_driver"
KNOB_DEFAULTED = "knob_defaulted"
_POWER_TRACE_ROLE = "measured_driver"
_POWER_TRACE_UNITS = "W"
_POWER_TIME_FIELD = "time_min"
_POWER_VALUE_FIELD = "expected_value"
_MINUTE_S = float(PROCESS_HOUR_S) / 60.0
_BISECTION_STEPS = 80
_UNSCORED_TEMPERATURE_SOURCE_CLASSES = frozenset({
    "assumption_with_sensitivity_marker",
    "engine_prediction",
    "not_reported",
})
# Gaier, Street and Gustafson 2010, NASA TM E-17366 / NTRS 20100025839.
# Large-particle lunar-simulant total solar absorptance is 0.669 to 0.906.
# The default is the midpoint of that measured band. It is not
# EquipmentDesigner.MELT_EMISSIVITY: a solar absorptance and a thermal-IR
# emissivity are not the same number, and 0.85 would only match by coincidence.
# Robinot Fig. 3 calls the molten cap highly reflective. That cap was not
# measured, so it is not folded into the midpoint.
GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_LOW = 0.669
GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_HIGH = 0.906
SOLAR_ABSORPTANCE_DEFAULT = (
    GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_LOW
    + GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_HIGH
) / 2.0
# The glass-sphere transmittance is unprinted. 1 is the clean-window upper
# bound. Opacification only lowers the power that reaches the pellet.
WINDOW_TRANSMITTANCE_UPPER_BOUND = 1.0
TYPED_UNKNOWN = "typed_unknown"
# The tonne sketch is anchored at 1000 kg and is still the area default
# at 1 kg (the solver docstring's sanity figure). Below 1 kg it is smaller
# than the printed top face of a gram pellet, so it is not a default.
TONNE_SIZER_DOMAIN_MIN_KG = 1.0
FACE_HOT_UPPER = "hot_upper"
FACE_FULL_SIDE = "full_side"
_FACE_SETS = frozenset({FACE_HOT_UPPER, FACE_FULL_SIDE})
# Water-cooled holder. The steel temperature is unprinted. 300 K is the
# typed-unknown boundary shared by the enclosure, the slab cold end, and
# the argon. It is not the preset holder series, which ends at 1500 C.
COLD_BOUNDARY_K = 300.0
# Molten and glassy basalt, used as a band. Büttner et al. report about
# 1.0 W m-1 K-1 at 1200 C and about 0.6 at 1350 C. Hofmeister and
# coworkers put basaltic melts near 1.4. Lesher and Spera, Encyclopedia
# of Volcanoes ch. 5, quote about 0.6 in the summary and about 1.2 to
# 1.5 in the compiled table. The default is 1.0, the midpoint of 0.6
# and 1.4 and Büttner's 1200 C value. It is not tuned to a pyrometer
# reading, and it is not THERMAL_BUDGET_WALL_CONDUCTIVITY_W_M_K.
PELLET_CONDUCTIVITY_LOW_W_M_K = 0.6
PELLET_CONDUCTIVITY_HIGH_W_M_K = 1.4
PELLET_CONDUCTIVITY_DEFAULT_W_M_K = 1.0
# Still-gas sphere. Continuum conduction at the printed diameter, not a
# fitted Nusselt number and not a free-molecular flux.
NU_STILL_SPHERE = 2.0
# engines.builtin.vapor_pressure.BUILTIN_VAPOR_BELOW_400K_LIMIT_K is
# the thermal physical zero. The kinetic flux, the analytical vapor
# loop, and this balance all treat a temperature below it as no
# evaporation. The bisection bracket starts at 0 K, and an empty
# pressure map there raises EvaporationFluxRefusal, so the callback
# is not called below the limit.
# Bracket ceiling, not a material limit. The 13 mm top face at
# emissivity 0.85 radiates eps * sigma * A * T**4. A_top = pi*(6.5 mm)**2
# = 1.327e-4 m². At 4000 K, T**4 = 2.56e14, sigma*T**4 = 1.45e7 W/m²,
# times A and 0.85 is about 1640 W. That is above the Robinot focal
# peak (1469 W) and above the doubled-power test's absorbed watts.
# A flux refusal at or above this temperature is the hot side of the
# bracket, not a missed root. Below it, a refusal still fails the step.
EVAPORATIVE_BRACKET_CEILING_K = 4000.0
# NIST argon thermal conductivity, lambda column, at 1000 K. Monatomic
# conductivity tracks viscosity, so the exponent 0.75 is that scaling.
ARGON_CONDUCTIVITY_ANCHOR_K = 1000.0
ARGON_CONDUCTIVITY_1000K_W_M_K = 0.0438
ARGON_CONDUCTIVITY_EXPONENT = 0.75
_MM_TO_M = 1.0e-3
_DECLARED_FOCAL_LENGTH_MM = (
    ("pellet_diameter_mm", "pellet_diameter_m"),
    ("pellet_height_mm", "pellet_height_m"),
    ("focal_spot_diameter_mm", "focal_spot_diameter_m"),
)


class PowerTemperatureRefusal(ValueError):
    """Typed refusal for a furnace-power temperature step.

    A missing emissivity takes the sizer default and emits
    ``knob_defaulted``. A missing area does that only at or above 1 kg.
    Below 1 kg the tonne sizer is ``radiative_area_out_of_domain``.
    On a printed pellet, a missing conductivity takes the basalt-band
    midpoint and is noticed. The cold boundary is not read from a
    holder series.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason)
        message = self.reason if not detail else f"{self.reason}: {detail}"
        super().__init__(message)


def measured_furnace_power_trace(
    document: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """The one units=W measured_driver, or None when the deck has none.

    Any other recorded trace is not this driver. Two watt drivers refuse
    ``ambiguous_furnace_power_trace`` rather than picking one.
    """

    if not isinstance(document, Mapping):
        return None
    comparison = document.get("trajectory_comparison")
    if comparison is None:
        return None
    if not isinstance(comparison, Mapping):
        raise PowerTemperatureRefusal(
            "furnace_power_trace_malformed",
            "trajectory_comparison must be a mapping",
        )
    traces = comparison.get("recorded_input_traces")
    if traces is None:
        return None
    if isinstance(traces, (str, bytes)) or not isinstance(traces, Sequence):
        raise PowerTemperatureRefusal(
            "furnace_power_trace_malformed",
            "recorded_input_traces must be a sequence",
        )
    selected: list[Mapping[str, Any]] = []
    for index, item in enumerate(traces):
        if not isinstance(item, Mapping):
            raise PowerTemperatureRefusal(
                "furnace_power_trace_malformed",
                f"recorded_input_traces[{index}] must be a mapping",
            )
        role = str(item.get("role") or "").strip()
        units = str(item.get("units") or "").strip()
        if role == _POWER_TRACE_ROLE and units == _POWER_TRACE_UNITS:
            selected.append(item)
    if not selected:
        return None
    if len(selected) > 1:
        raise PowerTemperatureRefusal(
            "ambiguous_furnace_power_trace",
            f"{len(selected)} units=W measured_driver traces",
        )
    return selected[0]


def furnace_power_driver_payload(
    trace: Mapping[str, Any],
    *,
    mass_kg: float,
    exposed_area_m2: float | None,
    pellet_diameter_m: float | None = None,
    pellet_height_m: float | None = None,
    focal_spot_diameter_m: float | None = None,
    radiating_face_set: str | None = None,
    solar_absorptance: float | None = None,
    window_transmittance: float | None = None,
    conductivity_W_m_K: float | None = None,
) -> dict[str, Any]:
    """Schedule payload the stepper reads. Emissivity stays unset here.

    The loader does not invent an emissivity. ``None`` tells the solver
    to take ``EquipmentDesigner.MELT_EMISSIVITY`` and notice it. A focal
    spot is booked only when the deck declares both diameters. Absorptance,
    window transmittance, and pellet conductivity stay unset so the solver
    notices its own defaults. This function does not multiply the trace
    and does not carry a holder temperature.
    """

    if not isinstance(trace, Mapping):
        raise PowerTemperatureRefusal(
            "furnace_power_trace_malformed",
            "trace must be a mapping",
        )
    return {
        "trace": copy.deepcopy(trace),
        "mass_kg": mass_kg,
        "exposed_area_m2": exposed_area_m2,
        "emissivity": None,
        "pellet_diameter_m": pellet_diameter_m,
        "pellet_height_m": pellet_height_m,
        "focal_spot_diameter_m": focal_spot_diameter_m,
        "radiating_face_set": radiating_face_set,
        "solar_absorptance": solar_absorptance,
        "window_transmittance": window_transmittance,
        "conductivity_W_m_K": conductivity_W_m_K,
    }


def declared_benchtop_lengths_m(
    sample: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Pellet cylinder, focal diameter, and face set the deck prints.

    Lengths are metres. A missing key is omitted. A present length that
    is not positive and finite refuses. This does not invent a spot
    diameter, a height, or a face set, and it does not turn millimetres
    into an area.
    """

    if sample is None:
        return {}
    if not isinstance(sample, Mapping):
        raise PowerTemperatureRefusal(
            "benchtop_geometry_malformed",
            "lab geometry sample must be a mapping",
        )
    lengths: dict[str, Any] = {}
    for source_key, dest_key in _DECLARED_FOCAL_LENGTH_MM:
        if source_key not in sample or sample.get(source_key) in (None, ""):
            continue
        millimetres = _declared_finite(
            sample.get(source_key),
            missing="benchtop_length_not_positive",
            invalid="benchtop_length_not_positive",
        )
        if millimetres <= 0.0:
            raise PowerTemperatureRefusal(
                "benchtop_length_not_positive",
                f"{source_key} is {millimetres}",
            )
        lengths[dest_key] = millimetres * _MM_TO_M
    face = sample.get("radiating_face_set")
    if face not in (None, ""):
        if not isinstance(face, str):
            raise PowerTemperatureRefusal(
                "radiating_face_set_unknown",
                "radiating face set must be a string",
            )
        lengths["radiating_face_set"] = face.strip()
    return lengths


def pellet_face_areas_m2(
    diameter_m: float,
    height_m: float,
) -> dict[str, float]:
    """Printed-cylinder faces. The bottom is the holder contact.

    Premise: Robinot section 3.2 prints a 13 mm by 10 mm cylinder on a
    water-cooled holder. The contacted base conducts. It does not see
    the chamber. Fig. 3 shows the lower half still solid, so that side
    wall is not at the top temperature. ``hot_upper`` is the top plus
    half the wall. The booked two-node path radiates that area from
    node 1 at T_top and the other half-wall from node 2 at T_side.
    ``full_side`` remains the isothermal bound.

    Algebra::

        A_top = pi * (d / 2) ** 2
        A_side = pi * d * H
        hot_upper = A_top + A_side / 2
        full_side = A_top + A_side

    Units: d and H in metres, areas in m².

    Sanity: d = 13 mm, H = 10 mm gives A_top = 1.327×10⁻⁴ m²,
    hot_upper = 3.369×10⁻⁴ m², full_side = 5.411×10⁻⁴ m². The tonne
    sizer at 3.38 g is 4.32×10⁻⁵ m², smaller than the top face.
    """

    top = math.pi * (diameter_m / 2.0) ** 2
    side = math.pi * diameter_m * height_m
    return {
        "top": top,
        "side": side,
        FACE_HOT_UPPER: top + 0.5 * side,
        FACE_FULL_SIDE: top + side,
    }


def _booked_radiating_area(
    *,
    charge_kg: float,
    area_m2: float | None,
    pellet_diameter_m: float | None,
    pellet_height_m: float | None,
    radiating_face_set: str | None,
) -> tuple[float, str | None, float | None, str | None, list[dict[str, Any]]]:
    """Booked area, its face set, and the other face as a sensitivity.

    An explicit area is the radiating area. A printed cylinder uses the
    declared face set. Below 1 kg an unset face set is the hot upper
    surface, noticed. The tonne sizer is the area only at or above 1 kg
    and only when no cylinder is printed. Below 1 kg, reaching that
    sizer refuses ``radiative_area_out_of_domain`` and does not use the
    number.
    """

    face = None if radiating_face_set in (None, "") else str(radiating_face_set).strip()
    if area_m2 is not None and face is not None:
        raise PowerTemperatureRefusal(
            "radiating_area_ambiguous",
            "a declared area and a face set are two radiating areas",
        )
    if area_m2 is not None:
        area = _declared_finite(
            area_m2,
            missing="melt_radiative_area_not_positive",
            invalid="melt_radiative_area_not_positive",
        )
        if area <= 0.0:
            raise PowerTemperatureRefusal(
                "melt_radiative_area_not_positive",
                f"area is {area} m2",
            )
        return area, None, None, None, []
    cylinder = pellet_diameter_m is not None and pellet_height_m is not None
    if face is not None and not cylinder:
        raise PowerTemperatureRefusal(
            "pellet_geometry_incomplete",
            "a face set needs the printed pellet diameter and height",
        )
    if pellet_diameter_m is not None and pellet_height_m is None:
        raise PowerTemperatureRefusal(
            "pellet_geometry_incomplete",
            "a printed pellet diameter needs its height",
        )
    if pellet_height_m is not None and pellet_diameter_m is None:
        raise PowerTemperatureRefusal(
            "pellet_geometry_incomplete",
            "a printed pellet height needs its diameter",
        )
    if cylinder:
        assert pellet_diameter_m is not None and pellet_height_m is not None
        areas = pellet_face_areas_m2(pellet_diameter_m, pellet_height_m)
        notices: list[dict[str, Any]] = []
        if face is None:
            if charge_kg < TONNE_SIZER_DOMAIN_MIN_KG:
                face = FACE_HOT_UPPER
                notices.append({
                    "kind": KNOB_DEFAULTED,
                    "knob": "radiating_face_set",
                    "value": face,
                    "unit": "1",
                    "source": (
                        "unset face set on a sample under 1 kg. "
                        "Fig. 3 default is the top face plus the hot upper side."
                    ),
                })
            else:
                raise PowerTemperatureRefusal(
                    "radiating_face_set_missing",
                    "a printed cylinder at or above 1 kg needs a face set",
                )
        elif face not in _FACE_SETS:
            raise PowerTemperatureRefusal(
                "radiating_face_set_unknown",
                f"face set {face!r} is not hot_upper or full_side",
            )
        other = FACE_FULL_SIDE if face == FACE_HOT_UPPER else FACE_HOT_UPPER
        return areas[face], face, areas[other], other, notices
    if charge_kg < TONNE_SIZER_DOMAIN_MIN_KG:
        raise PowerTemperatureRefusal(
            "radiative_area_out_of_domain",
            "the tonne-sizer area is not a default for a sample under 1 kg",
        )
    area = EquipmentDesigner.radiative_loss_area_m2(charge_kg)
    return area, None, None, None, [{
        "kind": KNOB_DEFAULTED,
        "knob": "melt_radiative_area_m2",
        "value": area,
        "unit": "m2",
        "source": "EquipmentDesigner.radiative_loss_area_m2",
    }]


def absorbed_focal_power_W(
    focal_power_W: float,
    *,
    pellet_diameter_m: float,
    focal_spot_diameter_m: float,
    solar_absorptance: float,
    window_transmittance: float,
) -> tuple[float, float]:
    """Watts absorbed by a pellet in a uniform focal disk.

    Premise: Robinot Eq. (6) is the power concentrated at the focal
    point, not a sample calorimeter. The pellet is the printed 13 mm
    disk. The spot is the paper's roughly 2 cm disk, the disk whose
    area is the stated 1.5 kW divided by 4.8 MW m-2. Sunlight that
    misses the pellet lands on the water-cooled holder and does not
    enter the melt balance. Solar absorptance is not the thermal
    emissivity. Window transmittance is a separate factor.

    Algebra (uniform-disk area ratio)::

        A_pellet / A_spot = (π d_pellet² / 4) / (π d_spot² / 4)
        intercept = min(1, (d_pellet / d_spot) ** 2)
        P_abs = P_focal * intercept * alpha * tau

    Units: both diameters use metres, so their ratio and the intercept are
    dimensionless; power remains watts after multiplying by alpha and tau.

    Sanity: 13 mm in a 20 mm spot intercepts (13/20)**2 = 0.4225.
    At the digitized peak 1468.819694 W, alpha 1 and tau 1, the pellet
    absorbs 620.576 W. The other 848 W hits the holder annulus.
    In the small-pellet limit the intercept tends to zero; at or above
    the spot diameter the cap gives an intercept of 1, so the pellet
    receives the whole focal disk, never more than it.
    """

    intercept = min(1.0, (pellet_diameter_m / focal_spot_diameter_m) ** 2)
    absorbed = (
        focal_power_W
        * intercept
        * solar_absorptance
        * window_transmittance
    )
    return absorbed, intercept


def _resolve_solar_absorptance(
    value: float | None,
) -> tuple[float, dict[str, Any] | None]:
    """Gaier midpoint when the deck does not print an absorptance.

    The notice records the band and that the molten cap was not measured.
    A declared value is used as given and is not noticed.
    """

    if value is None:
        alpha = float(SOLAR_ABSORPTANCE_DEFAULT)
        return alpha, {
            "kind": KNOB_DEFAULTED,
            "knob": "solar_absorptance",
            "value": alpha,
            "unit": "1",
            "band_low": GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_LOW,
            "band_high": GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_HIGH,
            "source": (
                "Gaier, Street and Gustafson 2010 large-particle "
                "simulant band 0.669 to 0.906, midpoint. Not "
                "MELT_EMISSIVITY. The reflective molten cap was not measured."
            ),
        }
    alpha = _declared_finite(
        value,
        missing="solar_absorptance_not_numeric",
        invalid="solar_absorptance_not_numeric",
    )
    if alpha < 0.0 or alpha > 1.0:
        raise PowerTemperatureRefusal(
            "solar_absorptance_out_of_range",
            f"solar absorptance is {alpha}",
        )
    return alpha, None


def _resolve_window_transmittance(
    value: float | None,
) -> tuple[float, dict[str, Any] | None]:
    """Unprinted transmittance stays 1, the clean-window upper bound.

    This is a typed unknown, not a d-456 in-domain default. A declared
    value is used as given and is not noticed.
    """

    if value is None:
        tau = float(WINDOW_TRANSMITTANCE_UPPER_BOUND)
        return tau, {
            "kind": TYPED_UNKNOWN,
            "knob": "window_transmittance",
            "value": tau,
            "unit": "1",
            "bound": "upper",
            "source": (
                "unprinted. 1 is the clean-window upper bound. "
                "Opacification only lowers the absorbed power."
            ),
        }
    tau = _declared_finite(
        value,
        missing="window_transmittance_not_numeric",
        invalid="window_transmittance_not_numeric",
    )
    if tau < 0.0 or tau > 1.0:
        raise PowerTemperatureRefusal(
            "window_transmittance_out_of_range",
            f"window transmittance is {tau}",
        )
    return tau, None


def argon_conductivity_W_m_K(temperature_K: float) -> float:
    """Monatomic extrapolation of the NIST argon conductivity.

    Premise: the NIST semiconductor-gas table gives argon
    lambda(1000 K) = 43.8 mW m-1 K-1. Monatomic conductivity tracks
    viscosity, so k scales as T to the 0.75. The exponent is that
    scaling. It is not fitted to a melt temperature.

    Algebra::

        k(T) = 0.0438 * (T / 1000) ** 0.75

    Units: T in kelvin, k in W m-1 K-1.

    Sanity: k(300 K) = 0.01775, which rounds to the accepted room
    temperature value 0.0178. At the 1800 C film,
    (2073.15 + 300) / 2 = 1186.6 K, k is 0.0498.
    """

    if temperature_K <= 0.0 or not math.isfinite(temperature_K):
        raise PowerTemperatureRefusal(
            "argon_conductivity_temperature_not_positive",
            f"temperature is {temperature_K} K",
        )
    ratio = temperature_K / ARGON_CONDUCTIVITY_ANCHOR_K
    return ARGON_CONDUCTIVITY_1000K_W_M_K * ratio ** ARGON_CONDUCTIVITY_EXPONENT


def _radiation_exchange_W(
    temperature_K: float,
    *,
    area_m2: float,
    emissivity: float,
    boundary_K: float,
) -> float:
    """Net radiation from one face to one boundary.

    Premise: the face is grey at the booked emissivity and the boundary
    is black. Algebra: Q = eps * sigma * A * (T**4 - T_b**4).
    Units: kelvin, m², watts. Limit: T = T_b gives 0.
    """

    return (
        STEFAN_BOLTZMANN
        * emissivity
        * area_m2
        * (temperature_K ** 4 - boundary_K ** 4)
    )


def _slab_conductance_W_per_K(
    conductivity_W_m_K: float,
    area_m2: float,
    length_m: float,
) -> float:
    """Conductance of one slab, k * A / L, in W/K.

    Premise: one-dimensional conduction. Limit: k → ∞ sends this to
    infinity, so a finite heat flow requires the temperature drop
    across that slab to vanish.
    """

    return conductivity_W_m_K * area_m2 / length_m


def _argon_loss_W(
    temperature_K: float,
    *,
    diameter_m: float,
    boundary_K: float,
) -> float:
    """Continuum argon on a still sphere of the printed diameter.

    Premise and Nu = 2 are the chamber limit named on
    ``pellet_energy_loss_W``. Algebra:
    Q = Nu * k_Ar((T + T_b) / 2) * pi * d * (T - T_b).
    Units: watts. Limit: T = T_b gives 0.
    """

    film_K = 0.5 * (temperature_K + boundary_K)
    return (
        NU_STILL_SPHERE
        * argon_conductivity_W_m_K(film_K)
        * math.pi
        * diameter_m
        * (temperature_K - boundary_K)
    )


def evaporative_enthalpy_sink_W(
    temperature_K: float,
    species_kg_hr: Mapping[str, float],
    *,
    vapor_pressures: Mapping[str, Any] | None = None,
) -> float:
    """Watts of vaporization enthalpy at the trial temperature.

    Premise: the pellet thermal time constant is a few seconds and the
    step is a minute, so the end-of-step temperature is the quasi-steady
    root of absorbed power against every sink at that same temperature.
    The evaporative term is sum_i J_i(T) * dH_i(T). Subtracting a sink
    that was evaluated at another temperature does not close: the flux
    falls as the melt cools, and the melt cools because of the flux.

    ``species_kg_hr`` is the booked rate at this T. This function does
    not own a flux law. dH_i is whatever ``evaporation_enthalpy_budget``
    already charges: oxide vapors (SiO, and the CEA oxide set) use the
    single cited reaction, and the O2 coproduct of
    SiO2 → SiO(g) + 1/2 O2 sits inside that SiO enthalpy (NIST-JANAF
    Chase 1998, +810.52 kJ/mol SiO), not as a second latent. Metals use
    the 298 K liquid→gas latent plus the 298 K oxide-dissociation
    reversal. Analytical channels use the NASA polynomial at
    ``temperature_K``. The sensible Kirchhoff correction on the 298 K
    tables is the approximation that owner already documents.

    Algebra: the budget returns kWh per hour at the supplied kg/h,
    which is a kilowatt. Watts = evaporation_thermal_kWh * 1000.

    Units: kg/h in, watts out. A zero rate contributes nothing.

    Sanity: 30.2 mg/min of SiO is 1.812×10⁻³ kg/h. Divided by the SiO
    molar mass and multiplied by 810.52 kJ/mol that is 9.25 W.
    401 mg/min is 122.8 W.
    """

    if not species_kg_hr:
        return 0.0
    if not math.isfinite(temperature_K) or temperature_K < 0.0:
        raise PowerTemperatureRefusal(
            "evaporative_sink_temperature_not_usable",
            f"temperature is {temperature_K} K",
        )
    budget = evaporation_enthalpy_budget(
        species_kg_hr,
        vapor_pressures=vapor_pressures,
        temperature_K=temperature_K,
    )
    watts = float(budget["evaporation_thermal_kWh"]) * 1000.0
    if not math.isfinite(watts) or watts < 0.0:
        raise PowerTemperatureRefusal(
            "evaporative_sink_not_usable",
            f"evaporative enthalpy is {watts} W",
        )
    return watts


def _species_rates_kg_hr(
    temperature_K: float,
    species_rate_kg_hr_at_K: Callable[[float], Mapping[str, float]] | None,
) -> Mapping[str, float]:
    """Booked kg/h at this trial temperature, or an empty map.

    Below ``BUILTIN_VAPOR_BELOW_400K_LIMIT_K`` the callback is not called.
    That is the vapor-pressure owner's physical zero, not a new cutoff.
    """

    if (
        species_rate_kg_hr_at_K is None
        or temperature_K < BUILTIN_VAPOR_BELOW_400K_LIMIT_K
    ):
        return {}
    rates = species_rate_kg_hr_at_K(float(temperature_K))
    if not isinstance(rates, Mapping):
        raise PowerTemperatureRefusal(
            "evaporative_rate_callback_malformed",
            "species rates must be a mapping of kg/h",
        )
    return rates


def _side_node_temperature_K(
    temperature_top_K: float,
    *,
    side_area_m2: float,
    emissivity: float,
    boundary_K: float,
    half_conductance_W_per_K: float,
) -> float:
    """Quasi-steady temperature of the lower body.

    Premise: node 2 stores nothing. Heat that arrives from node 1
    leaves by radiation from the area this call is given and by
    conduction through the lower half into the holder. The two-node
    caller passes the lower half-wall, ``pi * d * H / 2``.

    Algebra. Each half has conductance ``G_half = 2 k A_top / H``, so
    the two in series are the one-node holder term ``k A_top / H``::

        G_half * (T_top - T_side)
            = eps * sigma * A_node_2 * (T_side**4 - T_b**4)
            + G_half * (T_side - T_b)

    Units: kelvin in, kelvin out. Limit: as k → ∞ the nodes become one
    temperature and the holder term of that isothermal pellet is
    ``G_half / 2 * (T - T_b)``, the existing one-node slab. A holder
    leg of only ``k A / H`` in series with another ``k A / H`` would
    present half that conductance and would not meet the one-node
    full-side root.
    """

    def residual(temperature_side_K: float) -> float:
        coupling = half_conductance_W_per_K * (
            temperature_top_K - temperature_side_K
        )
        radiation = _radiation_exchange_W(
            temperature_side_K,
            area_m2=side_area_m2,
            emissivity=emissivity,
            boundary_K=boundary_K,
        )
        holder = half_conductance_W_per_K * (
            temperature_side_K - boundary_K
        )
        return coupling - radiation - holder

    low = min(temperature_top_K, boundary_K)
    high = max(temperature_top_K, boundary_K)
    if high <= low:
        return float(boundary_K)
    if residual(low) < 0.0 or residual(high) > 0.0:
        raise PowerTemperatureRefusal(
            "power_temperature_balance_did_not_close",
            "side-node conduction has no bracket",
        )
    for _step in range(_BISECTION_STEPS):
        mid = 0.5 * (low + high)
        if residual(mid) > 0.0:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


def pellet_energy_loss_W(
    temperature_K: float,
    *,
    radiating_area_m2: float,
    emissivity: float,
    conductivity_W_m_K: float,
    top_area_m2: float,
    height_m: float,
    diameter_m: float,
    species_rate_kg_hr_at_K: Callable[[float], Mapping[str, float]] | None = None,
    vapor_pressures: Mapping[str, Any] | None = None,
) -> float:
    """Watts leaving one isothermal pellet temperature.

    Premise: Robinot section 3.1 seats the pellet on a water-cooled
    steel holder, and section 4.1 flows argon at about 13 mbar. The
    mean free path at 13 mm and 13 mbar is tens of micrometres
    (Knudsen number about 3e-3 at 2073 K), so the gas is continuum.
    Free-molecular heat transfer does not apply. The enthalpy the
    same argon stream could carry is the same physical loss and is
    not added on top.

    This is the one-node balance: the booked radiating area, the
    holder slab, continuum argon, and the evaporative enthalpy, all
    at one temperature. The full-side face and an explicit area use
    it. It is also the high-conductivity limit of the two-node
    balance. The hot-upper face does not: that path is
    ``two_node_pellet_loss_W``.

    The cold boundary is the cooling water. T_sur, T_cold, and T_gas
    are that one temperature, 300 K. The preset holder series that
    ends at 1500 C is not read. Nu = 2 is the still-gas sphere on
    the printed diameter. The pellet is a cylinder for the slab and
    a sphere of that diameter for the gas term, which is the
    continuum limit named for this chamber. Conduction uses the top
    face, the contact, and the printed height. The bottom face is
    not a radiator.

    ``species_rate_kg_hr_at_K`` is the evaporation step's booked kg/h
    at the trial temperature, closed over the step-start composition.
    None is a zero sink. The rates are not a second flux formula.

    Algebra::

        Q = eps * sigma * A_face * (T**4 - T_b**4)
            + k * A_top * (T - T_b) / H
            + Nu * k_Ar((T + T_b) / 2) * pi * d * (T - T_b)
            + sum_i J_i(T) * dH_i(T)

        T_b = 300 K
        Nu = 2
        k_Ar from argon_conductivity_W_m_K

    Units: T kelvin, areas m2, k W m-1 K-1, H and d metres, Q watts.

    Sanity at 1800 C (2073.15 K), d = 13 mm, H = 10 mm, k = 1,
    A_top = 1.327e-4 m2, T_b = 300 K, no evaporation. Conduction is
    23.5 W. The investigation's 24 W is the same term at a 1773 K
    rise. Argon conductivity at the film is 0.0498, and Q_gas at
    Nu = 2 is 7.2 W. Coupled to the peak focal power, alpha 0.85,
    and the lumped hot-upper area at one temperature, the root is
    2072 C. The full side at the same power is 1816 C.
    """

    boundary_K = COLD_BOUNDARY_K
    radiation = _radiation_exchange_W(
        temperature_K,
        area_m2=radiating_area_m2,
        emissivity=emissivity,
        boundary_K=boundary_K,
    )
    conduction = _slab_conductance_W_per_K(
        conductivity_W_m_K,
        top_area_m2,
        height_m,
    ) * (temperature_K - boundary_K)
    gas = _argon_loss_W(
        temperature_K,
        diameter_m=diameter_m,
        boundary_K=boundary_K,
    )
    rates = _species_rates_kg_hr(temperature_K, species_rate_kg_hr_at_K)
    evaporative = evaporative_enthalpy_sink_W(
        temperature_K,
        rates,
        vapor_pressures=vapor_pressures,
    )
    return radiation + conduction + gas + evaporative


def two_node_pellet_loss_W(
    temperature_top_K: float,
    *,
    top_area_m2: float,
    side_area_m2: float,
    emissivity: float,
    conductivity_W_m_K: float,
    height_m: float,
    diameter_m: float,
    species_rate_kg_hr_at_K: Callable[[float], Mapping[str, float]] | None = None,
    vapor_pressures: Mapping[str, Any] | None = None,
) -> tuple[float, float]:
    """External watts from the two-node pellet, and the side temperature.

    Premise: the preset books the top face plus the hot upper side.
    That is a stated geometry, not a moving melt front. Node 1 is the
    top face plus the upper half-wall. It absorbs the beam, radiates
    that area at T_top, loses continuum argon, carries the evaporative
    sink, and conducts into node 2. Node 2 is the lower half-wall plus
    the holder. It radiates ``pi * d * H / 2`` at T_side and conducts
    with the same half-slab conductance.

    Gas is on node 1. A hot free surface keeps that sink. At high
    conductivity the nodes are one temperature, so which node owns
    the gas does not move the merged sum.

    Algebra, external sum (the inter-node flux cancels)::

        A_node_1 = A_top + A_side / 2
        A_node_2 = A_side / 2
        Q = eps * sigma * A_node_1 * (T_top**4 - T_b**4)
            + Q_gas(T_top)
            + sum_i J_i(T_top) * dH_i(T_top)
            + eps * sigma * A_node_2 * (T_side**4 - T_b**4)
            + G_half * (T_side - T_b)

        G_half = 2 * k * A_top / H
        T_side from _side_node_temperature_K

    Units: watts, and T_side in kelvin. High-k limit: both nodes sit
    on one temperature and radiate A_top + A_side, which is
    ``pellet_energy_loss_W`` on the full side. Node 1's area equals
    the lumped hot-upper area, and the lower half-wall still radiates
    at its own temperature when the nodes are not isothermal.
    """

    boundary_K = COLD_BOUNDARY_K
    half = 2.0 * _slab_conductance_W_per_K(
        conductivity_W_m_K,
        top_area_m2,
        height_m,
    )
    node_2_area_m2 = 0.5 * side_area_m2
    node_1_area_m2 = top_area_m2 + node_2_area_m2
    side_K = _side_node_temperature_K(
        temperature_top_K,
        side_area_m2=node_2_area_m2,
        emissivity=emissivity,
        boundary_K=boundary_K,
        half_conductance_W_per_K=half,
    )
    rates = _species_rates_kg_hr(
        temperature_top_K,
        species_rate_kg_hr_at_K,
    )
    evaporative = evaporative_enthalpy_sink_W(
        temperature_top_K,
        rates,
        vapor_pressures=vapor_pressures,
    )
    radiation_top = _radiation_exchange_W(
        temperature_top_K,
        area_m2=node_1_area_m2,
        emissivity=emissivity,
        boundary_K=boundary_K,
    )
    radiation_side = _radiation_exchange_W(
        side_K,
        area_m2=node_2_area_m2,
        emissivity=emissivity,
        boundary_K=boundary_K,
    )
    gas = _argon_loss_W(
        temperature_top_K,
        diameter_m=diameter_m,
        boundary_K=boundary_K,
    )
    holder = half * (side_K - boundary_K)
    return (
        radiation_top + gas + evaporative + radiation_side + holder,
        side_K,
        node_1_area_m2,
        node_2_area_m2,
    )


def _resolve_pellet_conductivity(
    value: float | None,
) -> tuple[float, dict[str, Any] | None]:
    """Basalt-band midpoint when the deck does not print a conductivity.

    The notice records the 0.6 to 1.4 band. A declared positive value
    is used as given, including outside that band: poor contact is a
    smaller conductivity, and the band is the citation of the default.
    """

    if value is None:
        conductivity = float(PELLET_CONDUCTIVITY_DEFAULT_W_M_K)
        return conductivity, {
            "kind": KNOB_DEFAULTED,
            "knob": "pellet_conductivity_W_m_K",
            "value": conductivity,
            "unit": "W/m/K",
            "band_low": PELLET_CONDUCTIVITY_LOW_W_M_K,
            "band_high": PELLET_CONDUCTIVITY_HIGH_W_M_K,
            "source": (
                "midpoint of the 0.6 to 1.4 W m-1 K-1 basalt band. "
                "Büttner et al. report about 1.0 at 1200 C. "
                "Not tuned to a pyrometer reading."
            ),
        }
    conductivity = _declared_finite(
        value,
        missing="pellet_conductivity_not_numeric",
        invalid="pellet_conductivity_not_numeric",
    )
    if conductivity <= 0.0:
        raise PowerTemperatureRefusal(
            "pellet_conductivity_not_positive",
            f"conductivity is {conductivity} W m-1 K-1",
        )
    return conductivity, None


def _tonne_sky_loss_W(
    temperature_K: float,
    *,
    area_m2: float,
    emissivity: float,
) -> float:
    """Tonne-path radiation to a 0 K sky.

    Premise: the pre-pellet balance, and EQ-2's concentrator sizing,
    book sigma * eps * A * T**4. A 300 K enclosure changes that flux
    by (300 / T)**4, under 1 K at 1800 C. This path keeps the 0 K sky
    so a call with no printed cylinder is the same balance as before.
    Process gas is not a sink here.

    Algebra: Q = sigma * eps * A * T**4.

    Units: watts. Sanity: 1 kg, sizer area 0.001954474441911621 m2,
    emissivity 0.85, 2073.15 K, is 1740.1373551492054 W.
    """

    return STEFAN_BOLTZMANN * emissivity * area_m2 * temperature_K ** 4


def _optional_positive_length(
    value: float | None,
    *,
    invalid: str,
) -> float | None:
    if value is None:
        return None
    number = _declared_finite(value, missing=invalid, invalid=invalid)
    if number <= 0.0:
        raise PowerTemperatureRefusal(invalid, f"length is {number} m")
    return number


def sample_furnace_power_W(trace: Mapping[str, Any], time_s: float) -> float:
    """Watts at ``time_s`` from the existing bracketing interpolator.

    Times on the trace are minutes. The curve handed to
    ``timeseries_concordance._interpolate`` is seconds. Outside the
    first and last sample the interpolator raises; this wraps that as
    ``power_trace_does_not_bracket`` and does not hold the last point.
    ``reproduction_compare._interpolate_runtime_sample`` is a different
    owner and is not called.
    """

    if not isinstance(trace, Mapping):
        raise PowerTemperatureRefusal(
            "furnace_power_trace_malformed",
            "trace must be a mapping",
        )
    role = str(trace.get("role") or "").strip()
    units = str(trace.get("units") or "").strip()
    if role != _POWER_TRACE_ROLE or units != _POWER_TRACE_UNITS:
        raise PowerTemperatureRefusal(
            "furnace_power_trace_malformed",
            "trace is not a units=W measured_driver",
        )
    if not is_declared_real_scalar(time_s, allow_numeric_str=True):
        raise PowerTemperatureRefusal(
            "power_trace_does_not_bracket",
            "sample time is not numeric",
        )
    sample_s = float(time_s)
    if not math.isfinite(sample_s):
        raise PowerTemperatureRefusal(
            "power_trace_does_not_bracket",
            "sample time is not finite",
        )
    curve = _power_trace_curve_s(trace)
    try:
        return _interpolate(curve, sample_s, field_name="furnace power")
    except ValueError as exc:
        message = str(exc)
        if "does not bracket" in message:
            raise PowerTemperatureRefusal(
                "power_trace_does_not_bracket",
                message,
            ) from exc
        raise PowerTemperatureRefusal(
            "furnace_power_trace_malformed",
            message,
        ) from exc


def solve_power_driven_melt_temperature_C(
    *,
    power_W: float,
    previous_temperature_C: float,
    dt_s: float,
    mass_kg: float,
    area_m2: float | None,
    emissivity: float | None,
    pellet_diameter_m: float | None = None,
    pellet_height_m: float | None = None,
    focal_spot_diameter_m: float | None = None,
    radiating_face_set: str | None = None,
    solar_absorptance: float | None = None,
    window_transmittance: float | None = None,
    conductivity_W_m_K: float | None = None,
    species_rate_kg_hr_at_K: Callable[[float], Mapping[str, float]] | None = None,
) -> dict[str, Any]:
    """Solve one end-of-step melt temperature from furnace power.

    Premise: ``power_W`` is frozen across the step. When both a pellet
    diameter and a focal-spot diameter are set, ``power_W`` is the
    focal-spot power (Robinot Eq. 6) and the residual uses
    ``absorbed_focal_power_W``. One diameter without the other refuses.
    Intercept is never defaulted to 1: that default is the bug that
    booked the whole spot onto the pellet. When neither diameter is
    set, ``power_W`` already enters the balance. That path is the
    tonne-scale call, not a silent intercept.

    The charge stores sensible heat at
    ``EquipmentDesigner.MELT_CP_J_KG_K``. A printed pellet diameter
    and height use ``pellet_face_areas_m2``. The hot-upper face, and
    an unset face below 1 kg, is the two-node balance. Node 1 is the
    top face plus the upper half-wall at T_top. Node 2 is the lower
    half-wall plus the holder at T_side. That split is the
    preset-declared geometry, flagged, and not a moving melt front.
    The booked temperature is T_top. The full side and an explicit
    area stay one node, ``pellet_energy_loss_W``, which is also the
    high-conductivity limit of the two nodes: isothermal nodes
    radiate the top and both half-walls. The other of those two
    solutions is reported beside the booked temperature and does not
    replace it. A missing conductivity takes the basalt-band
    midpoint and is noticed. The 300 K boundary is a typed unknown.
    The preset holder series is not a temperature this solver reads.
    With no printed cylinder the loss stays ``_tonne_sky_loss_W``:
    sigma * emissivity * area * T**4, sky at 0 K, and no gas term.
    A missing emissivity takes the silicate default and is noticed.
    A missing area takes the tonne sizer only at or above 1 kg. Below
    1 kg that sizer is out of domain and the step refuses.
    A missing solar absorptance takes the Gaier large-particle midpoint
    and is noticed. A missing window transmittance is the typed unknown
    1, an upper bound, and is noticed. None of these is tuned to a
    printed peak.

    ``species_rate_kg_hr_at_K`` is the evaporation step's booked kg/h
    at the trial kelvin, closed over the step-start composition. The
    root evaluates that sink at the same temperature as the other
    losses. None is a zero sink. The tonne sky has no pellet surface;
    a callback there is noticed and not applied.

    Algebra, both sides watts, losses at the unknown end temperature.
    One node (full side, explicit area, or the high-k limit)::

        m * cp * (T - T_prev) / dt = P_abs - pellet_energy_loss_W(T)

    Two nodes, hot upper, sensible heat on the top node only.
    Node 1 radiates ``A_top + pi * d * H / 2``::

        m * cp * (T_top - T_prev) / dt = P_abs
            - two_node_pellet_loss_W(T_top)

    No cylinder::

        m * cp * (T - T_prev) / dt = P - sigma * eps * A * T**4

    Units: m kg, cp J/(kg·K), T kelvin, dt seconds, P watts, A m²,
    k W m-1 K-1, sigma W/(m²·K⁴). The loss rises with T, so the root
    is the bisection of the adiabatic bracket.

    Sanity: 1 kg, no focal spot, has radiative area
    0.001954474441911621 m². Steady state at 1800 °C (2073.15 K) needs
    1740.1373551492054 W at sigma 5.670374e-8 and emissivity 0.85.
    A 13 mm pellet in a 20 mm spot intercepts 0.4225 of the focal watts.
    At the digitized peak, alpha 0.85, k = 1, Nu = 2, and T_b = 300 K,
    the one-node lumped hot-upper area is 2072 °C and the full side
    is 1816 °C. The booked hot-upper path is the two-node root. Node
    1's area is that lumped hot-upper area, and the lower half-wall
    radiates at T_side, so the booked root is not the top-face-only
    balance.
    """

    power = _declared_finite(
        power_W,
        missing="furnace_power_not_finite",
        invalid="furnace_power_not_finite",
    )
    if power < 0.0:
        raise PowerTemperatureRefusal(
            "furnace_power_not_nonnegative",
            f"power is {power}",
        )
    previous_C = _declared_finite(
        previous_temperature_C,
        missing="previous_temperature_not_finite",
        invalid="previous_temperature_not_finite",
    )
    previous_K = previous_C + CELSIUS_TO_KELVIN_OFFSET
    if previous_K < 0.0:
        raise PowerTemperatureRefusal(
            "previous_temperature_below_absolute_zero",
            f"previous temperature is {previous_C} C",
        )
    step_s = _declared_finite(
        dt_s,
        missing="process_dt_not_positive",
        invalid="process_dt_not_positive",
    )
    if step_s <= 0.0:
        raise PowerTemperatureRefusal(
            "process_dt_not_positive",
            f"dt_s is {step_s}",
        )
    charge_kg = _declared_finite(
        mass_kg,
        missing="charge_mass_missing",
        invalid="charge_mass_not_positive",
    )
    if charge_kg <= 0.0:
        raise PowerTemperatureRefusal(
            "charge_mass_not_positive",
            f"mass is {charge_kg} kg",
        )
    notices: list[dict[str, Any]] = []
    pellet_d = _optional_positive_length(
        pellet_diameter_m,
        invalid="pellet_diameter_not_positive",
    )
    focal_d = _optional_positive_length(
        focal_spot_diameter_m,
        invalid="focal_spot_diameter_not_positive",
    )
    optical_requested = (
        solar_absorptance is not None or window_transmittance is not None
    )
    absorbed = power
    intercept: float | None = None
    alpha: float | None = None
    tau: float | None = None
    if pellet_d is None and focal_d is None:
        if optical_requested:
            raise PowerTemperatureRefusal(
                "focal_diameter_missing",
                "solar absorptance and window transmittance apply only "
                "to a declared focal spot",
            )
    elif pellet_d is None or focal_d is None:
        raise PowerTemperatureRefusal(
            "focal_diameter_missing",
            "focal-spot power needs both the pellet diameter and the spot diameter",
        )
    else:
        alpha, alpha_notice = _resolve_solar_absorptance(solar_absorptance)
        tau, tau_notice = _resolve_window_transmittance(window_transmittance)
        if alpha_notice is not None:
            notices.append(alpha_notice)
        if tau_notice is not None:
            notices.append(tau_notice)
        absorbed, intercept = absorbed_focal_power_W(
            power,
            pellet_diameter_m=pellet_d,
            focal_spot_diameter_m=focal_d,
            solar_absorptance=alpha,
            window_transmittance=tau,
        )
    height_m = _optional_positive_length(
        pellet_height_m,
        invalid="pellet_height_not_positive",
    )
    area, face_set, other_area, other_face, area_notices = _booked_radiating_area(
        charge_kg=charge_kg,
        area_m2=area_m2,
        pellet_diameter_m=pellet_d,
        pellet_height_m=height_m,
        radiating_face_set=radiating_face_set,
    )
    notices.extend(area_notices)
    if emissivity is None:
        eps = float(EquipmentDesigner.MELT_EMISSIVITY)
        notices.append({
            "kind": KNOB_DEFAULTED,
            "knob": "melt_emissivity",
            "value": eps,
            "unit": "1",
            "source": "EquipmentDesigner.MELT_EMISSIVITY",
        })
    else:
        eps = _declared_finite(
            emissivity,
            missing="melt_emissivity_not_numeric",
            invalid="melt_emissivity_not_numeric",
        )
        if eps < 0.0 or eps > 1.0:
            raise PowerTemperatureRefusal(
                "melt_emissivity_out_of_range",
                f"emissivity is {eps}",
            )
    capacity = charge_kg * float(EquipmentDesigner.MELT_CP_J_KG_K) / step_s
    if not math.isfinite(capacity) or capacity <= 0.0:
        raise PowerTemperatureRefusal(
            "power_temperature_balance_did_not_close",
            "charge heat capacity is not positive",
        )
    pellet_balance = pellet_d is not None and height_m is not None
    cold_boundary_K: float | None = None
    surroundings_K = 0.0
    conductivity: float | None = None
    if pellet_balance:
        conductivity, conductivity_notice = _resolve_pellet_conductivity(
            conductivity_W_m_K
        )
        if conductivity_notice is not None:
            notices.append(conductivity_notice)
        cold_boundary_K = float(COLD_BOUNDARY_K)
        surroundings_K = cold_boundary_K
        notices.append({
            "kind": TYPED_UNKNOWN,
            "knob": "cold_boundary_K",
            "value": cold_boundary_K,
            "unit": "K",
            "source": (
                "unprinted. The holder is water-cooled, so the boundary "
                "is near ambient. The preset holder series that ends at "
                "1500 C is not T_sur."
            ),
        })
        faces = pellet_face_areas_m2(pellet_d, height_m)
        top_area_m2 = faces["top"]
        side_area_m2 = faces["side"]

        def loss_for(radiating_area_m2: float) -> Callable[[float], float]:
            def loss(temperature_K: float) -> float:
                return pellet_energy_loss_W(
                    temperature_K,
                    radiating_area_m2=radiating_area_m2,
                    emissivity=eps,
                    conductivity_W_m_K=conductivity,
                    top_area_m2=top_area_m2,
                    height_m=height_m,
                    diameter_m=pellet_d,
                    species_rate_kg_hr_at_K=species_rate_kg_hr_at_K,
                )
            return loss

        def two_node_loss(temperature_K: float) -> float:
            watts, _side_K, _node_1_m2, _node_2_m2 = two_node_pellet_loss_W(
                temperature_K,
                top_area_m2=top_area_m2,
                side_area_m2=side_area_m2,
                emissivity=eps,
                conductivity_W_m_K=conductivity,
                height_m=height_m,
                diameter_m=pellet_d,
                species_rate_kg_hr_at_K=species_rate_kg_hr_at_K,
            )
            return watts

        # hot_upper is the two-node brightness temperature. full_side
        # and an explicit area stay one node. The sensitivity is the
        # other solution and does not replace the booked temperature.
        if face_set == FACE_HOT_UPPER:
            nodes = "two_node"
            area = top_area_m2
            root_loss = two_node_loss
            other_loss = (
                loss_for(faces[FACE_FULL_SIDE])
                if other_area is not None
                else None
            )
        elif face_set == FACE_FULL_SIDE:
            nodes = "one_node"
            root_loss = loss_for(area)
            other_loss = two_node_loss if other_area is not None else None
            other_area = top_area_m2
        else:
            nodes = "one_node"
            root_loss = loss_for(area)
            other_loss = None
    else:
        top_area_m2 = None
        side_area_m2 = None
        nodes = "sky"
        if conductivity_W_m_K is not None:
            raise PowerTemperatureRefusal(
                "pellet_geometry_incomplete",
                "conductivity applies only to a printed pellet diameter and height",
            )
        if species_rate_kg_hr_at_K is not None:
            notices.append({
                "kind": TYPED_UNKNOWN,
                "knob": "species_rate_kg_hr_at_K",
                "value": "not_applied",
                "unit": "1",
                "source": (
                    "the tonne sky balance has no pellet evaporation "
                    "surface, so the rate callback is not a sink here"
                ),
            })

        def sky_loss(temperature_K: float) -> float:
            return _tonne_sky_loss_W(
                temperature_K,
                area_m2=area,
                emissivity=eps,
            )

        root_loss = sky_loss
        other_loss = None
    root_K = _bisect_power_temperature_K(
        power_W=absorbed,
        previous_K=previous_K,
        capacity_W_per_K=capacity,
        loss_W=root_loss,
    )
    node_1_area_m2 = None
    node_2_area_m2 = None
    if nodes == "two_node":
        _watts, side_K, node_1_area_m2, node_2_area_m2 = two_node_pellet_loss_W(
            root_K,
            top_area_m2=top_area_m2,
            side_area_m2=side_area_m2,
            emissivity=eps,
            conductivity_W_m_K=conductivity,
            height_m=height_m,
            diameter_m=pellet_d,
            species_rate_kg_hr_at_K=species_rate_kg_hr_at_K,
        )
        notices.append({
            "kind": TYPED_UNKNOWN,
            "knob": "two_node_geometry",
            "value": "top_face_plus_upper_half_wall",
            "unit": "1",
            "source": (
                "preset-declared geometry: the booked surface is the "
                "top face plus the hot upper side. Node 1 radiates "
                "that area at T_top. Node 2 radiates the lower "
                "half-wall and conducts to the holder. This is not a "
                "moving melt front."
            ),
        })
    elif nodes == "one_node":
        side_K = root_K
    else:
        side_K = None
    if nodes == "sky":
        evaporative_sink_W = None
    else:
        evaporative_sink_W = evaporative_enthalpy_sink_W(
            root_K,
            _species_rates_kg_hr(root_K, species_rate_kg_hr_at_K),
        )
    sensitivity = None
    if other_loss is not None:
        other_K = _bisect_power_temperature_K(
            power_W=absorbed,
            previous_K=previous_K,
            capacity_W_per_K=capacity,
            loss_W=other_loss,
        )
        sensitivity = {
            "face_set": other_face,
            "area_m2": other_area,
            "temperature_C": other_K - CELSIUS_TO_KELVIN_OFFSET,
            "temperature_K": other_K,
            "nodes": "one_node" if nodes == "two_node" else "two_node",
            "role": "sensitivity",
        }
    return {
        "temperature_C": root_K - CELSIUS_TO_KELVIN_OFFSET,
        "temperature_K": root_K,
        "temperature_side_C": (
            None if side_K is None else side_K - CELSIUS_TO_KELVIN_OFFSET
        ),
        "temperature_side_K": side_K,
        "power_W": absorbed,
        "focal_power_W": power if intercept is not None else None,
        "intercept": intercept,
        "solar_absorptance": alpha,
        "window_transmittance": tau,
        "face_set": face_set,
        "nodes": nodes,
        "sensitivity": sensitivity,
        "cold_boundary_K": cold_boundary_K,
        "surroundings_K": surroundings_K,
        "conductivity_W_m_K": conductivity,
        "dt_s": step_s,
        "mass_kg": charge_kg,
        "area_m2": area,
        "top_area_m2": top_area_m2,
        "side_area_m2": side_area_m2,
        "node_1_area_m2": node_1_area_m2,
        "node_2_area_m2": node_2_area_m2,
        "evaporative_sink_W": evaporative_sink_W,
        "emissivity": eps,
        "role": "prediction",
        "source_class": "engine_prediction",
        "scored_as_observed_temperature": False,
        "notices": tuple(notices),
    }


def melt_temperature_from_furnace_power(
    driver: Mapping[str, Any],
    *,
    time_s: float,
    previous_temperature_C: float,
    dt_s: float,
    live_mass_kg: float | None = None,
    species_rate_kg_hr_at_K: Callable[[float], Mapping[str, float]] | None = None,
) -> dict[str, Any]:
    """Prediction for one step. The live melt mass wins when it is set.

    ``MeltState.total_mass_kg`` starts at 0, which means the inventory
    has not been written yet. A positive finite live mass is the charge.
    Otherwise the printed sample mass on the driver is the charge.
    Neither is invented when both are missing. A holder temperature on
    the driver is not read. The pellet boundary stays the water-cooled
    typed unknown inside the solver.

    ``species_rate_kg_hr_at_K`` is not a driver field. The step passes
    the evaporation booking at the trial temperature, closed over the
    step-start composition. None leaves the evaporative sink at zero.
    """

    if not isinstance(driver, Mapping):
        raise PowerTemperatureRefusal(
            "furnace_power_driver_malformed",
            "furnace power driver must be a mapping",
        )
    trace = driver.get("trace")
    if not isinstance(trace, Mapping):
        raise PowerTemperatureRefusal(
            "furnace_power_driver_malformed",
            "furnace power driver is missing its trace",
        )
    power_W = sample_furnace_power_W(trace, time_s)
    record = solve_power_driven_melt_temperature_C(
        power_W=power_W,
        previous_temperature_C=previous_temperature_C,
        dt_s=dt_s,
        mass_kg=_charge_mass_kg(driver.get("mass_kg"), live_mass_kg),
        area_m2=driver.get("exposed_area_m2"),
        emissivity=driver.get("emissivity"),
        pellet_diameter_m=driver.get("pellet_diameter_m"),
        pellet_height_m=driver.get("pellet_height_m"),
        focal_spot_diameter_m=driver.get("focal_spot_diameter_m"),
        radiating_face_set=driver.get("radiating_face_set"),
        solar_absorptance=driver.get("solar_absorptance"),
        window_transmittance=driver.get("window_transmittance"),
        conductivity_W_m_K=driver.get("conductivity_W_m_K"),
        species_rate_kg_hr_at_K=species_rate_kg_hr_at_K,
    )
    record["time_s"] = _declared_finite(
        time_s,
        missing="power_trace_does_not_bracket",
        invalid="power_trace_does_not_bracket",
    )
    return record


def reported_temperature_observations(
    points: Sequence[Mapping[str, Any]] | None,
) -> tuple[Mapping[str, Any], ...]:
    """Printed temperature points that may be scored as observations.

    An engine prediction, an assumption, an unreported point, and a
    qualifier that names a range are not a T(t) observation. Sesko's
    1750–1900 K endpoints stay a range. A flagged power-driven
    prediction is not an observation even if it is handed in here.
    """

    if points is None:
        return ()
    if isinstance(points, (str, bytes)) or not isinstance(points, Sequence):
        raise PowerTemperatureRefusal(
            "temperature_observations_malformed",
            "temperature points must be a sequence",
        )
    kept: list[Mapping[str, Any]] = []
    for index, point in enumerate(points):
        if not isinstance(point, Mapping):
            raise PowerTemperatureRefusal(
                "temperature_observations_malformed",
                f"point {index} must be a mapping",
            )
        if point.get("scored_as_observed_temperature") is False:
            continue
        source = str(point.get("source_class") or "").strip()
        if not source:
            raise PowerTemperatureRefusal(
                "temperature_observation_missing_source_class",
                f"point {index}",
            )
        if source in _UNSCORED_TEMPERATURE_SOURCE_CLASSES:
            continue
        qualifier = str(point.get("qualifier") or "")
        if "range" in qualifier.casefold():
            continue
        kept.append(point)
    return tuple(kept)


def _charge_mass_kg(payload_mass: Any, live_mass_kg: Any) -> float:
    if _is_set_live_mass(live_mass_kg):
        live = _declared_finite(
            live_mass_kg,
            missing="charge_mass_not_positive",
            invalid="charge_mass_not_positive",
        )
        if live <= 0.0:
            raise PowerTemperatureRefusal(
                "charge_mass_not_positive",
                f"live mass is {live} kg",
            )
        return live
    if payload_mass is None:
        raise PowerTemperatureRefusal(
            "charge_mass_missing",
            "no printed sample mass and no live melt mass",
        )
    printed = _declared_finite(
        payload_mass,
        missing="charge_mass_missing",
        invalid="charge_mass_not_positive",
    )
    if printed <= 0.0:
        raise PowerTemperatureRefusal(
            "charge_mass_not_positive",
            f"printed mass is {printed} kg",
        )
    return printed


def _is_set_live_mass(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if not is_declared_real_scalar(value, allow_numeric_str=True):
        return True
    number = float(value)
    if not math.isfinite(number):
        return True
    return number != 0.0


def _declared_finite(value: Any, *, missing: str, invalid: str) -> float:
    if value is None:
        raise PowerTemperatureRefusal(missing, "value is missing")
    if not is_declared_real_scalar(value, allow_numeric_str=True):
        raise PowerTemperatureRefusal(invalid, f"value {value!r} is not numeric")
    number = float(value)
    if not math.isfinite(number):
        raise PowerTemperatureRefusal(invalid, f"value {value!r} is not finite")
    return number


def _power_trace_curve_s(
    trace: Mapping[str, Any],
) -> tuple[tuple[float, float], ...]:
    fields = trace.get("point_fields")
    if isinstance(fields, (str, bytes)) or not isinstance(fields, Sequence):
        raise PowerTemperatureRefusal(
            "furnace_power_trace_malformed",
            "point_fields must name time_min and expected_value",
        )
    names = [str(item) for item in fields]
    if _POWER_TIME_FIELD not in names or _POWER_VALUE_FIELD not in names:
        raise PowerTemperatureRefusal(
            "furnace_power_trace_malformed",
            "point_fields must name time_min and expected_value",
        )
    time_index = names.index(_POWER_TIME_FIELD)
    value_index = names.index(_POWER_VALUE_FIELD)
    points = trace.get("points")
    if isinstance(points, (str, bytes)) or not isinstance(points, Sequence):
        raise PowerTemperatureRefusal(
            "furnace_power_trace_malformed",
            "points must be a sequence",
        )
    if len(points) == 0:
        raise PowerTemperatureRefusal(
            "furnace_power_trace_empty",
            "furnace power trace needs points",
        )
    width = max(time_index, value_index) + 1
    curve: list[tuple[float, float]] = []
    for index, point in enumerate(points):
        if (
            isinstance(point, (str, bytes))
            or not isinstance(point, Sequence)
            or len(point) < width
        ):
            raise PowerTemperatureRefusal(
                "furnace_power_trace_malformed",
                f"point {index} does not carry time_min and expected_value",
            )
        time_min = _declared_finite(
            point[time_index],
            missing="furnace_power_trace_malformed",
            invalid="furnace_power_trace_malformed",
        )
        watts = _declared_finite(
            point[value_index],
            missing="furnace_power_trace_malformed",
            invalid="furnace_power_trace_malformed",
        )
        if watts < 0.0:
            raise PowerTemperatureRefusal(
                "furnace_power_not_nonnegative",
                f"point {index} power is {watts}",
            )
        curve.append((time_min * _MINUTE_S, watts))
    try:
        return _normalise_curve(curve, field_name="furnace power")
    except ValueError as exc:
        message = str(exc)
        if "unique" in message:
            raise PowerTemperatureRefusal(
                "furnace_power_trace_times_not_unique",
                message,
            ) from exc
        if "at least one" in message:
            raise PowerTemperatureRefusal(
                "furnace_power_trace_empty",
                message,
            ) from exc
        raise PowerTemperatureRefusal(
            "furnace_power_trace_malformed",
            message,
        ) from exc


def _bisect_power_temperature_K(
    *,
    power_W: float,
    previous_K: float,
    capacity_W_per_K: float,
    loss_W: Callable[[float], float],
) -> float:
    """Root of capacity * (T - T_prev) + loss(T) - power = 0.

    ``loss_W`` is ``pellet_energy_loss_W`` or ``_tonne_sky_loss_W``.
    Both rise with T above 0 K: the sky term is T**4, and the pellet
    terms are T**4, a linear slab, and k_Ar(T) * (T - T_b) with
    k_Ar proportional to T**0.75. The bracket is the adiabatic upper
    bound, which ignores a non-negative high-T loss and so still
    lies above the root.
    """

    def residual(temperature_K: float) -> float:
        try:
            loss = loss_W(temperature_K)
        except PowerTemperatureRefusal as exc:
            if (
                exc.reason == "evaporative_flux_above_bracket"
                and temperature_K >= EVAPORATIVE_BRACKET_CEILING_K
            ):
                return math.inf
            raise
        if not math.isfinite(loss):
            return math.inf
        return (
            loss
            + capacity_W_per_K * (temperature_K - previous_K)
            - power_W
        )

    lower = 0.0
    adiabatic = previous_K + power_W / capacity_W_per_K
    if not math.isfinite(adiabatic) or adiabatic > 1.0e7:
        adiabatic = max(previous_K, 1.0e7)
    upper = adiabatic
    # A steady pellet has a tiny heat capacity, so the adiabatic cap is
    # the 1e7 K ceiling. The evaporative callback is the booked flux
    # law, which is not a 1e7 K evaluation. Probe a few thousand kelvin
    # above the previous temperature and double, which is the expansion
    # below. Eighty bisections of either bracket close the watts inside
    # the 1e-6 relative tolerance.
    modest = max(previous_K, COLD_BOUNDARY_K) + 5000.0
    if upper > modest:
        upper = modest
    if upper < lower:
        upper = lower
    f_lower = residual(lower)
    f_upper = residual(upper)
    guard = 0
    while f_upper < 0.0 and upper < 1.0e7 and guard < 40:
        upper = min(1.0e7, upper * 2.0 if upper > 0.0 else 1.0)
        f_upper = residual(upper)
        guard += 1
    if f_lower > 0.0:
        raise PowerTemperatureRefusal(
            "power_temperature_balance_did_not_close",
            "residual has no root at or above 0 K",
        )
    if f_upper < 0.0:
        raise PowerTemperatureRefusal(
            "power_temperature_balance_did_not_close",
            "residual stays negative across the bracket",
        )
    for _step in range(_BISECTION_STEPS):
        mid = 0.5 * (lower + upper)
        if residual(mid) < 0.0:
            lower = mid
        else:
            upper = mid
    root = 0.5 * (lower + upper)
    closed = residual(root)
    scale = max(1.0, abs(power_W), abs(capacity_W_per_K * previous_K))
    if (
        not math.isfinite(root)
        or not math.isfinite(closed)
        or abs(closed) > 1.0e-6 * scale
    ):
        raise PowerTemperatureRefusal(
            "power_temperature_balance_did_not_close",
            "bisection did not close the watt balance",
        )
    return root
