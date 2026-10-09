"""t-1139 Build A step-1 pins: live vapour-rail outputs, dormant rows, stoich.

Tests-only. Pins are taken from the compiled catalog (legacy_view and
evaluate()), not from YAML, so later retirement of hand-written
stoich_oxide_per_vapor rows cannot silently rewrite the expected values.
"""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

import pytest

from simulator.accounting.formulas import parse_formula
from simulator.config import load_config_bundle
from simulator.vapour_rail.catalog import (
    compile_vapour_rail_catalog,
    vapor_pressure_legacy_view,
)
from simulator.vapour_rail.channel_generator import generate_first_batch
from simulator.yaml_cache import load_cached_safe_yaml

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "vapor_pressures.yaml"

PIN_TEMPERATURES_K = (1400.0, 1600.0, 1800.0)
PIN_PO2_BAR = 1.0e-8

# Low-Ti runner majors plus the existing first-batch dormant families.
# Hex strings are IEEE-754 float.hex() of evaluate(...).pressure_pa at
# T = 1400/1600/1800 K, pO2 = 1e-8 bar, source_activity = 1 when required.
PRESSURE_PINS: dict[str, tuple[str, str, str]] = {
    "Na": (
        "0x1.a02eefde7a0e1p+11",
        "0x1.2f0ae67486c5ap+16",
        "0x1.a55423594981cp+19",
    ),
    "K": (
        "0x1.0cb58219a726ep+16",
        "0x1.7ebc46c1a3bc3p+19",
        "0x1.3664efd8ac827p+22",
    ),
    "Fe": (
        "0x1.fb7046cbed2d8p-2",
        "0x1.6264292906e07p+1",
        "0x1.83248c0b7c5abp+3",
    ),
    "SiO": (
        "0x1.513d33b31126ap-25",
        "0x1.756a30af9df1dp-13",
        "0x1.e3195a0788ef0p-4",
    ),
    "Mg": (
        "0x1.ee2a82c427f79p-26",
        "0x1.37987e0195cc2p-14",
        "0x1.124233936d0f7p-5",
    ),
    "Ca": (
        "0x1.ce495a6a0e436p-35",
        "0x1.0204b4f6a6075p-22",
        "0x1.621e336e97c5fp-13",
    ),
    "Al": (
        "0x1.e5f2abc19a6d0p-57",
        "0x1.1a3ea795dbcc8p-39",
        "0x1.5700b4baec2d9p-26",
    ),
    "Mn": (
        "0x1.e7c9e9a06f435p-20",
        "0x1.dd9f7727d1988p-10",
        "0x1.723cacdad8549p-2",
    ),
    "Cr": (
        "0x1.bf7acb7113dd3p-35",
        "0x1.a487cc3f0de7dp-20",
        "0x1.12780216b65dbp-8",
    ),
    "TiO": (
        "0x1.6377cc3185127p-45",
        "0x1.cda2a2fbad8cap-31",
        "0x1.c5ce4589dd840p-20",
    ),
    "Si": (
        "0x1.6837ee94f5368p-69",
        "0x1.2746ffe659841p-48",
        "0x1.2fcc5729c1ddbp-32",
    ),
    "Ga": (
        "0x1.0c08e4f20ee31p-15",
        "0x1.ac8563f4bf219p-4",
        "0x1.b74e9469182f5p+5",
    ),
    "In": (
        "0x1.3b964f70e8f93p-2",
        "0x1.19afbe4688678p+8",
        "0x1.af8bd0ae7d362p+15",
    ),
    "GeO": (
        "0x1.15ad8f8c5f563p+10",
        "0x1.778c7b191922ep+17",
        "0x1.3707e44aa6394p+23",
    ),
    "SnO": (
        "0x1.c1c7a638a469ep+6",
        "0x1.a029289d82405p+10",
        "0x1.956e5f60cccc1p+13",
    ),
    "PbO": (
        "0x1.bfd2b86bbc76dp+7",
        "0x1.698dc1ca6c5a1p+11",
        "0x1.3bcb1d0702c42p+14",
    ),
    "Pb": (
        "0x1.4fb4040ac1e5bp+12",
        "0x1.0802ba37b9a95p+18",
        "0x1.4db0667914242p+22",
    ),
    "Cu": (
        "0x1.ef3dd942cd1fap-1",
        "0x1.bfee7c1436ed4p+5",
        "0x1.3e10b4343613dp+10",
    ),
    "Li": (
        "0x1.12a1931c41911p-4",
        "0x1.75d34864ff21dp+2",
        "0x1.6c8ce65de9558p+7",
    ),
    "Ge": (
        "0x1.ab604ca40b3dfp-18",
        "0x1.867ebe851b2fbp-4",
        "0x1.46d169944326cp+7",
    ),
    "Sn": (
        "0x1.30801258c051ap-7",
        "0x1.966e49eff91cfp+1",
        "0x1.21ef1e6044233p+8",
    ),
    "GaO": (
        "0x1.60c7b78a96944p-21",
        "0x1.1fc5b8c26a1e7p-11",
        "0x1.9a81ae4ef8a83p-4",
    ),
    "Ga2O": (
        "0x1.eaca9882289b7p-10",
        "0x1.198087fe2ba7bp+4",
        "0x1.3ad8efb3e2e1ep+14",
    ),
    "InO": (
        "0x1.41946e1d2b5d3p-10",
        "0x1.9c97bc758ffe6p-2",
        "0x1.1b981b73c43aap+5",
    ),
    "In2O": (
        "0x1.08b02ad9f6fdbp+4",
        "0x1.70777ec53c8cep+15",
        "0x1.4664e30ce283ap+24",
    ),
    "CuO": (
        "0x1.887f1627bb2bcp-16",
        "0x1.f97c9047f1e76p-11",
        "0x1.13161b44b2c77p-6",
    ),
    "Cu2": (
        "0x1.62cbedea90fdep-10",
        "0x1.2019d5665cddbp-1",
        "0x1.c804f7c81aeacp+5",
    ),
    "Li2O": (
        "0x1.06a2fe2b6c0e6p-4",
        "0x1.5369c5db17680p+1",
        "0x1.67612983c3a22p+5",
    ),
    "Rb2O": (
        "0x1.9d78f023491e3p+12",
        "0x1.1881caefc2232p+15",
        "0x1.e73fb4e15c6e4p+16",
    ),
    "Cs2O": (
        "0x1.37be354760525p+16",
        "0x1.5eddc9e458412p+18",
        "0x1.077e7d0600078p+20",
    ),
    "B2O3": (
        "0x1.ed0e09c634c14p-3",
        "0x1.d6b1193419d05p+3",
        "0x1.5826c1a851f0cp+8",
    ),
    "VO": (
        "0x1.bb75597dedb61p-28",
        "0x1.be2d47cf49e8fp-18",
        "0x1.6cb0ef04ed106p-10",
    ),
}

