"""Shared parsing and interpretation of printed uncertainty declarations."""

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

from simulator.battery.records import _LOCATOR_KEYS, as_decimal


# Two-sided normal coverage factor for a 95% interval.
NORMAL_95_TICKET_MULTIPLIER = Decimal("1.959963984540054")
_CONVENTION_FACTORS: dict[str, tuple[str, Decimal | None]] = {
    "one_sigma": ("one_sigma", Decimal(1)),
    "two_sigma": ("two_sigma", Decimal(2)),
    "ci95": ("ci95", NORMAL_95_TICKET_MULTIPLIER),
    "95%": ("ci95", NORMAL_95_TICKET_MULTIPLIER),
    "range_half_width": ("range_half_width", None),
}
_COVERAGE_FACTOR_TOLERANCE = Decimal("0.001")
_DESCRIPTIVE_KINDS = frozenset(
    {
        "absolute",
        "categorical",
        "derived",
        "point",
        "printed",
        "read",
        "replicate_error",
        "standard_deviation",
    }
)
_NO_DECLARATION = frozenset(
    {"", "none", "not", "not stated", "not_stated", "unknown", "unspecified"}
)


def standard_uncertainty_width(
    printed_half_width: Decimal, coverage_factor: Decimal
) -> Decimal:
    """Convert a printed half-width at coverage k to its 1σ half-width.

    A printed half-width w covers k standard deviations, so σ = w/k. A
    two-sigma band is ±2σ; callers apply their metric transform once to that
    width. For example, 80 kPa at k=2 gives σ=40 kPa.
    """
    return printed_half_width / coverage_factor


class UncertaintyConventionConflict(ValueError):
    """Raised when declared uncertainty conventions imply different factors."""


