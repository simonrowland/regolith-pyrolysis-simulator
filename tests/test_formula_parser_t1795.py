import pytest

from simulator.accounting.exceptions import AccountingError, UnknownSpeciesError
from simulator.accounting import formulas


ACCEPTED_FRACTIONAL_SUBSCRIPTS = (
    ("Fe.875S", {"Fe": 0.875, "S": 1.0}),
    ("Fe.90S", {"Fe": 0.90, "S": 1.0}),
    ("Fe.95O", {"Fe": 0.95, "O": 1.0}),
)


def _assert_formula_counts(formula: str, expected: dict[str, float]) -> None:
    try:
        parsed = formulas.parse_formula(formula)
    except AccountingError as exc:
        raise AssertionError(f"{formula} was rejected") from exc
    assert dict(parsed.elements) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("formula", "expected"), ACCEPTED_FRACTIONAL_SUBSCRIPTS
)
def test_formula_parser_accepts_printed_leading_decimal_subscripts(
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
        _assert_formula_counts(*ACCEPTED_FRACTIONAL_SUBSCRIPTS[0])


def test_leading_decimal_is_not_a_formula_segment_multiplier() -> None:
    with pytest.raises(UnknownSpeciesError):
        formulas.parse_formula(".5H2O")
