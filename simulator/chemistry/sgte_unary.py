"""SGTE Unary 5.0 (unary50.tdb) loader — native TDB structure, no conversion.

Compilations are engine reference functions, not validation measurements.
This module parses Thermo-Calc TDB statements exactly as published: ELEMENT,
FUNCTION (interval expressions kept as strings plus a parsed polynomial),
PHASE/CONSTITUENT, PARAMETER G/TC/BM/BMAGN. Ambiguities stay on the record;
nothing is typed measured.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

import yaml

from simulator.yaml_cache import load_cached_safe_yaml

ROOT = Path(__file__).resolve().parents[2]
COMPILATION_ROOT = ROOT / "data" / "literature" / "compilations" / "sgte-unary"
SOURCE_TDB = COMPILATION_ROOT / "source" / "unary50.tdb"
MANIFEST_PATH = COMPILATION_ROOT / "manifest.yaml"
RECORDS_DIR = COMPILATION_ROOT / "records"
FEEDSTOCKS_PATH = ROOT / "data" / "feedstocks.yaml"

SCHEMA_VERSION = "literature_compilation.v1"
MANIFEST_SCHEMA = "literature_compilation_manifest.v1"
SOURCE_ID = "sgte-unary-5.0"
ROUND_TRIP_REL_TOL = 1e-9

_NUMBER_RE = re.compile(r"(?:\d+\.\d*|\.\d+|\d+)(?:[Ee][+-]?\d+)?")
_TOKEN_RE = re.compile(
    r"\s+|"
    r"(\*\*)|"
    r"([*()/])|"
    r"(" + _NUMBER_RE.pattern + r")|"
    r"([A-Za-z][A-Za-z0-9_]*)|"
    r"([+-])"
)
_IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_FORMULA_TOKEN_RE = re.compile(r"([A-Z][a-z]?)(\d+(?:\.\d+)?|\.\d+)?")
_PHASE_SUFFIX_RE = re.compile(r"\((?:g|gas|l|liq|s|cr|solid)\)$", re.IGNORECASE)
_INTERVAL_END_RE = re.compile(
    r";\s*(" + _NUMBER_RE.pattern + r")\s*([YyNn])\b"
)
_PARAM_IDENT_RE = re.compile(
    r"^(G|TC|BM|BMAGN)\(([^,]+),([^;]+);(\d+)\)$"
)
_COMMENTED_PARAM_RE = re.compile(r"PARAMETER\s+((?:G|TC|BM|BMAGN)\([^)]+\))")


class SgteUnaryError(ValueError):
    """Published TDB text could not be parsed without guessing."""


class TemperatureOutOfIntervalError(SgteUnaryError):
    """Requested temperature is outside the published interval union."""

    def __init__(self, temperature_K: float, certified_band: tuple[tuple[float, float], ...]):
        self.temperature_K = temperature_K
        self.certified_band = certified_band
        super().__init__(f"T={temperature_K} K outside certified band {certified_band}")


class UnresolvedFunctionError(SgteUnaryError):
    """A required FUNCTION value or definition is missing."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        super().__init__(f"unresolved FUNCTION {symbol}")


@dataclass(frozen=True)
class PolynomialTerm:
    """One summand of a + b T + c T ln T + k T**n + f * FUNCTION."""

    coefficient: float
    t_exponent: int
    lnT_power: int
    function: str | None = None

    def evaluate(self, temperature_K: float, functions: Mapping[str, float]) -> float:
        value = self.coefficient
        if self.t_exponent:
            value *= temperature_K**self.t_exponent
        if self.lnT_power:
            value *= math.log(temperature_K) ** self.lnT_power
        if self.function is not None:
            value *= functions[self.function]
        return value

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "coefficient": self.coefficient,
            "t_exponent": self.t_exponent,
            "lnT_power": self.lnT_power,
        }
        if self.function is not None:
            payload["function"] = self.function
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PolynomialTerm:
        function = payload.get("function")
        return cls(
            coefficient=float(payload["coefficient"]),
            t_exponent=int(payload["t_exponent"]),
            lnT_power=int(payload["lnT_power"]),
            function=None if function is None else str(function),
        )


@dataclass(frozen=True)
class ParsedExpression:
    text: str
    terms: tuple[PolynomialTerm, ...]
    function_names: tuple[str, ...]
    certified_band: tuple[float, float] | None = None

    def evaluate(self, temperature_K: float, functions: Mapping[str, float] | None = None) -> float:
        if self.certified_band is None:
            raise SgteUnaryError("missing certified temperature bounds")
        if not self.certified_band[0] <= temperature_K <= self.certified_band[1]:
            raise TemperatureOutOfIntervalError(temperature_K, (self.certified_band,))
        env = dict(functions or {})
        for name in self.function_names:
            if name not in env:
                raise UnresolvedFunctionError(name)
        return sum(term.evaluate(temperature_K, env) for term in self.terms)

    def as_dict(self) -> dict[str, Any]:
        exponents = sorted({term.t_exponent for term in self.terms})
        return {
            "expression_as_published": self.text,
            "polynomial": {
                "form": "a + b*T + c*T*LN(T) + sum k_n*T**n + sum f_i*FUNCTION_i",
                "exponents": exponents,
                "terms": [term.as_dict() for term in self.terms],
            },
        }


@dataclass(frozen=True)
class TemperatureInterval:
    t_low_as_published: str
    t_high_as_published: str
    t_low: float
    t_high: float
    continuation: str
    expression: ParsedExpression

    def midpoint_K(self) -> float:
        return 0.5 * (self.t_low + self.t_high)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "T_low": {"value": self.t_low, "as_published": self.t_low_as_published},
            "T_high": {"value": self.t_high, "as_published": self.t_high_as_published},
            "continuation": self.continuation,
        }
        payload.update(self.expression.as_dict())
        return payload


@dataclass(frozen=True)
class FunctionDef:
    name: str
    line_start: int
    line_end: int
    intervals: tuple[TemperatureInterval, ...]
    statement_as_published: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_lines": [self.line_start, self.line_end],
            "interval_count": len(self.intervals),
            "intervals": [interval.as_dict() for interval in self.intervals],
        }


@dataclass(frozen=True)
class ElementDef:
    symbol: str
    reference_phase: str
    mass_as_published: str
    h298_minus_h0_as_published: str
    s298_as_published: str
    mass: float
    h298_minus_h0: float
    s298: float
    line_start: int
    line_end: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol_as_published": self.symbol,
            "reference_phase_as_published": self.reference_phase,
            "mass": {"value": self.mass, "as_published": self.mass_as_published},
            "H298_minus_H0": {
                "value": self.h298_minus_h0,
                "as_published": self.h298_minus_h0_as_published,
            },
            "S298": {"value": self.s298, "as_published": self.s298_as_published},
            "source_lines": [self.line_start, self.line_end],
        }


