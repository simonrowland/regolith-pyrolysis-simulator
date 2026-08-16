"""t-583 coverage-ledger composer.

This file is intentionally a real-file runner.  It parses YAML for selection and
validation, but edits the two owner files by text splicing only:

* new catalog families are appended beneath the existing ``families`` mapping;
* selected coverage-gap entry blocks are removed byte-for-byte.

The existing catalog is never round-tripped through ``yaml.safe_dump``.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.vapour_rail.catalog import compile_vapour_rail_catalog  # noqa: E402
from simulator.vapour_rail.channels import (  # noqa: E402
    ChannelCompositionRefusal,
    REFUSAL_CARBON_SIDE_OWNER_MISSING,
    attempt_channel_composition,
)
GAPS_PATH = ROOT / "data/vapour_rail_coverage_gaps.yaml"
CATALOG_PATH = ROOT / "data/vapor_pressures.yaml"
CEA_PATH = ROOT / "data/literature/extracts/nasa-cea-thermo.yaml"

STRICT = "COMPOSABLE-NOW-STRICT"
CLIPPED = "COMPOSABLE-NOW-CLIPPED"
NEEDS_CHANNEL = "NEEDS-CHANNEL"
OPERATING_LOW_K = 1300.0
OPERATING_HIGH_K = 2300.0
R_J_MOL_K = 8.31446261815324

_PATHWAY_RE = re.compile(r"\bpathway=([a-z0-9_]+)")
_ANCHOR_RE = re.compile(r"; anchor (\S+) (?:covers|partial)")
_OVERLAP_RE = re.compile(r"\boverlap_fraction=([0-9.]+)")
_FORMULA_TOKEN_RE = re.compile(r"([A-Z][a-z]?)(\d+(?:\.\d+)?|\.\d+)?")


@dataclass(frozen=True)
class Candidate:
    element: str
    carrier: str
    tier: str
    pathway: str
    anchor_key: str | None
    overlap_fraction: float
    missing: str


@dataclass(frozen=True)
class CompositionSpec:
    carrier: str
    elements: tuple[str, ...]
    pair_count: int
    tier: str
    pathway: str
    anchor_key: str | None
    overlap_fraction: float


@dataclass(frozen=True)
class Stoichiometry:
    anchor_nu: float
    target_nu: float
    o2_nu: float
    standard_state_ligand_nu: tuple[tuple[str, float], ...] = ()

    @property
    def po2_exponent(self) -> float:
        value = -self.o2_nu / self.target_nu
        return 0.0 if value == 0.0 else value


def _load_yaml(path: Path) -> Mapping[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise TypeError(f"{path}: expected mapping root")
    return payload


def _tier(missing: str) -> str | None:
    for value in (STRICT, CLIPPED):
        if missing.startswith(value + ":"):
            return value
    return None


def load_candidates() -> tuple[Candidate, ...]:
    payload = _load_yaml(GAPS_PATH)
    rows = payload.get("entries")
    if not isinstance(rows, list):
        raise TypeError("coverage ledger entries must be a list")
    out: list[Candidate] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        missing = str(row.get("missing") or "")
        tier = _tier(missing)
        if tier is None:
            continue
        pathway_match = _PATHWAY_RE.search(missing)
        anchor_match = _ANCHOR_RE.search(missing)
        if pathway_match is None:
            raise ValueError(f"unparseable composable row: {row!r}")
        pathway = pathway_match.group(1)
        if anchor_match is None and pathway != "catalog_self_executable":
            raise ValueError(f"unparseable composable row: {row!r}")
        overlap_match = _OVERLAP_RE.search(missing)
        overlap = float(overlap_match.group(1)) if overlap_match else 1.0
        out.append(
            Candidate(
                element=str(row["element"]),
                carrier=str(row["carrier"]),
                tier=tier,
                pathway=pathway,
                anchor_key=anchor_match.group(1) if anchor_match else None,
                overlap_fraction=overlap,
                missing=missing,
            )
        )
    return tuple(out)


def load_entries() -> tuple[Mapping[str, Any], ...]:
    rows = _load_yaml(GAPS_PATH).get("entries")
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise TypeError("coverage ledger entries must be mapping rows")
    return tuple(rows)


def typed_refusal(row: Mapping[str, Any]) -> ChannelCompositionRefusal | None:
    """Consume one tiered ledger row and return t-571's typed refusal.

    COMPOSABLE rows return ``None``.  NEEDS-CHANNEL rows must execute the
    channel resolver and may never fall through to the O2-only generator.
    """

    missing = str(row.get("missing") or "")
    if not missing.startswith(NEEDS_CHANNEL + ":"):
        return None
    pathway_match = _PATHWAY_RE.search(missing)
    result = attempt_channel_composition(
        carrier=str(row.get("carrier") or ""),
        element=str(row.get("element") or ""),
        pathway=pathway_match.group(1) if pathway_match else None,
        missing_text=missing,
    )
    if not isinstance(result, ChannelCompositionRefusal):
        raise AssertionError(
            f"NEEDS-CHANNEL carrier {row.get('carrier')} resolved numerically"
        )
    if (
        not result.missing_channels
        and result.disposition != REFUSAL_CARBON_SIDE_OWNER_MISSING
    ):
        raise AssertionError(
            f"NEEDS-CHANNEL refusal for {row.get('carrier')} omitted channel"
        )
    return result


def formula_atoms(formula: str) -> dict[str, float]:
    cleaned = re.sub(r"\([^)]*\)$", "", formula.strip())
    matches = list(_FORMULA_TOKEN_RE.finditer(cleaned))
    if not matches or "".join(match.group(0) for match in matches) != cleaned:
        raise ValueError(f"unsupported formula {formula!r}")
    atoms: dict[str, float] = {}
    for match in matches:
        atoms[match.group(1)] = atoms.get(match.group(1), 0.0) + float(
            match.group(2) or 1.0
        )
    return atoms


def collapse_candidates(candidates: Iterable[Candidate]) -> tuple[CompositionSpec, ...]:
    grouped: defaultdict[str, list[Candidate]] = defaultdict(list)
    for row in candidates:
        grouped[row.carrier].append(row)
    out: list[CompositionSpec] = []
    for carrier, rows in sorted(grouped.items()):
        signatures = {
            (row.tier, row.pathway, row.anchor_key, row.overlap_fraction)
            for row in rows
        }
        if len(signatures) != 1:
            raise ValueError(f"{carrier}: conflicting ledger composition signatures {signatures}")
        tier, pathway, anchor_key, overlap = signatures.pop()
        out.append(
            CompositionSpec(
                carrier=carrier,
                elements=tuple(sorted({row.element for row in rows})),
                pair_count=len(rows),
                tier=tier,
                pathway=pathway,
                anchor_key=anchor_key,
                overlap_fraction=overlap,
            )
        )
    return tuple(out)


def _observation_index() -> tuple[
    Mapping[str, Mapping[str, Any]], Mapping[str, tuple[tuple[str, Mapping[str, Any]], ...]]
]:
    species = _load_yaml(CEA_PATH).get("species")
    if not isinstance(species, Mapping):
        raise TypeError("CEA extract species must be a mapping")
    by_key: dict[str, Mapping[str, Any]] = {}
    gases: defaultdict[str, list[tuple[str, Mapping[str, Any]]]] = defaultdict(list)
    for key, raw in species.items():
        if not isinstance(raw, Mapping):
            continue
        observations = raw.get("observations")
        if not isinstance(observations, list):
            continue
        for observation in observations:
            if not isinstance(observation, Mapping):
                continue
            obs_id = str(observation.get("observation_id") or "")
            if not obs_id:
                continue
            by_key[str(key)] = observation
            values = observation.get("values")
            if isinstance(values, Mapping) and observation.get("phase") == "gas":
                formula = str(values.get("formula") or "")
                if formula:
                    gases[formula].append((str(key), observation))
    return by_key, {key: tuple(value) for key, value in gases.items()}


def _record_ref(observation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "extract_ref": {
            "source_id": "nasa-cea-thermo",
            "observation_id": str(observation["observation_id"]),
        }
    }


def _gas_observation(
    carrier: str,
    gases: Mapping[str, tuple[tuple[str, Mapping[str, Any]], ...]],
) -> Mapping[str, Any]:
    formula = carrier.removesuffix("_gas")
    matches = gases.get(carrier) or gases.get(formula) or ()
    if len(matches) != 1:
        raise ValueError(f"{carrier}: expected one gas observation, got {[key for key, _ in matches]}")
    return matches[0][1]


def _phase_suffix(phase: str) -> str:
    if phase == "condensed_liquid":
        return "(l)"
    if phase in {"condensed", "condensed_solid"}:
        return "(cr)"
    raise ValueError(f"unsupported condensed phase {phase!r}")


def derive_stoichiometry(anchor_formula: str, target_formula: str) -> Stoichiometry:
    """Atom-balance q A_cond -> V_g + n O2 for one mole of target vapor."""

    anchor = formula_atoms(anchor_formula)
    target = formula_atoms(target_formula)
    all_non_oxygen = sorted((set(anchor) | set(target)) - {"O"})
    if not all_non_oxygen:
        raise ValueError("composition requires at least one non-oxygen element")
    exchange_elements = {
        element
        for element in {"F", "Cl", "Br", "I", "H"}
        if anchor.get(element, 0.0) != target.get(element, 0.0)
    }
    primary_elements = [
        element for element in all_non_oxygen if element not in exchange_elements
    ]
    if not primary_elements:
        raise ValueError(
            f"{anchor_formula} -> {target_formula}: no shared non-ligand element"
        )
    ratios: list[float] = []
    for element in primary_elements:
        anchor_count = anchor.get(element, 0.0)
        target_count = target.get(element, 0.0)
        if anchor_count <= 0.0 or target_count <= 0.0:
            raise ValueError(
                f"{anchor_formula} -> {target_formula}: non-O element {element} is not shared"
            )
        ratios.append(target_count / anchor_count)
    q = ratios[0]
    if any(abs(value - q) > 1.0e-9 for value in ratios[1:]):
        raise ValueError(
            f"{anchor_formula} -> {target_formula}: non-O ratios disagree {ratios}"
        )
    o2_nu = (q * anchor.get("O", 0.0) - target.get("O", 0.0)) / 2.0
    ligand_nu = tuple(
        sorted(
            (
                f"{element}2",
                (q * anchor.get(element, 0.0) - target.get(element, 0.0)) / 2.0,
            )
            for element in exchange_elements
            if abs(
                (q * anchor.get(element, 0.0) - target.get(element, 0.0)) / 2.0
            )
            > 1.0e-12
        )
    )
    stoich = Stoichiometry(
        anchor_nu=q,
        target_nu=1.0,
        o2_nu=o2_nu,
        standard_state_ligand_nu=ligand_nu,
    )

    # Independent atom-balance sanity check over the exact generated reaction.
    for element in sorted(set(anchor) | set(target)):
        left = q * anchor.get(element, 0.0)
        right = target.get(element, 0.0) + (2.0 * o2_nu if element == "O" else 0.0)
        for ligand_formula, ligand_amount in ligand_nu:
            ligand_element = ligand_formula.removesuffix("2")
            if element == ligand_element:
                right += 2.0 * ligand_amount
        if abs(left - right) > 1.0e-9:
            raise AssertionError(
                f"{anchor_formula} -> {target_formula}: {element} imbalance {left} != {right}"
            )
    return stoich


def _domain(observation: Mapping[str, Any]) -> tuple[float, float]:
    bounds = observation.get("T_range_K")
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise ValueError(f"{observation.get('observation_id')}: invalid T_range_K")
    return float(bounds[0]), float(bounds[1])


def _valid_domain(
    spec: CompositionSpec,
    gas: Mapping[str, Any],
    anchor: Mapping[str, Any],
) -> tuple[float, float]:
    gas_low, gas_high = _domain(gas)
    anchor_low, anchor_high = _domain(anchor)
    low = max(OPERATING_LOW_K, gas_low, anchor_low)
    high = min(OPERATING_HIGH_K, gas_high, anchor_high)
    if not low < high:
        raise ValueError(f"{spec.carrier}: empty gas/anchor operating overlap")
    actual_overlap = (high - low) / (OPERATING_HIGH_K - OPERATING_LOW_K)
    if spec.tier == STRICT:
        if (low, high) != (OPERATING_LOW_K, OPERATING_HIGH_K):
            raise ValueError(f"{spec.carrier}: STRICT row has clipped domain {(low, high)}")
        if spec.overlap_fraction != 1.0:
            raise ValueError(f"{spec.carrier}: STRICT row declares overlap {spec.overlap_fraction}")
    elif spec.tier == CLIPPED:
        if (low, high) == (OPERATING_LOW_K, OPERATING_HIGH_K):
            raise ValueError(f"{spec.carrier}: CLIPPED row unexpectedly covers full envelope")
        if abs(actual_overlap - spec.overlap_fraction) > 5.0e-4:
            raise ValueError(
                f"{spec.carrier}: overlap {actual_overlap} != ledger {spec.overlap_fraction}"
            )
    else:
        raise ValueError(f"unsupported tier {spec.tier}")
    return low, high


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _fmt(value: float) -> str:
    if value == 0.0:
        return "0"
    if value.is_integer():
        return str(int(value))
    return format(value, ".12g")


def _is_subsequence(needles: Iterable[str], haystack: Iterable[str]) -> bool:
    iterator = iter(haystack)
    return all(any(candidate == needle for candidate in iterator) for needle in needles)


class _NoAliasSafeDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def _family_mapping(
    spec: CompositionSpec,
    *,
    gas: Mapping[str, Any],
    anchor: Mapping[str, Any],
) -> tuple[str, dict[str, Any], tuple[str, str, str, str]]:
    if spec.anchor_key is None:
        raise ValueError(f"{spec.carrier}: catalog-self rows do not generate families")
    gas_values = gas.get("values")
    anchor_values = anchor.get("values")
    if not isinstance(gas_values, Mapping) or not isinstance(anchor_values, Mapping):
        raise TypeError(f"{spec.carrier}: malformed CEA values")
    target_formula = str(gas_values["formula"])
    anchor_formula = str(anchor_values["formula"])
    anchor_phase = str(anchor["phase"])
    if anchor_phase == "condensed":
        anchor_phase = "condensed_solid"
    tagged_anchor = anchor_formula + _phase_suffix(anchor_phase)
    tagged_target = target_formula + "(g)"
    stoich = derive_stoichiometry(anchor_formula, target_formula)
    low, high = _valid_domain(spec, gas, anchor)
    family_id = f"t583_status_{_slug(spec.carrier)}_family"
    pure_self = formula_atoms(anchor_formula) == formula_atoms(target_formula) and abs(
        stoich.anchor_nu - 1.0
    ) < 1.0e-12

    reaction_reactants = [f"{_fmt(stoich.anchor_nu)} {tagged_anchor}"]
    reaction_products = [tagged_target]
    if stoich.o2_nu > 0.0:
        reaction_products.append(f"{_fmt(stoich.o2_nu)} O2(g)")
    elif stoich.o2_nu < 0.0:
        reaction_reactants.append(f"{_fmt(-stoich.o2_nu)} O2(g)")
    for ligand_formula, ligand_nu in stoich.standard_state_ligand_nu:
        if ligand_nu > 0.0:
            reaction_products.append(f"{_fmt(ligand_nu)} {ligand_formula}(g)")
        elif ligand_nu < 0.0:
            reaction_reactants.append(f"{_fmt(-ligand_nu)} {ligand_formula}(g)")
    reaction_formula = " + ".join(reaction_reactants) + " -> " + " + ".join(
        reaction_products
    )
    premise = (
        f"Premise: signed stoichiometry for {reaction_formula} has "
        f"nu_anchor=-{_fmt(stoich.anchor_nu)}, nu_target=+1, "
        f"nu_O2={_fmt(stoich.o2_nu)}."
    )
    algebra = (
        f"Algebra: e_O2=-nu_O2/nu_target=-({_fmt(stoich.o2_nu)})/1="
        f"{_fmt(stoich.po2_exponent)}; coefficients come from formula atom counts."
    )
    units = (
        "Unit check: stoichiometric mol-per-extent ratios and pO2/Pstd are dimensionless; "
        "the solved pressure ratio times Pstd is Pa."
    )
    sanity = (
        f"Sanity check: independent atom balance passed; valid domain [{_fmt(low)}, {_fmt(high)}] K "
        f"is {spec.tier} with overlap_fraction={_fmt(spec.overlap_fraction)}."
    )

    row: dict[str, Any] = {
        "formula": target_formula,
        "molar_mass_g_mol": float(gas_values["molecular_weight_g_per_mol"]),
        "chemical_family": "t583_composed_carrier",
        "authority_class": "analytical_non_authoritative",
        "acquisition_status": "t583_composed_from_corrected_domain_anchor",
        "runtime_disposition": "status_bearing_non_authoritative",
        "flux_dormant": True,
        "retain_analytical_pressure_channel": True,
        "coverage_tier": spec.tier,
        "coverage_pathway": spec.pathway,
        "coverage_elements": list(spec.elements),
        "coverage_ledger_pair_count": spec.pair_count,
        "coverage_overlap_fraction": spec.overlap_fraction,
        "coverage_scope": (
            "full_operating_envelope" if spec.tier == STRICT else "clipped_operating_envelope"
        ),
        "coverage_anchor_key": spec.anchor_key,
        "reaction": {
            "formula": reaction_formula,
            "basis": (
                "Corrected ae2f485 domain-covering CEA condensed anchor; "
                "pure condensed activity fixed at one."
            ),
        },
        "notes": (
            "Instrument-before-gate carrier. Evaluator is status-bearing and non-authoritative; "
            "flux remains dormant and certification ceiling is never."
        ),
        "validation": {
            "status": "pending_validation",
            "certification_ceiling": "never",
            "anchor_refs": [],
        },
    }

    # Always use the balanced source-reaction mode, including pure self phase
    # transfer.  Phase-tagged thermo keys keep A(condensed) distinct from A(g)
    # while retaining one exact atom-balanced derivation path.
    reaction_id = f"t583_{_slug(spec.carrier)}_from_{_slug(spec.anchor_key)}"
    reactants = [{"formula": tagged_anchor, "stoichiometry": stoich.anchor_nu}]
    products = [{"formula": tagged_target, "stoichiometry": stoich.target_nu}]
    if stoich.o2_nu > 0.0:
        products.append({"formula": "O2", "stoichiometry": stoich.o2_nu})
    elif stoich.o2_nu < 0.0:
        reactants.append({"formula": "O2", "stoichiometry": -stoich.o2_nu})
    for ligand_formula, ligand_nu in stoich.standard_state_ligand_nu:
        participant = {"formula": ligand_formula, "stoichiometry": abs(ligand_nu)}
        (products if ligand_nu > 0.0 else reactants).append(participant)
    row["source_reactions"] = [
        {"id": reaction_id, "reactants": reactants, "products": products}
    ]
    species_thermo = {
        tagged_anchor: _record_ref(anchor),
        tagged_target: _record_ref(gas),
    }
    if stoich.o2_nu != 0.0:
        _, gases = _observation_index()
        species_thermo["O2"] = _record_ref(_gas_observation("O2", gases))
    ligand_observation_ids: list[str] = []
    if stoich.standard_state_ligand_nu:
        _, gases = _observation_index()
        for ligand_formula, _ligand_nu in stoich.standard_state_ligand_nu:
            ligand_observation = _gas_observation(ligand_formula, gases)
            species_thermo[ligand_formula] = _record_ref(ligand_observation)
            ligand_observation_ids.append(str(ligand_observation["observation_id"]))
    model: dict[str, Any] = {
        "fit_target": "pure_component_psat" if pure_self else "standard_reaction_term",
        "pressure_kind": "equilibrium_partial_pressure",
        "species_basis": "monomer",
        "valid_domain": {"temperature_K": [low, high]},
        "provenance": [
            {
                "source_id": "nasa-cea-thermo",
                "observation_ids": [
                    str(gas["observation_id"]),
                    str(anchor["observation_id"]),
                ]
                + (["cea_O2_gibbs"] if stoich.o2_nu != 0.0 else [])
                + ligand_observation_ids,
                "status": "status_bearing_non_authoritative",
            }
        ],
        "evaluator_family": "nasa_cea_9",
        "source_reaction_id": reaction_id,
        "species_thermo": species_thermo,
        "activity_semantics": "pure_condensed_phase",
        "pure_condensed_phase_identity": {
            "component_id": tagged_anchor,
            "phase": anchor_phase,
            "source_account": "process.t583_diagnostic_pure_condensed",
        },
        "activity_exponent": 0.0,
        "pO2_reference_bar": 1.0,
    }
    if stoich.po2_exponent != 0.0:
        model["oxygen_fugacity_channel"] = "transport_headspace"
    row["pressure_models"] = [model]

    family = {
        "physical_properties": {"species": {spec.carrier: row}},
        "fiat_routing": {
            "plant_bin": None,
            "engineering_capture_policy": "diagnostic_only",
            "products_and_coproducts": [],
            "process_or_terminal_destination": "process.condensation_train",
            "compatibility_fields": {
                "flux_dormant": True,
                "consumer_status": "status_bearing_non_authoritative",
            },
        },
        "vaporisation_coefficients": {
            "evaporation_alpha": {
                "status": "no_data",
                "policy": "refuse_nonzero_flux",
                "compatibility_policy_field": "refuse_nonzero_flux",
            },
            "alpha_domain_and_uncertainty": {},
            "extrapolation_policy": "conservative_slope_continuation",
            "out_of_range_status": "out_of_range_conservative_continuation",
            "acquisition_flag": f"t583_status_only:{spec.carrier}",
        },
        "code_metadata": {
            "t583_status_only_composed": True,
            "formula_id": spec.carrier,
            "source_account": "process.t583_diagnostic_pure_condensed",
            "request_rule": "dormant_pending_validation",
            "solve_group_id": family_id,
            "compatibility_projection": "t583_status_only_carriers",
            "canonical_aliases": [],
            "hot_train_applicability": "not_applicable",
            "hot_train_not_applicable_reason": (
                "t-583 instrument-before-gate row; pressure is diagnostic and flux is dormant."
            ),
        },
    }
    return family_id, family, (premise, algebra, units, sanity)


def _render_family(
    family_id: str,
    family: Mapping[str, Any],
    comments: tuple[str, str, str, str],
) -> str:
    raw = yaml.dump(
        {family_id: family},
        Dumper=_NoAliasSafeDumper,
        sort_keys=False,
        width=110,
        default_flow_style=False,
    )
    lines = raw.splitlines()
    marker_index = next(
        index for index, line in enumerate(lines) if line.strip() == "reaction:"
    )
    indent = lines[marker_index][: len(lines[marker_index]) - len(lines[marker_index].lstrip())]
    lines[marker_index:marker_index] = [f"{indent}# {comment}" for comment in comments]
    return "\n".join("  " + line for line in lines) + "\n"


def _catalog_self_row(
    spec: CompositionSpec,
    existing_row: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, str, str, str]]:
    """Build an executable status-only model from a landed dormant correlation."""

    formula = str(existing_row.get("formula") or spec.carrier.removesuffix("_gas"))
    old_models = existing_row.get("pressure_models")
    if not isinstance(old_models, list) or len(old_models) != 1 or not isinstance(old_models[0], Mapping):
        raise ValueError(f"{spec.carrier}: catalog-self row requires one dormant model")
    old_model = old_models[0]
    valid_domain = old_model.get("valid_domain")
    if not isinstance(valid_domain, Mapping):
        raise ValueError(f"{spec.carrier}: catalog-self row missing valid_domain")
    provenance = list(old_model.get("provenance") or []) + [
        {
            "source": "t-583 catalog-self executable composition",
            "status": "status_bearing_non_authoritative",
        }
    ]
    source_reactions: list[dict[str, Any]] = []
    po2_exponent = 0.0

    antoine = existing_row.get("pure_component_antoine")
    if isinstance(antoine, Mapping) and all(key in antoine for key in ("A", "B", "C")):
        phase = "condensed_liquid"
        model: dict[str, Any] = {
            "fit_target": "pure_component_psat",
            "pressure_kind": "pure_component_saturation_pressure",
            "species_basis": str(old_model.get("species_basis") or "monomer"),
            "valid_domain": dict(valid_domain),
            "provenance": provenance,
            "evaluator_family": "antoine",
            "coefficients": {
                "A": float(antoine["A"]),
                "B": float(antoine["B"]),
                "C": float(antoine["C"]),
            },
            "activity_semantics": "pure_condensed_phase",
            "pure_condensed_phase_identity": {
                "component_id": formula + "(l)",
                "phase": phase,
                "source_account": "process.t583_diagnostic_pure_condensed",
            },
            "activity_exponent": 0.0,
            "pO2_reference_bar": 1.0,
        }
        premise = (
            f"Premise: landed catalog correlation transfers {formula}(condensed) to {formula}(g); "
            "nu_target=1 and nu_O2=0."
        )
    elif isinstance(existing_row.get("literature_correlation"), Mapping):
        correlation = existing_row["literature_correlation"]
        coeffs = correlation.get("A_i")
        bounds = correlation.get("valid_range_K")
        if not isinstance(coeffs, list) or not isinstance(bounds, list) or len(bounds) != 2:
            raise ValueError(f"{spec.carrier}: malformed landed literature correlation")
        low, high = float(bounds[0]), float(bounds[1])
        temperatures = (low, 0.5 * (low + high), high)
        points: list[dict[str, float]] = []
        for temperature in temperatures:
            ln_p_bar = sum(float(value) / (temperature**index) for index, value in enumerate(coeffs))
            points.append(
                {
                    "temperature_K": temperature,
                    "pressure_Pa": math.exp(ln_p_bar) * 1.0e5,
                }
            )
        model = {
            "fit_target": str(old_model.get("fit_target") or "pure_component_psat"),
            "pressure_kind": str(
                old_model.get("pressure_kind") or "pure_component_saturation_pressure"
            ),
            "species_basis": str(old_model.get("species_basis") or "monomer"),
            "valid_domain": dict(valid_domain),
            "provenance": provenance,
            "evaluator_family": "tabulated_equilibrium",
            "points": points,
            "activity_semantics": "pure_condensed_phase",
            "pure_condensed_phase_identity": {
                "component_id": formula + "(cr)",
                "phase": "condensed_solid",
                "source_account": "process.t583_diagnostic_pure_condensed",
            },
            "activity_exponent": 0.0,
            "pO2_reference_bar": 1.0,
        }
        premise = (
            f"Premise: landed catalog correlation transfers {formula}(condensed) to {formula}(g); "
            "nu_target=1 and nu_O2=0."
        )
    else:
        correlations = existing_row.get("t431_correlations")
        if not isinstance(correlations, list):
            raise ValueError(f"{spec.carrier}: no executable landed catalog correlation")
        pure_candidates = [
            value
            for value in correlations
            if isinstance(value, Mapping)
            and value.get("kind") == "pure_component_vapor_pressure"
            and isinstance(value.get("valid_range_K"), list)
            and len(value["valid_range_K"]) == 2
        ]
        if pure_candidates:
            correlation = max(
                pure_candidates,
                key=lambda value: float(value["valid_range_K"][1]),
            )
            source_form = str(correlation.get("source_form") or "")
            match = re.search(
                r"P_Pa\s*=\s*([0-9.eE+-]+)\s*\*\s*exp\[-([0-9.eE+-]+)"
                r"/\(R\*T_K\)\]",
                source_form,
            )
            if match is None:
                raise ValueError(
                    f"{spec.carrier}: unsupported pure-component source form {source_form!r}"
                )
            prefactor_pa = float(match.group(1))
            enthalpy_j_mol = float(match.group(2))
            bounds = [float(value) for value in correlation["valid_range_K"]]
            temperatures = (bounds[0], 0.5 * sum(bounds), bounds[1])
            points = [
                {
                    "temperature_K": temperature,
                    "pressure_Pa": prefactor_pa
                    * math.exp(-enthalpy_j_mol / (R_J_MOL_K * temperature)),
                }
                for temperature in temperatures
            ]
            model = {
                "fit_target": "pure_component_psat",
                "pressure_kind": "pure_component_saturation_pressure",
                "species_basis": str(old_model.get("species_basis") or "monomer"),
                "valid_domain": {"temperature_K": bounds},
                "provenance": provenance,
                "evaluator_family": "tabulated_equilibrium",
                "points": points,
                "activity_semantics": "pure_condensed_phase",
                "pure_condensed_phase_identity": {
                    "component_id": formula + "(l)",
                    "phase": "condensed_liquid",
                    "source_account": "process.t583_diagnostic_pure_condensed",
                },
                "activity_exponent": 0.0,
                "pO2_reference_bar": 1.0,
            }
            comments = (
                f"Premise: landed pure-component correlation {source_form}; nu_target=1 and nu_O2=0.",
                "Algebra: e_O2=-nu_O2/nu_target=0; no oxygen fugacity term is introduced.",
                "Unit check: the source correlation returns Pa and all pressure ratios remain dimensionless.",
                "Sanity check: catalog-self row remains flux-dormant, non-authoritative, and domain-bounded.",
            )
            return {"source_reactions": [], "pressure_models": [model]}, comments
        matching = [
            value
            for value in correlations
            if isinstance(value, Mapping)
            and re.sub(r"\([^)]*\)$", "", str(value.get("gas_species"))) == formula
        ]
        if len(matching) != 1:
            raise ValueError(f"{spec.carrier}: expected one matching t431 correlation")
        correlation = matching[0]
        landed_coefficients = correlation.get("coefficients")
        if isinstance(landed_coefficients, Mapping) and all(
            key in landed_coefficients for key in ("A", "B")
        ):
            model = {
                "fit_target": "standard_reaction_term",
                "pressure_kind": "equilibrium_partial_pressure",
                "species_basis": str(old_model.get("species_basis") or "monomer"),
                "valid_domain": dict(valid_domain),
                "provenance": provenance,
                "evaluator_family": "antoine",
                "coefficients": {
                    "A": float(landed_coefficients["A"]),
                    "B": float(landed_coefficients["B"]),
                    "C": float(landed_coefficients.get("C", 0.0)),
                },
                "activity_exponent": 0.0,
                "pO2_reference_bar": 1.0,
            }
            reaction_text = str(correlation.get("reaction") or "landed buffered reaction")
            comments = (
                f"Premise: landed catalog buffer reaction {reaction_text} has no O2 participant; nu_O2=0.",
                "Algebra: e_O2=-nu_O2/nu_target=0; coefficient is derived from the landed reaction.",
                "Unit check: the landed log10 pressure correlation returns Pa and all pressure ratios are dimensionless.",
                "Sanity check: catalog-self row remains flux-dormant, non-authoritative, and uses its landed validity domain.",
            )
            return {"source_reactions": [], "pressure_models": [model]}, comments
        reservoir = re.sub(
            r"\(c\)$", "(cr)", str(correlation["condensed_reservoir"])
        )
        stoich = derive_stoichiometry(reservoir, formula)
        po2_exponent = stoich.po2_exponent
        source_form = str(correlation["source_form"])
        match = re.search(
            r"DeltaG[^=]*=\s*([0-9.]+)\s*-\s*([0-9.]+)\s*\*?\s*T_K",
            source_form,
        )
        if match is None:
            raise ValueError(f"{spec.carrier}: cannot parse {source_form!r}")
        enthalpy, entropy = (float(match.group(1)), float(match.group(2)))
        gas_constant = 1.98720425864083 if "cal_mol" in source_form else 8.31446261815324
        standard_pressure = str(correlation.get("standard_pressure") or "1 bar").lower()
        pressure_standard_pa = 101325.0 if "atm" in standard_pressure else 100000.0
        reference_coefficients = {
            "A": math.log10(pressure_standard_pa)
            + entropy / (gas_constant * math.log(10.0)),
            "B": enthalpy / (gas_constant * math.log(10.0)),
            "C": 0.0,
        }
        tagged_target = formula + "(g)"
        reactants = [{"formula": reservoir, "stoichiometry": stoich.anchor_nu}]
        products = [{"formula": tagged_target, "stoichiometry": 1.0}]
        if stoich.o2_nu > 0.0:
            products.append({"formula": "O2", "stoichiometry": stoich.o2_nu})
        elif stoich.o2_nu < 0.0:
            reactants.append({"formula": "O2", "stoichiometry": -stoich.o2_nu})
        reaction_id = f"t583_{_slug(spec.carrier)}_catalog_self"
        source_reactions = [
            {"id": reaction_id, "reactants": reactants, "products": products}
        ]
        phase = "condensed_liquid" if reservoir.lower().endswith("(l)") else "condensed_solid"
        model = {
            "fit_target": "standard_reaction_term",
            "pressure_kind": "equilibrium_partial_pressure",
            "species_basis": str(old_model.get("species_basis") or "monomer"),
            "valid_domain": dict(valid_domain),
            "provenance": provenance,
            "evaluator_family": "standard_reaction_term",
            "source_reaction_id": reaction_id,
            "reference_pressure_model": {
                "evaluator_family": "antoine",
                "coefficients": reference_coefficients,
            },
            "activity_semantics": "pure_condensed_phase",
            "pure_condensed_phase_identity": {
                "component_id": reservoir,
                "phase": phase,
                "source_account": "process.t583_diagnostic_pure_condensed",
            },
            "activity_exponent": 0.0,
            "pO2_exponent": po2_exponent,
            "pO2_reference_bar": 1.0,
        }
        if po2_exponent != 0.0:
            model["oxygen_fugacity_channel"] = "transport_headspace"
        premise = (
            f"Premise: signed stoichiometry consumes {stoich.anchor_nu:g} {reservoir}, "
            f"produces {formula}(g), and has nu_O2={stoich.o2_nu:g}."
        )

    comments = (
        premise,
        f"Algebra: e_O2=-nu_O2/nu_target={po2_exponent:g}; coefficient is derived from the balanced reaction.",
        "Unit check: stoichiometric ratios and pressure/activity ratios are dimensionless; returned pressure is Pa.",
        "Sanity check: catalog-self row remains flux-dormant, non-authoritative, and uses its landed validity domain.",
    )
    return {
        "source_reactions": source_reactions,
        "pressure_models": [model],
    }, comments


def _render_fragment(
    value: Mapping[str, Any],
    *,
    indent: int,
    comments: tuple[str, str, str, str] | None = None,
) -> str:
    raw = yaml.dump(
        dict(value),
        Dumper=_NoAliasSafeDumper,
        sort_keys=False,
        width=110,
        default_flow_style=False,
    )
    lines = raw.splitlines()
    if comments is not None:
        marker_index = next(
            index
            for index, line in enumerate(lines)
            if line.strip().startswith("evaluator_family:")
        )
        marker_indent = lines[marker_index][
            : len(lines[marker_index]) - len(lines[marker_index].lstrip())
        ]
        lines[marker_index:marker_index] = [
            f"{marker_indent}# {comment}" for comment in comments
        ]
    prefix = " " * indent
    return "\n".join(prefix + line for line in lines) + "\n"


def _replace_row_key_block(row_text: str, key: str, replacement: str) -> str:
    start_match = re.search(rf"(?m)^          {re.escape(key)}:", row_text)
    if start_match is None:
        raise ValueError(f"existing row missing {key}")
    next_match = re.search(
        r"(?m)^          (?!-\s)[^ #\n][^\n]*:",
        row_text[start_match.end() :],
    )
    end = (
        start_match.end() + next_match.start()
        if next_match is not None
        else len(row_text)
    )
    old = row_text[start_match.start() : end]
    old_comments = [line for line in old.splitlines() if line.lstrip().startswith("#")]
    if old_comments:
        replacement = "\n".join(old_comments) + "\n" + replacement
    return row_text[: start_match.start()] + replacement + row_text[end:]


def _patch_existing_family(
    catalog_text: str,
    *,
    carrier: str,
    family_id: str,
    row_updates: Mapping[str, Any],
    comments: tuple[str, str, str, str],
    spec: CompositionSpec,
) -> str:
    family_match = re.search(rf"(?m)^  {re.escape(family_id)}:\n", catalog_text)
    if family_match is None:
        raise ValueError(f"{carrier}: family {family_id} not found in catalog text")
    next_family = re.search(r"(?m)^  [^ #\n][^\n]*:\n", catalog_text[family_match.end() :])
    family_end = (
        family_match.end() + next_family.start()
        if next_family is not None
        else len(catalog_text)
    )
    family_text = catalog_text[family_match.start() : family_end]
    species_match = re.search(rf"(?m)^        ['\"]?{re.escape(carrier)}['\"]?:\n", family_text)
    if species_match is None:
        raise ValueError(f"{carrier}: species block not found in {family_id}")
    routing_match = re.search(r"(?m)^    fiat_routing:\n", family_text[species_match.end() :])
    if routing_match is None:
        raise ValueError(f"{carrier}: fiat_routing boundary not found")
    row_end = species_match.end() + routing_match.start()
    row_text = family_text[species_match.start() : row_end]

    pressure_fragment = _render_fragment(
        {"pressure_models": row_updates["pressure_models"]},
        indent=10,
        comments=comments,
    )
    row_text = _replace_row_key_block(row_text, "pressure_models", pressure_fragment)
    reaction_fragment = _render_fragment(
        {"source_reactions": row_updates["source_reactions"]}, indent=10
    )
    row_text = _replace_row_key_block(row_text, "source_reactions", reaction_fragment)
    composition = {
        "t583_composition": {
            "status": "status_bearing_non_authoritative",
            "flux_dormant": True,
            "coverage_tier": spec.tier,
            "coverage_pathway": spec.pathway,
            "coverage_elements": list(spec.elements),
            "coverage_ledger_pair_count": spec.pair_count,
            "overlap_fraction": spec.overlap_fraction,
            "coverage_scope": (
                "full_operating_envelope"
                if spec.tier == STRICT
                else "clipped_operating_envelope"
            ),
            "anchor_key": spec.anchor_key,
        }
    }
    composition_fragment = _render_fragment(composition, indent=10)
    insert_at = re.search(r"(?m)^          source_reactions:", row_text)
    assert insert_at is not None
    row_text = row_text[: insert_at.start()] + composition_fragment + row_text[insert_at.start() :]

    family_text = family_text[: species_match.start()] + row_text + family_text[row_end:]
    code_match = re.search(r"(?m)^    code_metadata:\n", family_text)
    if code_match is None:
        raise ValueError(f"{carrier}: code_metadata block not found")
    family_text = (
        family_text[: code_match.end()]
        + "      t583_status_only_composed: true\n"
        + family_text[code_match.end() :]
    )
    code_start = code_match.end() + len("      t583_status_only_composed: true\n")
    family_text = (
        family_text[:code_start]
        + re.sub(
            r"(?m)^      source_account:.*$",
            "      source_account: process.t583_diagnostic_pure_condensed",
            family_text[code_start:],
            count=1,
        )
    )
    return catalog_text[: family_match.start()] + family_text + catalog_text[family_end:]


def _patch_existing_t583_coverage(
    catalog_text: str,
    *,
    carrier: str,
    family_id: str,
    existing_row: Mapping[str, Any],
    spec: CompositionSpec,
) -> str:
    """Merge later demand-pair receipts into a row created by the Ba pilot."""

    family_match = re.search(rf"(?m)^  {re.escape(family_id)}:\n", catalog_text)
    if family_match is None:
        raise ValueError(f"{carrier}: family {family_id} not found")
    next_family = re.search(r"(?m)^  [^ #\n][^\n]*:\n", catalog_text[family_match.end() :])
    family_end = (
        family_match.end() + next_family.start()
        if next_family is not None
        else len(catalog_text)
    )
    family_text = catalog_text[family_match.start() : family_end]
    species_match = re.search(rf"(?m)^        ['\"]?{re.escape(carrier)}['\"]?:\n", family_text)
    if species_match is None:
        raise ValueError(f"{carrier}: cannot isolate t-583 pilot row")
    routing_match = re.search(r"(?m)^    fiat_routing:\n", family_text[species_match.end() :])
    if routing_match is None:
        raise ValueError(f"{carrier}: cannot isolate t-583 pilot row")
    row_end = species_match.end() + routing_match.start()
    row_text = family_text[species_match.start() : row_end]

    elements = sorted(
        {str(value) for value in existing_row.get("coverage_elements", [])}
        | set(spec.elements)
    )
    element_lines = "\n".join(f"          - {element}" for element in elements)
    row_text, substitutions = re.subn(
        r"(?m)^          coverage_elements:\n(?:          - [^\n]+\n)+",
        f"          coverage_elements:\n{element_lines}\n",
        row_text,
        count=1,
    )
    if substitutions != 1:
        raise ValueError(f"{carrier}: coverage_elements block missing")
    pair_count = int(existing_row.get("coverage_ledger_pair_count", 0)) + spec.pair_count
    row_text, substitutions = re.subn(
        r"(?m)^          coverage_ledger_pair_count:.*$",
        f"          coverage_ledger_pair_count: {pair_count}",
        row_text,
        count=1,
    )
    if substitutions != 1:
        raise ValueError(f"{carrier}: coverage_ledger_pair_count missing")

    # The runtime-thermo compiler derives e_O2 directly from signed reaction
    # stoichiometry.  Pilot-era compatibility scalars are therefore removed.
    row_text = re.sub(r"(?m)^          pO2_exponent:.*\n", "", row_text)
    row_text = re.sub(r"(?m)^            pO2_exponent:.*\n", "", row_text)
    family_text = family_text[: species_match.start()] + row_text + family_text[row_end:]
    if "      t583_status_only_composed: true\n" not in family_text:
        code_match = re.search(r"(?m)^    code_metadata:\n", family_text)
        if code_match is None:
            raise ValueError(f"{carrier}: code_metadata block missing")
        family_text = (
            family_text[: code_match.end()]
            + "      t583_status_only_composed: true\n"
            + family_text[code_match.end() :]
        )
    return catalog_text[: family_match.start()] + family_text + catalog_text[family_end:]


def _patch_existing_executable_receipt(
    catalog_text: str,
    *,
    carrier: str,
    family_id: str,
    spec: CompositionSpec,
) -> str:
    """Record demand-pair wiring without altering an existing evaluator."""

    family_match = re.search(rf"(?m)^  {re.escape(family_id)}:\n", catalog_text)
    if family_match is None:
        raise ValueError(f"{carrier}: family {family_id} not found")
    next_family = re.search(r"(?m)^  [^ #\n][^\n]*:\n", catalog_text[family_match.end() :])
    family_end = (
        family_match.end() + next_family.start()
        if next_family is not None
        else len(catalog_text)
    )
    family_text = catalog_text[family_match.start() : family_end]
    code_match = re.search(r"(?m)^    code_metadata:\n", family_text)
    if code_match is None:
        raise ValueError(f"{carrier}: code_metadata block missing")
    receipt = _render_fragment(
        {
            "t583_existing_executable_composed": {
                "status": "existing_evaluator_wiring_receipt",
                "coverage_tier": spec.tier,
                "coverage_elements": list(spec.elements),
                "coverage_ledger_pair_count": spec.pair_count,
            }
        },
        indent=6,
    )
    family_text = family_text[: code_match.end()] + receipt + family_text[code_match.end() :]
    return catalog_text[: family_match.start()] + family_text + catalog_text[family_end:]


def _remove_selected_gap_blocks(
    text: str, selected: set[tuple[str, str, str]]
) -> tuple[str, int]:
    block_re = re.compile(r"(?ms)^- element:.*?(?=^- element:|\Z)")
    removed = 0
    parts: list[str] = []
    cursor = 0
    for match in block_re.finditer(text):
        parts.append(text[cursor : match.start()])
        parsed = yaml.safe_load(match.group(0))
        if not isinstance(parsed, list) or len(parsed) != 1 or not isinstance(parsed[0], Mapping):
            raise ValueError("failed to parse coverage-gap entry block")
        row = parsed[0]
        key = (str(row.get("element")), str(row.get("carrier")), str(row.get("missing")))
        if key in selected:
            removed += 1
        else:
            parts.append(match.group(0))
        cursor = match.end()
    parts.append(text[cursor:])
    if removed != len(selected):
        raise ValueError(f"removed {removed} rows but selected {len(selected)}")
    return "".join(parts), removed


def compose(*, pilot: str | None, apply: bool) -> Mapping[str, Any]:
    entries = load_entries()
    scoped = tuple(
        row for row in entries if pilot is None or str(row.get("element")) == pilot
    )
    refusals = tuple(result for row in scoped if (result := typed_refusal(row)) is not None)
    candidates = load_candidates()
    selected = tuple(
        row for row in candidates if pilot is None or row.element == pilot
    )
    specs = collapse_candidates(selected)
    catalog_species = _catalog_species_ids()
    catalog_before = CATALOG_PATH.read_text(encoding="utf-8")
    catalog_working = catalog_before
    compiled_before = compile_vapour_rail_catalog(_load_yaml(CATALOG_PATH))
    locations = _catalog_locations()
    by_key, gases = _observation_index()
    rendered: list[str] = []
    generated: list[str] = []
    reused: list[str] = []
    upgraded: list[str] = []
    per_tier_unique: Counter[str] = Counter()
    for spec in specs:
        per_tier_unique[spec.tier] += 1
        if spec.carrier in catalog_species:
            # b-189-exempt: catalog composition tooling
            if compiled_before.species[spec.carrier].evaluator is not None:
                family_id, existing_row, _family = locations[spec.carrier]
                if existing_row.get("chemical_family") == "t583_composed_carrier":
                    catalog_working = _patch_existing_t583_coverage(
                        catalog_working,
                        carrier=spec.carrier,
                        family_id=family_id,
                        existing_row=existing_row,
                        spec=spec,
                    )
                else:
                    catalog_working = _patch_existing_executable_receipt(
                        catalog_working,
                        carrier=spec.carrier,
                        family_id=family_id,
                        spec=spec,
                    )
                reused.append(spec.carrier)
                continue
            family_id, existing_row, _family = locations[spec.carrier]
            if spec.anchor_key is None:
                row_updates, comments = _catalog_self_row(spec, existing_row)
            else:
                gas = _gas_observation(spec.carrier, gases)
                anchor = by_key.get(spec.anchor_key)
                if anchor is None:
                    raise ValueError(f"{spec.carrier}: missing anchor {spec.anchor_key}")
                _new_id, generated_family, comments = _family_mapping(
                    spec, gas=gas, anchor=anchor
                )
                generated_row = generated_family["physical_properties"]["species"][
                    spec.carrier
                ]
                row_updates = {
                    "source_reactions": generated_row["source_reactions"],
                    "pressure_models": generated_row["pressure_models"],
                }
            catalog_working = _patch_existing_family(
                catalog_working,
                carrier=spec.carrier,
                family_id=family_id,
                row_updates=row_updates,
                comments=comments,
                spec=spec,
            )
            upgraded.append(spec.carrier)
            continue
        if spec.anchor_key is None:
            raise ValueError(f"{spec.carrier}: catalog_self_executable is absent from catalog")
        gas = _gas_observation(spec.carrier, gases)
        anchor = by_key.get(spec.anchor_key)
        if anchor is None:
            raise ValueError(f"{spec.carrier}: missing anchor {spec.anchor_key}")
        family_id, family, comments = _family_mapping(spec, gas=gas, anchor=anchor)
        rendered.append(_render_family(family_id, family, comments))
        generated.append(spec.carrier)

    catalog_after = catalog_working + "".join(rendered)
    compiled = compile_vapour_rail_catalog(yaml.safe_load(catalog_after))
    for carrier in generated + upgraded:
        species = compiled.species[carrier]
        # b-189-exempt: catalog composition tooling
        if species.evaluator is None:
            raise AssertionError(f"{carrier}: generated row did not compile an evaluator")
        if species.code_metadata.hot_train_applicability != "not_applicable":
            raise AssertionError(f"{carrier}: generated row became hot-train applicable")

    selected_keys = {(row.element, row.carrier, row.missing) for row in selected}
    gaps_before = GAPS_PATH.read_text(encoding="utf-8")
    gaps_after, removed = _remove_selected_gap_blocks(gaps_before, selected_keys)
    yaml.safe_load(gaps_after)
    if apply:
        CATALOG_PATH.write_text(catalog_after, encoding="utf-8")
        GAPS_PATH.write_text(gaps_after, encoding="utf-8")

    return {
        "mode": "apply" if apply else "preview",
        "pilot": pilot,
        "composable_pairs": len(selected),
        "unique_carriers": len(specs),
        "generated_unique": len(generated),
        "upgraded_unique": len(upgraded),
        "reused_unique": len(reused),
        "generated_carriers": generated,
        "upgraded_carriers": upgraded,
        "reused_carriers": reused,
        "pair_tiers": dict(sorted(Counter(row.tier for row in selected).items())),
        "unique_tiers": dict(sorted(per_tier_unique.items())),
        "needs_channel_refusals": len(refusals),
        "refusals": [dict(result.as_mapping()) for result in refusals] if pilot else [],
        "removed_gap_rows": removed,
        "preexisting_comments_preserved": _is_subsequence(
            [line for line in catalog_before.splitlines() if line.lstrip().startswith("#")],
            [line for line in catalog_after.splitlines() if line.lstrip().startswith("#")],
        ),
        "preexisting_yaml_anchors_preserved": re.findall(
            r"(?<!\w)(?:&|\*)[A-Za-z0-9_-]+", catalog_after
        )
        == re.findall(r"(?<!\w)(?:&|\*)[A-Za-z0-9_-]+", catalog_before),
    }


def _catalog_species_ids() -> set[str]:
    families = _load_yaml(CATALOG_PATH).get("families")
    if not isinstance(families, Mapping):
        raise TypeError("catalog families must be a mapping")
    out: set[str] = set()
    for family in families.values():
        if not isinstance(family, Mapping):
            continue
        physical = family.get("physical_properties")
        species = physical.get("species") if isinstance(physical, Mapping) else None
        if isinstance(species, Mapping):
            out.update(str(value) for value in species)
    return out


def _catalog_locations() -> Mapping[str, tuple[str, Mapping[str, Any], Mapping[str, Any]]]:
    families = _load_yaml(CATALOG_PATH).get("families")
    if not isinstance(families, Mapping):
        raise TypeError("catalog families must be a mapping")
    out: dict[str, tuple[str, Mapping[str, Any], Mapping[str, Any]]] = {}
    for family_id, family in families.items():
        if not isinstance(family, Mapping):
            continue
        physical = family.get("physical_properties")
        species = physical.get("species") if isinstance(physical, Mapping) else None
        if not isinstance(species, Mapping):
            continue
        for species_id, row in species.items():
            if isinstance(row, Mapping):
                out[str(species_id)] = (str(family_id), row, family)
    return out


def _summary(candidates: Iterable[Candidate]) -> dict[str, Any]:
    selected = tuple(candidates)
    by_key, gases = _observation_index()
    catalog_species = _catalog_species_ids()
    compiled_catalog = compile_vapour_rail_catalog(_load_yaml(CATALOG_PATH))
    locations = _catalog_locations()
    selected_ids = {row.carrier for row in selected}
    existing_evaluators = {
        # b-189-exempt: catalog composition tooling
        carrier: compiled_catalog.species[carrier].evaluator is not None
        for carrier in sorted(selected_ids & catalog_species)
    }
    missing_anchors = sorted(
        {
            row.anchor_key
            for row in selected
            if row.anchor_key is not None and row.anchor_key not in by_key
        }
    )
    missing_gases = sorted(
        {
            row.carrier
            for row in selected
            if row.carrier not in gases
            and row.carrier.removesuffix("_gas") not in gases
        }
    )
    ambiguous_gases = {
        carrier: [key for key, _ in rows]
        for carrier, rows in gases.items()
        if len(rows) > 1 and any(row.carrier == carrier for row in selected)
    }
    duplicates = {
        carrier: sorted(rows)
        for carrier, rows in _group_elements(selected).items()
        if len(rows) > 1
    }
    return {
        "count": len(selected),
        "unique_carriers": len({row.carrier for row in selected}),
        "already_cataloged_unique": sorted(
            {row.carrier for row in selected} & catalog_species
        ),
        "already_cataloged_evaluators": existing_evaluators,
        "catalog_self_sources": {
            carrier: {
                "row_keys": list(locations[carrier][1]),
                "pure_component_antoine": locations[carrier][1].get("pure_component_antoine"),
                "pressure_model": (locations[carrier][1].get("pressure_models") or [None])[0],
            }
            for carrier in sorted(existing_evaluators)
            if any(
                row.carrier == carrier and row.pathway == "catalog_self_executable"
                for row in selected
            )
            and not existing_evaluators[carrier]
        },
        "new_unique": len({row.carrier for row in selected} - catalog_species),
        "tiers": dict(sorted(Counter(row.tier for row in selected).items())),
        "pathways": dict(sorted(Counter(row.pathway for row in selected).items())),
        "catalog_self_carriers": sorted(
            {row.carrier for row in selected if row.pathway == "catalog_self_executable"}
        ),
        "elements": dict(sorted(Counter(row.element for row in selected).items())),
        "missing_anchors": missing_anchors,
        "missing_gases": missing_gases,
        "ambiguous_gases": ambiguous_gases,
        "duplicate_carriers": duplicates,
    }


def _group_elements(candidates: Iterable[Candidate]) -> Mapping[str, list[str]]:
    out: defaultdict[str, list[str]] = defaultdict(list)
    for row in candidates:
        out[row.carrier].append(row.element)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--pilot", metavar="ELEMENT")
    args = parser.parse_args()
    if args.audit and args.apply:
        parser.error("choose --audit or --apply")
    candidates = load_candidates()
    selected = (
        tuple(row for row in candidates if row.element == args.pilot)
        if args.pilot
        else candidates
    )
    if args.audit:
        print(yaml.safe_dump(_summary(selected), sort_keys=False).rstrip())
        return
    result = compose(pilot=args.pilot, apply=args.apply)
    print(yaml.safe_dump(dict(result), sort_keys=False, width=110).rstrip())


if __name__ == "__main__":
    main()
