"""NIST-JANAF 4th edition compilation loader.

Reads ``data/literature/compilations/janaf/`` (one YAML/JSON file per
table). Numbers stay as published tokens; there is no unit conversion,
smoothing, phase merging, or integer rewrite of printed formulas.

Source ``.txt`` bytes are not in git. Source round-trip tests re-parse
available original downloads and compare complete printed-token rows;
unavailable sources are explicitly unverified. The same native parser
supports the harvester, preserving blank cells by header position.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from simulator.yaml_cache import load_cached_safe_yaml

ROOT = Path(__file__).resolve().parents[2]
COMPILATION_ROOT = ROOT / "data" / "literature" / "compilations" / "janaf"
TABLES_DIR = COMPILATION_ROOT / "tables"
MANIFEST_PATH = COMPILATION_ROOT / "manifest.yaml"
FEEDSTOCKS_PATH = ROOT / "data" / "feedstocks.yaml"
SOURCE_CACHE_REL = "data/literature/compilations/janaf/source-cache"
SOURCE_CACHE_DIR = ROOT / "data" / "literature" / "compilations" / "janaf" / "source-cache"
SIDECAR_PATH = COMPILATION_ROOT / "source" / "sidecar.yaml"
SCHEMA_VERSION = "literature_compilation.v1"
MANIFEST_SCHEMA = "literature_compilation_manifest.v1"
SOURCE_ID = "nist-janaf-4th"

VALUE_KEYS = (
    "temperature",
    "heat_capacity",
    "entropy",
    "negative_gibbs_enthalpy_function",
    "enthalpy_increment",
    "formation_enthalpy",
    "formation_gibbs_energy",
    "log10_formation_equilibrium_constant",
)
VALUE_COLUMNS = (
    ("temperature", "T/K"),
    ("heat_capacity", "Cp°"),
    ("entropy", "S°"),
    ("negative_gibbs_enthalpy_function", "-[G°-H°(Tr)]/T"),
    ("enthalpy_increment", "H-H°(Tr)"),
    ("formation_enthalpy", "ΔfH°"),
    ("formation_gibbs_energy", "ΔfG°"),
    ("log10_formation_equilibrium_constant", "log Kf"),
)
HEADER_ALIASES = ("T/K", "T(K)")
NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$")
# After taking the first eight column positions and stripping trailing
# empty fields, numeric-first structured rows in this corpus have one of
# these content counts (1=T-only placeholder, 2=T+Cp only, 5=empty
# formation tail, 6=partial formation tail, 7=blank log Kf, 8=full row).
STRUCTURED_CONTENT_COUNTS = frozenset({1, 2, 5, 6, 7, 8})
NON_DATA_MARKER_KIND = "non_data_marker"
NON_DATA_MARKER_REASON = (
    "non-data marker line; no numeric thermochemical token"
)
REFUSED_LAYOUT_KIND = "refused_layout"
TRAILING_EMPTY_LAYOUT_REASON = (
    "row reaches the expected field count through trailing empty fields alone"
)
STRUCTURED_LAYOUT_REASON = (
    "stripped field count is not a printed structured layout"
)
INCONSISTENT_LAYOUT_REASON = (
    "raw field count does not match the table's printed layout for this row shape"
)
# 10 K is gcd(20, 50, 100): H-063 prints 20 K inserts (280–480), many
# tables print 250/350/450, and the 100 K ladder is universal. Structured
# rows never print a non-integer T other than the 298.15 K reference.
PRINTED_GRID_QUANTUM_K = Decimal("10")
REFERENCE_TEMPERATURE_K = Decimal("298.15")
# Max consecutive 100 K-ladder gap in the 1,655-table corpus is 400 K
# (Ta-003/Ta-004: 5600 → 6000). 616 tables skip 200 K around a labelled
# transition; five skip 300 K; two skip 400 K. A jump larger than 400 K
# is not a printed structured spacing of any table.
PRINTED_GRID_MAX_STEP_K = Decimal("400")
GRID_RANGE_REASON = (
    "temperature is not in this table's printed temperature set"
)
GRID_ORDER_REASON = (
    "temperature is not ordered within the printed table grid"
)
# Decimal subscripts are retained. Charge is optional.
FORMULA_TOKEN_RE = re.compile(r"([A-Z][a-z]?)(\d+(?:\.\d+)?)?")
CHARGE_RE = re.compile(r"[+-]$")
PHASE_SUFFIX_RE = re.compile(r"\((ref|cr|l|cr,l|g|l,g|fl)\)$")
SUBSCRIPT_ONE_RE = re.compile(r"([A-Z][a-z]?)1(?=[A-Z]|[+-]|$)")

# Review F2: these eleven printed formulas were integerised in c35ea8e1.
NON_STOICHIOMETRIC_TABLES: dict[str, dict[str, str]] = {
    "C-001": {
        "formula_as_published": "NbC0.98",
        "previous_integerised_formula": "CNb",
    },
    "Fe-001": {
        "formula_as_published": "Fe0.947O",
        "previous_integerised_formula": "FeO",
    },
    "Fe-002": {
        "formula_as_published": "Fe0.877S",
        "previous_integerised_formula": "FeS",
    },
    "H-097": {
        "formula_as_published": "H15O10.5S1",
        "previous_integerised_formula": "H15O10S",
    },
    "Mo-011": {
        "formula_as_published": "MoO2.750",
        "previous_integerised_formula": "MoO2",
    },
    "Mo-012": {
        "formula_as_published": "MoO2.875",
        "previous_integerised_formula": "MoO2",
    },
    "Mo-013": {
        "formula_as_published": "MoO2.889",
        "previous_integerised_formula": "MoO2",
    },
    "N-001": {
        "formula_as_published": "VN0.465",
        "previous_integerised_formula": "NV",
    },
    "O-049": {
        "formula_as_published": "WO2.72",
        "previous_integerised_formula": "O2W",
    },
    "O-050": {
        "formula_as_published": "WO2.90",
        "previous_integerised_formula": "O2W",
    },
    "O-051": {
        "formula_as_published": "WO2.96",
        "previous_integerised_formula": "O2W",
    },
}

# JANAF has no {El}-index.html for these feedstock elements (HTTP 404).
PINNED_JANAF_ABSENT_ELEMENTS = (
    "Ag", "As", "Au", "Bi", "Cd", "Ce", "Dy", "Er", "Eu", "Gd", "Ge",
    "In", "La", "Lu", "Nd", "Pt", "Sb", "Sc", "Se", "Sm", "Sn", "Te",
    "Th", "U", "Y", "Yb",
)

NON_STOICH_AMBIGUITY_REASON = (
    "Printed formula has non-integer subscripts; formula_as_published "
    "retained verbatim. formula_normalised keeps decimal coefficients; "
    "integer rewrite forbidden."
)
FORMULA_NORMALISED_RULE = (
    "formula_normalised is formula_as_published with trailing ion charge "
    "stripped, LaTeX-style _{n} subscripts flattened to n, and integer "
    "subscript-1 dropped. Decimal coefficients are kept exactly; they are "
    "never rounded to integers."
)

COMPILATION_SOURCE = {
    "database": "NIST Standard Reference Database 13",
    "citation": (
        "Chase, M. W. Jr., NIST-JANAF Thermochemical Tables, 4th Edition, "
        "J. Phys. Chem. Ref. Data Monograph 9 (1998)"
    ),
    "doi": "10.18434/T42S31",
    "official_url": "https://janaf.nist.gov/",
    "formula_index_url": "https://janaf.nist.gov/formula.html",
    "licence": (
        'Quoted from NIST SRD 13 (https://data.nist.gov/od/id/'
        "ECBCC1C1301D2ED9E04306570681B10735): \"These data are public.\" "
        "Quoted from https://www.nist.gov/copyrights-disclaimers: "
        '"With the exception of material marked as copyrighted, information '
        "presented on NIST sites are considered public information and may "
        'be distributed or copied."'
    ),
}

COMPILATION_ROLE = {
    "kind": "assessed_thermodynamic_functions",
    "engine_reference_input": True,
    "validation_measurement": False,
    "scoring_eligible": False,
    "battery_refusal": "gibbs_table_not_runtime_observable",
    "circularity_warning": (
        "Do not validate an engine against a compilation it consumes."
    ),
    # Declared engine_point disposition. Copied onto each generated row.
    # Peer pure-mineral Gibbs tables share the role flags above and do not
    # set this; readiness selects on the row field, not on a source id.
    "pure_substance_reference": True,
}

_NON_FORMULA_FEEDSTOCK_KEYS = frozenset(
    {
        "carbonaceous_organic",
        "carbonate_salts",
        "organics",
        "CH4_NH3_HCN",
        "CO_CO2",
    }
)
_FEEDSTOCK_COMPOSITION_SECTIONS = (
    "composition_wt_pct",
    "elemental_composition",
    "non_oxide_components",
    "bulk_additions",
    "structural_water",
    "trace_elements",
    "solar_wind_volatiles",
)
_FORMULA_KEY_RE = re.compile(r"^(?:[A-Z][a-z]?(?:\d+(?:\.\d+)?)?)+$")
ELEMENT_SYMBOLS = frozenset(
    {
        "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
        "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca",
        "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
        "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr",
        "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn",
        "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd",
        "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
        "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
        "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
        "Pa", "U", "Np", "Pu",
    }
)


class JanafParseError(ValueError):
    """Unrecoverable JANAF table parse defect."""


@dataclass
class ParsedTxtTable:
    """Native NIST ``.txt`` parse: 8-column rows plus short-row ambiguities."""

    title_lines: list[str]
    header_as_published: str
    values: list[dict[str, Any]]
    parse_ambiguities: list[dict[str, Any]] = field(default_factory=list)


def parse_published_number(token: str) -> float | None:
    """Parse a JANAF cell token. Non-numeric cells stay None, never guessed."""

    cleaned = str(token).strip()
    if cleaned == "" or cleaned.upper() == "INFINITE":
        return None
    if NUMBER_RE.fullmatch(cleaned):
        return float(cleaned)
    return None


def _looks_like_concatenated_numbers(token: str) -> bool:
    return token.count(".") >= 2 and re.fullmatch(r"[0-9Ee+.-]+", token) is not None


def _is_thermo_token(token: str) -> bool:
    cleaned = token.strip()
    return cleaned.upper() == "INFINITE" or NUMBER_RE.fullmatch(cleaned) is not None


def _strip_trailing_empty(fields: list[str]) -> list[str]:
    content = list(fields)
    while content and content[-1] == "":
        content.pop()
    return content


def _ambiguity_record(
    *,
    line_number: int,
    line: str,
    reason: str,
    table_id: str,
    url: str,
    kind: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "line_number": line_number,
        "raw_line": line,
        "reason": reason,
        "locator": {"table_id": table_id, "url": url},
    }
    if kind is not None:
        record["kind"] = kind
    return record


@dataclass
class _LineCandidate:
    line_number: int
    line: str
    raw_n: int
    content: list[str]
    first_is_number: bool
    concatenated: bool
    all_thermo_tokens: bool
    temperature: float | None


def _line_candidate(line_number: int, line: str) -> _LineCandidate:
    raw_fields = [field.strip() for field in line.split("\t")]
    raw_n = len(raw_fields)
    head = raw_fields[: len(VALUE_COLUMNS)] if raw_n >= len(VALUE_COLUMNS) else raw_fields
    content = _strip_trailing_empty(head)
    first = content[0] if content else ""
    first_is_number = NUMBER_RE.fullmatch(first) is not None
    concatenated = _looks_like_concatenated_numbers(first)
    temperature = parse_published_number(first) if first_is_number else None
    return _LineCandidate(
        line_number=line_number,
        line=line,
        raw_n=raw_n,
        content=content,
        first_is_number=first_is_number,
        concatenated=concatenated,
        all_thermo_tokens=bool(content) and all(_is_thermo_token(token) for token in content),
        temperature=temperature,
    )


def is_printed_grid_temperature(temperature: Decimal) -> bool:
    """True for 298.15 K or an integer-valued multiple of 10 K."""

    if temperature == REFERENCE_TEMPERATURE_K:
        return True
    if temperature < 0:
        return False
    return (
        temperature == temperature.to_integral_value()
        and temperature % PRINTED_GRID_QUANTUM_K == 0
    )


def _decimal_temperature_token(token: str) -> Decimal | None:
    cleaned = token.strip()
    if NUMBER_RE.fullmatch(cleaned) is None:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def table_printed_temperatures(tokens: Iterable[str]) -> frozenset[Decimal]:
    """Printed T set of THIS table, from its own structured-row T tokens.

    Grid-like T (298.15 K or an integer-valued multiple of 10 K) are
    connected when consecutive unique values differ by at most
    ``PRINTED_GRID_MAX_STEP_K``. The printed set is the largest connected
    component. A 4000 K row in B-133 (printed max 3000 K) is a 1000 K jump
    and does not join. This is membership in the table's printed T set,
    not a global [0, 6000] K range.
    """

    values: list[Decimal] = []
    for token in tokens:
        parsed = _decimal_temperature_token(str(token))
        if parsed is None or not is_printed_grid_temperature(parsed):
            continue
        values.append(parsed)
    unique = sorted(set(values))
    if not unique:
        return frozenset()
    parent = {item: item for item in unique}

    def find(item: Decimal) -> Decimal:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: Decimal, right: Decimal) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for left, right in zip(unique, unique[1:]):
        if right - left <= PRINTED_GRID_MAX_STEP_K:
            union(left, right)
    components: dict[Decimal, set[Decimal]] = defaultdict(set)
    for item in unique:
        components[find(item)].add(item)
    largest = max(components.values(), key=lambda group: (len(group), -min(group)))
    return frozenset(largest)


def _candidate_temperature_decimal(candidate: _LineCandidate) -> Decimal | None:
    if not candidate.content:
        return None
    return _decimal_temperature_token(candidate.content[0])


def _refuse_uncorroborated_temperatures(
    rows: list[_LineCandidate],
) -> tuple[list[_LineCandidate], list[_LineCandidate], list[str]]:
    """Keep rows whose T is in this table's printed T set and non-decreasing."""

    printed = table_printed_temperatures(
        candidate.content[0] for candidate in rows if candidate.content
    )
    kept: list[_LineCandidate] = []
    refused: list[_LineCandidate] = []
    reasons: list[str] = []
    for candidate in rows:
        parsed = _candidate_temperature_decimal(candidate)
        if parsed is None or parsed not in printed:
            refused.append(candidate)
            reasons.append(GRID_RANGE_REASON)
            continue
        kept.append(candidate)
    changed = True
    while changed:
        changed = False
        for index in range(len(kept) - 1):
            current_t = _candidate_temperature_decimal(kept[index])
            following_t = _candidate_temperature_decimal(kept[index + 1])
            if current_t is None or following_t is None:
                continue
            if current_t <= following_t:
                continue
            previous_t = (
                _candidate_temperature_decimal(kept[index - 1]) if index else None
            )
            drop_index = (
                index
                if previous_t is None or previous_t <= following_t
                else index + 1
            )
            refused.append(kept[drop_index])
            reasons.append(GRID_ORDER_REASON)
            del kept[drop_index]
            changed = True
            break
    return kept, refused, reasons