@dataclass(frozen=True)
class PhaseDef:
    name_as_published: str
    parameter_name: str
    type_code: str
    n_sublattices: int
    sites: tuple[str, ...]
    constituents: tuple[tuple[str, ...], ...]
    magnetic: dict[str, Any] | None
    line_start: int
    line_end: int
    constituent_lines: tuple[int, int]

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name_as_published": self.name_as_published,
            "parameter_name": self.parameter_name,
            "type_code_as_published": self.type_code,
            "n_sublattices": self.n_sublattices,
            "sites_as_published": list(self.sites),
            "constituents": [list(subl) for subl in self.constituents],
            "source_lines": [self.line_start, self.line_end],
            "constituent_source_lines": list(self.constituent_lines),
        }
        if self.magnetic is not None:
            payload["magnetic_type_definition"] = self.magnetic
        return payload


@dataclass(frozen=True)
class ParameterDef:
    kind: str
    identifier: str
    phase: str
    constituent: str
    constituent_element: str
    order: int
    line_start: int
    line_end: int
    intervals: tuple[TemperatureInterval, ...]
    statement_as_published: str
    comment_superseded: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "identifier": self.identifier,
            "phase": self.phase,
            "constituent_as_published": self.constituent,
            "order": self.order,
            "source_lines": [self.line_start, self.line_end],
            "interval_count": len(self.intervals),
            "intervals": [interval.as_dict() for interval in self.intervals],
        }
        if self.comment_superseded:
            payload["comment_superseded_as_published"] = list(self.comment_superseded)
        return payload


@dataclass
class Ambiguity:
    code: str
    text: str
    locator: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = {"code": self.code, "text": self.text}
        if self.locator:
            payload["locator"] = self.locator
        return payload


@dataclass
class SpeciesRecord:
    record_id: str
    formula: str
    phase: str
    name_as_published: str
    element: ElementDef | None
    phase_declaration: PhaseDef | None
    g_parameter: ParameterDef | None
    magnetic_parameters: tuple[ParameterDef, ...]
    functions: tuple[FunctionDef, ...]
    ambiguities: tuple[Ambiguity, ...]
    source_sha256: str
    source_path: str

    def coefficient_count(self) -> int:
        count = 0
        if self.g_parameter is not None:
            count += sum(len(interval.expression.terms) for interval in self.g_parameter.intervals)
        for function in self.functions:
            count += sum(len(interval.expression.terms) for interval in function.intervals)
        for parameter in self.magnetic_parameters:
            count += sum(len(interval.expression.terms) for interval in parameter.intervals)
        return count

    def interval_count(self) -> int:
        count = 0
        if self.g_parameter is not None:
            count += len(self.g_parameter.intervals)
        for function in self.functions:
            count += len(function.intervals)
        return count

    def source_lines(self) -> tuple[int, int]:
        lines = []
        if self.element is not None:
            lines.extend([self.element.line_start, self.element.line_end])
        if self.g_parameter is not None:
            lines.extend([self.g_parameter.line_start, self.g_parameter.line_end])
        if self.phase_declaration is not None:
            lines.extend([self.phase_declaration.line_start, self.phase_declaration.line_end])
        for parameter in self.magnetic_parameters:
            lines.extend([parameter.line_start, parameter.line_end])
        for function in self.functions:
            lines.extend([function.line_start, function.line_end])
        if not lines:
            return (0, 0)
        return (min(lines), max(lines))

    def as_dict(self) -> dict[str, Any]:
        compilation_role = {
            "kind": "assessed_thermodynamic_functions",
            "engine_reference_input": True,
            "validation_measurement": False,
            "scoring_eligible": False,
            "battery_refusal": "gibbs_table_not_runtime_observable",
            "circularity_warning": "Do not validate an engine against a compilation it consumes.",
        }
        start, end = self.source_lines()
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "source_id": SOURCE_ID,
            "record_id": self.record_id,
            "formula": self.formula,
            "phase": self.phase,
            "name_as_published": self.name_as_published,
            "compilation_role": compilation_role,
            "source": {
                "database": "SGTE Pure Element Database (UNARY)",
                "version": "5.0",
                "date": "2009-06-02",
                "official_url": "https://www.sgte.net/en/free-pure-elements-database",
                "licence": (
                    "This database can only be used for extracting data for "
                    "assessment work or to tabulate or plot data for the pure elements."
                ),
                "citation": (
                    "Dinsdale, A. T. (1991). SGTE data for pure elements. "
                    "CALPHAD 15, 317-425; SGTE Unary v5.0 (2 June 2009)."
                ),
                "path": self.source_path,
                "sha256": self.source_sha256,
                "lines": [start, end],
            },
            "coefficient_count": self.coefficient_count(),
            "interval_count": self.interval_count(),
            "ambiguity_count": len(self.ambiguities),
            "ambiguities": [item.as_dict() for item in self.ambiguities],
        }
        if self.element is not None:
            payload["element"] = self.element.as_dict()
        if self.phase_declaration is not None:
            payload["phase_declaration"] = self.phase_declaration.as_dict()
        if self.g_parameter is not None:
            payload["g_parameter"] = self.g_parameter.as_dict()
        if self.magnetic_parameters:
            payload["magnetic_parameters"] = [item.as_dict() for item in self.magnetic_parameters]
        if self.functions:
            payload["functions"] = [item.as_dict() for item in self.functions]
        return payload


@dataclass
class ParsedDatabase:
    source_path: str
    source_sha256: str
    elements: dict[str, ElementDef]
    functions: dict[str, FunctionDef]
    phases: dict[str, PhaseDef]
    parameters: list[ParameterDef]
    species: dict[str, str]
    extras: list[dict[str, Any]]
    commented_parameters: list[dict[str, Any]]
    ambiguities: list[Ambiguity]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def formula_from_tdb_symbol(symbol: str) -> str:
    if symbol in {"/-", "VA", "N2", "O2"}:
        return symbol
    if not symbol:
        return symbol
    return symbol[0].upper() + symbol[1:].lower()


def parameter_phase_name(phase_as_published: str) -> str:
    if phase_as_published.endswith(":L"):
        return phase_as_published[: -len(":L")]
    return phase_as_published


def _split_comment(line: str) -> tuple[str, str]:
    if "$" not in line:
        return line, ""
    code, _, comment = line.partition("$")
    return code, comment


