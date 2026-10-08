"""t-1139 source rail, tabulated JANAF family, and derived stoich."""

from __future__ import annotations

import math

import pytest

from simulator.accounting.formulas import parse_formula
from simulator.accounting.exceptions import UnknownSpeciesError
from simulator.reference_data import janaf
from simulator.reference_data.janaf import feedstock_element_symbols
from simulator.vapour_rail.catalog import (
    RUNTIME_THERMO_EVALUATOR_FAMILIES,
    CatalogCompileError,
    _legacy_species_row,
    _polynomial_from_thermo_record,
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
    TabulatedMissingNodeError,
    TabulatedThermo,
    interpolate_tabulated,
)
from simulator.yaml_cache import load_cached_safe_yaml
from tests.test_t1139_build_a_pins import CATALOG_PATH, STOICH_PINS, _legacy_row
from tests.test_vr4b_runtime_thermo_families import _strata


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


@pytest.mark.parametrize(
    ("formula", "atoms"),
    (
        ("Fe.947O", {"Fe": 0.947, "O": 1}),
        ("Fe0.947O", {"Fe": 0.947, "O": 1}),
        ("Mg1.8Fe0.2SiO4", {"Mg": 1.8, "Fe": 0.2, "Si": 1, "O": 4}),
        ("Ba0.543Sr0.457TiO3", {"Ba": 0.543, "Sr": 0.457, "Ti": 1, "O": 3}),
        ("Ni0.4Zn0.6Fe2O4", {"Ni": 0.4, "Zn": 0.6, "Fe": 2, "O": 4}),
        ("Fe.90S", {"Fe": 0.90, "S": 1}),
        ("NbC.98", {"Nb": 1, "C": 0.98}),
        ("(Na.78K.22)AlSiO4", {"Na": 0.78, "K": 0.22, "Al": 1, "Si": 1, "O": 4}),
        ("CuSO4.5H2O", {"Cu": 1, "S": 1, "O": 9, "H": 10}),
        ("H2SO4.2H2O", {"S": 1, "O": 6, "H": 6}),
        ("NaCl.2H2O", {"Na": 1, "Cl": 1, "O": 2, "H": 4}),
        ("SrHgO.4CO2.5H2O", {"Sr": 1, "Hg": 1, "C": 4, "O": 14, "H": 10}),
    ),
)
def test_printed_decimal_subscripts_preserve_molecular_adducts(formula, atoms) -> None:
    """USGS/JANAF leading-dot occupancy; hydrates retain whole water molecules."""
    assert dict(parse_formula(formula).elements) == pytest.approx(atoms)


@pytest.mark.parametrize(
    ("formula", "atoms"),
    (
        ("K0.5Na1.5AlSiO4", {"K": 0.5, "Na": 1.5, "Al": 1, "Si": 1, "O": 4}),
        ("Na.76K.22AlSiO4", {"Na": 0.76, "K": 0.22, "Al": 1, "Si": 1, "O": 4}),
        ("Mg1.5Fe.5SiO4", {"Mg": 1.5, "Fe": 0.5, "Si": 1, "O": 4}),
        ("Ca.5Mg.5CO3", {"Ca": 0.5, "Mg": 0.5, "C": 1, "O": 3}),
        ("Fe.5MgSiO4", {"Fe": 0.5, "Mg": 1, "Si": 1, "O": 4}),
        ("Mg1.5Fe1SiO4", {"Mg": 1.5, "Fe": 1, "Si": 1, "O": 4}),
        ("Mg1Fe0.2SiO4", {"Mg": 1, "Fe": 0.2, "Si": 1, "O": 4}),
    ),
)
def test_mixed_fractional_subscripts_are_not_adduct_multipliers(formula, atoms) -> None:
    assert dict(parse_formula(formula).elements) == pytest.approx(atoms)


def test_ambiguous_decimal_hydrate_coefficient_is_refused() -> None:
    with pytest.raises(UnknownSpeciesError):
        parse_formula("CaSO4.0.5H2O")