UNCERTAINTY_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
UNCERTAINTY_RANGE_RE = re.compile(
    rf"[\[(]\s*({UNCERTAINTY_NUMBER})\s*[,;–—]\s*({UNCERTAINTY_NUMBER})\s*[\])]"
)
UNCERTAINTY_PAIR_RE = re.compile(
    rf"({UNCERTAINTY_NUMBER})\s+(?:–|—|to)\s+({UNCERTAINTY_NUMBER})",
    re.IGNORECASE,
)
UNCERTAINTY_SCALAR_RE = re.compile(UNCERTAINTY_NUMBER)
# Width tokens. Locator and provenance containers are not width declarations:
# descending into them selected page numbers on Leister ESR rows. Other nested
# mappings are source-bound widths (De Maria figure_reading_estimate, KEMS
# digitization.read_uncertainty) and must still be walked.
_WIDTH_KEY_TOKENS = ("uncert", "error", "sigma", "std", "dex", "width", "range")
_NON_WIDTH_CONTAINERS = frozenset({"locator", "provenance"})
_LOCATOR_METADATA_KEYS = frozenset(_LOCATOR_KEYS) | _NON_WIDTH_CONTAINERS
# bounds, interval and limits are widths only as a two-number list. A string
# such as "95 pct confidence interval" on the key "interval" is a qualifier,
# and the green walk does not select it.
# Closed unit vocabulary, separate from basis and confidence qualifiers.
# Unknown prose is not a unit. Distinct physical units stay distinct so a
# real Pa-versus-bar or kJ/mol-versus-kcal/mol disagreement still conflicts.
_CLOSED_UNITS = {
    "%": "%",
    "percent": "%",
    "percentage": "%",
    "atom%": "atom%",
    "wt%": "wt%",
    "wt.%": "wt%",
    "wt_percent": "wt%",
    "mass%": "mass%",
    "masspercent": "mass%",
    "mass_percent": "mass%",
    "mass-percent": "mass%",
    "mol%": "mol%",
    "mol.%": "mol%",
    "molpct": "mol%",
    "mole%": "mol%",
    "molepercent": "mol%",
    "mole_percent": "mol%",
    "mole-percent": "mol%",
    "dimensionless": "dimensionless",
    "fraction": "fraction",
    "1": "1",
    "dex": "dex",
    "pa": "Pa",
    "pascal": "Pa",
    "pascals": "Pa",
    "kpa": "kPa",
    "mpa": "MPa",
    "gpa": "GPa",
    "bar": "bar",
    "kbar": "kbar",
    "kilobar": "kbar",
    "mbar": "mbar",
    "millibar": "mbar",
    "atm": "atm",
    "atmosphere": "atm",
    "atmospheres": "atm",
    "torr": "Torr",
    "mmhg": "Torr",
    "k": "K",
    "kelvin": "K",
    "c": "°C",
    "°c": "°C",
    "celsius": "°C",
    "degc": "°C",
    "kj/mol": "kJ/mol",
    "kj/mole": "kJ/mol",
    "kjpermol": "kJ/mol",
    "j/mol": "J/mol",
    "j/mole": "J/mol",
    "kcal/mol": "kcal/mol",
    "kcal/mole": "kcal/mol",
    "cal/mol": "cal/mol",
    "cal/mole": "cal/mol",
    "j/mol/k": "J/mol/K",
    "j/molk": "J/mol/K",
    "cal/mol/k": "cal/mol/K",
    "cal/molk": "cal/mol/K",
    "kg/m2": "kg/m2",
    "g/m2": "g/m2",
    "mg/m2": "mg/m2",
    "g/cm2": "g/cm2",
    "mg/cm2": "mg/cm2",
}
_UNIT_ALIASES = _CLOSED_UNITS
_QUALIFIER_RE = re.compile(
    r"\([^)]*\)"
    r"|\b(?:relative|absolute|as\s+printed|percentage\s+points?)\b"
    r"|\b[12]\s*-?\s*(?:sigma|σ)\b"
    r"|\b95\s*%?\s*(?:ci|confidence\s+interval)\b",
    re.IGNORECASE,
)
_SCALED_PERCENT_CANONICAL = frozenset({"dimensionless", "fraction"})
_PERCENT_CANONICAL = frozenset({"%", "atom%"})
# d-460: a percent width with no printed basis is predicted, not refused, and
# the flag names the default. Relative matches the percent-to-fraction
# conversion already used when the paper does not say percentage points.
PERCENT_BASIS_DEFAULT_REASON = (
    "uncertainty_basis_defaulted: relative; "
    "printed percent width has no basis"
)
_CONFIDENCE_TRAILER_RE = re.compile(
    r"\s*\(?\s*(?:95\s*%?\s*(?:ci|confidence interval)"
    r"|[12]\s*-?\s*(?:sigma|σ)|range|limits?"
    r"|maximum error|maximum deviation)\s*\)?\s*$",
    re.IGNORECASE,
)


class WidthUnitBasis(NamedTuple):
    basis: str | None
    conflict: str | None
    basis_default_reason: str | None = None


def _is_width_key(name: str) -> bool:
    folded = name.casefold()
    return any(token in folded for token in _WIDTH_KEY_TOKENS)


def _strip_unit_qualifiers(text: str) -> str:
    """Drop basis and confidence words so they are not read as a unit."""

    stripped = _QUALIFIER_RE.sub(" ", text)
    stripped = _CONFIDENCE_TRAILER_RE.sub("", stripped)
    return re.sub(r"\s+", " ", stripped).strip(" ,;[]()")


def _alias_unit(unit: str | None) -> str:
    text = _strip_unit_qualifiers(unit or "")
    if not text or text.casefold() == "range":
        return ""
    compact = re.sub(r"\s+", "", text.casefold())
    return _CLOSED_UNITS.get(compact, "")


def _is_scaled_percent_unit(unit: str | None, printed_units: str | None) -> bool:
    return (
        _alias_unit(unit) in _SCALED_PERCENT_CANONICAL
        and _alias_unit(printed_units) in _PERCENT_CANONICAL
    )


_UNKNOWN_UNIT_TOKEN_RE = re.compile(
    r"[A-Za-zµμ°%][A-Za-z0-9µμ°%./^+−\-]*"
)