def _iter_statements(text: str) -> Iterator[tuple[int, int, str, str]]:
    """Yield (line_start, line_end, statement, joined_comments) split on '!'."""

    pieces: list[tuple[int, str, str]] = []
    for line_no, raw in enumerate(text.splitlines(), 1):
        code, comment = _split_comment(raw)
        if code.strip() or comment.strip():
            pieces.append((line_no, code, comment))

    buf_code: list[tuple[int, str]] = []
    buf_comment: list[str] = []
    start_line: int | None = None
    for line_no, code, comment in pieces:
        if comment.strip():
            buf_comment.append(comment.strip())
        if not code.strip() and not buf_code:
            continue
        if start_line is None and code.strip():
            start_line = line_no
        if code.strip():
            buf_code.append((line_no, code))
        joined = "".join(chunk for _, chunk in buf_code)
        if "!" not in joined:
            continue
        while "!" in "".join(chunk for _, chunk in buf_code):
            acc = ""
            end_line = start_line or line_no
            consumed = 0
            statement = None
            for idx, (piece_line, chunk) in enumerate(buf_code):
                if "!" in chunk:
                    before, _, after = chunk.partition("!")
                    acc += before
                    statement = acc
                    end_line = piece_line
                    leftover: list[tuple[int, str]] = []
                    if after.strip():
                        leftover.append((piece_line, after))
                    leftover.extend(buf_code[idx + 1 :])
                    buf_code = leftover
                    consumed = 1
                    break
                acc += chunk
            if statement is None:
                break
            yield (start_line or end_line, end_line, statement.strip(), " | ".join(buf_comment))
            buf_comment = []
            start_line = buf_code[0][0] if buf_code else None
            if not consumed:
                break


def _first_word(statement: str) -> tuple[str, str]:
    parts = statement.split(None, 1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0].upper(), ""
    return parts[0].upper(), parts[1]


def parse_tdb_expression(text: str) -> ParsedExpression:
    compact = " ".join(text.split())
    if not compact:
        raise SgteUnaryError("empty TDB expression")
    tokens = _tokenize_expression(compact)
    parser = _ExpressionParser(tokens, compact)
    terms = parser.parse()
    names = tuple(sorted({term.function for term in terms if term.function}))
    return ParsedExpression(text=compact, terms=tuple(terms), function_names=names)