@pytest.mark.parametrize(
    "formula", ("Fe.9470", "W.465", "N1.5617IO.41959Ar.00937C.00032")
)
def test_unverified_ocr_formulas_do_not_claim_a_composition(formula) -> None:
    from simulator.battery.validate import _term_composition

    with pytest.raises(UnknownSpeciesError, match="unverified OCR formula"):
        parse_formula(formula)
    assert _term_composition(formula) is None


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
        "SnO",
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


def test_sn_oxide_liquids_support_both_valences(source_rail) -> None:
    """SnO is Sn2+ and SnO2 is Sn4+. Both NASA liquids load; JANAF has neither."""
    sno = source_rail.records_for("SnO", "condensed_liquid")
    sno2 = source_rail.records_for("SnO2", "condensed_liquid")
    assert [(record.source_id, record.record_id) for record in sno] == [
        ("nasa-glenn", "NG-1888")
    ]
    assert [(record.source_id, record.record_id) for record in sno2] == [
        ("nasa-glenn", "NG-1890")
    ]
    assert sno[0].T_min_K == 1250.0
    assert sno2[0].T_min_K == 1903.0
    assert source_rail.records_for("SnO", "condensed_solid")[0].record_id == "NG-1887"
    assert source_rail.records_for("SnO2", "condensed_solid")[0].record_id == "NG-1889"


def test_feedstock_element_enumerator_is_the_janaf_owner() -> None:
    symbols = feedstock_element_symbols()
    for element in ("Ga", "In", "Pb", "Ge", "Sn", "Rb", "Cs", "B", "Cu", "V", "Li"):
        assert element in symbols


def _legacy_model(**overrides):
    model = {
        "evaluator_family": "nasa_cea_9",
        "compatibility_omit_valid_range_K": True,
        "compatibility_omit_antoine": True,
        "availability": "unavailable_pending_acquisition",
        "valid_domain": {},
    }
    model.update(overrides)
    return model


def test_derived_species_refuses_an_unmatched_source_reaction() -> None:
    row = {
        "formula": "SiO",
        "parent_oxide": "SiO2",
        "source_reactions": [
            {
                "id": "real",
                "reactants": [{"formula": "SiO2(l)", "stoichiometry": 1.0}],
                "products": [
                    {"formula": "SiO(g)", "stoichiometry": 1.0},
                    {"formula": "O2(g)", "stoichiometry": 0.5},
                ],
            }
        ],
    }
    with pytest.raises(CatalogCompileError, match="source_reaction_id"):
        _legacy_species_row(
            species_id="SiO",
            row=row,
            model=_legacy_model(source_reaction_id="missing"),
            routing={},
            kinetics={},
            code={},
        )
    broken = {
        **row,
        "source_reactions": [
            {
                "id": "real",
                "reactants": [{"formula": "SiO2(l)", "stoichiometry": 1.0}],
                "products": [{"formula": "Si(g)", "stoichiometry": 1.0}],
            }
        ],
    }
    with pytest.raises(CatalogCompileError, match="cannot derive stoichiometry"):
        _legacy_species_row(
            species_id="SiO",
            row=broken,
            model=_legacy_model(source_reaction_id="real"),
            routing={},
            kinetics={},
            code={},
        )


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


def _blank_positive_gibbs_temperatures(table_id: str) -> tuple[float, ...]:
    """Positive-T JANAF rows whose formation-Gibbs cell is blank."""
    document = janaf.load_table_document(janaf.TABLES_DIR / f"{table_id}.yaml")
    rows = (document.get("table") or {}).get("values") or []
    printed: set[float] = set()
    missing: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        temperature = row.get("temperature") or {}
        gibbs = row.get("formation_gibbs_energy") or {}
        t_k = temperature.get("value") if isinstance(temperature, dict) else None
        if t_k is None or float(t_k) <= 0.0:
            continue
        if not isinstance(gibbs, dict) or gibbs.get("value") is None:
            missing.append(float(t_k))
        else:
            printed.add(float(t_k))
    return tuple(t_k for t_k in missing if t_k not in printed)