def _clean_unit_suffix(raw: str | None) -> str | None:
    """Canonical unit, or one unknown unit token the converter must still see.

    Qualifiers are removed first. A closed unit is canonical. One leftover
    token such as ``furlong`` stays, so an unmapped unit is refused instead
    of falling through to the quantity unit. Several leftover words are
    prose, not a unit. A short leading token (``ppm to 1 %``) is kept; a
    leading closed unit (``% of reading``) is canonical.
    """

    if raw is None:
        return None
    aliased = _alias_unit(raw)
    if aliased:
        return aliased
    text = _strip_unit_qualifiers(raw)
    if not text:
        return None
    parts = text.split()
    first = parts[0]
    canonical = _CLOSED_UNITS.get(re.sub(r"\s+", "", first.casefold()))
    if canonical:
        return canonical
    if not _UNKNOWN_UNIT_TOKEN_RE.fullmatch(first):
        return None
    if len(parts) == 1:
        return first
    # ppm, wt., kJ/mol-shaped tokens. An ordinary word opening a sentence
    # is not a unit.
    if len(first) <= 4 or any(character.isdigit() or character in "/%^.−-" for character in first):
        return first
    return None


def _unit_bound_to_number(raw: str, number: str) -> str | None:
    """Unit of the selected width number, not an earlier confidence percent."""
    escaped = re.escape(number)
    anchored = re.compile(
        rf"(?:±|\+/-)\s*{escaped}(?![\d.])\s*(.*)\Z",
        re.IGNORECASE | re.DOTALL,
    )
    match = anchored.search(raw)
    if match is None:
        loose = re.compile(
            rf"(?<![\d.]){escaped}(?![\d.])\s*(.*)\Z",
            re.DOTALL,
        )
        found = list(loose.finditer(raw))
        match = found[-1] if found else None
    if match is None:
        return None
    # A semicolon can start the next printed uncertainty clause, so it is not
    # part of the selected number's unit (for example, ``±10%; absolute``).
    return _clean_unit_suffix(match.group(1).split(";", maxsplit=1)[0])


def uncertainty_scalar_from_text(raw: str) -> str | None:
    match = re.search(rf"(?:±|\+/-)\s*({UNCERTAINTY_NUMBER})", raw)
    if match is not None:
        return match.group(1)
    match = re.search(
        rf"(?:standard\s+error|standard\s+deviation|\bsigma\b|\berror\b|\buncertainty\b)\s*[:=]?\s*({UNCERTAINTY_NUMBER})",
        raw,
        re.IGNORECASE,
    )
    if match is not None:
        return match.group(1)
    without_kind = re.sub(
        r"\b95\s*%\s*(?:ci|confidence interval)\b", " ", raw,
        flags=re.IGNORECASE,
    )
    without_kind = re.sub(
        r"\b95\s*(?:ci|confidence interval)\b|\b[12]\s*-?\s*(?:sigma|σ)\b",
        " ", without_kind, flags=re.IGNORECASE,
    )
    match = UNCERTAINTY_SCALAR_RE.search(without_kind)
    return None if match is None else match.group(0)


class WidthSelection(tuple):
    """Selected width. Unpacks as ``(value, unit)``; shape is not a unit.

    ``shape`` is ``scalar``, ``range`` (bracketed or a two-list), or ``pair``
    (a printed ``to`` / dash pair). ``text`` is the field string the number
    came from, so basis qualifiers stay attached to that field. ``declared_unit``
    retains a nested field's local unit beside any unit printed with the width.
    """

    def __new__(
        cls,
        value: object | None,
        unit: str | None,
        text: str | None = None,
        shape: str = "scalar",
        declared_unit: str | None = None,
    ) -> "WidthSelection":
        selected = super().__new__(cls, (value, unit))
        selected.text = text
        selected.shape = shape
        selected.declared_unit = declared_unit
        return selected

    @property
    def value(self) -> object | None:
        return self[0]

    @property
    def unit(self) -> str | None:
        return self[1]


