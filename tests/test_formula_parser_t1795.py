import json
from pathlib import Path
from decimal import Decimal

import pytest

from simulator.accounting.exceptions import AccountingError, UnknownSpeciesError
from simulator.accounting import formulas
from simulator.battery.enums import Phase
from simulator.battery.generators import usgs_b2131
from simulator.battery.migrate import _formula_phase_suffix
from simulator.battery.score import parse_species_formula
from simulator.diagnostic_helpers.species_rail import (
    parse_formula_composition,
    parse_formula_elements,
)


ACCEPTED_FORMULA_COUNTS = (
    ("Fe.875S", {"Fe": 0.875, "S": 1.0}),
    ("Fe.90S", {"Fe": 0.90, "S": 1.0}),
    ("Fe.95O", {"Fe": 0.95, "O": 1.0}),
    ("H2S2(g)", {"H": 2.0, "S": 2.0}),
    ("H2SO4(l)", {"H": 2.0, "S": 1.0, "O": 4.0}),
    ("H2SO4(g)", {"H": 2.0, "S": 1.0, "O": 4.0}),
    ("Fe2O3[c]", {"Fe": 2.0, "O": 3.0}),
    ("H2SeO4(aq)", {"H": 2.0, "Se": 1.0, "O": 4.0}),
    ("H3PO4·1/2H2O", {"H": 4.0, "P": 1.0, "O": 4.5}),
    ("CdSO4·8/3H2O", {"Cd": 1.0, "S": 1.0, "O": 20.0 / 3.0, "H": 16.0 / 3.0}),
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
    ("[Al(OH)4]", {"Al": 1.0, "O": 4.0, "H": 4.0}),
)

WEIGHTED_SOURCE_FORMULAS = (
    ("reference-p021-03", "CaFe2o4", {"Ca": 1.0, "Fe": 2.0, "O": 4.0}),
    ("reference-p032-04", "CaMgSio4", {"Ca": 1.0, "Mg": 1.0, "Si": 1.0, "O": 4.0}),
    ("reference-p011-15", "CuFes2", {"Cu": 1.0, "Fe": 1.0, "S": 2.0}),
    ("reference-p012-04", "Fe. 875 s", {"Fe": 0.875, "S": 1.0}),
    ("high-temperature-p190", "Fe.94 7o", {"Fe": 0.947, "O": 1.0}),
    ("reference-p021-13", "MgFe2o4", {"Mg": 1.0, "Fe": 2.0, "O": 4.0}),
    ("reference-p029-05", "Na2so4·10H2o", {"Na": 2.0, "S": 1.0, "O": 14.0, "H": 20.0}),
    ("reference-p037-13", "NaAlSio4", {"Na": 1.0, "Al": 1.0, "Si": 1.0, "O": 4.0}),
    ("reference-p024-04", "Naf", {"Na": 1.0, "F": 1.0}),
    ("reference-p022-01", "ZnFe2o4", {"Zn": 1.0, "Fe": 2.0, "O": 4.0}),
    ("reference-p030-08", "cawo4", {"Ca": 1.0, "W": 1.0, "O": 4.0}),
    ("reference-p015-16", "cao", {"Ca": 1.0, "O": 1.0}),
    ("reference-p029-10", "srso4", {"Sr": 1.0, "S": 1.0, "O": 4.0}),
    ("reference-p009-14", "u", {"U": 1.0}),
    ("reference-p009-15", "v", {"V": 1.0}),
    ("reference-p009-16", "w", {"W": 1.0}),
    ("reference-p013-14", "ws2", {"W": 1.0, "S": 2.0}),
)

PHASE_SUFFIX_CANONICAL = (
    ("aq", "aq", Phase.AQ),
    ("c", "cr", Phase.CR),
    ("cr", "cr", Phase.CR),
    ("g", "g", Phase.G),
    ("gl", "gl", Phase.GLASS),
    ("glass", "gl", Phase.GLASS),
    ("l", "l", Phase.L),
    ("liq", "l", Phase.L),
    ("liquid", "l", Phase.L),
    ("solid", "cr", Phase.CR),
    ("s", "cr", Phase.CR),
    ("gas", "g", Phase.G),
    ("vapor", "g", Phase.G),
)