def flatten_subscripts(value: str) -> str:
    value = re.sub(r"_\{([^}]*)\}", r"\1", value)
    return value.replace("{", "").replace("}", "").replace(" ", "")


def strip_charge(formula: str) -> str:
    return CHARGE_RE.sub("", formula)


def formula_normalised(formula_as_published: str) -> str:
    """Apply FORMULA_NORMALISED_RULE. Decimal coefficients are kept."""

    formula = flatten_subscripts(str(formula_as_published or ""))
    formula = PHASE_SUFFIX_RE.sub("", formula)
    formula = strip_charge(formula)
    formula = SUBSCRIPT_ONE_RE.sub(r"\1", formula)
    return formula


def formula_composition(value: str) -> tuple[tuple[str, float], ...] | None:
    """Parse a formula into (element, count) pairs. Decimals kept.

    Returns None when the string is not a composition (after charge strip).
    """

    formula = formula_normalised(value)
    if not formula:
        return None
    tokens = FORMULA_TOKEN_RE.findall(formula)
    rebuilt = "".join(element + (count or "") for element, count in tokens)
    if not tokens or rebuilt != formula:
        return None
    counts: dict[str, float] = defaultdict(float)
    for element, count in tokens:
        counts[element] += float(count or "1")
    return tuple(sorted(counts.items()))