class PrintedWidthSpan(tuple):
    """``(low, high, unit)`` plus the span's own shape."""

    def __new__(
        cls, low: str, high: str, unit: str | None, shape: str
    ) -> "PrintedWidthSpan":
        span = super().__new__(cls, (low, high, unit))
        span.shape = shape
        return span


def _is_locator_metadata_key(name: str) -> bool:
    return name.casefold() in _LOCATOR_METADATA_KEYS


def _selection_from_width_text(raw: str, name: str) -> WidthSelection | None:
    """Span when the field is a range or pair; otherwise the scalar width.

    A bracket or ``to`` pair whose endpoint is the scalar (``[2, 4] %``) keeps
    that shape. A printed ``±`` number that is not an endpoint of some other
    span in the same string stays the scalar the green walk selected.
    """

    scalar = uncertainty_scalar_from_text(raw)
    span = printed_width_span(raw)
    if span is not None and (scalar is None or scalar in {span[0], span[1]}):
        return WidthSelection(
            (span[0], span[1]), span[2], text=raw, shape=span.shape
        )
    if scalar is None:
        return None
    unit = _unit_bound_to_number(raw, scalar)
    if unit is None and "dex" in name:
        unit = "dex"
    return WidthSelection(scalar, unit, text=raw, shape="scalar")


def _selection_from_field(value: object, name: str) -> WidthSelection | None:
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return WidthSelection(value, None, shape="range")
    if isinstance(value, str):
        return _selection_from_width_text(value, name)
    try:
        as_decimal(value)
    except (TypeError, ValueError, ArithmeticError, InvalidOperation):
        return None
    unit = "dex" if "dex" in name else None
    return WidthSelection(value, unit, shape="scalar")


def _nested_width_selection(
    value: Mapping[str, object], parent_name: str
) -> WidthSelection | None:
    """Descend one nested mapping, except locator and provenance containers."""

    if _is_locator_metadata_key(parent_name):
        return None
    nested = mapping_uncertainty_value(value)
    if nested.value is None:
        return None
    declared_unit = mapping_uncertainty_unit(value) or nested.declared_unit
    unit = (
        nested.unit
        or declared_unit
        or ("dex" if "dex" in parent_name else None)
    )
    return WidthSelection(
        nested.value,
        unit,
        nested.text,
        nested.shape,
        declared_unit=declared_unit,
    )


def mapping_uncertainty_value(
    mapping: Mapping[str, object], *, exact_fields: Sequence[str] = ()
) -> WidthSelection:
    """Select a width from the exact field, then generic fields.

    Nested mappings are walked. Locator and provenance containers are not:
    their page numbers are not widths. A textual range or pair keeps that
    shape and the unit bound to the span, instead of the first number.
    """

    for name in exact_fields:
        if name not in mapping:
            continue
        selected = _selection_from_field(mapping[name], name.casefold())
        if selected is None:
            return WidthSelection(None, None)
        return selected
    for name in ("value", "amount", "error_value", "uncertainty_value"):
        if name not in mapping:
            continue
        selected = _selection_from_field(mapping[name], name)
        if selected is not None:
            return selected
    for key, value in mapping.items():
        name = str(key).casefold()
        if _is_locator_metadata_key(name):
            continue
        if isinstance(value, Mapping):
            nested = _nested_width_selection(value, name)
            if nested is not None:
                return nested
        if name in {"range", "bounds", "interval", "limits"} and isinstance(
            value, (tuple, list)
        ) and len(value) == 2:
            return WidthSelection(value, None, shape="range")
        if isinstance(value, (tuple, list)) and len(value) == 2:
            try:
                as_decimal(value[0])
                as_decimal(value[1])
            except (TypeError, ValueError, ArithmeticError, InvalidOperation):
                continue
            return WidthSelection(value, None, shape="range")
        if not _is_width_key(name) or isinstance(value, (tuple, list, Mapping)):
            continue
        if isinstance(value, str):
            selected = _selection_from_width_text(value, name)
            if selected is not None:
                return selected
            continue
        selected = _selection_from_field(value, name)
        if selected is not None:
            return selected
    return WidthSelection(None, None)