FIRST_BATCH_EXISTING = (
    "Ga",
    "In",
    "GeO",
    "SnO",
    "PbO",
    "Pb",
    "Cu",
    "Li",
    "Ge",
    "Sn",
    "GaO",
    "Ga2O",
    "InO",
    "In2O",
    "CuO",
    "Cu2",
    "Li2O",
    "Rb2O",
    "Cs2O",
    "B2O3",
    "VO",
)

# Hand-written species-level stoich rows (34) as compiled into legacy_view.
STOICH_PINS: dict[str, tuple[float, float, str]] = {
    "Si": (2.13932704290547, 1.13932704290547, "SiO2"),
    "TiO": (1.25050887796324, 0.250508877963236, "TiO2"),
    "TiO2_gas": (1.0, 0.0, "TiO2"),
    "CaO_gas": (1.0, 0.0, "CaO"),
    "AlO": (1.18611911993611, 0.186119119936108, "Al2O3"),
    "Al2O": (1.45736206487981, 0.457362064879806, "Al2O3"),
    "Al2": (1.8894414971926081, 0.8894414971926082, "Al2O3"),
    "Al2O2": (1.1861191199361079, 0.18611911993610783, "Al2O3"),
    "Al2O3_gas": (1.0, 0.0, "Al2O3"),
    "AlO2": (0.8643682164450982, -0.13563178355490185, "Al2O3"),
    "Ca2": (1.3991965666949446, 0.39919656669494485, "CaO"),
    "CrO": (1.1176481834720444, 0.11764818347204432, "Cr2O3"),
    "SiO": (1.362920787587333, 0.36292078758733326, "SiO2"),
    "CrO2": (0.9047611677486871, -0.0952388322513129, "Cr2O3"),
    "CrO3": (0.759998439892353, -0.240001560107647, "Cr2O3"),
    "PO": (1.5109024672856537, 0.5109024672856538, "P2O5"),
    "PO2": (1.1270331295518468, 0.12703312955184684, "P2O5"),
    "P4O6": (1.2910376873446114, 0.2910376873446116, "P2O5"),
    "P4O10": (1.0, 0.0, "P2O5"),
    "P2": (2.291334904768193, 1.2913349047681928, "P2O5"),
    "P4": (2.291334904768193, 1.2913349047681928, "P2O5"),
    "K2": (1.2045996884775, 0.204599688477504, "K2O"),
    "K2O_gas": (1.0, 0.0, "K2O"),
    "Mg2": (1.65825961736268, 0.658259617362683, "MgO"),
    "MgO_gas": (1.0, 0.0, "MgO"),
    "Na2": (1.34795912488601, 0.347959124886007, "Na2O"),
    "Na2O_gas": (1.0, 0.0, "Na2O"),
    "Si2": (2.13932704290547, 1.13932704290547, "SiO2"),
    "Si3": (2.13932704290547, 1.13932704290547, "SiO2"),
    "SiO2_gas": (1.0, 0.0, "SiO2"),
    "FeO_association_gas": (1.0, 0.0, "FeO"),
    "NiO_gas": (1.0, 0.0, "NiO"),
    "MnO_gas": (1.0, 0.0, "MnO"),
    "CoO_gas": (1.0, 0.0, "CoO"),
}


