"""U0 vapour-rail canonical manifest — freeze + membership gate.

CI-safe: the checked-in fixture is self-contained and does not require
docs-private. When docs-private inventory + refractory registry are present,
an optional regeneration path asserts exact set equality against the three
inputs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.state import OXIDE_SPECIES
from simulator.vapour_rail.u0_manifest import (
    ASSOCIATION_POLYMER_IDS,
    CARRIER_ONLY_IDS,
    COLLISION_GAS_IDS,
    DEFAULT_FIXTURE_PATH,
    DEFAULT_INVENTORY_PATH,
    DEFAULT_REFRACTORY_PATH,
    FEEDSTOCK_DELTA_IDS,
    GROUP_A_ELEMENT_IDS,
    GROUP_A_GAS_IDS,
    GROUP_B_ELEMENT_IDS,
    GROUP_B_GAS_IDS,
    REFRACTORY_GAS_IDS_RAW,
    UNKNOWN_NO_SOURCE_IDS,
    VAPOROCK_42_IDS,
    build_u0_manifest,
    canonicalize_gas_id,
    load_u0_manifest,
    refractory_canonical_id,
    validate_manifest_document,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = DEFAULT_FIXTURE_PATH

# Pinned U0 union size after inventory ∪ gas-closure ∪ refractory de-dup.
# Updated only with a reviewed regeneration (not a free-hand count).
_PINNED_U0_ROW_COUNT = 320

# Gas-closure contribution IDs, frozen as an explicit literal derived once from
# docs-private/research/2026-07-30-rail-seams/species-inventory-gas-closure.md
# §6.1/§6.2 + DESIGN-REV5 §1.1 (Group-A gases/elements, Group-B gases/elements
# incl. Be polymer ladder BeO+(BeO)_2..6+Be2O as Be2O2..Be6O6, UNKNOWN-NO-SOURCE
# PdO/TiOH4, association polymers, monatomic O, rail-gap re-assertions).
# NOT imported from the generator constants — a coded-expansion drift fails here.
EXPECTED_GAS_CLOSURE_IDS: frozenset[str] = frozenset(
    {
        # Group-A gases
        "PO",
        "P4O6",
        "P4O7",
        "P4O8",
        "P4O9",
        "P4O10",
        "Li2O",
        "LiO",
        "Li3O",
        "Li2O2",
        "Rb2O",
        "RbO",
        "Rb2",
        "Rb2O2",
        "Cs2O",
        "CsO",
        "Cs2O2",
        "Se2",
        "Se3",
        "Se4",
        "Se5",
        "Se6",
        "Se7",
        "Se8",
        "Ga2O",
        "As4O6",
        "Sb4O6",
        "VO",
        "VO2",
        "SrO",
        "BaO",
        # Group-A elements
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
        # Group-B gases
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
        "BeO",
        "Be2O2",
        "Be3O3",
        "Be4O4",
        "Be5O5",
        "Be6O6",
        "Be2O",
        "PtO2",
        "RhO2",
        "IrO3",
        "RuO3",
        "OsO3",
        "OsO4",
        # Group-B elements
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
        # UNKNOWN-NO-SOURCE
        "PdO",
        "TiOH4",
        # Association / polymer target
        "Na2Cl2",
        "K2Cl2",
        "Al2",
        "Ca2",
        "Mg2",
        "Si2",
        "Si3",
        # Monatomic O + rail-gap re-assertions
        "O",
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
    }
)

# Feedstock DELTA 33 — literal pin from species-inventory.md §DELTA / DESIGN-REV5
# (independent of FEEDSTOCK_DELTA_IDS used at generation time).
EXPECTED_FEEDSTOCK_DELTA_IDS: frozenset[str] = frozenset(
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


def _ids(document: dict) -> set[str]:
    return {row["id"] for row in document["species"]}


@pytest.fixture(scope="module")
def manifest() -> dict:
    assert FIXTURE.is_file(), f"missing checked-in U0 fixture: {FIXTURE}"
    return load_u0_manifest(FIXTURE)


def test_fixture_validates_and_row_count_is_computed(manifest: dict) -> None:
    errors = validate_manifest_document(manifest)
    assert errors == [], errors
    # Final count is computed from the species list, never hand-added separately.
    assert manifest["row_count"] == len(manifest["species"])
    assert manifest["row_count"] == _PINNED_U0_ROW_COUNT


def test_every_row_appears_once(manifest: dict) -> None:
    ids = [row["id"] for row in manifest["species"]]
    assert len(ids) == len(set(ids))


def test_row_schema_fields(manifest: dict) -> None:
    for row in manifest["species"]:
        assert row["disposition"] in {"V", "C", "R", "U"}
        assert row["validation_status"] == "pending_validation"
        assert isinstance(row["feedstock_presence"], bool)
        assert set(row["sources"]) >= {
            "inventory",
            "gas_closure",
            "refractory_registry",
        }
        assert any(row["sources"].values())
        assert "millibar" in row["regime"]
        assert "hard_vacuum" in row["regime"]
        assert "applicable" in row["regime"]["millibar"]
        assert "applicable" in row["regime"]["hard_vacuum"]
        # formula/atom map: formula may be null for carriers; atoms null with it.
        if row["formula"] is not None:
            assert isinstance(row["formula"], str) and row["formula"]
        if row["atoms"] is not None:
            assert isinstance(row["atoms"], dict)
            assert all(isinstance(v, (int, float)) for v in row["atoms"].values())


def test_collision_only_gas_canonicalization() -> None:
    for oxide in OXIDE_SPECIES:
        assert canonicalize_gas_id(oxide, treat_as_gas=True) == f"{oxide}_gas"
        assert canonicalize_gas_id(f"{oxide}_gas") == f"{oxide}_gas"
        # Bare condensed carrier spelling stays bare when not treated as gas.
        assert canonicalize_gas_id(oxide, treat_as_gas=False) == oxide
    # Non-colliding bare gases stay bare.
    for bare in ("Al", "SiO", "Al2O", "O", "Na", "TiO"):
        assert canonicalize_gas_id(bare, treat_as_gas=True) == bare
    with pytest.raises(ValueError):
        canonicalize_gas_id("SiO_gas")  # SiO is not an OXIDE_SPECIES collision


def test_membership_vaporock_42(manifest: dict) -> None:
    ids = _ids(manifest)
    assert len(VAPOROCK_42_IDS) == 42
    missing = sorted(VAPOROCK_42_IDS - ids)
    assert missing == [], f"VapoRock-42 missing from manifest: {missing}"


def test_vaporock_42_lockstep_with_adapter() -> None:
    """U0 VapoRock-42 must match vaporock.py get_vapor_species bare+collision set."""

    from simulator.melt_backend.vaporock import VapoRockBackend

    adapter_ids = set(VapoRockBackend().get_vapor_species())
    assert adapter_ids == VAPOROCK_42_IDS


def test_membership_collision_14(manifest: dict) -> None:
    ids = _ids(manifest)
    assert len(COLLISION_GAS_IDS) == 14
    assert len(COLLISION_GAS_IDS) == len(OXIDE_SPECIES)
    missing = sorted(COLLISION_GAS_IDS - ids)
    assert missing == [], f"collision _gas rows missing: {missing}"
    for species_id in COLLISION_GAS_IDS:
        assert species_id.endswith("_gas")


def test_membership_carrier_only_11(manifest: dict) -> None:
    ids = _ids(manifest)
    assert len(CARRIER_ONLY_IDS) == 11
    missing = sorted(CARRIER_ONLY_IDS - ids)
    assert missing == [], f"carrier-only rows missing: {missing}"


def test_membership_feedstock_delta_33(manifest: dict) -> None:
    ids = _ids(manifest)
    # Literal pin (from inventory §DELTA) must match generator constant + fixture.
    assert FEEDSTOCK_DELTA_IDS == EXPECTED_FEEDSTOCK_DELTA_IDS
    assert len(EXPECTED_FEEDSTOCK_DELTA_IDS) == 33
    missing = sorted(EXPECTED_FEEDSTOCK_DELTA_IDS - ids)
    assert missing == [], f"feedstock DELTA rows missing: {missing}"


def test_membership_refractory_22(manifest: dict) -> None:
    ids = _ids(manifest)
    assert len(REFRACTORY_GAS_IDS_RAW) == 22
    for raw in REFRACTORY_GAS_IDS_RAW:
        canon = refractory_canonical_id(raw)
        assert canon in ids, f"refractory {raw!r} -> {canon!r} absent"
    # Collision members of the refractory set must land as *_gas.
    for oxide in ("SiO2", "TiO2", "CaO", "MgO"):
        assert oxide in REFRACTORY_GAS_IDS_RAW
        assert refractory_canonical_id(oxide) == f"{oxide}_gas"
        assert f"{oxide}_gas" in ids


def test_membership_monatomic_oxygen(manifest: dict) -> None:
    ids = _ids(manifest)
    assert "O" in ids
    row = next(r for r in manifest["species"] if r["id"] == "O")
    assert row["formula"] == "O"
    assert row["atoms"] == {"O": 1.0} or row["atoms"] == {"O": 1}
    assert "monatomic_oxygen" in (row.get("flags") or [])
    assert row["sources"].get("gas_closure") is True
    assert row["sources"].get("refractory_registry") is True


def test_membership_group_a(manifest: dict) -> None:
    ids = _ids(manifest)
    missing_gas = sorted(GROUP_A_GAS_IDS - ids)
    missing_el = sorted(GROUP_A_ELEMENT_IDS - ids)
    assert missing_gas == [], f"Group-A gas rows missing: {missing_gas}"
    assert missing_el == [], f"Group-A element rows missing: {missing_el}"
    # Spot-check plant-relevant carriers called out in DESIGN-REV5.
    for species_id in ("PO", "P4O10", "Li2O", "Cs2O", "Se6", "Ga2O", "As4O6", "VO"):
        assert species_id in ids


def test_membership_group_b(manifest: dict) -> None:
    ids = _ids(manifest)
    missing_el = sorted(GROUP_B_ELEMENT_IDS - ids)
    missing_gas = sorted(GROUP_B_GAS_IDS - ids)
    assert len(GROUP_B_ELEMENT_IDS) == 26
    assert missing_el == [], f"Group-B elements missing: {missing_el}"
    assert missing_gas == [], f"Group-B gas rows missing: {missing_gas}"
    # Regime split must be recorded (millibar ≠ hard-vacuum).
    sample = next(r for r in manifest["species"] if r["id"] == "LaO")
    assert sample["regime"]["millibar"]["dominance"] == "negligible"
    assert sample["regime"]["hard_vacuum"]["applicable"] is True
    assert sample["regime"]["hard_vacuum"]["dominance"] != sample["regime"]["millibar"][
        "dominance"
    ]
    # Be polymer ladder (BeO)_n: atoms {Be:n, O:n}; wrong bare BeOn IDs must not freeze.
    for n, species_id in (
        (1, "BeO"),
        (2, "Be2O2"),
        (3, "Be3O3"),
        (4, "Be4O4"),
        (5, "Be5O5"),
        (6, "Be6O6"),
    ):
        row = next(r for r in manifest["species"] if r["id"] == species_id)
        assert row["formula"] == species_id
        atoms = {k: int(v) for k, v in row["atoms"].items()}
        assert atoms == {"Be": n, "O": n}, f"{species_id}: {atoms}"
    # Mis-stoichiometry hyperoxide spellings must not be present as polymer IDs.
    for bad in ("BeO2", "BeO3", "BeO4", "BeO5", "BeO6"):
        assert bad not in ids


def test_membership_association_polymer(manifest: dict) -> None:
    ids = _ids(manifest)
    missing = sorted(ASSOCIATION_POLYMER_IDS - ids)
    assert missing == [], f"association/polymer rows missing: {missing}"
    for species_id in ("Na2Cl2", "K2Cl2", "Al2", "Si3"):
        assert species_id in ids


def test_membership_unknown_no_source(manifest: dict) -> None:
    ids = _ids(manifest)
    assert UNKNOWN_NO_SOURCE_IDS <= ids
    for species_id in UNKNOWN_NO_SOURCE_IDS:
        row = next(r for r in manifest["species"] if r["id"] == species_id)
        assert row["disposition"] == "U"
        assert row["regime"]["millibar"]["outcome"] == "refuse"


def test_p2o5_gas_flagged_unphysical(manifest: dict) -> None:
    row = next(r for r in manifest["species"] if r["id"] == "P2O5_gas")
    assert "not_a_reported_literature_molecule" in (row.get("flags") or []) or (
        row.get("notes") == "not_a_reported_literature_molecule"
    )


def test_naf_kept_with_diagnostic_flag(manifest: dict) -> None:
    row = next(r for r in manifest["species"] if r["id"] == "NaF")
    assert row["disposition"] == "U"
    flags = set(row.get("flags") or [])
    assert "diagnostic_only" in flags or "tranche_2_do_not_promote" in flags


def test_embedded_membership_sets_match_constants(manifest: dict) -> None:
    """Fixture carries membership sets so CI need not re-derive from docs-private."""

    embedded = manifest["membership_sets"]
    assert set(embedded["vaporock_42"]) == VAPOROCK_42_IDS
    assert set(embedded["collision_gas"]) == COLLISION_GAS_IDS
    assert set(embedded["carrier_only"]) == CARRIER_ONLY_IDS
    assert set(embedded["feedstock_delta"]) == FEEDSTOCK_DELTA_IDS
    assert set(embedded["group_a_gas"]) == GROUP_A_GAS_IDS
    assert set(embedded["group_b_elements"]) == GROUP_B_ELEMENT_IDS
    assert set(embedded["association_polymer"]) == ASSOCIATION_POLYMER_IDS
    assert set(embedded["refractory_raw"]) == REFRACTORY_GAS_IDS_RAW


@pytest.mark.skipif(
    not DEFAULT_INVENTORY_PATH.is_file() or not DEFAULT_REFRACTORY_PATH.is_file(),
    reason="docs-private inventory / refractory sources unavailable (CI fixture-only mode)",
)
def test_regenerated_union_matches_fixture_and_inputs(manifest: dict) -> None:
    """When sources exist: exact set equality against all three inputs + fixture pin."""

    built = build_u0_manifest()
    errors = validate_manifest_document(built)
    assert errors == [], errors

    built_ids = _ids(built)
    fixture_ids = _ids(manifest)
    assert built_ids == fixture_ids
    assert built["row_count"] == manifest["row_count"] == _PINNED_U0_ROW_COUNT
    # Full-document pin (IDs + content), not count-only.
    assert built["species"] == manifest["species"]

    # Exact set equality: every input ID is in the union; union has no extras.
    from simulator.vapour_rail.u0_manifest import input_id_sets

    sets = input_id_sets()
    assert built_ids == sets["union"]
    assert sets["inventory"] <= built_ids
    assert sets["refractory_registry"] <= built_ids
    # Gas-closure leg: assert coded expansion against the frozen literal (not
    # self-parity against the same constants that built the contribution).
    assert sets["gas_closure"] == EXPECTED_GAS_CLOSURE_IDS
    assert EXPECTED_GAS_CLOSURE_IDS <= built_ids
    # Inventory contribution is still 242 distinct IDs.
    assert len(sets["inventory"]) == 242
    # Refractory contribution is 22 canonical IDs (after collision fold).
    assert len(sets["refractory_registry"]) == 22


def test_fixture_is_self_contained_yaml(manifest: dict) -> None:
    """Checked-in fixture must load without any docs-private path."""

    raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    assert raw["row_count"] == len(raw["species"]) == _PINNED_U0_ROW_COUNT
    # No required external path in the species body.
    for row in raw["species"]:
        assert "id" in row
        assert "sources" in row