def _tokenize_expression(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            raise SgteUnaryError(f"unrecognized TDB expression token at {text[pos:]!r}")
        pos = match.end()
        if match.group(0).isspace() or match.lastindex is None:
            continue
        if match.group(1):
            tokens.append(("POWER", match.group(1)))
        elif match.group(2):
            symbol = match.group(2)
            name = {"*": "STAR", "(": "LPAREN", ")": "RPAREN", "/": "SLASH"}[symbol]
            tokens.append((name, symbol))
        elif match.group(3):
            tokens.append(("NUMBER", match.group(3)))
        elif match.group(4):
            tokens.append(("IDENT", match.group(4)))
        else:
            tokens.append(("SIGN", match.group(5)))
    tokens.append(("EOF", ""))
    return tokens


@dataclass
class _TermAcc:
    coefficient: float = 1.0
    t_exponent: int = 0
    lnT_power: int = 0
    function: str | None = None

    def mul(self, other: _TermAcc) -> _TermAcc:
        if self.function and other.function:
            raise SgteUnaryError(f"product of functions {self.function}*{other.function}")
        return _TermAcc(
            coefficient=self.coefficient * other.coefficient,
            t_exponent=self.t_exponent + other.t_exponent,
            lnT_power=self.lnT_power + other.lnT_power,
            function=self.function or other.function,
        )

    def pow_int(self, exponent: int) -> _TermAcc:
        if self.function is not None or self.lnT_power:
            raise SgteUnaryError("cannot raise function or LN(T) term to a power")
        if self.t_exponent == 0 and abs(self.coefficient) != 0:
            # constant ** n
            return _TermAcc(coefficient=self.coefficient**exponent)
        if self.coefficient not in {1.0, -1.0} and exponent not in {0, 1}:
            # (k * T^a)**n = k^n * T^(a n) — allowed for numeric k
            return _TermAcc(
                coefficient=self.coefficient**exponent,
                t_exponent=self.t_exponent * exponent,
            )
        return _TermAcc(
            coefficient=self.coefficient**exponent if exponent != 1 else self.coefficient,
            t_exponent=self.t_exponent * exponent,
        )

    def to_term(self) -> PolynomialTerm:
        return PolynomialTerm(
            coefficient=self.coefficient,
            t_exponent=self.t_exponent,
            lnT_power=self.lnT_power,
            function=self.function,
        )


class _ExpressionParser:
    def __init__(self, tokens: list[tuple[str, str]], source: str) -> None:
        self.tokens = tokens
        self.source = source
        self.index = 0

    def peek(self) -> tuple[str, str]:
        return self.tokens[self.index]

    def accept(self, *kinds: str) -> tuple[str, str] | None:
        kind, value = self.peek()
        if kind in kinds:
            self.index += 1
            return kind, value
        return None

    def expect(self, *kinds: str) -> tuple[str, str]:
        got = self.accept(*kinds)
        if got is None:
            kind, value = self.peek()
            raise SgteUnaryError(f"expected {kinds} in {self.source!r}, got {kind} {value!r}")
        return got

    def parse(self) -> list[PolynomialTerm]:
        terms = self._add()
        self.expect("EOF")
        collapsed: dict[tuple[int, int, str | None], float] = {}
        order: list[tuple[int, int, str | None]] = []
        for acc in terms:
            key = (acc.t_exponent, acc.lnT_power, acc.function)
            if key not in collapsed:
                collapsed[key] = 0.0
                order.append(key)
            collapsed[key] += acc.coefficient
        return [
            PolynomialTerm(coefficient=collapsed[key], t_exponent=key[0], lnT_power=key[1], function=key[2])
            for key in order
            if collapsed[key] != 0.0 or key == (0, 0, None) and len(order) == 1
        ]

    def _add(self) -> list[_TermAcc]:
        terms = self._mul()
        while True:
            sign = self.accept("SIGN")
            if sign is None:
                break
            right = self._mul()
            if sign[1] == "-":
                for item in right:
                    item.coefficient *= -1.0
            terms.extend(right)
        return terms

    def _mul(self) -> list[_TermAcc]:
        terms = self._power()
        while self.accept("STAR"):
            right = self._power()
            terms = [left.mul(right_term) for left in terms for right_term in right]
        if self.peek()[0] == "SLASH":
            raise SgteUnaryError(f"division is not in the SGTE unary polynomial form: {self.source!r}")
        return terms

    def _power(self) -> list[_TermAcc]:
        base = self._unary()
        if self.accept("POWER"):
            exponent_terms = self._unary()
            if len(exponent_terms) != 1 or exponent_terms[0].t_exponent or exponent_terms[0].lnT_power or exponent_terms[0].function:
                raise SgteUnaryError(f"non-integer power in {self.source!r}")
            exponent = exponent_terms[0].coefficient
            if abs(exponent - round(exponent)) > 1e-12:
                raise SgteUnaryError(f"non-integer power {exponent} in {self.source!r}")
            return [item.pow_int(int(round(exponent))) for item in base]
        return base

    def _unary(self) -> list[_TermAcc]:
        sign = self.accept("SIGN")
        if sign is None:
            return self._primary()
        terms = self._unary()
        if sign[1] == "-":
            for item in terms:
                item.coefficient *= -1.0
        return terms

    def _primary(self) -> list[_TermAcc]:
        if self.accept("LPAREN"):
            terms = self._add()
            self.expect("RPAREN")
            return terms
        number = self.accept("NUMBER")
        if number is not None:
            return [_TermAcc(coefficient=float(number[1]))]
        ident = self.accept("IDENT")
        if ident is None:
            kind, value = self.peek()
            raise SgteUnaryError(f"unexpected {kind} {value!r} in {self.source!r}")
        name = ident[1]
        if name == "LN":
            self.expect("LPAREN")
            argument = self._add()
            self.expect("RPAREN")
            if len(argument) != 1 or argument[0].function or argument[0].lnT_power:
                raise SgteUnaryError(f"LN() argument is not T in {self.source!r}")
            if argument[0].t_exponent == 1 and argument[0].coefficient == 1.0:
                return [_TermAcc(lnT_power=1)]
            if argument[0].t_exponent == 0 and argument[0].coefficient != 0:
                # LN(constant) — not expected
                return [_TermAcc(coefficient=math.log(argument[0].coefficient))]
            raise SgteUnaryError(f"LN() argument is not T in {self.source!r}")
        if name == "T":
            return [_TermAcc(t_exponent=1)]
        return [_TermAcc(function=name)]


def evaluate_expression_string(
    text: str,
    temperature_K: float,
    functions: Mapping[str, float] | None = None,
) -> float:
    """Evaluate a TDB expression string independently of the polynomial parser."""

    compact = " ".join(text.split())
    python_src = re.sub(r"\bLN\s*\(", "log(", compact)
    names = sorted({match.group(4) for match in _TOKEN_RE.finditer(compact)
                    if match.group(4) is not None} - {"T", "LN"})
    env: dict[str, Any] = {"T": float(temperature_K), "log": math.log}
    for name in names:
        if functions is None or name not in functions:
            raise UnresolvedFunctionError(name)
        env[name] = float(functions[name])
    try:
        value = eval(python_src, {"__builtins__": {}}, env)  # noqa: S307 — closed env, TDB arithmetic only
    except Exception as exc:  # pragma: no cover - parse failures surface as ambiguities
        raise SgteUnaryError(f"cannot evaluate TDB expression {compact!r}: {exc}") from exc
    return float(value)


def evaluate_function(name: str, temperature_K: float, functions: Mapping[str, Mapping[str, Any]]) -> float:
    """Select the first published interval containing T and resolve dependencies."""
    if name not in functions:
        raise UnresolvedFunctionError(name)
    expressions = [expression_from_interval_dict(interval) for interval in functions[name]["intervals"]]
    for expression in expressions:
        low, high = expression.certified_band
        if low <= temperature_K <= high:
            values = {ref: evaluate_function(ref, temperature_K, functions) for ref in expression.function_names}
            return expression.evaluate(temperature_K, values)
    raise TemperatureOutOfIntervalError(temperature_K, tuple(expr.certified_band for expr in expressions))


def function_round_trip_ok(
    expression: ParsedExpression, temperature_K: float, functions: Mapping[str, Mapping[str, Any]],
) -> tuple[bool, float, float]:
    values = {name: evaluate_function(name, temperature_K, functions) for name in expression.function_names}
    parsed_value = expression.evaluate(temperature_K, values)
    string_value = evaluate_expression_string(expression.text, temperature_K, values)
    scale = max(abs(parsed_value), abs(string_value))
    if scale == 0.0:
        return True, parsed_value, string_value
    ok = abs(parsed_value - string_value) <= ROUND_TRIP_REL_TOL * scale
    return ok, parsed_value, string_value


def _parse_intervals(t_low_token: str, body: str, locator: str) -> tuple[TemperatureInterval, ...]:
    rest = body.strip()
    t_low_as_published = t_low_token
    t_low = float(t_low_token)
    intervals: list[TemperatureInterval] = []
    while True:
        match = _INTERVAL_END_RE.search(rest)
        if match is None:
            raise SgteUnaryError(f"{locator}: missing ; T Y/N interval terminator in {rest!r}")
        expr_text = rest[: match.start()].strip()
        t_high_as_published = match.group(1)
        continuation = match.group(2).upper()
        t_high = float(t_high_as_published)
        expression = replace(parse_tdb_expression(expr_text), certified_band=(t_low, t_high))
        intervals.append(
            TemperatureInterval(
                t_low_as_published=t_low_as_published,
                t_high_as_published=t_high_as_published,
                t_low=t_low,
                t_high=t_high,
                continuation=continuation,
                expression=expression,
            )
        )
        rest = rest[match.end() :].strip()
        t_low_as_published = t_high_as_published
        t_low = t_high
        if continuation == "N":
            if rest:
                raise SgteUnaryError(f"{locator}: trailing text after N: {rest!r}")
            break
    return tuple(intervals)


def _parse_parameter_identifier(identifier: str) -> tuple[str, str, str, str, int]:
    match = _PARAM_IDENT_RE.match(identifier)
    if match is None:
        raise SgteUnaryError(f"unrecognized PARAMETER identifier {identifier!r}")
    kind, phase, constituent, order = match.group(1), match.group(2), match.group(3), int(match.group(4))
    element = constituent.split(":", 1)[0]
    return kind, phase, constituent, element, order


def _parse_function_or_parameter_body(head: str, locator: str) -> tuple[str, str, tuple[TemperatureInterval, ...]]:
    """Split '<name> <Tlow> <intervals>' or '<IDENT> <Tlow> <intervals>'."""

    tokens = head.split(None, 2)
    if len(tokens) < 3:
        raise SgteUnaryError(f"{locator}: expected name, T_low, expression; got {head!r}")
    name, t_low, body = tokens[0], tokens[1], tokens[2]
    if _NUMBER_RE.fullmatch(t_low) is None:
        raise SgteUnaryError(f"{locator}: T_low is not a number: {t_low!r}")
    return name, t_low, _parse_intervals(t_low, body, locator)


def _parse_constituents(body: str) -> tuple[tuple[str, ...], ...]:
    # CONSTITUENT NAME : a,b : c : !
    _, rest = body.split(None, 1)
    slots = [slot.strip() for slot in rest.split(":")]
    slots = [slot for slot in slots if slot != ""]
    sublattices: list[tuple[str, ...]] = []
    for slot in slots:
        members = tuple(member.strip() for member in slot.split(",") if member.strip())
        sublattices.append(members)
    return tuple(sublattices)


def parse_tdb(text: str, *, source_path: str, source_sha256: str) -> ParsedDatabase:
    elements: dict[str, ElementDef] = {}
    functions: dict[str, FunctionDef] = {}
    phases: dict[str, PhaseDef] = {}
    parameters: list[ParameterDef] = []
    species: dict[str, str] = {}
    extras: list[dict[str, Any]] = []
    ambiguities: list[Ambiguity] = []
    commented_parameters: list[dict[str, Any]] = []
    pending_magnetic: dict[str, dict[str, Any]] = {}
    pending_constituent: dict[str, tuple[tuple[tuple[str, ...], ...], tuple[int, int]]] = {}

    for line_no, raw in enumerate(text.splitlines(), 1):
        _, comment = _split_comment(raw)
        if not comment:
            continue
        match = _COMMENTED_PARAM_RE.search(comment)
        if match:
            commented_parameters.append(
                {
                    "line": line_no,
                    "identifier": match.group(1),
                    "comment_as_published": comment.strip(),
                }
            )

    for start, end, statement, comments in _iter_statements(text):
        keyword, rest = _first_word(statement)
        if keyword == "ELEMENT":
            fields = rest.split()
            if len(fields) != 5:
                raise SgteUnaryError(f"line {start}: ELEMENT expected 5 fields, got {fields!r}")
            symbol, ref_phase, mass_s, h_s, s_s = fields
            elements[symbol] = ElementDef(
                symbol=symbol,
                reference_phase=ref_phase,
                mass_as_published=mass_s,
                h298_minus_h0_as_published=h_s,
                s298_as_published=s_s,
                mass=float(mass_s),
                h298_minus_h0=float(h_s),
                s298=float(s_s),
                line_start=start,
                line_end=end,
            )
        elif keyword == "FUNCTION":
            name, _t_low, intervals = _parse_function_or_parameter_body(rest, f"FUNCTION line {start}")
            if name in functions:
                raise SgteUnaryError(f"line {start}: duplicate FUNCTION {name}")
            functions[name] = FunctionDef(
                name=name,
                line_start=start,
                line_end=end,
                intervals=intervals,
                statement_as_published=statement,
            )
        elif keyword == "PARAMETER":
            ident, t_low, intervals = _parse_function_or_parameter_body(rest, f"PARAMETER line {start}")
            kind, phase, constituent, element, order = _parse_parameter_identifier(ident)
            superseded = tuple(
                item["comment_as_published"]
                for item in commented_parameters
                if item["identifier"] == ident
            )
            parameters.append(
                ParameterDef(
                    kind=kind,
                    identifier=ident,
                    phase=phase,
                    constituent=constituent,
                    constituent_element=element,
                    order=order,
                    line_start=start,
                    line_end=end,
                    intervals=intervals,
                    statement_as_published=statement,
                    comment_superseded=superseded,
                )
            )
        elif keyword == "PHASE":
            tokens = rest.split()
            if len(tokens) < 3:
                raise SgteUnaryError(f"line {start}: PHASE too short: {rest!r}")
            name_as_published, type_code, n_subl_s, *sites = tokens
            phase_key = parameter_phase_name(name_as_published)
            magnetic = pending_magnetic.get(name_as_published) or pending_magnetic.get(phase_key)
            phases[phase_key] = PhaseDef(
                name_as_published=name_as_published,
                parameter_name=phase_key,
                type_code=type_code,
                n_sublattices=int(n_subl_s),
                sites=tuple(sites),
                constituents=tuple(),
                magnetic=magnetic,
                line_start=start,
                line_end=end,
                constituent_lines=(start, end),
            )
        elif keyword == "CONSTITUENT":
            name_token = rest.split(None, 1)[0]
            phase_key = parameter_phase_name(name_token)
            constituents = _parse_constituents(rest)
            pending_constituent[phase_key] = (constituents, (start, end))
            if phase_key in phases:
                phase = phases[phase_key]
                phases[phase_key] = PhaseDef(
                    name_as_published=phase.name_as_published,
                    parameter_name=phase.parameter_name,
                    type_code=phase.type_code,
                    n_sublattices=phase.n_sublattices,
                    sites=phase.sites,
                    constituents=constituents,
                    magnetic=phase.magnetic,
                    line_start=phase.line_start,
                    line_end=phase.line_end,
                    constituent_lines=(start, end),
                )
        elif keyword == "TYPE_DEFINITION":
            extras.append({"kind": "TYPE_DEFINITION", "lines": [start, end], "text": rest})
            magnetic_match = re.search(
                r"GES\s+A_P_D\s+(\S+)\s+MAGNETIC\s+(\S+)\s+(\S+)",
                rest,
            )
            if magnetic_match:
                pending_magnetic[magnetic_match.group(1)] = {
                    "AF_as_published": magnetic_match.group(2),
                    "p_as_published": magnetic_match.group(3),
                    "AF": float(magnetic_match.group(2)),
                    "p": float(magnetic_match.group(3)),
                    "source_lines": [start, end],
                    "text_as_published": rest.strip(),
                }
        elif keyword == "SPECIES":
            fields = rest.split()
            if len(fields) != 2:
                raise SgteUnaryError(f"line {start}: SPECIES expected 2 fields, got {fields!r}")
            species[fields[0]] = fields[1]
        elif keyword in {"DEFINE_SYSTEM_DEFAULT", "DEFAULT_COMMAND"}:
            extras.append({"kind": keyword, "lines": [start, end], "text": rest})
        else:
            ambiguities.append(
                Ambiguity(
                    code="unrecognized_statement",
                    text=f"keyword {keyword!r} was not classified",
                    locator={"lines": [start, end], "statement": statement[:120]},
                )
            )

    for phase in phases.values():
        if len(phase.constituents) != phase.n_sublattices:
            raise SgteUnaryError(f"{phase.name_as_published}: constituent group count differs from PHASE declaration")

    for item in commented_parameters:
        ambiguities.append(
            Ambiguity(
                code="commented_parameter_superseded",
                text=(
                    "A PARAMETER identifier appears in a $ comment as well as a live "
                    "statement; the comment is kept and the live statement is ingested."
                ),
                locator=item,
            )
        )

    phase_names = {phase.name_as_published for phase in phases.values()} | set(phases)
    for element in elements.values():
        if element.symbol in {"/-", "VA"}:
            continue
        if element.reference_phase not in phase_names:
            ambiguities.append(
                Ambiguity(
                    code="element_reference_phase_not_a_phase_name",
                    text=(
                        f"ELEMENT {element.symbol} reference phase "
                        f"{element.reference_phase!r} is not a PHASE name as declared"
                    ),
                    locator={"symbol": element.symbol, "line": element.line_start},
                )
            )

    bm_kinds = {parameter.kind for parameter in parameters if parameter.kind in {"BM", "BMAGN"}}
    if len(bm_kinds) > 1:
        ambiguities.append(
            Ambiguity(
                code="magnetic_parameter_identifier_bm_vs_bmagn",
                text=(
                    "Magnetic moment parameters are published as both BM(...) and "
                    "BMAGN(...); identifiers are kept as published and not unified."
                ),
                locator={"kinds": sorted(bm_kinds)},
            )
        )

    if "LIQUID" in phases and phases["LIQUID"].name_as_published != "LIQUID":
        ambiguities.append(
            Ambiguity(
                code="liquid_phase_declared_with_type_suffix",
                text=(
                    f"PHASE is declared {phases['LIQUID'].name_as_published!r} while "
                    "PARAMETER identifiers use LIQUID"
                ),
                locator={"name_as_published": phases["LIQUID"].name_as_published},
            )
        )

    return ParsedDatabase(
        source_path=source_path,
        source_sha256=source_sha256,
        elements=elements,
        functions=functions,
        phases=phases,
        parameters=parameters,
        species=species,
        extras=extras,
        commented_parameters=commented_parameters,
        ambiguities=ambiguities,
    )


def load_tdb(path: Path | None = None) -> ParsedDatabase:
    tdb_path = path or SOURCE_TDB
    payload = tdb_path.read_text(encoding="utf-8")
    relative = tdb_path.as_posix()
    try:
        relative = tdb_path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        relative = str(tdb_path)
    return parse_tdb(payload, source_path=relative, source_sha256=sha256_file(tdb_path))


_FUNCTION_PREFIX_PHASE = {
    "GLIQ": "LIQUID",
    "GBCC": "BCC_A2",
    "GFCC": "FCC_A1",
    "GHCP": "HCP_A3",
    "GDHCP": "DHCP",
    "GHSER": None,
}


def _function_element_and_prefix(name: str, symbols: Iterable[str]) -> tuple[str | None, str | None]:
    symbol_set = {symbol for symbol in symbols if symbol not in {"/-", "VA"}}
    for prefix in sorted(_FUNCTION_PREFIX_PHASE, key=len, reverse=True):
        if name.startswith(prefix):
            symbol = name[len(prefix) :]
            if symbol in symbol_set:
                return symbol, prefix
    for symbol in sorted(symbol_set, key=len, reverse=True):
        if name.endswith(symbol) and len(name) > len(symbol):
            return symbol, name[: -len(symbol)]
    return None, None


def _collect_function_tree(name: str, functions: Mapping[str, FunctionDef], seen: set[str]) -> list[FunctionDef]:
    if name in seen or name not in functions:
        return []
    seen.add(name)
    collected = [functions[name]]
    for interval in functions[name].intervals:
        for ref in interval.expression.function_names:
            collected.extend(_collect_function_tree(ref, functions, seen))
    return collected


def _element_only_record_id(symbol: str, reference_phase: str) -> str:
    if symbol == "/-":
        return "ELECTRON_GAS"
    return f"{symbol}-{reference_phase}"


def build_records(database: ParsedDatabase) -> list[SpeciesRecord]:
    by_key: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"g": None, "magnetic": []})
    for parameter in database.parameters:
        key = (parameter.constituent_element, parameter.phase)
        if parameter.kind == "G":
            if by_key[key]["g"] is not None:
                # Two live G parameters for one (element, phase) — keep both via ambiguity later.
                by_key[key]["magnetic"].append(parameter)
            else:
                by_key[key]["g"] = parameter
        else:
            by_key[key]["magnetic"].append(parameter)

    records: list[SpeciesRecord] = []

    for (symbol, phase_name), bundle in sorted(by_key.items()):
        g_parameter: ParameterDef | None = bundle["g"]
        magnetic = tuple(bundle["magnetic"])
        element = database.elements.get(symbol)
        phase_decl = database.phases.get(phase_name)
        formula = formula_from_tdb_symbol(symbol)
        record_id = f"{symbol}-{phase_name}"
        name_as_published = f"{symbol} {phase_name}"
        local_ambiguities: list[Ambiguity] = []

        if g_parameter is None and magnetic:
            local_ambiguities.append(
                Ambiguity(
                    code="magnetic_without_g",
                    text=f"{record_id} has TC/BM parameters but no G parameter",
                    locator={"record_id": record_id},
                )
            )
        if g_parameter is not None and g_parameter.comment_superseded:
            local_ambiguities.append(
                Ambiguity(
                    code="commented_parameter_superseded",
                    text="Live PARAMETER replaced a commented earlier expression; both kept as published.",
                    locator={
                        "identifier": g_parameter.identifier,
                        "comment_superseded_as_published": list(g_parameter.comment_superseded),
                    },
                )
            )
        if (
            element is not None
            and (
                (symbol == "NP" and phase_name == "ORTHO_AC" and element.reference_phase == "ORTHORHOMBIC_AC")
                or (symbol == "SM" and phase_name == "RHOMB_C19" and element.reference_phase == "RHOMBOHEDRAL_SM")
            )
        ):
            local_ambiguities.append(
                Ambiguity(
                    code="element_reference_phase_not_a_phase_name",
                    text=(
                        f"ELEMENT {symbol} lists reference phase {element.reference_phase!r}; "
                        f"this record uses PARAMETER phase {phase_name!r}"
                    ),
                    locator={"symbol": symbol, "phase": phase_name},
                )
            )
        if symbol in database.species:
            local_ambiguities.append(
                Ambiguity(
                    code="species_not_element",
                    text=f"{symbol} is declared SPECIES {database.species[symbol]} rather than ELEMENT",
                    locator={"species": symbol},
                )
            )

        functions: list[FunctionDef] = []
        seen: set[str] = set()
        if g_parameter is not None:
            for interval in g_parameter.intervals:
                for name in interval.expression.function_names:
                    functions.extend(_collect_function_tree(name, database.functions, seen))

        # Attach ELEMENT metadata to every phase record of that element.
        records.append(
            SpeciesRecord(
                record_id=record_id,
                formula=formula,
                phase=phase_name,
                name_as_published=name_as_published,
                element=element,
                phase_declaration=phase_decl,
                g_parameter=g_parameter,
                magnetic_parameters=magnetic,
                functions=tuple(functions),
                ambiguities=tuple(local_ambiguities),
                source_sha256=database.source_sha256,
                source_path=database.source_path,
            )
        )

    used_function_names = {fn.name for rec in records for fn in rec.functions}
    record_keys = {(rec.g_parameter.constituent_element, rec.phase) for rec in records if rec.g_parameter is not None}
    record_keys.update((rec.element.symbol, rec.phase) for rec in records if rec.element is not None)
    for name, function in sorted(database.functions.items()):
        if name in used_function_names:
            continue
        symbol, prefix = _function_element_and_prefix(name, database.elements)
        if symbol is None or prefix is None:
            database.ambiguities.append(
                Ambiguity(
                    code="function_not_attached_to_element_phase",
                    text=f"FUNCTION {name} is not referenced by any PARAMETER G and could not be mapped",
                    locator={"function": name, "lines": [function.line_start, function.line_end]},
                )
            )
            continue
        if prefix == "GHSER":
            phase_name = parameter_phase_name(database.elements[symbol].reference_phase)
        else:
            phase_name = _FUNCTION_PREFIX_PHASE.get(prefix)
        if not phase_name:
            database.ambiguities.append(
                Ambiguity(
                    code="function_not_attached_to_element_phase",
                    text=f"FUNCTION {name} prefix {prefix!r} is not a known phase mapping",
                    locator={"function": name, "prefix": prefix, "symbol": symbol},
                )
            )
            continue
        key = (symbol, phase_name)
        if key in record_keys:
            continue
        record_keys.add(key)
        seen: set[str] = set()
        functions = _collect_function_tree(name, database.functions, seen)
        used_function_names.update(fn.name for fn in functions)
        records.append(
            SpeciesRecord(
                record_id=f"{symbol}-{phase_name}",
                formula=formula_from_tdb_symbol(symbol),
                phase=phase_name,
                name_as_published=f"{symbol} {phase_name}",
                element=database.elements.get(symbol),
                phase_declaration=database.phases.get(phase_name),
                g_parameter=None,
                magnetic_parameters=tuple(),
                functions=tuple(functions),
                ambiguities=(
                    Ambiguity(
                        code="function_without_g_parameter",
                        text=(
                            f"FUNCTION {name} is published for {symbol} {phase_name} but no "
                            "PARAMETER G was published (header notes data removed for B BCC_A2/"
                            "FCC_A1/HCP_A3). The function is ingested; it is not silently dropped."
                        ),
                        locator={"function": name, "lines": [function.line_start, function.line_end]},
                    ),
                ),
                source_sha256=database.source_sha256,
                source_path=database.source_path,
            )
        )

    leftover = sorted(set(database.functions) - used_function_names)
    for name in leftover:
        function = database.functions[name]
        database.ambiguities.append(
            Ambiguity(
                code="function_not_attached_to_element_phase",
                text=f"FUNCTION {name} was not attached to any (element, phase) record",
                locator={"function": name, "lines": [function.line_start, function.line_end]},
            )
        )

    for symbol, element in database.elements.items():
        has_param = any(key[0] == symbol for key in by_key) or any(
            rec.element is not None and rec.element.symbol == symbol for rec in records
        )
        if has_param:
            continue
        records.append(
            SpeciesRecord(
                record_id=_element_only_record_id(symbol, element.reference_phase),
                formula=formula_from_tdb_symbol(symbol),
                phase=element.reference_phase,
                name_as_published=f"{symbol} {element.reference_phase}",
                element=element,
                phase_declaration=None,
                g_parameter=None,
                magnetic_parameters=tuple(),
                functions=tuple(),
                ambiguities=(
                    Ambiguity(
                        code="element_without_g_parameter",
                        text="ELEMENT is declared but no PARAMETER G was published for it",
                        locator={"symbol": symbol, "line": element.line_start},
                    ),
                ),
                source_sha256=database.source_sha256,
                source_path=database.source_path,
            )
        )

    records.sort(key=lambda rec: rec.record_id)
    return records


