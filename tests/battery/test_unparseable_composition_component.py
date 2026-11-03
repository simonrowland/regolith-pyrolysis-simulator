"""Decimal-subscript oxides take a JANAF molar mass. Both-fail names refuse one row."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.accounting.exceptions import (
    AccountingError,
    UnparseableCompositionComponent,
)
from simulator.accounting.formulas import (
    ATOMIC_WEIGHTS_G_PER_MOL,
    atoms_parse_formula_else_janaf,
    parse_formula,
)
from simulator.battery.composition import composition_wt_pct
from simulator.battery.enums import (
    AmountBasis,
    Engine,
    EvidenceClass,
    Quantity,
    Rail,
    RefusalReason,
    ResidualStatus,
)
from simulator.battery.fo2_ladder import _weight_percent
from simulator.battery.records import Composition, State
from simulator.battery.score import (
    ScoreContext,
    _oxide_element_inventory,
    _predict_fe_redox,
    compile_residual,
    score_store,
)
from simulator.battery.validate import _term_composition
from simulator.diagnostic_helpers.binary_pot_battery import composition_kg_and_mol
from simulator.diagnostic_helpers.binary_pot_scoring import oxide_molar_mass_g_mol
from tests.battery.factories import (
    activity_identity,
    migrate_one_extract,
    observation,
    tabulation_experiment,
    work,
)


def _moles(*pairs: tuple[str, str]) -> Composition:
    return Composition(
        "oxides",
        tuple((name, Decimal(amount)) for name, amount in pairs),
        AmountBasis.MOLE_FRACTION,
    )


# Independent CIAAW pins. FeO1.5 is Fe + 1.5 O; SiO2 is the tabulated mass.
_FEO15_G_MOL = 79.8435
_SIO2_G_MOL = 60.083
_PARSER_EXCEPTION_NAMES = frozenset({"AccountingError", "UnknownSpeciesError"})


def _assert_not_parser_engine_exception(detail: dict) -> None:
    """A residual may refuse for a domain reason. The formula parser must not."""

    if detail.get("reason") != "engine_call_exception":
        return
    exception = str(detail.get("exception", ""))
    text = str(detail.get("detail", ""))
    assert exception not in _PARSER_EXCEPTION_NAMES, detail
    assert "formula segment" not in text, detail
    assert "formula multiplier must be positive" not in text, detail


def _activity_residual(observation_id: str, composition: Composition):
    identity = activity_identity(
        formula="SiO2",
        component_basis="SiO2",
        composition=composition,
    )
    experiment = tabulation_experiment()
    row = observation(
        observation_id,
        experiment.experiment_id,
        identity,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_REDUCED,
        source_id="eliezer-howald-1978-potassium-silicate-vapor-pressure",
    )
    context = ScoreContext(
        works={experiment.work_id: work()},
        experiments={experiment.experiment_id: experiment},
        observations={row.observation_id: row},
        hostname="test",
    )
    try:
        residual, _candidate = compile_residual(row, Engine.OPENIMCC, context=context)
    except AccountingError as exc:
        raise AssertionError(
            f"{observation_id} composition parse escaped compile_residual: {exc}"
        ) from exc
    assert residual is not None
    detail = {} if residual.refusal is None else dict(residual.refusal.detail)
    assert detail.get("reason") != "unparseable_composition_component"
    _assert_not_parser_engine_exception(detail)
    return detail


def test_equal_mole_feo15_sio2_weight_percent() -> None:
    weights = composition_wt_pct(_moles(("FeO1.5", "1"), ("SiO2", "1")))
    assert weights is not None
    assert weights["FeO1.5"] == pytest.approx(57.061028, abs=1e-6)
    assert weights["SiO2"] == pytest.approx(42.938972, abs=1e-6)
    ladder = _weight_percent(_moles(("FeO1.5", "1"), ("SiO2", "1")), None, None, None)
    assert ladder is not None
    assert ladder["FeO1.5"] == pytest.approx(57.061028, abs=1e-6)
    assert ladder["SiO2"] == pytest.approx(42.938972, abs=1e-6)


def test_ko05_nao05_and_cro15_use_the_janaf_fallback() -> None:
    atomic = ATOMIC_WEIGHTS_G_PER_MOL
    silica = atomic["Si"] + 2.0 * atomic["O"]
    expected = {
        "KO0.5": atomic["K"] + 0.5 * atomic["O"],
        "NaO0.5": atomic["Na"] + 0.5 * atomic["O"],
        "CrO1.5": atomic["Cr"] + 1.5 * atomic["O"],
        "FeO1.5": atomic["Fe"] + 1.5 * atomic["O"],
    }
    for name, molar_mass in expected.items():
        assert oxide_molar_mass_g_mol(name) == pytest.approx(molar_mass, abs=1e-9)
        weights = composition_wt_pct(_moles((name, "1"), ("SiO2", "1")))
        assert weights is not None
        share = 100.0 * molar_mass / (molar_mass + silica)
        assert weights[name] == pytest.approx(share, abs=1e-6)
        assert weights["SiO2"] == pytest.approx(100.0 - share, abs=1e-6)
    assert dict(_term_composition("KO0.5") or ()) == {"K": 1.0, "O": 0.5}
    assert dict(_term_composition("FeO1.5") or ()) == {"Fe": 1.0, "O": 1.5}
    for formula in ("FeO1.5", "KO0.5", "NaO0.5", "CrO1.5", "CuSO4.5H2O"):
        assert _term_composition(formula) == atoms_parse_formula_else_janaf(formula)


def test_successful_parse_formula_wins_over_janaf_atoms() -> None:
    parsed = parse_formula("CuSO4.5H2O")
    assert oxide_molar_mass_g_mol("CuSO4.5H2O") == parsed.molar_mass_g_mol
    assert parsed.molar_mass_g_mol == pytest.approx(249.677, abs=1e-6)
    hydrate = dict(_term_composition("CuSO4.5H2O") or ())
    assert hydrate["H"] == 10.0
    assert hydrate["O"] == 9.0
    weights = composition_wt_pct(_moles(("SiO2", "1"), ("MgO", "1")))
    assert weights is not None
    assert weights["SiO2"] == pytest.approx(59.851375178060906, abs=1e-12)
    assert weights["MgO"] == pytest.approx(40.148624821939094, abs=1e-12)


def _atoms(formula: str) -> dict[str, float]:
    parsed = parse_formula(formula)
    assert parsed.species == formula
    return {element: float(count) for element, count in parsed.elements.items()}


def test_decimal_subscript_is_one_species_and_hydrate_dot_still_splits() -> None:
    """A printed decimal subscript is one species. A hydrate dot still splits.

    Expected decimal atoms come from JANAF formula_composition, the other
    tokenizer. Hydrate atoms are the water count: CuSO4 plus five H2O is
    Cu1 S1 O9 H10. JANAF reads the ASCII dot as decimal oxygen (O 5.5),
    so an O of 9 is the adduct split and an O of 5.5 would mean it was lost.
    """

    from simulator.battery.composition import parse_species_formula as composition_parse
    from simulator.battery.generators.usgs_b1259 import (
        _parseable_formula as b1259_parseable,
    )
    from simulator.battery.generators.usgs_b1452 import (
        _parseable_formula as b1452_parseable,
    )
    from simulator.battery.score import parse_species_formula as score_parse
    from simulator.reference_data.janaf import formula_composition

    decimals = (
        "FeO1.5",
        "AlO1.5",
        "NaO0.5",
        "KO0.5",
        "Fe0.947O",
        "FeO1.33",
        "SiO1.5C0.5",
        # Fractional digits that also read as an integer adduct count stay
        # on one species. A part with one element, or no element, is not
        # an adduct (Fe1|17Si3|13O8, Ca1|5SiO3, Fe2|3O4, FeO1|5, SiO1|5C0|5).
        "Fe1.17Si3.13O8",
        "Na1.2Fe1.3Si3O8",
        "Mg1.8Fe1.2SiO4",
        "Ca1.5SiO3",
        "Mg1.5SiO4",
        "Fe2.3O4",
    )
    for formula in decimals:
        atoms = _atoms(formula)
        janaf = formula_composition(formula)
        assert janaf is not None
        assert atoms == dict(janaf)
        assert atoms_parse_formula_else_janaf(formula) == janaf
        assert score_parse(formula) == janaf
        assert composition_parse(formula) == janaf
    assert _atoms("FeO1.5") == {"Fe": 1.0, "O": 1.5}
    assert _atoms("FeO1.5") != _atoms("Fe2O3")
    assert _atoms("Fe0.947O") == {"Fe": 0.947, "O": 1.0}
    assert _atoms("AlO1.5") != _atoms("Al2O3")
    assert _atoms("Fe1.17Si3.13O8") == {"Fe": 1.17, "Si": 3.13, "O": 8.0}
    assert _atoms("Na1.2Fe1.3Si3O8") == {"Na": 1.2, "Fe": 1.3, "Si": 3.0, "O": 8.0}
    assert _atoms("Mg1.8Fe1.2SiO4") == {"Mg": 1.8, "Fe": 1.2, "Si": 1.0, "O": 4.0}
    assert _atoms("Ca1.5SiO3") == {"Ca": 1.5, "Si": 1.0, "O": 3.0}
    assert _atoms("Mg1.5SiO4") == {"Mg": 1.5, "Si": 1.0, "O": 4.0}
    assert _atoms("Fe2.3O4") == {"Fe": 2.3, "O": 4.0}
    assert _atoms("SiO1.5C0.5") == {"Si": 1.0, "O": 1.5, "C": 0.5}

    five_waters = {"Cu": 1.0, "S": 1.0, "O": 9.0, "H": 10.0}
    assert _atoms("CuSO4·5H2O") == five_waters
    assert _atoms("CuSO4.5H2O") == five_waters
    janaf_dot = dict(formula_composition("CuSO4.5H2O") or ())
    assert janaf_dot.get("O") == 5.5
    assert janaf_dot.get("H") == 2.0
    assert _atoms("3Al2O3.2SiO2") == {"Al": 6.0, "O": 13.0, "Si": 2.0}
    assert _atoms("Na2O.SiO2") == {"Na": 2.0, "O": 3.0, "Si": 1.0}
    assert _atoms("Na2O.2SiO2") == {"Na": 2.0, "O": 5.0, "Si": 2.0}
    assert _atoms("K2O.2SiO2") == {"K": 2.0, "O": 5.0, "Si": 2.0}
    # Kaolinite hand count: Al2O3 + 2 SiO2 + 2 H2O. A chain of digit-flanked
    # adducts, not one decimal coefficient (that reading is Al 2, Si 1, O 6.4).
    kaolinite = {"Al": 2.0, "Si": 2.0, "O": 9.0, "H": 4.0}
    assert _atoms("Al2O3.2SiO2.2H2O") == kaolinite
    assert _atoms("Al2O3·2SiO2·2H2O") == kaolinite
    for parseable in (b1259_parseable, b1452_parseable):
        candidate = parseable("Al2O3·2SiO2·2H2O")
        assert candidate == "Al2O3.2SiO2.2H2O"
        assert _atoms(candidate) == kaolinite
    # Feldspar hand count. The fractional parts stay on one species.
    assert _atoms("Na1.08Fe1.17Si3.13O8") == {
        "Na": 1.08,
        "Fe": 1.17,
        "Si": 3.13,
        "O": 8.0,
    }

    for junk in ("foobar", "not-a-formula", "Fe_tO", "Fe(1-x)O", "unknown"):
        with pytest.raises(AccountingError):
            parse_formula(junk)
        assert atoms_parse_formula_else_janaf(junk) is None
        assert composition_parse(junk) is None
        assert score_parse(junk) is None

    # Printed x is an element token, not a decimal subscript.
    with pytest.raises(AccountingError, match="Ox"):
        parse_formula("FeOx")
    assert atoms_parse_formula_else_janaf("FeOx") == formula_composition("FeOx")


def test_decimal_subscript_batch_moles_use_the_mass_owner() -> None:
    weights = composition_wt_pct(_moles(("FeO1.5", "1"), ("SiO2", "1")))
    assert weights is not None
    _kg, mol = composition_kg_and_mol(weights)
    n_each = 1000.0 / (_FEO15_G_MOL + _SIO2_G_MOL)
    assert mol["FeO1.5"] == pytest.approx(n_each, rel=1e-12)
    assert mol["SiO2"] == pytest.approx(n_each, rel=1e-12)

    # Printed Eliezer amounts are mole fractions. The weight owner converts
    # them; the 1 kg batch then returns moles from those masses.
    x_ko = 0.21537
    x_si = 0.78463
    ko_mass = ATOMIC_WEIGHTS_G_PER_MOL["K"] + 0.5 * ATOMIC_WEIGHTS_G_PER_MOL["O"]
    ko_weights = composition_wt_pct(_moles(("KO0.5", "0.21537"), ("SiO2", "0.78463")))
    assert ko_weights is not None
    _kg, ko_mol = composition_kg_and_mol(ko_weights)
    denom = x_ko * ko_mass + x_si * _SIO2_G_MOL
    assert ko_mol["KO0.5"] == pytest.approx(1000.0 * x_ko / denom, rel=1e-12)
    assert ko_mol["SiO2"] == pytest.approx(1000.0 * x_si / denom, rel=1e-12)


def test_oxide_element_inventory_accepts_decimal_subscript() -> None:
    inventory = _oxide_element_inventory(
        {"FeO1.5": _FEO15_G_MOL, "SiO2": _SIO2_G_MOL},
        amount_basis=AmountBasis.MASS_PERCENT,
    )
    assert inventory["Fe"] == pytest.approx(1.0)
    assert inventory["Si"] == pytest.approx(1.0)
    assert inventory["O"] == pytest.approx(3.5)

    moles = _oxide_element_inventory(
        {"FeO1.5": 2.0},
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    assert moles["Fe"] == pytest.approx(2.0)
    assert moles["O"] == pytest.approx(3.0)


def test_composition_kg_and_mol_refuses_both_fail_name() -> None:
    with pytest.raises(UnparseableCompositionComponent) as raised:
        composition_kg_and_mol({"foobar": 100.0})
    assert raised.value.component == "foobar"


def test_mass_percent_both_fail_stays_unparseable_on_the_redox_inventory() -> None:
    identity = replace(
        activity_identity(),
        quantity=Quantity.FE3_FE2_RATIO,
        composition=State.of(
            Composition(
                "oxides",
                (("foobar", Decimal("50")), ("FeO", Decimal("50"))),
                AmountBasis.MASS_PERCENT,
            )
        ),
    )
    row = observation("foobar-redox", "exp-foobar-redox", identity, Decimal("1"))
    with pytest.raises(UnparseableCompositionComponent) as raised:
        _predict_fe_redox(Engine.OPENIMCC, row, identity, "fe-redox")
    assert raised.value.component == "foobar"


def test_openimcc_printed_ko05_compile_residual_does_not_raise() -> None:
    detail = _activity_residual(
        "eliezer-ko05",
        _moles(("KO0.5", "0.21537"), ("SiO2", "0.78463")),
    )
    assert detail.get("component") != "KO0.5"


def test_openimcc_feo15_compile_residual_is_not_a_parser_exception() -> None:
    detail = _activity_residual(
        "feo15-sio2",
        _moles(("FeO1.5", "1"), ("SiO2", "1")),
    )
    assert detail.get("component") != "FeO1.5"


def test_score_store_refuses_one_unparseable_name_and_scores_the_other_row() -> None:
    root = Path(__file__).resolve().parents[2]
    extract = root / "data/literature/extracts/borisov-2018-compilation.yaml"
    migrator = migrate_one_extract(root, extract)
    migrator.finalize()
    result = migrator.result
    good = result.observations[f"{extract.stem}::borisov_2018_table2_row_4"]
    composition = good.identity.composition.value
    bad_components = (("foobar", composition.components[0][1]),) + tuple(
        composition.components[1:]
    )
    bad_id = f"{good.observation_id}::unparseable-foobar"
    bad = replace(
        good,
        observation_id=bad_id,
        identity=replace(
            good.identity,
            composition=State.of(replace(composition, components=bad_components)),
        ),
    )
    context = ScoreContext(
        result.works,
        result.experiments,
        {good.observation_id: good, bad_id: bad},
        origins={
            good.observation_id: extract.name,
            bad_id: extract.name,
        },
        benches=result.benches,
    )
    residuals, _candidates = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
        include_diagnostics=False,
    )
    by_reference: dict[str, list] = {}
    for residual in residuals:
        by_reference.setdefault(residual.reference, []).append(residual)
    assert set(by_reference) == {good.observation_id, bad_id}

    good_residuals = by_reference[good.observation_id]
    assert good_residuals
    assert any(residual.numeric is not None for residual in good_residuals)

    bad_residuals = by_reference[bad_id]
    assert bad_residuals
    assert all(residual.status is ResidualStatus.REFUSED for residual in bad_residuals)
    assert all(residual.numeric is None for residual in bad_residuals)
    assert any(
        residual.refusal is not None
        and residual.refusal.reason is RefusalReason.INVALID_SOURCE
        and residual.refusal.detail.get("reason") == "unparseable_composition_component"
        and residual.refusal.detail.get("component") == "foobar"
        for residual in bad_residuals
    )
    with pytest.raises(UnparseableCompositionComponent) as raised:
        _weight_percent(
            _moles(("foobar", "1"), ("SiO2", "1")),
            None,
            None,
            None,
        )
    assert raised.value.component == "foobar"
