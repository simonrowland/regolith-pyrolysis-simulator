import pytest

from simulator.accounting.exceptions import AccountingError, UnknownSpeciesError
from simulator.accounting import formulas
from simulator.battery.score import parse_species_formula


ACCEPTED_FORMULA_COUNTS = (
    ("Fe.875S", {"Fe": 0.875, "S": 1.0}),
    ("Fe.90S", {"Fe": 0.90, "S": 1.0}),
    ("Fe.95O", {"Fe": 0.95, "O": 1.0}),
    ("H2S2(g)", {"H": 2.0, "S": 2.0}),
    ("H2SO4(l)", {"H": 2.0, "S": 1.0, "O": 4.0}),
    ("H2SO4(g)", {"H": 2.0, "S": 1.0, "O": 4.0}),
    ("H2SeO4(aq)", {"H": 2.0, "Se": 1.0, "O": 4.0}),
    ("H3PO4·1/2H2O", {"H": 4.0, "P": 1.0, "O": 4.5}),
    ("CdSO4·8/3H2O", {"Cd": 1.0, "S": 1.0, "O": 20.0 / 3.0, "H": 16.0 / 3.0}),
    ("CaFe2o4", {"Ca": 1.0, "Fe": 2.0, "O": 4.0}),
    ("Fe. 875 s", {"Fe": 0.875, "S": 1.0}),
    ("Fe.94 7o", {"Fe": 0.947, "O": 1.0}),
    ("CuFes2", {"Cu": 1.0, "Fe": 1.0, "S": 2.0}),
    ("CaMgSio4", {"Ca": 1.0, "Mg": 1.0, "Si": 1.0, "O": 4.0}),
    ("NaAlSio4", {"Na": 1.0, "Al": 1.0, "Si": 1.0, "O": 4.0}),
    ("H₂SO₄", {"H": 2.0, "S": 1.0, "O": 4.0}),
    ("FeO1.5", {"Fe": 1.0, "O": 1.5}),
    ("FeO₁.₅", {"Fe": 1.0, "O": 1.5}),
    ("$NH_{4}OH(aq)$", {"N": 1.0, "H": 5.0, "O": 1.0}),
    (
        "$\\mathrm{CdSO}_{4}\\cdot8/3\\mathrm{H}_{2}\\mathrm{O}(c)$",
        {"Cd": 1.0, "S": 1.0, "O": 20.0 / 3.0, "H": 16.0 / 3.0},
    ),
    (
        "$(NH_{4})_{2}CO_{3}(aq)$",
        {"N": 2.0, "H": 8.0, "C": 1.0, "O": 3.0},
    ),
    ("$PCl_{3}(l)$", {"P": 1.0, "Cl": 3.0}),
    ("$SO_{3}(l)$", {"S": 1.0, "O": 3.0}),
    ("$H_3AsO_3(aq)$", {"H": 3.0, "As": 1.0, "O": 3.0}),
    ("$NH_4HCO_3(aq)$", {"N": 1.0, "H": 5.0, "C": 1.0, "O": 3.0}),
    ("H2Se(aq)", {"H": 2.0, "Se": 1.0}),
    ("H2S(aq)", {"H": 2.0, "S": 1.0}),
    ("HCN(aq)", {"H": 1.0, "C": 1.0, "N": 1.0}),
    ("HCNO(aq)", {"H": 1.0, "C": 1.0, "N": 1.0, "O": 1.0}),
    ("H2N2O2(aq)", {"H": 2.0, "N": 2.0, "O": 2.0}),
    ("H2SO4(aq)", {"H": 2.0, "S": 1.0, "O": 4.0}),
    ("H2CO3(aq)", {"H": 2.0, "C": 1.0, "O": 3.0}),
    ("H2SO3(aq)", {"H": 2.0, "S": 1.0, "O": 3.0}),
    ("Na2B4O7(gl)", {"Na": 2.0, "B": 4.0, "O": 7.0}),
    ("H2S2O8(aq)", {"H": 2.0, "S": 2.0, "O": 8.0}),
    ("H3PO4(aq)", {"H": 3.0, "P": 1.0, "O": 4.0}),
    ("H2AsO2(aq)", {"H": 2.0, "As": 1.0, "O": 2.0}),
    ("$H_3AsO_4(aq)$", {"H": 3.0, "As": 1.0, "O": 4.0}),
    ("H2SeO3(aq)", {"H": 2.0, "Se": 1.0, "O": 3.0}),
    (
        "Ca1.02Na0.14K.01(Al2.18Si6.82O18) · 7.33H2O",
        {
            "Ca": 1.02,
            "Na": 0.14,
            "K": 0.01,
            "Al": 2.18,
            "Si": 6.82,
            "O": 25.33,
            "H": 14.66,
        },
    ),
    ("Fe2Tio4", {"Fe": 2.0, "Ti": 1.0, "O": 4.0}),
    ("Hgf", {"Hg": 1.0, "F": 1.0}),
    ("MgFe2o4", {"Mg": 1.0, "Fe": 2.0, "O": 4.0}),
    ("Na2so4·10H2o", {"Na": 2.0, "S": 1.0, "O": 14.0, "H": 20.0}),
    ("Naf", {"Na": 1.0, "F": 1.0}),
    ("ZnFe2o4", {"Zn": 1.0, "Fe": 2.0, "O": 4.0}),
    ("c", {"C": 1.0}),
    ("ca2Fe2  as", {"Ca": 2.0, "Fe": 2.0, "As": 1.0}),
    ("cao", {"Ca": 1.0, "O": 1.0}),
    ("cao2", {"Ca": 1.0, "O": 2.0}),
    ("cawo4", {"Ca": 1.0, "W": 1.0, "O": 4.0}),
    ("sr2", {"Sr": 2.0}),
    ("srso4", {"Sr": 1.0, "S": 1.0, "O": 4.0}),
    ("u", {"U": 1.0}),
    ("v", {"V": 1.0}),
    ("w", {"W": 1.0}),
    ("ws2", {"W": 1.0, "S": 2.0}),
    ("zn2", {"Zn": 2.0}),
)