def printed_width_span(raw: str) -> PrintedWidthSpan | None:
    """Endpoints of a printed range or pair, and the unit after the span."""
    ranged = UNCERTAINTY_RANGE_RE.search(raw)
    if ranged is not None:
        return PrintedWidthSpan(
            ranged.group(1),
            ranged.group(2),
            _clean_unit_suffix(raw[ranged.end() :]),
            "range",
        )
    pair = UNCERTAINTY_PAIR_RE.search(raw)
    if pair is None:
        return None
    return PrintedWidthSpan(
        pair.group(1),
        pair.group(2),
        _clean_unit_suffix(raw[pair.end() :]),
        "pair",
    )


def mapping_uncertainty_unit(mapping: Mapping[str, object]) -> str | None:
    for key, value in mapping.items():
        if str(key).casefold() in {"unit", "units", "basis_unit"} and isinstance(value, str):
            return value
    return None


def paired_width_unit(printed_value: str | None) -> str | None:
    """Read the shared unit only from a value's printed parenthetical width."""
    if printed_value is not None and re.search(
        r"\d\s*\(\s*(?:±|\+/-)?\s*\d+(?:\.\d+)?\s*\)\s*%",
        printed_value,
    ):
        return "%"
    return None


_PAIRED_VALUE_KEYS = (
    "value_as_published",
    "value_as_printed",
    "paired_value",
    "printed_value",
)


def source_paired_value(*containers: object) -> str | None:
    """Printed value that shares a parenthetical width, from existing keys.

    Does not invent a persisted field. Callers pass the uncertainty verbatim
    and, when they have it, the observation provenance.
    """

    for container in containers:
        if not isinstance(container, Mapping):
            continue
        for key in _PAIRED_VALUE_KEYS:
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def uncertainty_exact_fields(
    quantity: object,
    *,
    derivation_parameters: Sequence[tuple[str, object]] = (),
    subtype: object = None,
) -> tuple[str, ...]:
    """Return scorer and migrator width fields in the shared precedence order."""
    fields = [
        name.removeprefix("original_")
        for name, _ in derivation_parameters
        if name.startswith("original_")
    ]
    subtype_token = subtype
    if hasattr(subtype, "is_value"):
        subtype_token = subtype.value if subtype.is_value else None
    subtype_token = getattr(subtype_token, "value", subtype_token)
    if isinstance(subtype_token, str) and subtype_token:
        fields.append(subtype_token)
    token = getattr(quantity, "value", quantity)
    if isinstance(token, str):
        fields.append(token)
    return tuple(fields)


