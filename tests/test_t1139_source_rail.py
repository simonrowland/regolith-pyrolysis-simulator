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
    GIBBS_CONVENTION_ABSOLUTE,
    GIBBS_CONVENTION_FORMATION,
    STANDARD_PRESSURE_PA,
    SourceCoverageGap,
    _element_reference_species_g,
    compare_g_over_overlap,
    convert_gas_g_j_per_mol,
    load_source_rail,
    supercooled_liquid_from_crystal,
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
    assert all(
        row["left_gibbs_convention"] == GIBBS_CONVENTION_FORMATION
        and row["right_gibbs_convention"] == GIBBS_CONVENTION_FORMATION
        for row in comparison
    )


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


def _record_by_source(overlap, source_id: str):
    for record in overlap:
        if record.source_id == source_id:
            return record
    raise AssertionError(f"missing {source_id}")


def test_every_returned_record_names_formation_gibbs_convention(source_rail) -> None:
    samples = (
        ("Ga", "gas"),
        ("Cu", "gas"),
        ("B2O3", "gas"),
        ("B2O3", "condensed_liquid"),
        ("O2", "gas"),
        ("Ga2O3", "condensed_liquid"),
        ("Cu2O", "condensed_liquid"),
    )
    for formula, state in samples:
        for record in source_rail.records_for(formula, state):
            assert record.gibbs_convention == GIBBS_CONVENTION_FORMATION
            assert (
                record.species_thermo["gibbs_convention"]
                == record.native_gibbs_convention
            )
            if record.source_id in {"nasa-glenn", "burcat"}:
                assert record.native_gibbs_convention == GIBBS_CONVENTION_ABSOLUTE
            else:
                assert record.native_gibbs_convention == GIBBS_CONVENTION_FORMATION


def test_janaf_o2_reference_is_zero_by_definition(source_rail) -> None:
    oxygen = source_rail.require("O2", "gas")
    assert oxygen.source_id == "nist-janaf-4th"
    assert oxygen.record_id == "O-029"
    assert oxygen.native_phase == "ref"
    assert oxygen.standard_state == "gas"
    assert oxygen.species_thermo["gibbs_defined_zero"] is True
    for temperature_K in (298.15, 1400.0, 1600.0, 1800.0):
        assert oxygen.thermo.evaluate(temperature_K).g_J_per_mol == pytest.approx(
            0.0, abs=1e-9
        )
    bromine = source_rail.records_for("Br2", "gas")
    assert all(record.native_phase != "ref" for record in bromine)
    aluminium = [
        record
        for record in source_rail.records_for("Al", "condensed_solid")
        if record.source_id == "nist-janaf-4th"
    ]
    assert aluminium
    assert all(record.native_phase != "ref" for record in aluminium)


def test_nasa_o2_formation_gibbs_is_the_zero_of_the_reference(source_rail) -> None:
    oxygen = _record_by_source(source_rail.records_for("O2", "gas"), "nasa-glenn")
    for temperature_K in (1400.0, 1600.0, 1800.0):
        dfg = oxygen.thermo.evaluate(temperature_K).g_J_per_mol
        assert dfg == pytest.approx(0.0, abs=1e-6)


def test_two_source_formation_gibbs_residuals_on_common_basis(source_rail) -> None:
    """JANAF ΔfG vs NASA converted ΔfG. Residuals stay visible; none are tuned."""
    samples = (
        ("Ga", "gas"),
        ("Cu", "gas"),
        ("B2O3", "gas"),
        ("B2O3", "condensed_liquid"),
        ("Si", "gas"),
        ("SiO", "gas"),
        ("Al", "gas"),
        ("Al2O3", "condensed_solid"),
        ("Fe", "gas"),
        ("FeO", "gas"),
    )
    reported: list[str] = []
    for formula, state in samples:
        overlap = source_rail.overlapping_sources(formula, state)
        janaf = _record_by_source(overlap, "nist-janaf-4th")
        nasa = _record_by_source(overlap, "nasa-glenn")
        rows = compare_g_over_overlap(janaf, nasa)
        assert rows
        for row in rows:
            residual_kJ = row["delta_g_J_per_mol"] / 1000.0
            line = (
                f"{formula}({state}) T={row['T_K']:.0f} "
                f"JANAF-NASA={residual_kJ:.3f} kJ/mol"
            )
            reported.append(line)
            assert math.isfinite(residual_kJ)
            # Unconverted CEA G° disagrees by 75–500 kJ and grows with T.
            # After conversion the residual is a few kJ. Anything above ~5 kJ
            # is a real data disagreement: fail with the residual visible.
            assert abs(residual_kJ) < 5.0, line
    print("\n".join(reported))
    assert len(reported) == 30