@pytest.fixture(scope="module")
def production_catalog():
    payload = load_cached_safe_yaml(CATALOG_PATH.read_text(encoding="utf-8"))
    return compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


@pytest.fixture(scope="module")
def generated_rows():
    return {channel.species_id: channel.family["physical_properties"]["species"][channel.species_id]
            for channel in generate_first_batch().channels}


def _legacy_row(catalog, species_id: str) -> dict:
    for group in catalog.legacy_view().values():
        if isinstance(group, dict) and species_id in group:
            row = group[species_id]
            if isinstance(row, dict):
                return row
    raise AssertionError(f"{species_id!r} missing from compiled legacy_view")


def test_production_catalog_species_count_pinned(production_catalog) -> None:
    assert len(production_catalog.species) == 247


@pytest.mark.parametrize("species_id", sorted(PRESSURE_PINS))
def test_vapour_rail_pressure_pins(production_catalog, species_id: str) -> None:
    compiled = production_catalog.species[species_id]
    evaluator = compiled.evaluator
    assert evaluator is not None
    expected = PRESSURE_PINS[species_id]
    kwargs: dict = {"pO2_bar": PIN_PO2_BAR}
    if evaluator.activity_exponent:
        kwargs["source_activity"] = 1.0
    for temperature_K, hex_value in zip(PIN_TEMPERATURES_K, expected):
        result = evaluator.evaluate(temperature_K, **kwargs)
        assert result.pressure_pa.hex() == hex_value, (
            f"{species_id} at {temperature_K} K: "
            f"got {result.pressure_pa.hex()} want {hex_value}"
        )