def resolve_width_unit_basis(
    unit: str | None,
    *,
    inline_unit: str | None = None,
    expected_unit: str | None = None,
    declared_basis: str | None = None,
    printed_value: str | None = None,
    paired_value: str | None = None,
    basis_evidence: str = "",
    printed_units: str | None = None,
) -> WidthUnitBasis:
    """Resolve width basis from the original unit declarations.

    A range shape is not a unit. A percent width already scaled to a
    dimensionless or fraction value keeps that canonical unit and the
    original percent in ``printed_units``; that pair is one declaration.
    Any other two units, including ones outside the percent vocabulary,
    are a typed conflict. A percent width with no basis uses the stated
    relative default and names it.
    """
    left = _alias_unit(unit) or _clean_unit_suffix(unit)
    right = _alias_unit(inline_unit) or _clean_unit_suffix(inline_unit)
    left_key = _alias_unit(left) or (left or "").casefold()
    right_key = _alias_unit(right) or (right or "").casefold()
    printed_key = _alias_unit(printed_units)
    if (
        _is_scaled_percent_unit(left, printed_units)
        and right_key in {"", printed_key}
    ):
        right = None
        right_key = ""
    if left_key and right_key and left_key != right_key:
        return WidthUnitBasis(
            None,
            "width unit declarations disagree: "
            f"{left!r} and {right!r}",
            None,
        )
    normalized = left_key or right_key
    declared = (declared_basis or "").strip().casefold()
    if declared.startswith("uncertainty_convention_conflict:"):
        return WidthUnitBasis(None, declared.partition(":")[2], None)
    evidence = f"{basis_evidence} {printed_value or ''}".casefold()
    relative = declared == "relative" or bool(re.search(r"\brelative\b", evidence))
    absolute = declared in {"absolute", "dimensionless"} or bool(
        re.search(r"percentage\s*points?", evidence)
    ) or "atom%" in evidence or "atom %" in evidence
    if normalized in _PERCENT_CANONICAL:
        if relative:
            return WidthUnitBasis("relative", None, None)
        if absolute or paired_width_unit(paired_value) is not None:
            return WidthUnitBasis("dimensionless", None, None)
        return WidthUnitBasis("relative", None, PERCENT_BASIS_DEFAULT_REASON)
    if normalized in {"dimensionless", "fraction", "1"}:
        return WidthUnitBasis(
            "relative" if relative else "dimensionless", None, None
        )
    if normalized == "dex":
        if expected_unit and expected_unit.casefold() in {
            "dimensionless", "fraction", "1"
        }:
            return WidthUnitBasis(
                None, f"dex width is incompatible with {expected_unit} value", None
            )
        if relative:
            return WidthUnitBasis(
                None, "relative basis conflicts with dex width unit", None
            )
        return WidthUnitBasis("dex", None, None)
    return WidthUnitBasis(None, None, None)


def resolve_observed_width_basis(
    raw: object,
    *,
    inline_unit: str | None,
    uncertainty_basis: str = "",
    paired_value: str | None = None,
    expected_unit: str | None = None,
    selected_text: str | None = None,
    nested_unit: str | None = None,
) -> WidthUnitBasis:
    """One basis resolution for migration and scoring.

    Both paths pass the same arguments: the unit declared beside the selected
    field (or at the top level), the unit bound to that width, the printed
    paired value, and the qualifier text. A parenthetical percent on the
    paired value supplies the unit only when the width itself names none.
    """

    basis = uncertainty_basis or ""
    printed_units = None
    declared_basis = basis
    printed_value = None
    evidence: list[str] = []
    declared_unit = nested_unit
    if isinstance(raw, Mapping):
        declared_unit = declared_unit or mapping_uncertainty_unit(raw)
        stored_printed_units = raw.get("printed_units")
        if isinstance(stored_printed_units, str):
            printed_units = stored_printed_units
        if not basis.startswith("uncertainty_convention_conflict:"):
            declared_basis = str(raw.get("basis") or basis)
        as_printed = raw.get("as_printed")
        if as_printed is not None:
            printed_value = str(as_printed)
        evidence.append(str(raw.get("as_printed") or ""))
        evidence.append(str(raw.get("basis") or ""))
        locator = raw.get("locator")
        if isinstance(locator, Mapping):
            evidence.append(str(locator.get("note") or ""))
    elif isinstance(raw, str):
        printed_value = raw
        evidence.append(raw)
    if selected_text:
        evidence.append(selected_text)
        if re.search(r"\babsolute\b", selected_text, re.IGNORECASE):
            if declared_basis.strip().casefold() == "relative":
                return WidthUnitBasis(
                    None,
                    "width basis declarations disagree: 'relative' and 'absolute'",
                    None,
                )
            declared_basis = "absolute"
    if paired_value is None:
        paired_value = source_paired_value(raw)
    paired_unit = paired_width_unit(paired_value)
    if paired_unit and not _clean_unit_suffix(inline_unit):
        if _clean_unit_suffix(declared_unit):
            inline_unit = paired_unit
        else:
            declared_unit = paired_unit
    if declared_unit is None and not isinstance(raw, Mapping):
        declared_unit = inline_unit
    return resolve_width_unit_basis(
        declared_unit,
        inline_unit=inline_unit,
        expected_unit=expected_unit,
        declared_basis=declared_basis,
        printed_value=printed_value,
        paired_value=paired_value,
        basis_evidence=" ".join(evidence),
        printed_units=printed_units,
    )