def record_to_yaml(record: SpeciesRecord) -> str:
    return yaml.safe_dump(record.as_dict(), sort_keys=False, allow_unicode=True, width=120)


def manifest_entry(record: SpeciesRecord, record_path: str) -> dict[str, Any]:
    start, end = record.source_lines()
    return {
        "record_id": record.record_id,
        "formula": record.formula,
        "phase": record.phase,
        "name_as_published": record.name_as_published,
        "source": {
            "database": "SGTE Pure Element Database (UNARY)",
            "version": "5.0",
            "date": "2009-06-02",
            "official_url": "https://www.sgte.net/en/free-pure-elements-database",
            "licence": (
                "This database can only be used for extracting data for "
                "assessment work or to tabulate or plot data for the pure elements."
            ),
            "citation": (
                "Dinsdale, A. T. (1991). SGTE data for pure elements. "
                "CALPHAD 15, 317-425; SGTE Unary v5.0 (2 June 2009)."
            ),
        },
        "original_record": {
            "path": record.source_path,
            "lines": [start, end],
        },
        "coefficient_count": record.coefficient_count(),
        "interval_count": record.interval_count(),
        "ambiguity_count": len(record.ambiguities),
        "ambiguities": [item.as_dict() for item in record.ambiguities],
        "sha256": record.source_sha256,
        "path": record_path,
    }


