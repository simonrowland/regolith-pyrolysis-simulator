"""U0 canonical vapour-rail species-manifest union.

Freezes:

  species-inventory.md
    UNION species-inventory-gas-closure.md (binding coverage delta)
    UNION data/refractory_vapor_species.yaml gas_species (22 IDs)

after collision-only ``_gas`` canonicalization and de-duplication.

Golden- and cache-neutral: this module produces a proposal fixture only. It does
not edit runtime YAML, does not touch authority, and does not feed flux.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from simulator.accounting.formulas import parse_formula
from simulator.state import OXIDE_SPECIES

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY_PATH = (
    REPO_ROOT
    / "docs-private"
    / "research"
    / "2026-07-30-rail-seams"
    / "species-inventory.md"
)
DEFAULT_GAS_CLOSURE_PATH = (
    REPO_ROOT
    / "docs-private"
    / "research"
    / "2026-07-30-rail-seams"
    / "species-inventory-gas-closure.md"
)
DEFAULT_REFRACTORY_PATH = REPO_ROOT / "data" / "refractory_vapor_species.yaml"
DEFAULT_FIXTURE_PATH = REPO_ROOT / "data" / "vapour_rail_u0_manifest.yaml"

_GAS_SUFFIX = "_gas"
_OXIDE_COLLIDING = frozenset(OXIDE_SPECIES)

# VapoRock advertised set: 28 bare + 14 collision-namespaced (OXIDE_SPECIES + _gas).
# Derived from simulator/melt_backend/vaporock.py::get_vapor_species — keep in lockstep.
_VAPOROCK_BARE: tuple[str, ...] = (
    "Na",
    "K",
    "Fe",
    "Mg",
    "Ca",
    "Si",
    "Al",
    "Ti",
    "Cr",
    "Mn",
    "SiO",
    "AlO",
    "TiO",
    "NaO",
    "KO",
    "CrO",
    "CrO2",
    "Al2O",
    "Ti2O3",
    "O2",
    "O",
    "Na2",
    "K2",
    "NaOH",
    "KOH",
    "Si2",
    "Mg2",
    "Ca2",
)
COLLISION_GAS_IDS: frozenset[str] = frozenset(
    f"{oxide}{_GAS_SUFFIX}" for oxide in OXIDE_SPECIES
)
VAPOROCK_42_IDS: frozenset[str] = frozenset(_VAPOROCK_BARE) | COLLISION_GAS_IDS

# Catalog formula-null aggregate/generic carriers (inventory contradictions §;
# excludes CrO2 which has an explicit atom map).
CARRIER_ONLY_IDS: frozenset[str] = frozenset(
    {
        "REE_oxides",
        "carbonate_salts",
        "NaCl_KCl_salts",
        "CO_CO2",
        "CH4_NH3_HCN",
        "NH3_HCN",
        "CO_CH4_propellant",
        "Fe_Ni_alloy",
        "metallic_FeNi",
        "generic_carbonaceous_hydrocarbon",
        "generic_carbonaceous_organic",
    }
)

# species-inventory.md DELTA (33 feedstock identifiers, no rail/tranche/VR source).
FEEDSTOCK_DELTA_IDS: frozenset[str] = frozenset(
    {
        "Ar",
        "C",
        "Co",
        "H",
        "He3",
        "He4",
        "Ne",
        "Ni",
        "P",
        "Th",
        "Zr",
        "ClO4",
        "FeS_troilite",
        "MgSO4",
        "REE_oxides",
        "SO3",
        "ZrO2",
        "carbonate_salts",
        "metallic_FeNi",
        "Fe_Ni_alloy",
        "sulfuric_acid_feedstock",
        "CH4_NH3_HCN",
        "CO_CH4_propellant",
        "CO_CO2",
        "CO_propellant",
        "NH3",
        "NH3_HCN",
        "carbonaceous_organic",
        "generic_carbonaceous_hydrocarbon",
        "generic_carbonaceous_organic",
        "hydrocarbons",
        "organics",
        "unreported_loi_residual",
    }
)

# data/refractory_vapor_species.yaml gas_species keys (raw; canonicalize on union).
REFRACTORY_GAS_IDS_RAW: frozenset[str] = frozenset(
    {
        "O",
        "O2",
        "Al",
        "Al2",
        "AlO",
        "AlO2",
        "Al2O",
        "Al2O2",
        "Ca",
        "Ca2",
        "CaO",
        "Mg",
        "Mg2",
        "MgO",
        "Si",
        "Si2",
        "Si3",
        "SiO",
        "SiO2",
        "Ti",
        "TiO",
        "TiO2",
    }
)

# Named association / polymer coverage target (DESIGN-REV5 §1.1).
ASSOCIATION_POLYMER_IDS: frozenset[str] = frozenset(
    {"Na2Cl2", "K2Cl2", "Al2", "Ca2", "Mg2", "Si2", "Si3"}
)

# Group A plant-relevant dominant gas forms (finite expansion of 11 ranked rows /
# 13 elements). Element bare rows already live in the inventory when present;
# these are the gas-closure gas IDs that must enter the union.
GROUP_A_GAS_IDS: frozenset[str] = frozenset(
    {
        # P
        "PO",
        "P4O6",
        "P4O7",
        "P4O8",
        "P4O9",
        "P4O10",
        # Li
        "Li2O",
        "LiO",
        "Li3O",
        "Li2O2",
        # Rb
        "Rb2O",
        "RbO",
        "Rb2",
        "Rb2O2",
        # Cs
        "Cs2O",
        "CsO",
        "Cs2O2",
        # Se allotrope ladder
        "Se2",
        "Se3",
        "Se4",
        "Se5",
        "Se6",
        "Se7",
        "Se8",
        # metalloids / V / alkaline earths / volatile REE metals as gas intent
        "Ga2O",
        "As4O6",
        "Sb4O6",
        "VO",
        "VO2",
        "SrO",
        "BaO",
    }
)
GROUP_A_ELEMENT_IDS: frozenset[str] = frozenset(
    {
        "P",
        "Li",
        "Rb",
        "Cs",
        "Se",
        "Ga",
        "As",
        "Sb",
        "V",
        "Sr",
        "Ba",
        "Eu",
        "Yb",
    }
)

# Group B 26 elements (regime-aware; millibar-negligible ≠ hard-vacuum zero).
GROUP_B_ELEMENT_IDS: frozenset[str] = frozenset(
    {
        "Sc",
        "Y",
        "La",
        "Ce",
        "Pr",
        "Nd",
        "Sm",
        "Gd",
        "Tb",
        "Dy",
        "Ho",
        "Er",
        "Tm",
        "Lu",
        "Zr",
        "Hf",
        "Nb",
        "Ta",
        "Th",
        "U",
        "Be",
        "Pt",
        "Rh",
        "Ir",
        "Ru",
        "Os",
    }
)
# Dominant oxide/metal gas families named for Group B in gas-closure §6.1 / tables.
# Be polymer ladder: gas-closure Be entry + LH87 §2.2.a — (BeO)_n for n=1..6 plus
# Be2O. IDs MUST be Be_n O_n (Be2O2…Be6O6), never bare BeOn (that freezes the
# monomeric hyperoxide {Be:1,O:n}, wrong stoichiometry for the polymer).
# HfO2 / RuO4 appear in sweep tables as co-dominant/secondary but are NOT in the
# §6.1 binding primary set (HfO; RuO3) — deferred SECONDARY, not U0 binding.
GROUP_B_GAS_IDS: frozenset[str] = frozenset(
    {
        "ScO",
        "YO",
        "LaO",
        "CeO",
        "CeO2",
        "PrO",
        "NdO",
        "SmO",
        "GdO",
        "TbO",
        "DyO",
        "HoO",
        "ErO",
        "TmO",
        "LuO",
        "ZrO",
        "HfO",
        "NbO",
        "NbO2",
        "TaO",
        "TaO2",
        "ThO",
        "ThO2",
        "UO",
        "UO2",
        "UO3",
        "BeO",  # (BeO)_1
        "Be2O2",  # (BeO)_2
        "Be3O3",  # (BeO)_3
        "Be4O4",  # (BeO)_4
        "Be5O5",  # (BeO)_5
        "Be6O6",  # (BeO)_6
        "Be2O",
        "PtO2",
        "RhO2",
        "IrO3",
        "RuO3",
        "OsO3",
        "OsO4",
    }
)

# Explicit UNKNOWN-NO-SOURCE refusals from gas-closure §6.1.
UNKNOWN_NO_SOURCE_IDS: frozenset[str] = frozenset({"PdO", "TiOH4"})

# Disposition letters allowed on every U0 row.
_VALID_DISPOSITIONS = frozenset({"V", "C", "R", "U"})

_SOURCE_INVENTORY = "inventory"
_SOURCE_GAS_CLOSURE = "gas_closure"
_SOURCE_REFRACTORY = "refractory_registry"

_MANIFEST_SCHEMA_VERSION = 1
_MANIFEST_KIND = "u0_vapour_rail_manifest"


def canonicalize_gas_id(species_id: str, *, treat_as_gas: bool = False) -> str:
    """Apply collision-only ``_gas`` namespacing.

    Bare condensed oxide carriers keep their bare ID. Gas forms of members of
    ``OXIDE_SPECIES`` become ``{oxide}_gas``. Already-suffixed IDs are validated
    and returned unchanged. Non-colliding bare gas IDs (Al, SiO, Al2O, O, …)
    stay bare.
    """

    raw = str(species_id).strip()
    if not raw:
        raise ValueError("species_id is required")
    if raw.endswith(_GAS_SUFFIX):
        bare = raw[: -len(_GAS_SUFFIX)]
        if bare not in _OXIDE_COLLIDING:
            raise ValueError(
                f"{raw!r} uses {_GAS_SUFFIX!r} but {bare!r} is not an "
                "OXIDE_SPECIES collision member"
            )
        return raw
    if treat_as_gas and raw in _OXIDE_COLLIDING:
        return f"{raw}{_GAS_SUFFIX}"
    return raw


def refractory_canonical_id(raw_id: str) -> str:
    """Canonicalize a refractory-registry gas ID (always a gas form)."""

    return canonicalize_gas_id(raw_id, treat_as_gas=True)


def _formula_for_id(species_id: str, explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    if species_id.endswith(_GAS_SUFFIX):
        return species_id[: -len(_GAS_SUFFIX)]
    # Unknown-no-source hydroxide uses a readable formula spelling.
    if species_id == "TiOH4":
        return "Ti(OH)4"
    if species_id in CARRIER_ONLY_IDS:
        return None
    # Group/aggregate feedstock carriers with no single formula.
    if species_id in {
        "carbonaceous_organic",
        "hydrocarbons",
        "organics",
        "unreported_loi_residual",
        "sulfuric_acid_feedstock",
        "FeS_troilite",
        "CO_propellant",
        "CH4_NH3_HCN",
        "CO_CH4_propellant",
        "CO_CO2",
        "NH3_HCN",
        "REE_oxides",
        "carbonate_salts",
        "metallic_FeNi",
        "Fe_Ni_alloy",
        "generic_carbonaceous_hydrocarbon",
        "generic_carbonaceous_organic",
    }:
        return None
    return species_id


def _atoms_for_formula(formula: str | None, species_id: str) -> dict[str, float] | None:
    if formula is None:
        return None
    try:
        parsed = parse_formula(formula, species=species_id)
    except Exception:
        # He3 / He4 isotopes and similar non-standard tokens.
        return None
    return {element: float(count) for element, count in sorted(parsed.elements.items())}


def _empty_sources() -> dict[str, bool]:
    return {
        _SOURCE_INVENTORY: False,
        _SOURCE_GAS_CLOSURE: False,
        _SOURCE_REFRACTORY: False,
    }


def _default_regime(
    *,
    group_a: bool = False,
    group_b: bool = False,
    unknown: bool = False,
) -> dict[str, Any]:
    if unknown:
        return {
            "millibar": {
                "applicable": False,
                "dominance": "unknown_no_source",
                "outcome": "refuse",
            },
            "hard_vacuum": {
                "applicable": False,
                "dominance": "unknown_no_source",
                "outcome": "refuse",
            },
        }
    if group_b:
        return {
            "millibar": {
                "applicable": True,
                "dominance": "negligible",
                "outcome": "retain_rump",
            },
            "hard_vacuum": {
                "applicable": True,
                "dominance": "regime_open",
                "outcome": "evolve_or_compute",
            },
        }
    if group_a:
        return {
            "millibar": {
                "applicable": True,
                "dominance": "plant_relevant",
                "outcome": "evolve",
            },
            "hard_vacuum": {
                "applicable": True,
                "dominance": "plant_relevant",
                "outcome": "evolve",
            },
        }
    return {
        "millibar": {
            "applicable": True,
            "dominance": "unspecified",
            "outcome": "as_disposition",
        },
        "hard_vacuum": {
            "applicable": True,
            "dominance": "unspecified",
            "outcome": "as_disposition",
        },
    }


def _new_row(
    species_id: str,
    *,
    disposition: str,
    feedstock_presence: bool,
    formula: str | None = None,
    sources: Mapping[str, bool] | None = None,
    regime: Mapping[str, Any] | None = None,
    notes: str | None = None,
    flags: Sequence[str] | None = None,
) -> dict[str, Any]:
    if disposition not in _VALID_DISPOSITIONS:
        raise ValueError(f"invalid disposition {disposition!r} for {species_id}")
    formula_text = _formula_for_id(species_id, formula)
    source_map = _empty_sources()
    if sources:
        for key, value in sources.items():
            if key not in source_map:
                raise ValueError(f"unknown source key {key!r}")
            source_map[key] = bool(value)
    row: dict[str, Any] = {
        "id": species_id,
        "formula": formula_text,
        "atoms": _atoms_for_formula(formula_text, species_id),
        "disposition": disposition,
        "validation_status": "pending_validation",
        "validation_anchor_refs": [],
        "feedstock_presence": bool(feedstock_presence),
        "sources": source_map,
        "regime": dict(regime) if regime is not None else _default_regime(),
        "flags": sorted(set(flags or ())),
    }
    if notes:
        row["notes"] = notes
    return row


def _merge_row(
    existing: MutableMapping[str, Any],
    incoming: Mapping[str, Any],
) -> dict[str, Any]:
    """De-duplicate on id; union source membership; prefer richer inventory fields."""

    if existing["id"] != incoming["id"]:
        raise ValueError("merge requires matching ids")
    had_inventory = bool(existing["sources"].get(_SOURCE_INVENTORY))
    incoming_inventory = bool(incoming["sources"].get(_SOURCE_INVENTORY))
    for key in (_SOURCE_INVENTORY, _SOURCE_GAS_CLOSURE, _SOURCE_REFRACTORY):
        existing["sources"][key] = bool(
            existing["sources"].get(key) or incoming["sources"].get(key)
        )
    # Prefer inventory disposition/formula when inventory is the newly added source.
    if incoming_inventory and not had_inventory:
        existing["disposition"] = incoming["disposition"]
        existing["feedstock_presence"] = incoming["feedstock_presence"]
        if incoming.get("formula") is not None:
            existing["formula"] = incoming["formula"]
            existing["atoms"] = incoming["atoms"]
    elif existing.get("formula") is None and incoming.get("formula") is not None:
        existing["formula"] = incoming["formula"]
        existing["atoms"] = incoming["atoms"]
    # Union flags.
    existing["flags"] = sorted(
        set(existing.get("flags") or ()) | set(incoming.get("flags") or ())
    )
    # Prefer more specific regime when merging group tags.
    if "group_b" in existing["flags"] or "group_b" in (incoming.get("flags") or ()):
        existing["regime"] = _default_regime(group_b=True)
    elif "group_a" in existing["flags"] or "group_a" in (incoming.get("flags") or ()):
        existing["regime"] = _default_regime(group_a=True)
    elif "unknown_no_source" in existing["flags"] or "unknown_no_source" in (
        incoming.get("flags") or ()
    ):
        existing["regime"] = _default_regime(unknown=True)
    if incoming.get("notes") and not existing.get("notes"):
        existing["notes"] = incoming["notes"]
    # feedstock_presence: true if either source asserts exact presence.
    existing["feedstock_presence"] = bool(
        existing["feedstock_presence"] or incoming["feedstock_presence"]
    )
    return dict(existing)


def _parse_inventory_species_cell(cell: str) -> tuple[str, bool]:
    """Return (raw_id, is_collision_managed_gas_row).

    Only the dedicated ``…_gas [collision-managed gas]`` rows are gas forms.
    Condensed carriers annotated ``[condensed-name collision; gas=X_gas]`` stay
    bare — the parenthetical names the *other* row, not this one.
    """

    text = cell.strip()
    is_collision_gas = "collision-managed gas" in text.lower()
    # Strip trailing bracket annotations: "Al2O3 [condensed-name ...]"
    bare = re.split(r"\s*\[", text, maxsplit=1)[0].strip()
    if bare.endswith(_GAS_SUFFIX):
        is_collision_gas = True
    return bare, is_collision_gas


def _parse_disposition(cell: str) -> str:
    letter = cell.strip().split(":", 1)[0].strip().upper()
    if letter not in _VALID_DISPOSITIONS:
        raise ValueError(f"unparseable disposition cell: {cell!r}")
    return letter


def _parse_feedstock_presence(cell: str) -> bool:
    """Exact feedstock_presence: true only when the inventory asserts 'exact'."""

    text = cell.strip().lower()
    if text.startswith("exact"):
        return True
    # "elements …" and empty/unparsed → not exact presence of this species/carrier.
    return False


def parse_inventory_rows(path: Path) -> list[dict[str, Any]]:
    """Parse the 242-row inventory markdown table into U0 row dicts."""

    text = path.read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    in_table = False
    for line in text.splitlines():
        if line.startswith("| Species"):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            if rows:
                break
            continue
        if line.startswith("|---"):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 10:
            continue
        raw_id, is_collision_gas = _parse_inventory_species_cell(cols[0])
        # Inventory already uses collision-managed gas IDs where needed.
        # Condensed bare oxides stay bare (carrier rows). Do not force _gas.
        species_id = canonicalize_gas_id(raw_id, treat_as_gas=False)
        if is_collision_gas and not species_id.endswith(_GAS_SUFFIX):
            # Defensive: a collision-managed gas row that somehow lacks the suffix.
            species_id = canonicalize_gas_id(species_id, treat_as_gas=True)
        disposition = _parse_disposition(cols[9])
        feedstock_presence = _parse_feedstock_presence(cols[2])
        flags: list[str] = []
        if species_id in VAPOROCK_42_IDS or (
            cols[5].lower().startswith("yes") and species_id in VAPOROCK_42_IDS
        ):
            flags.append("vaporock_42")
        if species_id in COLLISION_GAS_IDS:
            flags.append("collision_gas")
        if species_id in CARRIER_ONLY_IDS:
            flags.append("carrier_only")
        if species_id in FEEDSTOCK_DELTA_IDS:
            flags.append("feedstock_delta")
        if species_id == "O":
            flags.append("monatomic_oxygen")
        if species_id == "P2O5_gas":
            flags.append("not_a_reported_literature_molecule")
        if species_id == "NaF":
            flags.append("diagnostic_only")
            flags.append("tranche_2_do_not_promote")
        rows.append(
            _new_row(
                species_id,
                disposition=disposition,
                feedstock_presence=feedstock_presence,
                sources={_SOURCE_INVENTORY: True},
                flags=flags,
                notes=(
                    "diagnostic_only; tranche_2_do_not_promote"
                    if species_id == "NaF"
                    else (
                        "not_a_reported_literature_molecule"
                        if species_id == "P2O5_gas"
                        else None
                    )
                ),
            )
        )
    if len(rows) != 242:
        raise ValueError(
            f"inventory parse expected 242 rows, got {len(rows)} from {path}"
        )
    return rows


def _gas_closure_contribution_rows() -> list[dict[str, Any]]:
    """Finite enumerated expansion of the gas-closure binding coverage delta.

    Encodes DESIGN-REV5 §1.1 Group A / Group B / unknown / association targets
    as explicit canonical rows. This is the machine-readable form of the
    gas-closure addendum contribution to the U0 union (not a free-form scrape
    of prose, which would be non-deterministic).
    """

    rows: list[dict[str, Any]] = []

    for species_id in sorted(GROUP_A_GAS_IDS):
        rows.append(
            _new_row(
                canonicalize_gas_id(species_id, treat_as_gas=False),
                disposition="V",
                feedstock_presence=False,
                sources={_SOURCE_GAS_CLOSURE: True},
                regime=_default_regime(group_a=True),
                flags=["group_a", "gas_closure_delta"],
            )
        )
    for species_id in sorted(GROUP_A_ELEMENT_IDS):
        rows.append(
            _new_row(
                species_id,
                disposition="R",
                feedstock_presence=False,
                sources={_SOURCE_GAS_CLOSURE: True},
                regime=_default_regime(group_a=True),
                flags=["group_a", "group_a_element", "gas_closure_delta"],
            )
        )
    for species_id in sorted(GROUP_B_GAS_IDS):
        rows.append(
            _new_row(
                species_id,
                disposition="V",
                feedstock_presence=False,
                sources={_SOURCE_GAS_CLOSURE: True},
                regime=_default_regime(group_b=True),
                flags=["group_b", "gas_closure_delta"],
            )
        )
    for species_id in sorted(GROUP_B_ELEMENT_IDS):
        rows.append(
            _new_row(
                species_id,
                disposition="R",
                feedstock_presence=False,
                sources={_SOURCE_GAS_CLOSURE: True},
                regime=_default_regime(group_b=True),
                flags=["group_b", "group_b_element", "gas_closure_delta"],
            )
        )
    for species_id in sorted(UNKNOWN_NO_SOURCE_IDS):
        formula = "Ti(OH)4" if species_id == "TiOH4" else species_id
        rows.append(
            _new_row(
                species_id,
                disposition="U",
                feedstock_presence=False,
                formula=formula,
                sources={_SOURCE_GAS_CLOSURE: True},
                regime=_default_regime(unknown=True),
                flags=["unknown_no_source", "gas_closure_delta"],
                notes="UNKNOWN-NO-SOURCE refusal from gas-closure §6.1",
            )
        )
    for species_id in sorted(ASSOCIATION_POLYMER_IDS):
        rows.append(
            _new_row(
                species_id,
                disposition="V",
                feedstock_presence=False,
                sources={_SOURCE_GAS_CLOSURE: True},
                flags=["association_polymer", "gas_closure_delta"],
            )
        )
    # Monatomic oxygen is first-class (also in inventory + refractory); tag it.
    rows.append(
        _new_row(
            "O",
            disposition="V",
            feedstock_presence=False,
            sources={_SOURCE_GAS_CLOSURE: True},
            flags=["monatomic_oxygen", "gas_closure_delta", "rail_gap_dominant"],
        )
    )
    # Rail-gap-dominant principal species already in inventory/VR; re-assert
    # gas-closure membership so source flags record the addendum.
    for species_id in (
        "Al2O",
        "TiO",
        "TiO2_gas",
        "SiO2_gas",
        "FeO_gas",
        "Ni",
        "Co",
        "NaOH",
        "KOH",
        "CaO_gas",
        "MgO_gas",
        "Na2O_gas",
        "K2O_gas",
        "NaO",
        "KO",
        "CrO",
        "Cr2O3_gas",
        "MnO_gas",
    ):
        rows.append(
            _new_row(
                species_id,
                disposition="U",
                feedstock_presence=False,
                sources={_SOURCE_GAS_CLOSURE: True},
                flags=["rail_gap_dominant", "gas_closure_delta"],
            )
        )
    return rows


def parse_refractory_rows(path: Path) -> list[dict[str, Any]]:
    """Load the 22 refractory gas IDs and emit collision-canonicalized rows."""

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    gas_species = payload.get("gas_species")
    if not isinstance(gas_species, Mapping):
        raise ValueError(f"{path} missing gas_species mapping")
    raw_ids = sorted(gas_species.keys())
    if set(raw_ids) != set(REFRACTORY_GAS_IDS_RAW):
        missing = set(REFRACTORY_GAS_IDS_RAW) - set(raw_ids)
        extra = set(raw_ids) - set(REFRACTORY_GAS_IDS_RAW)
        raise ValueError(
            f"refractory gas_species set drift at {path}: "
            f"missing={sorted(missing)} extra={sorted(extra)}"
        )
    rows: list[dict[str, Any]] = []
    for raw_id in raw_ids:
        body = gas_species[raw_id] or {}
        formula = body.get("formula") if isinstance(body, Mapping) else None
        canon = refractory_canonical_id(raw_id)
        flags = ["refractory_registry"]
        if canon in COLLISION_GAS_IDS:
            flags.append("collision_gas")
        if canon == "O":
            flags.append("monatomic_oxygen")
        if canon in ASSOCIATION_POLYMER_IDS:
            flags.append("association_polymer")
        rows.append(
            _new_row(
                canon,
                disposition="V",
                feedstock_presence=False,
                formula=str(formula) if formula else None,
                sources={_SOURCE_REFRACTORY: True},
                flags=flags,
                notes=(
                    f"refractory raw_id={raw_id}"
                    if canon != raw_id
                    else "refractory_registry"
                ),
            )
        )
    if len(rows) != 22:
        raise ValueError(f"expected 22 refractory gas rows, got {len(rows)}")
    return rows


def _union_rows(row_groups: Iterable[Iterable[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for group in row_groups:
        for row in group:
            species_id = row["id"]
            if species_id in by_id:
                by_id[species_id] = _merge_row(by_id[species_id], row)
            else:
                by_id[species_id] = dict(row)
                by_id[species_id]["sources"] = dict(row["sources"])
                by_id[species_id]["flags"] = list(row.get("flags") or [])
                by_id[species_id]["regime"] = dict(row["regime"])
    # Re-stamp membership flags from frozen sets so merged rows stay exact.
    for species_id, row in by_id.items():
        flags = set(row.get("flags") or ())
        if species_id in VAPOROCK_42_IDS:
            flags.add("vaporock_42")
        if species_id in COLLISION_GAS_IDS:
            flags.add("collision_gas")
        if species_id in CARRIER_ONLY_IDS:
            flags.add("carrier_only")
        if species_id in FEEDSTOCK_DELTA_IDS:
            flags.add("feedstock_delta")
        if species_id in ASSOCIATION_POLYMER_IDS:
            flags.add("association_polymer")
        if species_id in GROUP_A_GAS_IDS or species_id in GROUP_A_ELEMENT_IDS:
            flags.add("group_a")
        if species_id in GROUP_B_GAS_IDS or species_id in GROUP_B_ELEMENT_IDS:
            flags.add("group_b")
        if species_id in UNKNOWN_NO_SOURCE_IDS:
            flags.add("unknown_no_source")
        if species_id == "O":
            flags.add("monatomic_oxygen")
        # DESIGN-REV5 §1.1 O6: live NaF is keep-with-flag diagnostic-only U,
        # not a promotable V channel, until tranche-2 evidence closes.
        if species_id == "NaF":
            row["disposition"] = "U"
            flags.update({"diagnostic_only", "tranche_2_do_not_promote"})
            row["notes"] = "diagnostic_only; tranche_2_do_not_promote"
        if species_id == "P2O5_gas":
            flags.add("not_a_reported_literature_molecule")
            row["notes"] = "not_a_reported_literature_molecule"
        row["flags"] = sorted(flags)
    return [by_id[key] for key in sorted(by_id)]


def input_id_sets(
    *,
    inventory_path: Path | None = None,
    refractory_path: Path | None = None,
) -> dict[str, frozenset[str]]:
    """Canonical ID sets for each U0 input (for exact set-equality tests)."""

    inventory_path = inventory_path or DEFAULT_INVENTORY_PATH
    refractory_path = refractory_path or DEFAULT_REFRACTORY_PATH
    inv = frozenset(row["id"] for row in parse_inventory_rows(inventory_path))
    gc = frozenset(row["id"] for row in _gas_closure_contribution_rows())
    ref = frozenset(row["id"] for row in parse_refractory_rows(refractory_path))
    return {
        "inventory": inv,
        "gas_closure": gc,
        "refractory_registry": ref,
        "union": inv | gc | ref,
    }


def build_u0_manifest(
    *,
    inventory_path: Path | None = None,
    gas_closure_path: Path | None = None,
    refractory_path: Path | None = None,
) -> dict[str, Any]:
    """Build the de-duplicated U0 manifest document.

    ``gas_closure_path`` is accepted for provenance/documentation; the binding
    coverage delta is the finite expansion in ``_gas_closure_contribution_rows``
    (DESIGN-REV5 §1.1). When the path exists it is recorded in provenance.
    """

    inventory_path = inventory_path or DEFAULT_INVENTORY_PATH
    gas_closure_path = gas_closure_path or DEFAULT_GAS_CLOSURE_PATH
    refractory_path = refractory_path or DEFAULT_REFRACTORY_PATH

    if not inventory_path.is_file():
        raise FileNotFoundError(
            f"inventory input missing: {inventory_path} "
            "(generator requires docs-private sources; use the checked-in fixture at CI)"
        )
    if not refractory_path.is_file():
        raise FileNotFoundError(f"refractory registry missing: {refractory_path}")

    inventory_rows = parse_inventory_rows(inventory_path)
    gas_closure_rows = _gas_closure_contribution_rows()
    refractory_rows = parse_refractory_rows(refractory_path)
    species = _union_rows((inventory_rows, gas_closure_rows, refractory_rows))

    id_sets = {
        "inventory": frozenset(r["id"] for r in inventory_rows),
        "gas_closure": frozenset(r["id"] for r in gas_closure_rows),
        "refractory_registry": frozenset(r["id"] for r in refractory_rows),
    }
    union_ids = id_sets["inventory"] | id_sets["gas_closure"] | id_sets["refractory_registry"]
    manifest_ids = frozenset(r["id"] for r in species)
    if manifest_ids != union_ids:
        raise AssertionError(
            "manifest IDs drifted from input union: "
            f"only_in_manifest={sorted(manifest_ids - union_ids)} "
            f"only_in_union={sorted(union_ids - manifest_ids)}"
        )

    # Computed count — never hand-added.
    row_count = len(species)

    return {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "kind": _MANIFEST_KIND,
        "description": (
            "U0 frozen canonical vapour-rail species manifest: de-duplicated "
            "union of species-inventory.md, species-inventory-gas-closure.md "
            "(binding coverage delta), and refractory_vapor_species.yaml gas IDs "
            "after collision-only _gas canonicalization."
        ),
        "validation_status_default": "pending_validation",
        "row_count": row_count,
        "provenance": {
            "inventory": str(inventory_path.relative_to(REPO_ROOT))
            if inventory_path.is_relative_to(REPO_ROOT)
            else str(inventory_path),
            "gas_closure": str(gas_closure_path.relative_to(REPO_ROOT))
            if gas_closure_path.is_file() and gas_closure_path.is_relative_to(REPO_ROOT)
            else str(gas_closure_path),
            "gas_closure_path_present": gas_closure_path.is_file(),
            "refractory_registry": str(refractory_path.relative_to(REPO_ROOT))
            if refractory_path.is_relative_to(REPO_ROOT)
            else str(refractory_path),
            "input_counts": {
                "inventory": len(id_sets["inventory"]),
                "gas_closure": len(id_sets["gas_closure"]),
                "refractory_registry": len(id_sets["refractory_registry"]),
                "union_before_dedup_sum": (
                    len(id_sets["inventory"])
                    + len(id_sets["gas_closure"])
                    + len(id_sets["refractory_registry"])
                ),
            },
        },
        "membership_sets": {
            "vaporock_42": sorted(VAPOROCK_42_IDS),
            "collision_gas": sorted(COLLISION_GAS_IDS),
            "carrier_only": sorted(CARRIER_ONLY_IDS),
            "feedstock_delta": sorted(FEEDSTOCK_DELTA_IDS),
            "refractory_raw": sorted(REFRACTORY_GAS_IDS_RAW),
            "refractory_canonical": sorted(
                refractory_canonical_id(raw) for raw in REFRACTORY_GAS_IDS_RAW
            ),
            "group_a_gas": sorted(GROUP_A_GAS_IDS),
            "group_a_elements": sorted(GROUP_A_ELEMENT_IDS),
            "group_b_gas": sorted(GROUP_B_GAS_IDS),
            "group_b_elements": sorted(GROUP_B_ELEMENT_IDS),
            "association_polymer": sorted(ASSOCIATION_POLYMER_IDS),
            "unknown_no_source": sorted(UNKNOWN_NO_SOURCE_IDS),
            "monatomic_oxygen": ["O"],
        },
        "species": species,
    }


# Process-wide memo: path + (mtime_ns, size) → parsed mapping.
# Invalidates when the fixture file changes on disk. Callers must not mutate
# the returned mapping (a shallow copy of the top-level dict is returned so
# top-level rebinding is safe; nested rows are shared read-only).
_U0_MANIFEST_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}


def clear_u0_manifest_cache() -> None:
    """Drop the process-wide U0 manifest memo (tests / hot-reload)."""

    _U0_MANIFEST_CACHE.clear()


def load_u0_manifest(path: Path | None = None) -> dict[str, Any]:
    """Load a checked-in or generated U0 manifest fixture.

    Cached by resolved path + ``(st_mtime_ns, st_size)`` so repeated
    ``compile_vapour_rail_catalog`` calls with default U0 rule emission do
    not re-parse the YAML on every compile (~150 ms → ~1 ms after warm).
    """

    path = Path(path or DEFAULT_FIXTURE_PATH)
    key = str(path.resolve())
    stat = path.stat()
    fingerprint = (stat.st_mtime_ns, stat.st_size)
    cached = _U0_MANIFEST_CACHE.get(key)
    if cached is not None and cached[0] == fingerprint[0] and cached[1] == fingerprint[1]:
        # Shallow top-level copy: callers may rebind keys without poisoning
        # the cache; nested species rows stay shared (treat as immutable).
        return dict(cached[2])

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"U0 manifest at {path} is not a mapping")
    stored = dict(payload)
    _U0_MANIFEST_CACHE[key] = (fingerprint[0], fingerprint[1], stored)
    return dict(stored)


def write_u0_manifest(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            dict(document),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=100,
        ),
        encoding="utf-8",
    )


def validate_manifest_document(document: Mapping[str, Any]) -> list[str]:
    """Return a list of validation errors (empty means OK)."""

    errors: list[str] = []
    if document.get("kind") != _MANIFEST_KIND:
        errors.append(f"kind must be {_MANIFEST_KIND!r}")
    species = document.get("species")
    if not isinstance(species, list) or not species:
        errors.append("species must be a non-empty list")
        return errors
    ids = [row.get("id") for row in species]
    if len(ids) != len(set(ids)):
        seen: set[str] = set()
        dups = []
        for item in ids:
            if item in seen:
                dups.append(item)
            seen.add(str(item))
        errors.append(f"duplicate species ids: {dups}")
    if document.get("row_count") != len(species):
        errors.append(
            f"row_count {document.get('row_count')!r} != len(species) {len(species)}"
        )
    required = {
        "id",
        "formula",
        "atoms",
        "disposition",
        "validation_status",
        "feedstock_presence",
        "sources",
        "regime",
    }
    for row in species:
        if not isinstance(row, Mapping):
            errors.append(f"non-mapping species row: {row!r}")
            continue
        missing = required - set(row)
        if missing:
            errors.append(f"{row.get('id')}: missing fields {sorted(missing)}")
            continue
        if row["disposition"] not in _VALID_DISPOSITIONS:
            errors.append(f"{row['id']}: bad disposition {row['disposition']!r}")
        if row["validation_status"] != "pending_validation":
            errors.append(
                f"{row['id']}: validation_status must default to pending_validation"
            )
        if not isinstance(row["feedstock_presence"], bool):
            errors.append(f"{row['id']}: feedstock_presence must be bool")
        sources = row["sources"]
        for key in (_SOURCE_INVENTORY, _SOURCE_GAS_CLOSURE, _SOURCE_REFRACTORY):
            if key not in sources:
                errors.append(f"{row['id']}: sources missing {key}")
            elif not isinstance(sources[key], bool):
                errors.append(f"{row['id']}: sources.{key} must be bool")
        if not any(sources.get(k) for k in (
            _SOURCE_INVENTORY,
            _SOURCE_GAS_CLOSURE,
            _SOURCE_REFRACTORY,
        )):
            errors.append(f"{row['id']}: no source membership")
        regime = row["regime"]
        for band in ("millibar", "hard_vacuum"):
            if band not in regime:
                errors.append(f"{row['id']}: regime missing {band}")
            elif "applicable" not in regime[band]:
                errors.append(f"{row['id']}: regime.{band} missing applicable")
        if row["atoms"] is not None and not isinstance(row["atoms"], Mapping):
            errors.append(f"{row['id']}: atoms must be mapping or null")
    return errors