_B2131_ROOT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/compilations/robie-hemingway-1995-usgs-b2131"
)


def _load_b2131_record(record_id: str) -> dict:
    folder = "summary" if record_id.startswith("reference-") else "records"
    path = _B2131_ROOT / folder / f"{record_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


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


@pytest.mark.parametrize(("record_id", "published", "expected"), WEIGHTED_SOURCE_FORMULAS)
def test_case_repaired_spelling_matches_its_printed_formula_weight(
    record_id: str, published: str, expected: dict[str, float]
) -> None:
    document = _load_b2131_record(record_id)
    assert document["formula_as_published"] == published
    formula, evidence = usgs_b2131.formula_for_record(document)
    assert formula != published
    assert evidence is not None and "mass" in evidence
    _assert_formula_counts(formula, expected)
    assert parse_species_formula(formula) == tuple(sorted(expected.items()))
    printed_weight = usgs_b2131._printed_formula_weight(document)
    assert printed_weight is not None
    parsed_mass = formulas.parse_formula(formula).molar_mass_g_mol
    assert abs(parsed_mass - float(printed_weight)) <= max(
        0.05, float(printed_weight) * 0.0002
    )


def test_bornite_raster_formula_matches_printed_weight() -> None:
    document = _load_b2131_record("high-temperature-p129")
    assert usgs_b2131._printed_formula_weight(document) == Decimal("501.841")
    formula, evidence = usgs_b2131.formula_for_record(document)
    assert formula == "Cu5FeS4"
    assert evidence is not None and "matches printed 501.841" in evidence
    assert dict(formulas.parse_formula(formula).elements) == pytest.approx(
        {"Cu": 5.0, "Fe": 1.0, "S": 4.0}
    )


@pytest.mark.parametrize(
    ("record_id", "published", "parsed_mass", "printed_mass"),
    (
        (
            "reference-p021-04",
            "ca2Fe2  as",
            "266.767595",
            "271.847000",
        ),
        (
            "high-temperature-p184",
            "cao2",
            "72.076000",
            "172.114000",
        ),
    ),
)
def test_mass_mismatched_formula_is_stored_as_nonparsing_refusal(
    record_id: str, published: str, parsed_mass: str, printed_mass: str
) -> None:
    document = _load_b2131_record(record_id)
    assert document["formula_as_published"] == published
    formula, evidence = usgs_b2131.formula_for_record(document)
    assert formula.startswith(f"{published} (formula refusal:")
    assert evidence is not None and "refused" in evidence
    assert parsed_mass in evidence and printed_mass in evidence
    with pytest.raises(UnknownSpeciesError):
        formulas.parse_formula(formula)
    assert parse_species_formula(formula) is None


def test_no_weight_case_repair_and_diamond_name_remain_refused() -> None:
    fe2tio4 = _load_b2131_record("reference-p021-09")
    assert fe2tio4["formula_as_published"] == "Fe2Tio4"
    formula, evidence = usgs_b2131.formula_for_record(fe2tio4)
    assert formula == "Fe2Tio4"
    assert evidence is None
    with pytest.raises(UnknownSpeciesError):
        formulas.parse_formula(formula)

    diamond = _load_b2131_record("reference-p005-16")
    assert diamond["formula_as_published"] == "c"
    formula, reason = usgs_b2131.formula_for_record(diamond)
    assert formula == "c"
    assert reason == formulas.DIAMOND_POLYMORPH_REFUSAL_REASON
    with pytest.raises(UnknownSpeciesError, match="diamond polymorph"):
        formulas.parse_formula(formula, name=diamond["name_as_published"])


@pytest.mark.parametrize(
    "record_id", ("high-temperature-p179", "high-temperature-p340")
)
def test_no_weight_raster_correction_stays_refused(record_id: str) -> None:
    document = _load_b2131_record(record_id)
    published = document["formula_as_published"]
    assert usgs_b2131._printed_formula_weight(document) is None

    formula, evidence = usgs_b2131.formula_for_record(document)

    assert published in formula
    assert evidence is None or "refused" in evidence
    assert parse_species_formula(formula) is None


