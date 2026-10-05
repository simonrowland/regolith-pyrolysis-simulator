"""t-1139 source rail, tabulated JANAF family, and derived stoich."""

from __future__ import annotations

import math

import pytest

from simulator.accounting.formulas import parse_formula
from simulator.reference_data.janaf import feedstock_element_symbols
from simulator.vapour_rail.catalog import (
    RUNTIME_THERMO_EVALUATOR_FAMILIES,
    compile_vapour_rail_catalog,
)
from simulator.vapour_rail.nasa_cea import R_J_PER_MOL_K
from simulator.vapour_rail.source_rail import (
    ATM_PRESSURE_PA,
    STANDARD_PRESSURE_PA,
    compare_g_over_overlap,
    convert_gas_g_j_per_mol,
    load_source_rail,
)
from simulator.vapour_rail.stoich import CATALOG_DERIVED_STOICH_SPECIES
from simulator.vapour_rail.tabulated_gibbs import (
    TabulatedDomainError,
    TabulatedThermo,
    interpolate_tabulated,
)
from simulator.yaml_cache import load_cached_safe_yaml
from tests.test_t1139_build_a_pins import CATALOG_PATH, STOICH_PINS, _legacy_row


def test_decimal_subscript_formulas_parse_for_activity_basis() -> None:
    alo15 = parse_formula("AlO1.5")
    al2o3 = parse_formula("Al2O3")
    assert alo15.elements["Al"] == pytest.approx(1.0)
    assert alo15.elements["O"] == pytest.approx(1.5)
    assert alo15.molar_mass_g_per_mol() == pytest.approx(
        al2o3.molar_mass_g_per_mol() / 2.0
    )
    cuo05 = parse_formula("CuO0.5")
    cu2o = parse_formula("Cu2O")
    assert cuo05.elements["Cu"] == pytest.approx(1.0)
    assert cuo05.elements["O"] == pytest.approx(0.5)
    assert cuo05.molar_mass_g_per_mol() == pytest.approx(
        cu2o.molar_mass_g_per_mol() / 2.0
    )
    hydrate = parse_formula("H2SO4.2H2O")
    assert hydrate.elements["H"] == pytest.approx(6.0)
    assert hydrate.elements["S"] == pytest.approx(1.0)
    assert hydrate.elements["O"] == pytest.approx(6.0)


def test_tabulated_janaf_is_a_runtime_thermo_family() -> None:
    assert "tabulated_janaf" in RUNTIME_THERMO_EVALUATOR_FAMILIES
    assert "nasa_cea_7" in RUNTIME_THERMO_EVALUATOR_FAMILIES
    assert "nasa_cea_9" in RUNTIME_THERMO_EVALUATOR_FAMILIES
    assert "shomate" in RUNTIME_THERMO_EVALUATOR_FAMILIES


def test_tabulated_delta_f_g_interpolates_and_refuses_oor() -> None:
    points = ((1000.0, 1000.0), (2000.0, 3000.0))
    poly = TabulatedThermo(
        name="probe",
        standard_state="gas",
        formation_gibbs_J_per_mol=points,
    )
    mid = poly.evaluate(1500.0)
    expected_g = interpolate_tabulated(points, 1500.0)
    assert mid.g_J_per_mol == pytest.approx(expected_g)
    assert mid.g_over_RT == pytest.approx(
        expected_g / (R_J_PER_MOL_K * 1500.0)
    )
    with pytest.raises(TabulatedDomainError):
        poly.evaluate(500.0)


def test_one_bar_gas_conversion_is_rt_ln() -> None:
    t_k = 1600.0
    g_atm = 50_000.0
    g_bar = convert_gas_g_j_per_mol(
        g_atm, t_k, native_pressure_Pa=ATM_PRESSURE_PA
    )
    expected = g_atm + R_J_PER_MOL_K * t_k * math.log(
        STANDARD_PRESSURE_PA / ATM_PRESSURE_PA
    )
    assert g_bar == pytest.approx(expected)
    assert STANDARD_PRESSURE_PA == 100_000.0
    assert ATM_PRESSURE_PA == 101_325.0


@pytest.fixture(scope="module")
def source_rail():
    return load_source_rail()


@pytest.fixture(scope="module")
def production_catalog():
    payload = load_cached_safe_yaml(CATALOG_PATH.read_text(encoding="utf-8"))
    return compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


def test_janaf_ga_gas_is_selected_before_nasa(source_rail) -> None:
    record = source_rail.require("Ga", "gas")
    assert record.source_id == "nist-janaf-4th"
    assert record.native_phase == "g"
    assert record.reference_pressure_Pa == STANDARD_PRESSURE_PA
    overlap = source_rail.overlapping_sources("Ga", "gas")
    sources = {item.source_id for item in overlap}
    assert "nist-janaf-4th" in sources
    assert "nasa-glenn" in sources
    comparison = compare_g_over_overlap(overlap[0], overlap[1])
    assert comparison
    # Disagreements stay visible: the sample is finite, not forced to zero.
    assert all(math.isfinite(row["delta_g_J_per_mol"]) for row in comparison)


def test_liquid_parents_are_on_the_rail(source_rail) -> None:
    for formula in (
        "Ga2O3",
        "In2O3",
        "PbO",
        "GeO2",
        "SnO2",
        "Rb2O",
        "Cs2O",
        "B2O3",
        "Cu2O",
        "V2O3",
        "Li2O",
    ):
        record = source_rail.require(formula, "condensed_liquid")
        assert record.standard_state == "condensed_liquid"
        assert record.T_max_K > record.T_min_K


def test_feedstock_element_enumerator_is_the_janaf_owner() -> None:
    symbols = feedstock_element_symbols()
    for element in ("Ga", "In", "Pb", "Ge", "Sn", "Rb", "Cs", "B", "Cu", "V", "Li"):
        assert element in symbols


def test_catalog_derives_exactly_the_retired_hand_rows() -> None:
    assert CATALOG_DERIVED_STOICH_SPECIES == frozenset(STOICH_PINS)


@pytest.mark.parametrize("species_id", sorted(STOICH_PINS))
def test_derived_stoich_matches_retired_hand_rows(
    production_catalog, species_id: str
) -> None:
    oxide, o2, parent = STOICH_PINS[species_id]
    compiled = production_catalog.species[species_id]
    row = _legacy_row(production_catalog, species_id)
    assert row["parent_oxide"] == parent
    assert row["stoich_oxide_per_vapor"] == pytest.approx(oxide, rel=0.0, abs=1e-12)
    assert row["stoich_O2_per_vapor"] == pytest.approx(o2, rel=0.0, abs=1e-12)
    assert compiled.formula