@pytest.mark.parametrize("species_id", FIRST_BATCH_EXISTING)
def test_first_batch_existing_rows_follow_declared_activation(
    production_catalog, generated_rows, species_id: str
) -> None:
    compiled = production_catalog.species[species_id]
    declared = generated_rows[species_id]
    dormant = declared["flux_dormant"]
    assert dormant is bool(declared.get("dormancy_reason"))
    assert compiled.code_metadata.request_rule == (
        "dormant_pending_validation" if dormant else "trace_source_inventory"
    )
    assert compiled.code_metadata.hot_train_applicability == (
        "not_applicable" if dormant else "derived_from_condensation_onset"
    )
    row = _legacy_row(production_catalog, species_id)
    assert row["flux_dormant"] is dormant
    assert row.get("dormancy_reason") == declared.get("dormancy_reason")
    assert compiled.evaluator is not None


@pytest.mark.parametrize("species_id", sorted(STOICH_PINS))
def test_hand_stoich_rows_pinned_from_legacy_view(
    production_catalog, species_id: str
) -> None:
    oxide, o2, parent = STOICH_PINS[species_id]
    row = _legacy_row(production_catalog, species_id)
    assert row["parent_oxide"] == parent
    assert row["stoich_oxide_per_vapor"] == pytest.approx(oxide, rel=0.0, abs=1e-12)
    assert row["stoich_O2_per_vapor"] == pytest.approx(o2, rel=0.0, abs=1e-12)
    assert math.isclose(
        float(row["stoich_oxide_per_vapor"]),
        1.0 + float(row["stoich_O2_per_vapor"]),
        rel_tol=1e-6,
        abs_tol=1e-9,
    )


def _atom_moles_for_kg(formula: str, kg: float) -> dict[str, float]:
    if kg <= 0.0:
        return {}
    parsed = parse_formula(formula)
    moles = float(kg) / parsed.molar_mass_kg_per_mol()
    return parsed.atom_moles(moles)


@pytest.mark.parametrize("species_id", sorted(STOICH_PINS))
def test_hand_stoich_atom_and_mass_closure(
    production_catalog, species_id: str
) -> None:
    oxide_kg, o2_kg, parent = STOICH_PINS[species_id]
    row = _legacy_row(production_catalog, species_id)
    vapor_formula = str(row["formula"])
    debit = defaultdict(float, _atom_moles_for_kg(parent, oxide_kg))
    credit = defaultdict(float, _atom_moles_for_kg(vapor_formula, 1.0))
    if o2_kg >= 0.0:
        for element, moles in _atom_moles_for_kg("O2", o2_kg).items():
            credit[element] += moles
    else:
        for element, moles in _atom_moles_for_kg("O2", abs(o2_kg)).items():
            debit[element] += moles
    for element in set(debit) | set(credit):
        assert math.isclose(
            debit[element],
            credit[element],
            rel_tol=1e-6,
            abs_tol=1e-9,
        ), (
            f"{species_id} {element}: debit={debit[element]} "
            f"credit={credit[element]}"
        )
    assert math.isclose(oxide_kg, 1.0 + o2_kg, rel_tol=1e-6, abs_tol=1e-9)


def test_live_catalog_views_expose_inserted_rows_with_canonical_species_ids(generated_rows) -> None:
    payload = load_cached_safe_yaml(CATALOG_PATH.read_text(encoding="utf-8"))
    catalog = compile_vapour_rail_catalog(payload, emit_u0_request_rules=True)
    bundle = load_config_bundle()
    views = (catalog.legacy_view(), vapor_pressure_legacy_view(payload),
             bundle.vapor_pressures)
    for species_id, declared in generated_rows.items():
        assert species_id in catalog.species
        assert not species_id.startswith("t1139_")
        compiled = catalog.species[species_id]
        assert compiled.family_id.startswith("t1139_")
        stored = bundle.vapor_pressures.catalog_payload["families"][compiled.family_id]
        assert stored["physical_properties"]["species"][species_id] == declared
        for view in views:
            rows = [group[species_id] for group in view.values()
                    if isinstance(group, dict) and species_id in group]
            assert len(rows) == 1, species_id
            assert rows[0]["flux_dormant"] is declared["flux_dormant"]
            assert rows[0]["parent_oxide"] == declared["parent_oxide"]
    for view in views:
        assert not any(str(species_id).startswith("t1139_")
                       for group in view.values() if isinstance(group, dict)
                       for species_id in group)