@pytest.mark.parametrize(
    ("record_id", "published"),
    (
        ("reference-p021-04", "ca2Fe2  as"),
        ("high-temperature-p184", "cao2"),
    ),
)
def test_mutation_probe_mass_gate_removal_reproduces_wrong_p0_formula(
    monkeypatch, record_id: str, published: str
) -> None:
    document = _load_b2131_record(record_id)

    def assert_refused() -> None:
        formula, evidence = usgs_b2131.formula_for_record(document)
        assert formula.startswith(f"{published} (formula refusal:")
        assert evidence is not None and "refused" in evidence
        with pytest.raises(UnknownSpeciesError):
            formulas.parse_formula(formula)
        assert parse_species_formula(formula) is None

    def omit_mass_gate(
        formula: str,
        _printed_weight_g_mol: object,
        *,
        name: str | None = None,
        repaired: bool = False,
    ) -> tuple[str | None, str]:
        body, suffix = formulas._strip_terminal_phase_suffix(formula)
        candidate = formulas._unambiguous_element_case(formulas._clean_formula_text(body))
        if suffix is not None:
            candidate += f"({suffix})"
        return candidate, "mutated mass gate omitted"

    assert_refused()
    monkeypatch.setattr(
        usgs_b2131, "_canonicalize_formula_from_printed_weight", omit_mass_gate
    )
    with pytest.raises(AssertionError):
        assert_refused()


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


def test_diamond_name_is_not_collapsed_to_the_bare_carbon_formula() -> None:
    with pytest.raises(UnknownSpeciesError, match="diamond polymorph"):
        formulas.parse_formula("C", name="diamond")


def test_exact_case_formula_requires_its_printed_weight_when_weight_is_present() -> None:
    formula, reason = formulas._canonicalize_formula_from_printed_weight(
        "CaO2",
        Decimal("172.114"),
        name="CERIANITE",
    )
    assert formula is None
    assert "parsed mass 72.076000 g/mol" in reason
    assert "printed 172.114000 g/mol" in reason


def test_diamond_name_refusal_precedes_an_exact_carbon_mass_match() -> None:
    formula, reason = formulas._canonicalize_formula_from_printed_weight(
        "C",
        Decimal("12.011"),
        name="DIAMOND",
    )
    assert formula is None
    assert reason == formulas.DIAMOND_POLYMORPH_REFUSAL_REASON


@pytest.mark.parametrize(
    "formula",
    ("coo", "caso4", "cs2o", "cu", "H4SiO4o", "ca2•", "s2·", "so4••"),
)
def test_ambiguous_case_and_ocr_spellings_stay_unparsed(formula: str) -> None:
    with pytest.raises(UnknownSpeciesError):
        formulas.parse_formula(formula)


def test_scoring_path_preserves_fractional_formula_counts() -> None:
    assert parse_species_formula("Fe.875S") == (("Fe", 0.875), ("S", 1.0))


def test_species_rail_uses_accounting_decimal_and_phase_rules() -> None:
    assert parse_formula_elements("Fe.947O[c]") == ("Fe", "O")
    assert parse_formula_composition("Fe.947O[c]") == (
        ("Fe", 0.947),
        ("O", 1.0),
    )


@pytest.mark.parametrize(("label", "canonical", "phase"), PHASE_SUFFIX_CANONICAL)
def test_formula_phase_suffix_owner_lifts_parentheses_and_brackets(
    label: str, canonical: str, phase: Phase
) -> None:
    for suffix in (f"({label})", f"[{label}]"):
        formula = f"Fe2O3{suffix}"
        body, parsed_suffix = formulas._strip_terminal_phase_suffix(formula)
        assert body == "Fe2O3"
        assert parsed_suffix == canonical
        normalized, parsed_phase = _formula_phase_suffix(formula, None)
        assert normalized == "Fe2O3"
        assert parsed_phase is not None and parsed_phase.value is phase