def build_manifest(
    database: ParsedDatabase,
    records: list[SpeciesRecord],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    elements = feedstock_elements()
    coverage = coverage_table(records, elements)
    entries = []
    for record in records:
        rel = f"data/literature/compilations/sgte-unary/records/{record.record_id}.yaml"
        entries.append(manifest_entry(record, rel))
    payload: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA,
        "source_id": SOURCE_ID,
        "corpus_status": {
            "scope": "complete unary50.tdb ingest; every ELEMENT, FUNCTION, PHASE, PARAMETER G/TC/BM/BMAGN, SPECIES",
            "source_file": database.source_path,
            "source_sha256": database.source_sha256,
            "official_url": "https://www.sgte.net/en/free-pure-elements-database",
            "version": "5.0 (2 June 2009)",
        },
        "compilation_role": {
            "engine_reference_input": True,
            "validation_measurement": False,
            "scoring_eligible": False,
            "battery_refusal": "gibbs_table_not_runtime_observable",
        },
        "summary": {
            "record_count": len(records),
            "element_count": len(database.elements),
            "function_count": len(database.functions),
            "phase_count": len(database.phases),
            "parameter_count": len(database.parameters),
            "g_parameter_count": sum(1 for item in database.parameters if item.kind == "G"),
            "parse_ambiguity_count": sum(len(record.ambiguities) for record in records) + len(database.ambiguities),
            "global_ambiguity_count": len(database.ambiguities),
            "feedstock_element_count": len(elements),
            "feedstock_elements_with_record": sum(1 for row in coverage.values() if row["present"]),
            "feedstock_elements_missing": sorted(element for element, row in coverage.items() if not row["present"]),
        },
        "global_ambiguities": [item.as_dict() for item in database.ambiguities],
        "feedstock_coverage": coverage,
        "entries": entries,
    }
    if generated_at is not None:
        payload["generated_at"] = generated_at
    return payload


