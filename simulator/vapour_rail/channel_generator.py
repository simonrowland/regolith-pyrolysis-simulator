"""Element identity → Mode 2 ``source_reactions`` + ``species_thermo`` rows.

Emits demand-manifest carriers as complete source-inventory families or
typed coverage gaps. The production catalog owns their compilation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from simulator.accounting.formulas import parse_formula
from simulator.alpha_kinetics import ANALYTICAL_UPPER_BOUND_ALPHA_STATUS
from simulator.reference_data.janaf import feedstock_element_symbols
from simulator.trace_oxide_parents import (
    ACTIVITY_BASIS,
    LIQUID_PARENT_OXIDE,
)
from simulator.vapour_rail.catalog import _formula_atoms
from simulator.vapour_rail.source_rail import (
    STANDARD_PRESSURE_PA,
    SourceRail,
    SourceRailRecord,
    load_source_rail,
    supercooled_liquid_from_crystal,
)
from simulator.vapour_rail.stoich import (
    balance_oxide_evaporation,
    derive_stoich_oxide_per_vapor,
    oxygen_fugacity_plane,
    strip_phase,
)
from simulator.yaml_cache import load_cached_safe_yaml

ROOT = Path(__file__).resolve().parents[2]
DEMAND_MANIFEST_PATH = ROOT / "data" / "vapour_rail_demand_manifest.yaml"

FIRST_BATCH_ELEMENTS: tuple[str, ...] = (
    "Ga",
    "In",
    "Pb",
    "Ge",
    "Sn",
    "Rb",
    "Cs",
    "B",
    "Cu",
    "V",
    "Li",
)

# Re-exported from simulator.trace_oxide_parents. One parent table.
PREFERRED_CARRIERS: Mapping[str, tuple[str, ...]] = {
    "Ge": ("GeO",),
    "Sn": ("SnO",),
    "B": ("BO2", "B2O3"),
    "V": ("VO2", "VO"),
}


@dataclass(frozen=True)
class CoverageGap:
    element: str
    carrier: str
    formula: str
    kind: str
    detail: str


@dataclass(frozen=True)
class GeneratedChannel:
    element: str
    carrier: str
    species_id: str
    family: Mapping[str, Any]
    selected_sources: Mapping[str, str]
    parent_oxide: str
    activity_basis: str
    native_phases: Mapping[str, str]
    bands_K: Mapping[str, tuple[float, float]]


@dataclass(frozen=True)
class GeneratedBatch:
    channels: tuple[GeneratedChannel, ...]
    gaps: tuple[CoverageGap, ...]
    feedstock_elements: tuple[str, ...] = field(default_factory=tuple)


def load_demand_manifest(
    path: Path | None = None,
) -> Mapping[str, Any]:
    payload = load_cached_safe_yaml(
        (path or DEMAND_MANIFEST_PATH).read_text(encoding="utf-8")
    )
    if not isinstance(payload, Mapping):
        raise ValueError("demand manifest must be a mapping")
    return payload


def demand_pairs_for_element(
    element: str, *, manifest: Mapping[str, Any] | None = None
) -> tuple[Mapping[str, Any], ...]:
    payload = manifest if manifest is not None else load_demand_manifest()
    pairs = payload.get("pairs") or []
    return tuple(
        row
        for row in pairs
        if isinstance(row, Mapping) and row.get("element") == element
    )


def _is_metaborate(formula: str) -> bool:
    atoms = parse_formula(formula).elements
    metals = {symbol for symbol in atoms if symbol not in {"B", "O"}}
    return "B" in atoms and "O" in atoms and bool(metals)


def _foreign_atoms(element: str, formula: str) -> frozenset[str]:
    atoms = parse_formula(formula).elements
    return frozenset(symbol for symbol in atoms if symbol not in {element, "O"})


def _extend_parent_liquid(
    rail: SourceRail, parent_oxide: str, liquid: SourceRailRecord
) -> SourceRailRecord:
    """Use the same-source crystal that ends at Tm to extend the liquid."""
    if liquid.evaluator_family not in {"nasa_cea_7", "nasa_cea_9"}:
        return liquid
    solids = [
        record
        for record in rail.records_for(parent_oxide, "condensed_solid")
        if record.source_id == liquid.source_id
        and math.isclose(record.T_max_K, liquid.T_min_K, abs_tol=1e-4)
    ]
    if len(solids) != 1:
        return liquid
    extended = supercooled_liquid_from_crystal(liquid, solids[0])
    return extended if extended is not None else liquid


def _intersection_band(
    records: Mapping[str, SourceRailRecord]
) -> tuple[float, float] | None:
    low = max(item.T_min_K for item in records.values())
    high = min(item.T_max_K for item in records.values())
    if high <= low:
        return None
    return (low, high)


def _four_strata_family(
    *,
    species_id: str,
    element: str,
    carrier: str,
    formula: str,
    parent_oxide: str,
    activity_basis: str,
    reaction: Mapping[str, Any],
    species_thermo: Mapping[str, Mapping[str, Any]],
    evaluator_family: str,
    domain: tuple[float, float],
    selected_sources: Mapping[str, str],
    native_phases: Mapping[str, str],
    bands_K: Mapping[str, tuple[float, float]],
    oxide_per_vapor: float,
    o2_per_vapor: float,
    vapor_oxygen_atoms: float,
) -> dict[str, Any]:
    family_id = f"t1139_{element}_{carrier}_family"
    missing_enthalpy = [formula for formula, record in species_thermo.items()
                        if record.get("evaluator_family") == "tabulated_janaf"
                        and not record.get("formation_enthalpy_points")]
    dormancy_reason = None
    if missing_enthalpy:
        dormancy_reason = {
            "kind": "missing_printed_formation_enthalpy",
            "participants": missing_enthalpy,
        }
    else:
        # Controller source ruling: Cu-020's unsigned printed 1600 K tail
        # cannot supply reaction heat. A same-table sign repair removes this
        # missing node and makes the row complete through this same predicate.
        for participant_formula, record in species_thermo.items():
            if (record.get("record_id") == "Cu-020"
                    and 1600.0 in record.get("missing_enthalpy_nodes", ())):
                dormancy_reason = {
                    "kind": "missing_printed_formation_enthalpy",
                    "detail": "ambiguous_printed_sign",
                    "participant": participant_formula,
                    "table": "Cu-020",
                    "temperature_K": 1600.0,
                }
                break
    flux_dormant = dormancy_reason is not None
    source_reaction = dict(reaction)
    source_reaction["activity_input"] = {
        **source_reaction["activity_input"],
        "activity_model": "provider_reported_thermodynamic_activity",
        "allow_henrian_upper_bound": True,
        "standard_state": {
            **source_reaction["activity_input"]["standard_state"],
            "component_basis": "raoultian_pure_endmember",
        },
    }
    family = {
        "physical_properties": {
            "species": {
                species_id: {
                    "formula": formula,
                    "molar_mass_g_mol": parse_formula(formula).molar_mass_g_per_mol(),
                    "parent_oxide": parent_oxide,
                    "chemical_family": "t1139_generated_carrier",
                    "authority_class": "analytical_non_authoritative",
                    "runtime_disposition": "status_bearing_non_authoritative",
                    "flux_dormant": flux_dormant,
                    "retain_analytical_pressure_channel": True,
                    "activity_basis": activity_basis,
                    "selected_sources": dict(selected_sources),
                    "native_phases": dict(native_phases),
                    "source_bands_K": {
                        key: [low, high] for key, (low, high) in bands_K.items()
                    },
                    "liquid_parent_extension": bool(
                        species_thermo.get(f"{parent_oxide}(l)", {}).get(
                            "supercooled_liquid_extension"
                        )
                    ),
                    "validation": {
                        "status": "pending_validation",
                        "certification_ceiling": "never",
                        "anchor_refs": [],
                    },
                    "source_reactions": [source_reaction],
                    "pressure_models": [
                        {
                            "fit_target": "standard_reaction_term",
                            "pressure_kind": "equilibrium_partial_pressure",
                            "species_basis": "monomer",
                            "valid_domain": {
                                "temperature_K": [domain[0], domain[1]],
                            },
                            "evaluator_family": evaluator_family,
                            "source_reaction_id": reaction["id"],
                            "species_thermo": dict(species_thermo),
                            "reference_pressure_Pa": STANDARD_PRESSURE_PA,
                            "activity_semantics": "source_reaction_activity",
                            # balance_oxide_evaporation normalizes to one
                            # mole of vapor; its first reactant is the parent.
                            # The mass ratio belongs only to the ledger bridge.
                            "activity_exponent": reaction["reactants"][0]["stoichiometry"],
                            "pO2_reference_bar": 1.0,
                            "oxygen_fugacity_channel": oxygen_fugacity_plane(
                                vapor_oxygen_atoms=vapor_oxygen_atoms
                            ),
                        }
                    ],
                }
            }
        },
        "fiat_routing": {
            "plant_bin": None,
            "engineering_capture_policy": "derived_from_condensation_onset",
            "products_and_coproducts": [],
            "process_or_terminal_destination": "process.condensation_train",
            "compatibility_fields": {
                "consumer_status": "status_bearing_non_authoritative",
                "source_activity_basis": "parent_oxide",
                "parent_oxide": parent_oxide,
                "stoich_oxide_per_vapor": oxide_per_vapor,
                "stoich_O2_per_vapor": o2_per_vapor,
            },
        },
        "vaporisation_coefficients": {
            "evaporation_alpha": {
                "value": 1.0,
                "status": ANALYTICAL_UPPER_BOUND_ALPHA_STATUS,
            },
            "alpha_domain_and_uncertainty": {},
            "extrapolation_policy": "conservative_slope_continuation",
            "out_of_range_status": "out_of_range_conservative_continuation",
            "acquisition_flag": f"t1139_generated:{species_id}",
        },
        "code_metadata": {
            "formula_id": species_id,
            "source_account": "process.cleaned_melt",
            "request_rule": ("dormant_pending_validation" if flux_dormant
                             else "trace_source_inventory"),
            "solve_group_id": family_id,
            "compatibility_projection": "t1139_generated_carriers",
            "canonical_aliases": [],
            "hot_train_applicability": ("not_applicable" if flux_dormant
                                        else "derived_from_condensation_onset"),
        },
    }
    if dormancy_reason is not None:
        family["physical_properties"]["species"][species_id]["dormancy_reason"] = dormancy_reason
        family["code_metadata"]["hot_train_not_applicable_reason"] = (
            "missing_printed_formation_enthalpy: " + str(dormancy_reason)
        )
    return family


def generate_element_channels(
    element: str,
    *,
    rail: SourceRail | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> tuple[tuple[GeneratedChannel, ...], tuple[CoverageGap, ...]]:
    source_rail = rail if rail is not None else load_source_rail()
    parent_oxide = LIQUID_PARENT_OXIDE.get(element)
    activity_basis = ACTIVITY_BASIS.get(element, parent_oxide or "")
    channels: list[GeneratedChannel] = []
    gaps: list[CoverageGap] = []
    if parent_oxide is None:
        return (), (
            CoverageGap(
                element,
                "",
                "",
                "missing_liquid_parent_binding",
                f"no liquid parent oxide declared for {element}",
            ),
        )
    for pair in demand_pairs_for_element(element, manifest=manifest):
        carrier = str(pair.get("carrier") or "")
        formula = str(pair.get("formula") or carrier)
        if _is_metaborate(formula):
            gaps.append(
                CoverageGap(
                    element,
                    carrier,
                    formula,
                    "metaborate",
                    "metaborate is a typed gap with a lower-bound flag",
                )
            )
            continue
        foreign = _foreign_atoms(element, formula)
        if foreign:
            gaps.append(
                CoverageGap(
                    element,
                    carrier,
                    formula,
                    "non_oxide_carrier",
                    f"carrier atoms {sorted(foreign)} are not in the oxide parent",
                )
            )
            continue
        try:
            bare = strip_phase(formula)
            vapor_atoms = _formula_atoms(bare)
            reaction = balance_oxide_evaporation(
                parent_oxide,
                bare,
                parent_atoms=_formula_atoms(parent_oxide),
                vapor_atoms=vapor_atoms,
            )
        except ValueError as exc:
            gaps.append(
                CoverageGap(
                    element,
                    carrier,
                    formula,
                    "unbalanced_parent",
                    str(exc),
                )
            )
            continue
        uses_o2 = any(
            strip_phase(str(part.get("formula"))) == "O2"
            for side in ("reactants", "products")
            for part in reaction[side]
        )
        participants: list[tuple[str, str]] = [
            (parent_oxide, "condensed_liquid"),
            (strip_phase(formula), "gas"),
        ]
        if uses_o2:
            participants.append(("O2", "gas"))
        selected = source_rail.select_common_source(tuple(participants))
        if selected is None:
            gaps.append(
                CoverageGap(
                    element,
                    carrier,
                    formula,
                    "no_single_source_reaction",
                    "no one compilation tabulates every reaction participant",
                )
            )
            continue
        _source_id, chosen = selected
        records = {
            f"{parent_oxide}(l)": _extend_parent_liquid(
                source_rail,
                parent_oxide,
                chosen[(parent_oxide, "condensed_liquid")],
            ),
            f"{strip_phase(formula)}(g)": chosen[(strip_phase(formula), "gas")],
        }
        if uses_o2:
            records["O2(g)"] = chosen[("O2", "gas")]
        domain = _intersection_band(records)
        if domain is None:
            gaps.append(
                CoverageGap(
                    element,
                    carrier,
                    formula,
                    "no_overlapping_band",
                    "participant thermo bands do not overlap",
                )
            )
            continue
        species_thermo = {
            key: dict(record.species_thermo) for key, record in records.items()
        }
        families = {record.evaluator_family for record in records.values()}
        if len(families) != 1:
            gaps.append(
                CoverageGap(
                    element,
                    carrier,
                    formula,
                    "mixed_evaluator_family",
                    f"common source mixed families {sorted(families)}",
                )
            )
            continue
        evaluator_family = next(iter(families))
        derived = derive_stoich_oxide_per_vapor(
            formula=formula,
            parent_oxide=parent_oxide,
            reaction=reaction,
        )
        oxide, o2 = derived[1], derived[2]
        species_id = carrier
        family = _four_strata_family(
            species_id=species_id,
            element=element,
            carrier=carrier,
            formula=formula,
            parent_oxide=parent_oxide,
            activity_basis=activity_basis,
            reaction=reaction,
            species_thermo=species_thermo,
            evaluator_family=evaluator_family,
            domain=domain,
            selected_sources={
                key: record.source_id for key, record in records.items()
            },
            native_phases={
                key: record.native_phase for key, record in records.items()
            },
            bands_K={
                key: (record.T_min_K, record.T_max_K)
                for key, record in records.items()
            },
            oxide_per_vapor=oxide,
            o2_per_vapor=o2,
            vapor_oxygen_atoms=float(vapor_atoms.get("O", 0.0)),
        )
        family_id = f"t1139_{element}_{carrier}_family"
        payload_family = {family_id: family}
        channels.append(
            GeneratedChannel(
                element=element,
                carrier=carrier,
                species_id=species_id,
                family=payload_family[family_id],
                selected_sources={
                    key: record.source_id for key, record in records.items()
                },
                parent_oxide=parent_oxide,
                activity_basis=activity_basis,
                native_phases={
                    key: record.native_phase for key, record in records.items()
                },
                bands_K={
                    key: (record.T_min_K, record.T_max_K)
                    for key, record in records.items()
                },
            )
        )
    return tuple(channels), tuple(gaps)


def generate_first_batch(
    *,
    rail: SourceRail | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> GeneratedBatch:
    source_rail = rail if rail is not None else load_source_rail()
    payload = manifest if manifest is not None else load_demand_manifest()
    channels: list[GeneratedChannel] = []
    gaps: list[CoverageGap] = []
    for element in FIRST_BATCH_ELEMENTS:
        element_channels, element_gaps = generate_element_channels(
            element, rail=source_rail, manifest=payload
        )
        channels.extend(element_channels)
        gaps.extend(element_gaps)
    return GeneratedBatch(
        channels=tuple(channels),
        gaps=tuple(gaps),
        feedstock_elements=tuple(feedstock_element_symbols()),
    )


def catalog_payload_from_channels(
    channels: tuple[GeneratedChannel, ...],
) -> dict[str, Any]:
    families: dict[str, Any] = {}
    for channel in channels:
        family_id = f"t1139_{channel.element}_{channel.carrier}_family"
        families[family_id] = channel.family
    return {"schema_version": 2, "families": families}