def _assert_formula_counts(formula: str, expected: dict[str, float]) -> None:
    try:
        parsed = formulas.parse_formula(formula)
    except AccountingError as exc:
        raise AssertionError(f"{formula} was rejected") from exc
    assert dict(parsed.elements) == pytest.approx(expected)


@pytest.mark.parametrize(("formula", "expected"), ACCEPTED_FORMULA_COUNTS)
def test_formula_parser_accepts_printed_unambiguous_spellings(
    formula: str, expected: dict[str, float]
) -> None:
    _assert_formula_counts(formula, expected)


def test_mutation_probe_detects_a_dropped_formula_spelling(monkeypatch) -> None:
    original_parse = formulas._FormulaParser.parse

    def reject_fe875s(parser: formulas._FormulaParser) -> dict[str, float]:
        if parser.text == "Fe.875S":
            raise UnknownSpeciesError("mutated parser no longer accepts Fe.875S")
        return original_parse(parser)

    monkeypatch.setattr(formulas._FormulaParser, "parse", reject_fe875s)
    with pytest.raises(AssertionError, match=r"Fe\.875S was rejected"):
        _assert_formula_counts(*ACCEPTED_FORMULA_COUNTS[0])


def test_leading_decimal_is_not_a_formula_segment_multiplier() -> None:
    with pytest.raises(UnknownSpeciesError):
        formulas.parse_formula(".5H2O")


@pytest.mark.parametrize(
    "formula",
    ("coo", "caso4", "cs2o", "cu", "H4SiO4o", "ca2•", "s2·", "so4••"),
)
def test_ambiguous_case_and_ocr_spellings_stay_unparsed(formula: str) -> None:
    with pytest.raises(UnknownSpeciesError):
        formulas.parse_formula(formula)


def test_scoring_path_preserves_fractional_formula_counts() -> None:
    assert parse_species_formula("Fe.875S") == (("Fe", 0.875), ("S", 1.0))
