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
- process gas flow is never a cooling term; pO2 and neutral sweep remain
  chemistry controls, not furnace-wall heat sinks;
- wall conductivity, wall thickness, wall area, solidus, outer-wall
  temperature, sky temperature, and view factor are caller-supplied unless a
  caller provides source tags;
- creep, thermal shock, and mbar forced-convection coefficient are structured
  uncertified gaps and are not part of the ideal/minimum heat-balance floor.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from simulator.equipment import EquipmentDesigner, STEFAN_BOLTZMANN
from simulator.furnace_materials import load_furnace_materials
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.state import MOLAR_MASS, OXIDE_TO_METAL, STOICH_RATIOS


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
    # Oxide vapors (SiO, CrO2) are NOT reduced metals: they do not go through a
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
}

# Species that evaporate as an oxide vapor (single-reaction path), not a metal.
_OXIDE_VAPOR_SPECIES: frozenset[str] = frozenset({"SiO", "CrO2"})


def evaporation_enthalpy_budget(
    species_kg_hr: Mapping[str, float],
    *,
    vapor_pressures: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one-hour evaporation-enthalpy sinks for evaporated species.

    This is diagnostic-only: it reads vapor species metadata and cited
    coefficients, but does not touch AtomLedger or any kg-keyed state.

    APPROXIMATION: the metal path charges (M(l)->M(g) latent) + (MOx->M+1/2 O2
    dissociation from 298 K ΔfH). It omits the M(s)->M(l) fusion step (~1-4% of
    the per-metal total, Si worst) and all 298 K->process-T sensible heat, so it
    is a consistent low-order ESTIMATE of the evaporation enthalpy, not the full
    MOx(melt, T)->M(g)+1/2 O2 path enthalpy. Oxide vapors (SiO, CrO2) use a
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
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number