def printed_uncertainty_convention(
    declaration: object,
    *,
    fallback_token: str | None = None,
) -> tuple[str | None, Decimal | None] | None:
    """Return the stated confidence token and its coverage factor, if valid.

    ``None`` means no convention was declared. Unsupported tokens, invalid
    factors, and conflicting usable declarations raise
    ``UncertaintyConventionConflict``. Factors within 0.001 agree, allowing
    a printed ``k=1.96`` to agree with the normal 95% factor.
    A numeric ``k`` remains an exact coverage factor; its confidence token is
    the matching sigma token when k is one or two, otherwise one_sigma after
    the width has been reduced to standard uncertainty by the caller.
    """
    if not isinstance(declaration, Mapping):
        if fallback_token is None:
            return None
        declaration = {}

    factors: list[Decimal] = []
    numeric_factor: Decimal | None = None
    confidence_token: str | None = None
    declared_range = False
    for key in ("confidence_level", "kind"):
        raw_token = declaration.get(key)
        if raw_token is None:
            continue
        if not isinstance(raw_token, str):
            raise UncertaintyConventionConflict(f"{key} must be a string")
        token = raw_token.strip().lower()
        if token in _NO_DECLARATION:
            continue
        if key == "kind" and token in _DESCRIPTIVE_KINDS:
            continue
        if token not in _CONVENTION_FACTORS:
            raise UncertaintyConventionConflict(f"unrecognized {key} token {token!r}")
        canonical_token, mapped_factor = _CONVENTION_FACTORS[token]
        if mapped_factor is None:
            declared_range = True
        else:
            factors.append(mapped_factor)
        if confidence_token is None:
            confidence_token = canonical_token

    if "two_sigma" in declaration:
        try:
            as_decimal(declaration["two_sigma"])
        except (TypeError, ValueError, ArithmeticError, InvalidOperation):
            pass
        else:
            factors.append(Decimal(2))
            if confidence_token is None:
                confidence_token = "two_sigma"

    if confidence_token is None and fallback_token is not None:
        token = fallback_token.strip().lower()
        if token in _NO_DECLARATION:
            token = ""
        if token and token not in _CONVENTION_FACTORS:
            raise UncertaintyConventionConflict(
                f"unrecognized fallback confidence token {token!r}"
            )
        if token:
            confidence_token, mapped_factor = _CONVENTION_FACTORS[token]
            if mapped_factor is None:
                declared_range = True
            else:
                factors.append(mapped_factor)

    if "k" in declaration and declaration.get("k") is not None:
        try:
            coverage_factor = Decimal(str(declaration["k"]))
        except (InvalidOperation, TypeError, ValueError):
            raise UncertaintyConventionConflict("k must be a positive numeric factor")
        if not coverage_factor.is_finite() or coverage_factor <= 0:
            raise UncertaintyConventionConflict("k must be a positive finite factor")
        numeric_factor = coverage_factor
        factors.append(coverage_factor)

    if declared_range and factors:
        raise UncertaintyConventionConflict(
            "uncertainty_convention_conflict: range_half_width with coverage factor"
        )
    if declared_range:
        return "range_half_width", None
    if not factors:
        if confidence_token is None:
            return None
        return confidence_token, Decimal(1)
    if any(
        abs(factor - factors[0]) > _COVERAGE_FACTOR_TOLERANCE
        for factor in factors[1:]
    ):
        raise UncertaintyConventionConflict(
            "uncertainty_convention_conflict: declarations imply different coverage factors"
        )

    factor = numeric_factor if numeric_factor is not None else factors[0]
    if (
        confidence_token is None
        or _CONVENTION_FACTORS[confidence_token][1] is None
    ):
        confidence_token = next(
            (
                canonical_token
                for canonical_token, mapped_factor in _CONVENTION_FACTORS.values()
                if mapped_factor == factor
            ),
            "one_sigma",
        )
    return confidence_token, factor