def load_record_yaml(path: Path) -> dict[str, Any]:
    payload = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SgteUnaryError(f"{path}: record is not a mapping")
    return payload


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    manifest_path = path or MANIFEST_PATH
    payload = load_cached_safe_yaml(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SgteUnaryError(f"{manifest_path}: manifest is not a mapping")
    return payload


def iter_record_paths(records_dir: Path | None = None) -> list[Path]:
    directory = records_dir or RECORDS_DIR
    return sorted(directory.glob("*.yaml"))


def expression_from_interval_dict(payload: Mapping[str, Any]) -> ParsedExpression:
    try:
        band = (float(payload["T_low"]["value"]), float(payload["T_high"]["value"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise SgteUnaryError("missing or invalid certified temperature bounds") from exc
    if not all(math.isfinite(value) and value > 0 for value in band):
        raise SgteUnaryError(f"invalid certified temperature bounds {band}")
    terms = tuple(PolynomialTerm.from_dict(term) for term in (payload.get("polynomial") or {}).get("terms") or [])
    text = str(payload.get("expression_as_published") or "")
    names = tuple(sorted({term.function for term in terms if term.function}))
    return ParsedExpression(text=text, terms=terms, function_names=names, certified_band=band)


def feedstock_elements(feedstocks_path: Path | None = None) -> tuple[str, ...]:
    """Element symbols declared in data/feedstocks.yaml, including oxygen."""

    path = feedstocks_path or FEEDSTOCKS_PATH
    payload = load_cached_safe_yaml(path.read_text(encoding="utf-8")) or {}
    elements: set[str] = set()
    if not isinstance(payload, Mapping):
        raise SgteUnaryError(f"{path}: expected a mapping")
    for row in payload.values():
        if not isinstance(row, Mapping):
            continue
        for field in ("composition_wt_pct", "elemental_composition"):
            composition = row.get(field)
            if not isinstance(composition, Mapping):
                continue
            for formula, amount in composition.items():
                if not _positive_amount(amount):
                    continue
                atoms = _formula_atoms(str(formula))
                if atoms:
                    elements.update(atoms)
        stage0 = row.get("stage0_formula_inventory")
        if isinstance(stage0, Mapping):
            for entry in stage0.values():
                if not isinstance(entry, Mapping):
                    continue
                atoms = entry.get("atoms")
                if isinstance(atoms, Mapping):
                    elements.update(
                        str(element)
                        for element, count in atoms.items()
                        if _positive_amount(count)
                    )
    return tuple(sorted(elements))


def _positive_amount(value: Any) -> bool:
    try:
        return float(value) > 0.0
    except (TypeError, ValueError):
        return False


def _formula_atoms(formula: str) -> dict[str, float] | None:
    cleaned = _PHASE_SUFFIX_RE.sub("", formula.strip())
    if cleaned.endswith("_gas"):
        cleaned = cleaned[:-4]
    cleaned = re.sub(r"[+-]+$", "", cleaned)
    matches = list(_FORMULA_TOKEN_RE.finditer(cleaned))
    if not matches or "".join(match.group(0) for match in matches) != cleaned:
        return None
    atoms: dict[str, float] = {}
    for match in matches:
        element = match.group(1)
        atoms[element] = atoms.get(element, 0.0) + float(match.group(2) or 1.0)
    return atoms


def coverage_table(
    records: Iterable[SpeciesRecord],
    elements: Iterable[str],
) -> dict[str, dict[str, Any]]:
    by_formula: dict[str, list[SpeciesRecord]] = defaultdict(list)
    for record in records:
        by_formula[record.formula].append(record)
        if record.element is not None:
            by_formula[formula_from_tdb_symbol(record.element.symbol)].append(record)
    table: dict[str, dict[str, Any]] = {}
    for element in elements:
        matches = by_formula.get(element, [])
        # de-duplicate by record_id
        unique = {record.record_id: record for record in matches}
        table[element] = {
            "present": bool(unique),
            "record_count": len(unique),
            "phases": sorted({record.phase for record in unique.values()}),
            "record_ids": sorted(unique),
        }
    return table


def function_round_trip_failures(database: ParsedDatabase) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    functions = {name: function.as_dict() for name, function in database.functions.items()}
    for function in database.functions.values():
        for index, interval in enumerate(function.intervals):
            temperature = interval.midpoint_K()
            if interval.t_low > interval.t_high:
                try:
                    interval.expression.evaluate(temperature)
                except TemperatureOutOfIntervalError:
                    continue
                failures.append({"function": function.name, "interval_index": index, "error": "reversed band evaluated"})
                continue
            ok, parsed_value, string_value = function_round_trip_ok(interval.expression, temperature, functions)
            if not ok:
                failures.append(
                    {
                        "function": function.name,
                        "interval_index": index,
                        "T_K": temperature,
                        "parsed": parsed_value,
                        "string": string_value,
                    }
                )
    for parameter in database.parameters:
        if parameter.kind != "G":
            continue
        for index, interval in enumerate(parameter.intervals):
            temperature = interval.midpoint_K()
            ok, parsed_value, string_value = function_round_trip_ok(interval.expression, temperature, functions)
            if not ok:
                failures.append(
                    {
                        "parameter": parameter.identifier,
                        "interval_index": index,
                        "T_K": temperature,
                        "parsed": parsed_value,
                        "string": string_value,
                    }
                )
    return failures