def test_supercooled_ga2o3_meets_the_liquid_and_reaches_below_tm(source_rail) -> None:
    liquid = _record_by_source(
        source_rail.records_for("Ga2O3", "condensed_liquid"), "nasa-glenn"
    )
    crystal = _record_by_source(
        source_rail.records_for("Ga2O3", "condensed_solid"), "nasa-glenn"
    )
    extended = supercooled_liquid_from_crystal(liquid, crystal)
    assert extended is not None
    assert extended.species_thermo["supercooled_liquid_extension"] is True
    assert extended.species_thermo["supercooled_crystal_record_id"] == crystal.record_id
    assert extended.T_min_K == pytest.approx(crystal.T_min_K)
    assert extended.T_min_K < 2080.0
    tm = liquid.T_min_K
    liquid_native = liquid.thermo.native
    crystal_native = crystal.thermo.native
    extended_native = extended.thermo.native
    assert extended_native.evaluate(tm).g_J_per_mol == pytest.approx(
        liquid_native.evaluate(tm).g_J_per_mol, rel=0.0, abs=1e-6
    )
    crystal_side = next(
        seg for seg in extended_native.segments if math.isclose(seg.T_max_K, tm)
    )
    _cp, _h, _s, g_over_rt = crystal_side.evaluate_ratios(tm)
    assert g_over_rt * R_J_PER_MOL_K * tm == pytest.approx(
        liquid_native.evaluate(tm).g_J_per_mol, rel=0.0, abs=1e-4
    )

    def extension_offset(temperature_K: float) -> float:
        return (
            extended_native.evaluate(temperature_K).g_J_per_mol
            - crystal_native.evaluate(temperature_K).g_J_per_mol
        )

    step = 50.0
    offsets = [extension_offset(tm - step * i) for i in (1, 2, 3)]
    assert offsets[0] - offsets[1] == pytest.approx(
        offsets[1] - offsets[2], rel=0.0, abs=1e-4
    )
    assert math.isfinite(extended.thermo.evaluate(1000.0).g_J_per_mol)


def test_nasa_si_reference_is_crystal_inside_condensed_coverage(source_rail) -> None:
    """NG-1858's inverted first interval is skipped; gas is not the ref below Tm."""
    index = source_rail._indexes["nasa-glenn"]
    crystal = next(
        record
        for record in index.native_records_for("Si", "condensed_solid")
        if record.record_id == "NG-1858"
    )
    assert crystal.T_min_K == pytest.approx(298.15)
    assert crystal.T_max_K == pytest.approx(1690.0)
    g_ref, atoms = _element_reference_species_g(index, "Si", 1400.0)
    assert atoms == 1.0
    assert g_ref == pytest.approx(crystal.thermo.evaluate(1400.0).g_J_per_mol)
    gas = next(
        record
        for record in index.native_records_for("Si", "gas")
        if record.T_min_K <= 1400.0 <= record.T_max_K
    )
    assert abs(g_ref - gas.thermo.evaluate(1400.0).g_J_per_mol) > 100_000.0
    with pytest.raises(SourceCoverageGap, match="condensed coverage"):
        _element_reference_species_g(index, "Si", 250.0)
    g_hot, _atoms = _element_reference_species_g(index, "Si", 6050.0)
    hot_gas = next(
        record
        for record in index.native_records_for("Si", "gas")
        if record.T_min_K <= 6050.0 <= record.T_max_K
    )
    assert g_hot == pytest.approx(hot_gas.thermo.evaluate(6050.0).g_J_per_mol)


def test_janaf_and_nasa_same_reaction_agree_once_converted(source_rail) -> None:
    """B2O3(l) → B2O3(g): one compilation per side, common ΔfG basis."""
    janaf_liquid = source_rail.require("B2O3", "condensed_liquid")
    assert janaf_liquid.source_id == "nist-janaf-4th"
    nasa_liquid = _record_by_source(
        source_rail.records_for("B2O3", "condensed_liquid"), "nasa-glenn"
    )
    janaf_gas = _record_by_source(
        source_rail.records_for("B2O3", "gas"), "nist-janaf-4th"
    )
    nasa_gas = _record_by_source(
        source_rail.records_for("B2O3", "gas"), "nasa-glenn"
    )
    residuals_kJ: list[float] = []
    for temperature_K in (1400.0, 1600.0, 1800.0):
        dG_janaf = (
            janaf_gas.thermo.evaluate(temperature_K).g_J_per_mol
            - janaf_liquid.thermo.evaluate(temperature_K).g_J_per_mol
        )
        dG_nasa = (
            nasa_gas.thermo.evaluate(temperature_K).g_J_per_mol
            - nasa_liquid.thermo.evaluate(temperature_K).g_J_per_mol
        )
        residual_kJ = (dG_janaf - dG_nasa) / 1000.0
        residuals_kJ.append(residual_kJ)
        assert math.isfinite(residual_kJ)
        assert abs(residual_kJ) < 5.0, (
            f"B2O3(l)->B2O3(g) T={temperature_K:.0f} "
            f"JANAF-NASA={residual_kJ:.3f} kJ/mol"
        )
    print(
        "B2O3(l)->B2O3(g) JANAF-NASA kJ/mol at 1400/1600/1800 K: "
        + ", ".join(f"{value:.3f}" for value in residuals_kJ)
    )


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