@pytest.mark.parametrize(
    ("element", "record_id"),
    (("Sn", "BU-3341"), ("V", "BU-3366")),
)
def test_burcat_gas_only_element_is_not_the_elemental_reference(
    source_rail, element: str, record_id: str
) -> None:
    """Burcat has no condensed Sn or V. The gas record must not become the ref."""
    index = source_rail._indexes["burcat"]
    condensed = []
    for state in ("condensed_liquid", "condensed_solid", "condensed"):
        condensed.extend(index.native_records_for(element, state))
    assert condensed == []
    gas = [
        record
        for record in index.native_records_for(element, "gas")
        if record.record_id == record_id
    ]
    assert len(gas) == 1
    assert gas[0].T_min_K == pytest.approx(200.0)
    assert gas[0].T_max_K == pytest.approx(6000.0)
    for temperature_K in (200.0, 1400.0, 6000.0):
        assert gas[0].T_min_K <= temperature_K <= gas[0].T_max_K
        with pytest.raises(SourceCoverageGap, match="gas is not the elemental reference"):
            _element_reference_species_g(index, element, temperature_K)
    converted = next(
        record
        for record in index.records_for(element, "gas")
        if record.record_id == record_id
    )
    with pytest.raises(SourceCoverageGap, match="gas is not the elemental reference"):
        converted.thermo.evaluate(1400.0)


def test_burcat_o2_stays_the_gas_elemental_reference(source_rail) -> None:
    """O2 is a JANAF `ref` gas standard, so Burcat's gas record is the reference."""
    index = source_rail._indexes["burcat"]
    g_ref, atoms = _element_reference_species_g(index, "O", 1400.0)
    assert atoms == 2.0
    covering = [
        record
        for record in index.native_records_for("O2", "gas")
        if record.T_min_K <= 1400.0 <= record.T_max_K
    ]
    assert covering
    assert g_ref == pytest.approx(covering[0].thermo.evaluate(1400.0).g_J_per_mol)


def test_compiled_tabulated_janaf_refuses_at_a_missing_printed_node(
    source_rail,
) -> None:
    record = next(
        item
        for item in source_rail.records_for("Al2O3", "condensed_liquid")
        if item.record_id == "Al-100"
    )
    blanks = _blank_positive_gibbs_temperatures("Al-100")
    assert blanks
    assert record.thermo.missing_nodes == blanks
    assert record.species_thermo["missing_nodes"] == [float(node) for node in blanks]
    rebuilt = _polynomial_from_thermo_record(
        name="Al-100",
        family="tabulated_janaf",
        record=record.species_thermo,
    )
    assert rebuilt.missing_nodes == blanks

    points = record.species_thermo["formation_gibbs_points"]
    left, middle, right = points[0], points[1], points[2]
    query = (float(left["T_K"]) + float(middle["T_K"])) / 2.0
    gas = dict(record.species_thermo)
    gas["standard_state"] = "gas"
    gas["formation_gibbs_points"] = [left, right]
    gas["missing_nodes"] = list(record.species_thermo["missing_nodes"]) + [
        middle["T_K"]
    ]
    condensed = dict(record.species_thermo)
    condensed["formation_gibbs_points"] = [left, right]
    condensed.pop("missing_nodes", None)
    domain = [float(left["T_K"]), float(right["T_K"])]

    def payload(gas_record: dict) -> dict:
        return _strata(
            species_id="BlankNode",
            formula="Al2O3",
            pressure_models=[
                {
                    "evaluator_family": "tabulated_janaf",
                    "pressure_kind": "pure_component_saturation_pressure",
                    "species_basis": "monomer",
                    "valid_domain": {"temperature_K": domain},
                    "reference_pressure_Pa": STANDARD_PRESSURE_PA,
                    "gas_thermo_record": gas_record,
                    "condensed_thermo_record": condensed,
                }
            ],
        )

    refused = compile_vapour_rail_catalog(payload(gas), emit_u0_request_rules=False)
    with pytest.raises(TabulatedMissingNodeError) as caught:
        refused.evaluator_for("BlankNode").evaluate(query)
    assert caught.value.missing_node == pytest.approx(float(middle["T_K"]))

    open_bracket = dict(gas)
    open_bracket.pop("missing_nodes")
    admitted = compile_vapour_rail_catalog(
        payload(open_bracket), emit_u0_request_rules=False
    )
    pressure = admitted.evaluator_for("BlankNode").evaluate(query).pressure_pa
    assert math.isfinite(pressure)
    assert pressure > 0.0


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