def formula_elements(value: str) -> tuple[str, ...]:
    composition = formula_composition(value)
    if composition is None:
        return ()
    return tuple(element for element, _count in composition)


def non_stoich_ambiguity(table_id: str) -> dict[str, str]:
    meta = NON_STOICHIOMETRIC_TABLES[table_id]
    return {
        "kind": "non_stoichiometric_formula_not_rewritten",
        "formula_as_published": meta["formula_as_published"],
        "formula_normalised": formula_normalised(meta["formula_as_published"]),
        "previous_integerised_formula": meta["previous_integerised_formula"],
        "reason": NON_STOICH_AMBIGUITY_REASON,
    }


def _header_index(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(HEADER_ALIASES):
            return index
    raise JanafParseError("missing T/K or T(K) column header")


def parse_janaf_txt(
    payload: bytes | str,
    *,
    table_id: str,
    url: str,
    download_url: str,
) -> ParsedTxtTable:
    """Parse a live NIST-JANAF ``.txt`` download (tab-delimited, ``T(K)`` header)."""

    source = payload.decode("utf-8", errors="strict") if isinstance(payload, bytes) else payload
    source = source.replace("\r\n", "\n")
    lines = source.splitlines()
    header_index = _header_index(lines)
    header_as_published = lines[header_index].strip()
    header_fields = [field.strip() for field in lines[header_index].split("\t")]
    while header_fields and not header_fields[-1]:
        header_fields.pop()
    if len(header_fields) != len(VALUE_COLUMNS):
        raise JanafParseError(f"{table_id}: expected eight native header columns")
    values: list[dict[str, Any]] = []
    ambiguities: list[dict[str, Any]] = []
    structured_candidates: list[_LineCandidate] = []
    for line_number, line in enumerate(lines[header_index + 1 :], start=header_index + 2):
        if not line.strip():
            continue
        candidate = _line_candidate(line_number, line)
        if not candidate.content:
            continue
        legacy_fields = [field.strip() for field in line.split("\t")]
        while len(legacy_fields) > len(header_fields) and legacy_fields[-1] == "":
            legacy_fields.pop()
        legacy_n = len(legacy_fields)
        if not candidate.first_is_number and not candidate.concatenated:
            ambiguities.append(
                _ambiguity_record(
                    line_number=line_number,
                    line=line,
                    reason=NON_DATA_MARKER_REASON,
                    table_id=table_id,
                    url=url,
                    kind=NON_DATA_MARKER_KIND,
                )
            )
            continue
        if candidate.concatenated or not candidate.first_is_number:
            reason = (
                f"expected {len(VALUE_COLUMNS)} tab-separated values; "
                f"found {legacy_n}"
                if legacy_n != len(header_fields)
                else "first field is not one published number"
            )
            ambiguities.append(
                _ambiguity_record(
                    line_number=line_number,
                    line=line,
                    reason=reason,
                    table_id=table_id,
                    url=url,
                )
            )
            continue
        if (
            candidate.raw_n >= len(VALUE_COLUMNS)
            and len(candidate.content) in STRUCTURED_CONTENT_COUNTS
            and candidate.all_thermo_tokens
        ):
            structured_candidates.append(candidate)
            continue
        if candidate.all_thermo_tokens and candidate.raw_n > len(VALUE_COLUMNS):
            reason = TRAILING_EMPTY_LAYOUT_REASON
            kind: str | None = REFUSED_LAYOUT_KIND
        else:
            reason = (
                f"expected {len(VALUE_COLUMNS)} tab-separated values; "
                f"found {legacy_n}"
                if legacy_n != len(header_fields)
                else STRUCTURED_LAYOUT_REASON
            )
            kind = REFUSED_LAYOUT_KIND if candidate.all_thermo_tokens else None
        ambiguities.append(
            _ambiguity_record(
                line_number=line_number,
                line=line,
                reason=reason,
                table_id=table_id,
                url=url,
                kind=kind,
            )
        )

    by_content: dict[int, list[_LineCandidate]] = defaultdict(list)
    for candidate in structured_candidates:
        by_content[len(candidate.content)].append(candidate)
    layout_consistent: list[_LineCandidate] = []
    for group in by_content.values():
        mode_raw_n = Counter(item.raw_n for item in group).most_common(1)[0][0]
        for candidate in group:
            if candidate.raw_n != mode_raw_n:
                ambiguities.append(
                    _ambiguity_record(
                        line_number=candidate.line_number,
                        line=candidate.line,
                        reason=INCONSISTENT_LAYOUT_REASON,
                        table_id=table_id,
                        url=url,
                        kind=REFUSED_LAYOUT_KIND,
                    )
                )
            else:
                layout_consistent.append(candidate)
    layout_consistent.sort(key=lambda item: item.line_number)
    corroborated, uncorroborated, grid_reasons = _refuse_uncorroborated_temperatures(
        layout_consistent
    )
    for candidate, reason in zip(uncorroborated, grid_reasons, strict=True):
        ambiguities.append(
            _ambiguity_record(
                line_number=candidate.line_number,
                line=candidate.line,
                reason=reason,
                table_id=table_id,
                url=url,
                kind=REFUSED_LAYOUT_KIND,
            )
        )
    corroborated.sort(key=lambda item: item.line_number)
    ambiguities.sort(key=lambda item: (item.get("line_number") is None, item.get("line_number") or 0))
    for candidate in corroborated:
        fields = candidate.content + [""] * (len(VALUE_COLUMNS) - len(candidate.content))
        temperature_token = fields[0]
        row: dict[str, Any] = {}
        for index, ((key, default_column), token) in enumerate(
            zip(VALUE_COLUMNS, fields, strict=True)
        ):
            column = header_fields[index] if index < len(header_fields) else default_column
            row[key] = {
                "value": parse_published_number(token),
                "as_published": token,
                "locator": {
                    "table_id": table_id,
                    "url": url,
                    "download_url": download_url,
                    "row_temperature_as_published": temperature_token,
                    "column": column,
                },
            }
        values.append(row)
    if not values:
        raise JanafParseError(f"{table_id}: no unambiguous thermodynamic rows parsed")
    title_lines = [line.strip() for line in lines[:header_index] if line.strip()]
    return ParsedTxtTable(
        title_lines=title_lines,
        header_as_published=header_as_published,
        values=values,
        parse_ambiguities=ambiguities,
    )


def iter_table_paths(directory: Path | None = None) -> Iterator[Path]:
    root = directory or TABLES_DIR
    yield from sorted(root.glob("*.yaml"))


def load_table_document(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if raw.lstrip().startswith(b"{"):
        document = json.loads(raw.decode("utf-8"))
    else:
        document = load_cached_safe_yaml(raw)
    if not isinstance(document, Mapping):
        raise JanafParseError(f"{path}: expected a mapping")
    return dict(document)


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    payload = load_cached_safe_yaml(
        (path or MANIFEST_PATH).read_text(encoding="utf-8")
    )
    if not isinstance(payload, Mapping):
        raise JanafParseError("manifest is not a mapping")
    return dict(payload)


def table_formula_as_published(table: Mapping[str, Any]) -> str:
    index_entry = table.get("index_entry") or {}
    published = index_entry.get("formula_as_published")
    if published:
        return str(published)
    formula = index_entry.get("formula")
    if formula:
        return str(formula)
    raise JanafParseError("table missing formula_as_published")


def table_formula_normalised_value(table: Mapping[str, Any]) -> str:
    index_entry = table.get("index_entry") or {}
    stored = index_entry.get("formula_normalised")
    if stored:
        return str(stored)
    return formula_normalised(table_formula_as_published(table))


def round_trip_failures(document: Mapping[str, Any]) -> list[str]:
    """Every stored number must re-parse from its ``as_published`` token."""

    table = document.get("table") or {}
    table_id = str(table.get("table_id") or "")
    failures: list[str] = []
    rows = table.get("values") or []
    for row_number, row in enumerate(rows):
        if not isinstance(row, Mapping):
            failures.append(f"{table_id} row {row_number}: not a mapping")
            continue
        for key in VALUE_KEYS:
            cell = row.get(key)
            if not isinstance(cell, Mapping) or "as_published" not in cell:
                failures.append(f"{table_id} row {row_number} {key}: missing as_published")
                continue
            token = cell.get("as_published")
            stored = cell.get("value")
            parsed = parse_published_number("" if token is None else str(token))
            if parsed != stored:
                failures.append(
                    f"{table_id} row {row_number} {key}: "
                    f"as_published={token!r} stored={stored!r} parsed={parsed!r}"
                )
    return failures


def feedstock_element_symbols(feedstocks_path: Path | None = None) -> list[str]:
    """Element symbols declared in ``data/feedstocks.yaml`` compositions."""

    path = feedstocks_path or FEEDSTOCKS_PATH
    payload = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise JanafParseError(f"{path}: expected mapping")
    found: set[str] = set()

    def add_formula(token: str) -> None:
        raw = token.strip()
        raw = re.sub(r"_(ppm|wt_pct|kg_per_tonne)$", "", raw)
        if raw in _NON_FORMULA_FEEDSTOCK_KEYS:
            if raw == "CH4_NH3_HCN":
                found.update({"C", "H", "N"})
            elif raw == "CO_CO2":
                found.update({"C", "O"})
            return
        if raw in ELEMENT_SYMBOLS:
            found.add(raw)
            return
        composition = formula_composition(raw)
        if composition:
            found.update(element for element, _count in composition)
            return
        if _FORMULA_KEY_RE.fullmatch(flatten_subscripts(raw)):
            for element, _count in FORMULA_TOKEN_RE.findall(flatten_subscripts(raw)):
                found.add(element)

    def walk_mapping(node: Mapping[str, Any]) -> None:
        for key, child in node.items():
            add_formula(str(key))
            if isinstance(child, Mapping):
                walk_mapping(child)

    def walk_stage0(node: Mapping[str, Any]) -> None:
        for entry in node.values():
            if not isinstance(entry, Mapping):
                continue
            atoms = entry.get("atoms")
            if isinstance(atoms, Mapping):
                for element, count in atoms.items():
                    try:
                        if float(count) > 0.0:
                            found.add(str(element))
                    except (TypeError, ValueError):
                        continue

    for entry in payload.values():
        if not isinstance(entry, Mapping):
            continue
        for section in _FEEDSTOCK_COMPOSITION_SECTIONS:
            body = entry.get(section)
            if isinstance(body, Mapping):
                walk_mapping(body)
        stage0 = entry.get("stage0_formula_inventory")
        if isinstance(stage0, Mapping):
            walk_stage0(stage0)
    return sorted(found)


def coverage_by_element(
    documents: Iterable[Mapping[str, Any]],
    elements: Iterable[str],
) -> dict[str, dict[str, Any]]:
    present: dict[str, list[str]] = defaultdict(list)
    for document in documents:
        table = document.get("table") or document
        table_id = str(table.get("table_id") or "")
        published = table_formula_as_published(table)
        for element in formula_elements(published):
            present[element].append(table_id)
    table: dict[str, dict[str, Any]] = {}
    for element in elements:
        ids = present.get(element, [])
        table[element] = {
            "element": element,
            "has_record": bool(ids),
            "table_count": len(ids),
            "table_ids_head": ids[:8],
        }
    return table


def harvest_era(document: Mapping[str, Any]) -> str:
    extraction = document.get("extraction") or {}
    method = str(extraction.get("method") or "")
    if "HTML" in method or "html table" in method.lower():
        return "html"
    if extraction.get("source_sha256"):
        return "txt"
    return "unknown"
