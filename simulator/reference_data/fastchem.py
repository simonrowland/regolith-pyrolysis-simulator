"""FastChem v3.1.3 mass-action ln K fit compilation.

Parses the seated gas, extended-gas, and condensate coefficient files.
Coefficients stay the printed tokens. Nothing is refit and no second
numeric copy is stored.

On-disk home: ``data/literature/compilations/fastchem-v3-1-3/``.
"""

from __future__ import annotations

import copy
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Iterator

from simulator.reference_data.nasa_glenn import ELEMENT_SYMBOLS
from simulator.yaml_cache import load_cached_safe_yaml

ROOT = Path(__file__).resolve().parents[2]
COMPILATION_ROOT = ROOT / "data" / "literature" / "compilations" / "fastchem-v3-1-3"
SCHEMA_VERSION = "literature_compilation.v1"
MANIFEST_SCHEMA = "literature_compilation_manifest.v1"
SOURCE_ID = "fastchem-v3-1-3"
CORPUS_SOURCE_ID = "fastchem-v3-1-3-gas-condensate-coefficients"
TAG_COMMIT = "51857a84e2b25d4246624c978cdebe988789a729"
OFFICIAL_URL = "https://github.com/NewStrangeWorlds/FastChem"
TAG_TREE = (
    "https://github.com/NewStrangeWorlds/FastChem/tree/"
    "51857a84e2b25d4246624c978cdebe988789a729"
)

GAS_SOURCE_NAME = "fastchem-v3-1-3-gas-condensate-coefficients.dat"
CONDENSATE_SOURCE_NAME = "logK_condensates.dat"
EXTENDED_SOURCE_NAME = "logK_extended.dat"
GAS_SOURCE_REL = f"data/literature/compilations/fastchem-v3-1-3/source/{GAS_SOURCE_NAME}"
CONDENSATE_SOURCE_REL = (
    f"data/literature/compilations/fastchem-v3-1-3/source/{CONDENSATE_SOURCE_NAME}"
)
EXTENDED_SOURCE_REL = (
    f"data/literature/compilations/fastchem-v3-1-3/source/{EXTENDED_SOURCE_NAME}"
)

# File header, gas and condensate files, first line. SOURCE.md parenthesises
# ln(T) and writes T squared; the bytes below are what the files print.
FORM_AS_PUBLISHED = (
    "#logK = a1/T + a2 ln T + a3 + a4 T + a5 T^2 for FastChem:"
)
COEFFICIENT_ORDER = ("a1", "a2", "a3", "a4", "a5")
DOCUMENTATION_ALIASES_NOT_APPLIED = ("a0", "a1", "b0", "b1", "b2")

GAS_T_UNKNOWN_REASON = "gas file prints no per-species temperature bound"
CONDENSATE_T_LOW_REASON = (
    "condensate file prints upper limits only; no lower bound"
)
CONDENSATE_T_HIGH_REASON = (
    "condensate file prints successive upper limits on each coefficient "
    "row, not one species T_max"
)
GAS_PHASE_UNKNOWN_REASON = "gas file prints no phase token"

# Tag v3.1.3 molecule_struct.cpp calcMassActionConstant, comment and factor.
# The parenthetical writes 1e-6 dyn cm-2. One bar is 1e6 dyn cm^-2. The
# factor 1.0e-6 * k * T is kT/1e6, so the arithmetic uses 1 bar and the
# parenthetical is reversed. Coefficients are not rewritten to "fix" it.
GAS_PRESSURE_COMMENT = (
    "adjusting log_K from its standard pressure (1 bar = 1e-6 dyn cm-2) "
    "to the actual pressure"
)
GAS_PRESSURE_FACTOR = "1.0e-6 * CONST_K * temperature"
CONDENSATE_PRESSURE_FACTOR = "1.0e6 / (CONST_K * temperature)"
GAS_SIGMA_RULE = "sigma = 1 - sum(stoichiometric_vector)"
CONDENSATE_SIGMA_RULE = "sigma = sum(stoichiometric_vector)"

SOURCE_LN_SENTENCE = (
    "Stored fits use natural logarithms, T in kelvin and standard pressure 1 bar."
)
SOURCE_DIRECTION_SENTENCE = (
    "Operational mass action is n_i = K_i product(n_j^nu_ij): association of "
    "atomic components into a species, inverse to a conventional dissociation "
    "constant."
)
SOURCE_NOT_FORMATION_SENTENCE = (
    "Do not infer reaction direction solely from that prose or read "
    "coefficients as elemental-reference formation Gibbs energies."
)

# Method papers, seated corpus, read from the preprint PDFs (printed page
# numbers). Quotes are the papers' words. Equation lines are transcriptions
# of the printed formulas, marked as such. The coefficient-packet SOURCE.md
# is not this authority.
STOCK_2022_CITATION = (
    "Stock, J. W.; Kitzmann, D.; Patzer, A. B. C. (2022). FastChem 2. "
    "arXiv:2206.08247v2 preprint, pages as printed on the PDF; related "
    "MNRAS 517, 4070. Corpus raw/stock-2022-fastchem2-methods/"
    "stock-2022-fastchem2-methods.pdf."
)
KITZMANN_2024_CITATION = (
    "Kitzmann, D.; Stock, J. W.; Patzer, A. B. C. (2024). FastChem Cond. "
    "arXiv:2309.02337v2 preprint, pages as printed on the PDF; related "
    "MNRAS 527, 7263. Corpus raw/kitzmann-2024-fastchem-cond-methods/"
    "kitzmann-2024-fastchem-cond-methods.pdf."
)
# Stock et al. 2022, preprint p. 3, sentence through Eq. (8).
STOCK_EQ8_QUOTE = (
    "The temperature dependent mass action constants for each molecule "
    "or ion are fitted as before ln Kbar_i(T) = a0/T + a1 ln T + b0 + "
    "b1 T + b2 T^2, with the five fit coefficients a0, a1, b0, b1, and b2."
)
# Stock et al. 2022, preprint p. 3, sentence around Eq. (9). The page
# prints p with a superscript circle and 10 to the fifth.
STOCK_EQ9_QUOTE = (
    "The natural logarithm of the dimensionless mass action constants "
    "are related to the change in the Gibbs free energy of the "
    "dissociation reaction (1) with respect to the standard-state "
    "pressure p° = 1 bar (= 10^5 Pa)"
)
# Stock et al. 2022, preprint p. 2. Eq. (5) is the product; the words
# around it are the quote.
STOCK_EQ5_QUOTE = (
    "can be calculated using the law of mass action (Guldberg & Waage "
    "1864, 1867, 1879)"
)
STOCK_EQ5_WHERE = (
    "where n_i is the number density of species i and K_i the "
    "temperature dependent mass action constant"
)
STOCK_EQ5_TRANSCRIPTION = (
    "n_i = K_i * product_j n_j**nu_ij, for all i in S excluding the elements"
)
STOCK_EQ1_QUOTE = (
    "dissociative equilibrium is assumed, where the dissociation reaction"
)
# Kitzmann et al. 2024, preprint p. 16.
KITZMANN_PRESSURE_QUOTE = (
    "For the data used in the FastChem code, p° is equal to 1 bar."
)
KITZMANN_EQ_A6_QUOTE = (
    "As stated by Stock et al. (2018), we parameterise ln Kbar_c as a "
    "function of the temperature T in form of the polynomial "
    "ln Kbar_c(T) = a0/T + a1 ln T + b0 + b1 T + b2 T^2, where a_i and "
    "b_i are the corresponding fit coefficients that are tabulated in "
    "the FastChem input file for each species."
)
# Kitzmann et al. 2024, preprint p. 15.
KITZMANN_EQ_A1_QUOTE = (
    "The dimension-less equilibrium constant Kbar_c(T) for a specific "
    "condensate c is calculated from the Gibbs free energy of reaction"
)
KITZMANN_DISSOCIATION_QUOTE = (
    "where Delta_r G_c is the change of Gibbs free energy for the "
    "dissociation reaction"
)
KITZMANN_INTO_ELEMENTS_QUOTE = "of a condensate species into its elements"
# Eq. (A3) second line, transcribed. The dissociation sentence and this
# difference are both printed. Coefficients are not rewritten to reconcile them.
KITZMANN_EQ_A3_TRANSCRIPTION = "G_c(T) - sum_j nu_cj * G_j(T)"
KITZMANN_JANAF_QUOTE = (
    "Thus, the ln Kbar used in FastChem and given in Table A1 should "
    "not be confused with the corresponding ln Kbar tabulated in "
    "thermochemical databases, such as the JANAF tables."
)

FIT_BASIS = {
    "form_as_published": FORM_AS_PUBLISHED,
    "log_basis": "ln",
    "temperature_unit": "K",
    "standard_pressure": "1 bar",
    "coefficient_order": list(COEFFICIENT_ORDER),
    "documentation_column_aliases_not_applied": list(
        DOCUMENTATION_ALIASES_NOT_APPLIED
    ),
    "paper_names_applied": False,
    "coefficients_inverted": False,
    "reaction_direction_status": "not_resolved",
    "authorities": {
        "gas": {
            "citation": STOCK_2022_CITATION,
            "form_page": "preprint p. 3, Eq. (8)",
            "form_quote": STOCK_EQ8_QUOTE,
            "log_basis_and_pressure_page": "preprint p. 3, Eq. (9)",
            "log_basis_and_pressure_quote": STOCK_EQ9_QUOTE,
            "mass_action_page": "preprint p. 2, Eq. (5)",
            "mass_action_quote": STOCK_EQ5_QUOTE,
            "mass_action_where": STOCK_EQ5_WHERE,
            "mass_action_equation_transcription": STOCK_EQ5_TRANSCRIPTION,
            "dissociation_page": "preprint p. 2, Eq. (1)",
            "dissociation_quote": STOCK_EQ1_QUOTE,
        },
        "condensed": {
            "citation": KITZMANN_2024_CITATION,
            "pressure_page": "preprint p. 16, after Eq. (A5)",
            "pressure_quote": KITZMANN_PRESSURE_QUOTE,
            "form_page": "preprint p. 16, Eq. (A6)",
            "form_quote": KITZMANN_EQ_A6_QUOTE,
            "gibbs_page": "preprint p. 15, Eq. (A1)–(A3)",
            "gibbs_quote": KITZMANN_EQ_A1_QUOTE,
            "dissociation_quote": KITZMANN_DISSOCIATION_QUOTE,
            "into_elements_quote": KITZMANN_INTO_ELEMENTS_QUOTE,
            "eq_A3_transcription": KITZMANN_EQ_A3_TRANSCRIPTION,
            "not_janaf_lnK_quote": KITZMANN_JANAF_QUOTE,
        },
    },
}

COMPILATION_ROLE = {
    "kind": "fit",
    "engine_reference_input": True,
    "validation_measurement": False,
    "scoring_eligible": False,
    "battery_refusal": "mass_action_lnK_fit_not_a_closed_quantity",
    "circularity_warning": "Do not validate an engine against a compilation it consumes.",
}

COMPILATION_SOURCE = {
    "database": "FastChem v3.1.3 gas and condensate equilibrium-constant fits",
    "citation": (
        "FastChem project contributors. FastChem v3.1.3 standard gas and "
        "condensate equilibrium-constant fit coefficients, tag commit "
        f"{TAG_COMMIT}. Repository https://github.com/NewStrangeWorlds/FastChem."
    ),
    "version": f"v3.1.3 tag commit {TAG_COMMIT}",
    "official_url": OFFICIAL_URL,
    "tag_commit": TAG_COMMIT,
    "corpus_source_id": CORPUS_SOURCE_ID,
    "licence": (
        "Repository GPL-3 (licence.md). Upstream thermochemical compilations "
        "named on each record keep their own rights; the GPL text does not "
        "relicense them."
    ),
}

# Overlap table only. Not a scorer preference and not PHASE_MAP.
# "ref" is a JANAF element table that prints crystal, liquid, and ideal-gas
# rows, so it meets both phase classes. Aqueous and unparsed labels meet
# neither. Polymorph letters are the condensed labels Burcat and NASA Glenn
# print (a, b, I, gr, ...).
_GAS_PHASE_WORDS = frozenset({"g", "gas", "(g)"})
_CONDENSED_PHASE_WORDS = frozenset(
    {
        "s",
        "solid",
        "l",
        "liquid",
        "liq",
        "cr",
        "c",
        "condensed",
        "crystal",
        "cryst",
        "(c)",
        "(cr)",
        "(l)",
        "(s)",
        "graphite",
        "diamond",
        "gr",
        "a",
        "b",
        "d",
        "i",
        "ii",
        "iii",
        "iv",
        "alpha",
        "beta",
        "gamma",
        "am",
    }
)
_PHASE_QUALIFIERS = frozenset(
    {
        "triplet",
        "singlet",
        "doublet",
        "quartet",
        "trans",
        "cis",
        "anti",
        "syn",
        "ortho",
        "para",
        "gauche",
        "meta",
        "vdw",
        "undissoc",
    }
)
PHASE_CLASS_RULE = (
    "gas tokens g/gas/(g) and an ATcT leading 'g' plus an electronic-state "
    "qualifier; condensed tokens s/solid/l/liquid/liq/cr/c/condensed/crystal "
    "and the Burcat/NASA polymorph labels a/b/d/I-IV/gr/alpha/beta/gamma/am; "
    "JANAF phase ref meets both classes because that table prints crystal, "
    "liquid, and ideal-gas rows; aqueous and not_parsed meet neither. "
    "NASA-7 formula element E is the electron in Burcat and NASA Glenn "
    "composition slots. No compilation is preferred over another."
)

_CONDENSATE_PHASE_FLAGS = {
    "s": "solid",
    "l": "liquid",
    "sl": "solid_liquid",
    "solid": "solid",
    "liquid": "liquid",
    "solid_liquid": "solid_liquid",
}
_ELEMENT_RE = re.compile(r"([A-Z][a-z]?)(\d+(?:\.\d+)?)?")
_TRAILING_PHASE_RE = re.compile(
    r"\((?:g|s|l|cr|c|gr|gas|solid|liquid|ref)\)\s*$",
    re.IGNORECASE,
)
_FLOAT_TOKEN_RE = re.compile(
    r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$"
)


class FastChemParseError(ValueError):
    """The coefficient file is not the v3.1.3 record grammar."""


@dataclass
class FastChemRecord:
    record_id: str
    record_group: str
    symbol: str
    name: str
    phase: str
    phase_as_published: str | None
    phase_flag_normalized: str | None
    charge: int
    stoichiometry: list[dict[str, str]]
    formula: str
    coefficients: list[list[str]]
    temperature_limits: list[str]
    lineage_citation: str | None
    source_rel: str
    header_line: int
    coefficient_lines: list[int]
    end_line: int
    raw_header: str
    raw_coefficient_lines: list[str]
    raw_phase_line: str | None = None
    raw_limits_line: str | None = None
    ambiguities: list[dict[str, Any]] = field(default_factory=list)

    def to_document(self, *, source_sha256: str) -> dict[str, Any]:
        gas = self.phase == "gas"
        t_min: dict[str, str] = {
            "kind": "unknown",
            "reason": GAS_T_UNKNOWN_REASON if gas else CONDENSATE_T_LOW_REASON,
        }
        t_max: dict[str, str] = {
            "kind": "unknown",
            "reason": GAS_T_UNKNOWN_REASON if gas else CONDENSATE_T_HIGH_REASON,
        }
        intervals: list[dict[str, Any]] = []
        for index, coeffs in enumerate(self.coefficients):
            high: dict[str, Any]
            if index < len(self.temperature_limits):
                high = {
                    "as_published": self.temperature_limits[index],
                    "kind": "value",
                }
            else:
                high = {"kind": "unknown", "reason": t_max["reason"]}
            intervals.append(
                {
                    "index": index,
                    "T_low_K": dict(t_min),
                    "T_high_K": high,
                    "coefficients_as_published": list(coeffs),
                    "source_line": self.coefficient_lines[index],
                }
            )
        if self.phase_as_published is None:
            phase_published: Any = {
                "kind": "unknown",
                "reason": GAS_PHASE_UNKNOWN_REASON,
            }
        else:
            phase_published = self.phase_as_published
        if self.lineage_citation is None:
            lineage: Any = {
                "kind": "unknown",
                "reason": "record prints no citation after #",
            }
        else:
            lineage = self.lineage_citation
        document: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "source_id": SOURCE_ID,
            "source": {
                **COMPILATION_SOURCE,
                "path": self.source_rel,
                "sha256": source_sha256,
            },
            "compilation_role": dict(COMPILATION_ROLE),
            "record_id": self.record_id,
            "record_kind": "mass_action_lnK_fit",
            "method_class": "fit",
            "evaluation_class": "compiled",
            "symbol_as_published": self.symbol,
            "name_as_published": self.name,
            "formula": self.formula,
            "phase": self.phase,
            "phase_as_published": phase_published,
            "charge": self.charge,
            "stoichiometry_as_published": self.stoichiometry,
            "fit_basis": copy.deepcopy(FIT_BASIS),
            "source_form": FORM_AS_PUBLISHED,
            "coefficients": [list(row) for row in self.coefficients],
            "coefficients_as_published": [list(row) for row in self.coefficients],
            "T_min_K": t_min,
            "T_max_K": t_max,
            "temperature_limit_rule": (
                "not_printed"
                if gas
                else "successive_upper_bounds"
            ),
            "intervals": intervals,
            "lineage_citation_as_published": lineage,
            "source_locator": {
                "path": self.source_rel,
                "header_line": self.header_line,
                "coefficient_lines": list(self.coefficient_lines),
                "end_line": self.end_line,
            },
            "source_text": {
                "header_line": self.raw_header,
                "coefficient_lines": list(self.raw_coefficient_lines),
                "phase_line": self.raw_phase_line,
                "limits_line": self.raw_limits_line,
            },
            "ambiguities": list(self.ambiguities),
        }
        if self.phase_flag_normalized is not None:
            document["phase_flag_normalized"] = self.phase_flag_normalized
        return document


def _unknown(reason: str) -> dict[str, str]:
    return {"kind": "unknown", "reason": reason}


def parse_species_header(line: str) -> dict[str, Any] | None:
    """One species line: ``symbol [name] : Elem count ... # citation``."""

    if not line or line.startswith("#") or ":" not in line or "#" not in line:
        return None
    left, _, right = line.partition(":")
    stoich_text, hash_mark, citation = right.partition("#")
    if not hash_mark:
        return None
    left_text = left.strip()
    if not left_text:
        return None
    parts = left_text.split()
    symbol = parts[0]
    name = " ".join(parts[1:])
    tokens = stoich_text.split()
    ambiguities: list[dict[str, Any]] = []
    if len(tokens) % 2:
        ambiguities.append(
            {
                "kind": "odd_stoichiometry_token_count",
                "tokens": " ".join(tokens),
            }
        )
    stoich: list[dict[str, str]] = []
    for element, count in zip(tokens[0::2], tokens[1::2]):
        stoich.append({"element": element, "count_as_published": count})
    citation_text = citation.strip()
    return {
        "symbol": symbol,
        "name": name,
        "stoichiometry": stoich,
        "citation": citation_text or None,
        "ambiguities": ambiguities,
    }


def _charge_from_suffix(suffix: str) -> int | None:
    if suffix in {"", None}:
        return 0
    if suffix == "+":
        return 1
    if suffix == "-":
        return -1
    if suffix == "++":
        return 2
    if suffix == "--":
        return -2
    if re.fullmatch(r"\+\d+", suffix):
        return int(suffix[1:])
    if re.fullmatch(r"-\d+", suffix):
        return -int(suffix[1:])
    if re.fullmatch(r"\d+\+", suffix):
        return int(suffix[:-1])
    if re.fullmatch(r"\d+-", suffix):
        return -int(suffix[:-1])
    return None


def _strip_charge_suffix(text: str) -> tuple[str, int]:
    for suffix in ("++", "--", "+", "-"):
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)], _charge_from_suffix(suffix) or 0
    match = re.search(r"(\d+[+-]|[+-]\d+)$", text)
    if match and match.start() > 0:
        charge = _charge_from_suffix(match.group(1))
        if charge is not None:
            return text[: match.start()], charge
    return text, 0


def symbol_element_counts(
    symbol: str,
) -> tuple[dict[str, int], int | None, str | None]:
    """Parse a FastChem symbol. Charge is None when the symbol prints none.

    A parenthetical formula such as ``Ca(OH)2`` is not a flat element-count
    symbol. Callers strip a trailing phase marker first. Leftover
    parentheses stay unparsed rather than dropping the group.
    """

    body = symbol.strip()
    if "(" in body or ")" in body or "." in body:
        return {}, None, "symbol_not_comparable"
    charge: int | None = None
    for suffix, value in (("++", 2), ("--", -2), ("+", 1), ("-", -1)):
        if body.endswith(suffix) and len(body) > len(suffix):
            body = body[: -len(suffix)]
            charge = value
            break
    counts: dict[str, int] = {}
    pos = 0
    while pos < len(body):
        match = re.match(r"([A-Z][a-z]?)(\d*)", body[pos:])
        if match is None:
            return {}, charge, "symbol_not_comparable"
        element = match.group(1)
        if element not in ELEMENT_SYMBOLS or element == "E":
            return {}, charge, "symbol_not_comparable"
        number = int(match.group(2) or "1")
        counts[element] = counts.get(element, 0) + number
        pos += match.end()
    if not counts:
        return {}, charge, "symbol_not_comparable"
    return counts, charge, None


def _integer_token(token: str) -> int | None:
    if re.fullmatch(r"[+-]?\d+", token):
        return int(token)
    return None


def _stoich_maps(
    stoich: list[dict[str, str]],
) -> tuple[dict[str, int], int, list[dict[str, Any]]]:
    """Element counts excluding electrons, and charge = -stoich(e-)."""

    counts: dict[str, int] = {}
    charge = 0
    saw_electron = False
    ambiguities: list[dict[str, Any]] = []
    for pair in stoich:
        element = pair["element"]
        token = pair["count_as_published"]
        number = _integer_token(token)
        if number is None:
            ambiguities.append(
                {
                    "kind": "non_integer_stoichiometry",
                    "element": element,
                    "count_as_published": token,
                }
            )
            continue
        if element == "e-":
            saw_electron = True
            charge = -number
            continue
        if element not in ELEMENT_SYMBOLS or element == "E":
            ambiguities.append(
                {
                    "kind": "unrecognized_element_token",
                    "element": element,
                    "count_as_published": token,
                }
            )
            continue
        counts[element] = counts.get(element, 0) + number
    if not saw_electron:
        charge = 0
    return counts, charge, ambiguities


def _format_count(count: Decimal | int) -> str:
    if isinstance(count, int) or count == count.to_integral_value():
        return str(int(count))
    return format(count.normalize(), "f")


def hill_formula(counts: dict[str, int], charge: int) -> str:
    if "C" in counts:
        ordered = ["C"]
        if "H" in counts:
            ordered.append("H")
        ordered.extend(sorted(element for element in counts if element not in {"C", "H"}))
    else:
        ordered = sorted(counts)
    parts: list[str] = []
    for element in ordered:
        number = counts[element]
        parts.append(element if number == 1 else f"{element}{_format_count(number)}")
    body = "".join(parts)
    if charge == 0:
        return body
    if charge == 1:
        return body + "+"
    if charge == -1:
        return body + "-"
    if charge == 2:
        return body + "++"
    if charge == -2:
        return body + "--"
    if charge > 0:
        return body + f"+{charge}"
    return body + f"{charge}"


def _symbol_bodies(symbol: str) -> list[str]:
    """Printed symbol, then an isomer tag stripped off when the symbol does not parse.

    ``Al1H1O1_1`` and ``F2N2cis`` keep the tag in ``symbol_as_published``.
    The tag is only ignored while comparing element counts to the stoichiometry.
    A symbol that already parses, such as ``F2Si1`` or ``NaCO3(s,l)``, is
    compared as printed.
    """

    body = re.sub(r"\([^)]*\)$", "", symbol).strip()
    bodies = [body]
    if "_" in body:
        bodies.append(body.split("_", 1)[0])
    for suffix in ("cis", "trans"):
        if body.lower().endswith(suffix) and len(body) > len(suffix):
            bodies.append(body[: -len(suffix)])
    unique: list[str] = []
    for item in bodies:
        if item and item not in unique:
            unique.append(item)
    return unique


def _symbol_ambiguities(
    symbol: str,
    stoich_counts: dict[str, int],
    charge: int,
) -> list[dict[str, Any]]:
    bodies = _symbol_bodies(symbol)
    first_parsed: tuple[dict[str, int], int | None] | None = None
    for index, body in enumerate(bodies):
        parsed, symbol_charge, problem = symbol_element_counts(body)
        if problem is not None:
            continue
        if first_parsed is None:
            first_parsed = (parsed, symbol_charge)
        counts_match = parsed == stoich_counts
        charge_match = symbol_charge is None or symbol_charge == charge
        if counts_match and charge_match:
            return []
        # A symbol that parses is a stoichiometry claim. Do not drop a
        # disagreement by trying a shorter suffix.
        if index == 0:
            break
    if first_parsed is None:
        return [{"kind": "symbol_not_comparable", "symbol": symbol}]
    parsed, symbol_charge = first_parsed
    found: list[dict[str, Any]] = []
    if parsed != stoich_counts:
        found.append(
            {
                "kind": "symbol_stoichiometry_mismatch",
                "symbol": symbol,
                "symbol_counts": {key: parsed[key] for key in sorted(parsed)},
                "stoichiometry_counts": {
                    key: stoich_counts[key] for key in sorted(stoich_counts)
                },
            }
        )
    if symbol_charge is not None and symbol_charge != charge:
        found.append(
            {
                "kind": "symbol_charge_mismatch",
                "symbol": symbol,
                "symbol_charge": symbol_charge,
                "stoichiometry_charge": charge,
            }
        )
    return found


def _coefficient_tokens(line: str) -> list[str]:
    return line.split()


def _looks_like_coefficients(tokens: list[str]) -> bool:
    return len(tokens) >= 1 and all(_FLOAT_TOKEN_RE.fullmatch(token) for token in tokens)


def _coefficient_ambiguity(tokens: list[str]) -> dict[str, Any] | None:
    if len(tokens) == 5 and _looks_like_coefficients(tokens):
        return None
    return {
        "kind": "coefficient_token_count",
        "count": len(tokens),
        "expected": 5,
        "tokens": list(tokens),
    }


def _take_separator(lines: list[str], index: int) -> tuple[int, dict[str, Any] | None]:
    if index >= len(lines):
        return index, None
    if not lines[index].strip():
        return index + 1, None
    if parse_species_header(lines[index]) is not None:
        return index, None
    return index + 1, {
        "kind": "non_header_separator",
        "line_number": index + 1,
        "text": lines[index],
    }


def parse_gas_lines(
    lines: list[str],
    *,
    source_rel: str,
    record_prefix: str,
    record_group: str,
) -> list[FastChemRecord]:
    if not lines or lines[0] != FORM_AS_PUBLISHED:
        raise FastChemParseError(
            f"{source_rel}: first line is not the FastChem ln K header"
        )
    index = 0
    while index < len(lines) and lines[index].startswith("#"):
        index += 1
    records: list[FastChemRecord] = []
    while index < len(lines):
        if not lines[index].strip():
            raise FastChemParseError(
                f"{source_rel}:{index + 1}: blank line where a species header belongs"
            )
        header = parse_species_header(lines[index])
        if header is None:
            raise FastChemParseError(
                f"{source_rel}:{index + 1}: not a species header"
            )
        header_line = index + 1
        coeff_index = index + 1
        if coeff_index >= len(lines):
            raise FastChemParseError(
                f"{source_rel}:{header_line}: species header has no coefficient line"
            )
        tokens = _coefficient_tokens(lines[coeff_index])
        if not _looks_like_coefficients(tokens):
            raise FastChemParseError(
                f"{source_rel}:{coeff_index + 1}: coefficient line is not five numbers"
            )
        stoich_counts, charge, stoich_notes = _stoich_maps(header["stoichiometry"])
        ambiguities = list(header["ambiguities"])
        ambiguities.extend(stoich_notes)
        ambiguities.extend(_symbol_ambiguities(header["symbol"], stoich_counts, charge))
        coeff_note = _coefficient_ambiguity(tokens)
        if coeff_note is not None:
            ambiguities.append(coeff_note)
        formula = hill_formula(stoich_counts, charge) if stoich_counts else ""
        if not formula:
            ambiguities.append({"kind": "empty_formula", "symbol": header["symbol"]})
        next_index, separator_note = _take_separator(lines, coeff_index + 1)
        if separator_note is not None:
            ambiguities.append(separator_note)
        records.append(
            FastChemRecord(
                record_id=f"{record_prefix}{len(records) + 1:04d}",
                record_group=record_group,
                symbol=header["symbol"],
                name=header["name"],
                phase="gas",
                phase_as_published=None,
                phase_flag_normalized=None,
                charge=charge,
                stoichiometry=header["stoichiometry"],
                formula=formula,
                coefficients=[tokens],
                temperature_limits=[],
                lineage_citation=header["citation"],
                source_rel=source_rel,
                header_line=header_line,
                coefficient_lines=[coeff_index + 1],
                end_line=coeff_index + 1,
                raw_header=lines[index],
                raw_coefficient_lines=[lines[coeff_index]],
                ambiguities=ambiguities,
            )
        )
        index = next_index
    return records


def parse_condensate_lines(
    lines: list[str],
    *,
    source_rel: str,
) -> list[FastChemRecord]:
    if not lines or lines[0] != FORM_AS_PUBLISHED:
        raise FastChemParseError(
            f"{source_rel}: first line is not the FastChem ln K header"
        )
    index = 0
    while index < len(lines) and lines[index].startswith("#"):
        index += 1
    records: list[FastChemRecord] = []
    while index < len(lines):
        if not lines[index].strip():
            raise FastChemParseError(
                f"{source_rel}:{index + 1}: blank line where a species header belongs"
            )
        header = parse_species_header(lines[index])
        if header is None:
            raise FastChemParseError(
                f"{source_rel}:{index + 1}: not a species header"
            )
        header_line = index + 1
        if index + 2 >= len(lines):
            raise FastChemParseError(
                f"{source_rel}:{header_line}: condensate record is truncated"
            )
        phase_line = lines[index + 1]
        limits_line = lines[index + 2]
        phase_token = phase_line.strip()
        normalized = _CONDENSATE_PHASE_FLAGS.get(phase_token)
        ambiguities = list(header["ambiguities"])
        if normalized is None:
            ambiguities.append(
                {
                    "kind": "unrecognized_phase_flag",
                    "token": phase_token,
                }
            )
        limits = limits_line.split()
        if not limits or not all(_FLOAT_TOKEN_RE.fullmatch(token) for token in limits):
            raise FastChemParseError(
                f"{source_rel}:{header_line + 2}: temperature-limit line is not floats"
            )
        coeff_start = index + 3
        coeff_end = coeff_start + len(limits)
        if coeff_end > len(lines):
            raise FastChemParseError(
                f"{source_rel}:{header_line}: not enough coefficient lines for {len(limits)} limits"
            )
        rows: list[list[str]] = []
        raw_rows: list[str] = []
        coeff_line_numbers: list[int] = []
        for line_index in range(coeff_start, coeff_end):
            tokens = _coefficient_tokens(lines[line_index])
            if not _looks_like_coefficients(tokens):
                raise FastChemParseError(
                    f"{source_rel}:{line_index + 1}: coefficient line is not numeric"
                )
            note = _coefficient_ambiguity(tokens)
            if note is not None:
                note["line_number"] = line_index + 1
                ambiguities.append(note)
            rows.append(tokens)
            raw_rows.append(lines[line_index])
            coeff_line_numbers.append(line_index + 1)
        stoich_counts, charge, stoich_notes = _stoich_maps(header["stoichiometry"])
        ambiguities.extend(stoich_notes)
        ambiguities.extend(_symbol_ambiguities(header["symbol"], stoich_counts, charge))
        formula = hill_formula(stoich_counts, charge) if stoich_counts else ""
        if not formula:
            ambiguities.append({"kind": "empty_formula", "symbol": header["symbol"]})
        next_index, separator_note = _take_separator(lines, coeff_end)
        if separator_note is not None:
            ambiguities.append(separator_note)
        records.append(
            FastChemRecord(
                record_id=f"FC-C-{len(records) + 1:04d}",
                record_group="condensate",
                symbol=header["symbol"],
                name=header["name"],
                phase="condensed",
                phase_as_published=phase_token,
                phase_flag_normalized=normalized,
                charge=charge,
                stoichiometry=header["stoichiometry"],
                formula=formula,
                coefficients=rows,
                temperature_limits=limits,
                lineage_citation=header["citation"],
                source_rel=source_rel,
                header_line=header_line,
                coefficient_lines=coeff_line_numbers,
                end_line=coeff_line_numbers[-1],
                raw_header=lines[index],
                raw_coefficient_lines=raw_rows,
                raw_phase_line=phase_line,
                raw_limits_line=limits_line,
                ambiguities=ambiguities,
            )
        )
        index = next_index
    return records


def shared_gas_differences(
    standard: list[FastChemRecord],
    extended: list[FastChemRecord],
) -> list[str]:
    """Standard rows must reappear in the extended file with the same tokens.

    Comparison is the printed header and the whitespace-split coefficient
    tokens. Trailing spaces on a coefficient line are not a disagreement.
    """

    by_header: dict[str, list[FastChemRecord]] = defaultdict(list)
    for record in standard:
        by_header[record.raw_header].append(record)
    used: Counter[str] = Counter()
    differences: list[str] = []
    for record in extended:
        bucket = by_header.get(record.raw_header)
        if not bucket:
            continue
        seen = used[record.raw_header]
        if seen >= len(bucket):
            differences.append(f"extra copy of standard header: {record.raw_header}")
            continue
        used[record.raw_header] += 1
        source = bucket[seen]
        if record.coefficients != source.coefficients:
            differences.append(
                f"coefficient mismatch for {record.raw_header}: "
                f"standard {source.coefficients} extended {record.coefficients}"
            )
    for header, bucket in by_header.items():
        if used[header] != len(bucket):
            differences.append(
                f"standard header missing from extended file: {header}"
            )
    return differences


def extended_only_records(
    standard: list[FastChemRecord],
    extended: list[FastChemRecord],
) -> list[FastChemRecord]:
    remaining: Counter[str] = Counter(record.raw_header for record in standard)
    selected: list[FastChemRecord] = []
    for record in extended:
        if remaining[record.raw_header]:
            remaining[record.raw_header] -= 1
            continue
        selected.append(record)
    return selected


def mark_duplicate_symbols(records: list[FastChemRecord]) -> None:
    counts = Counter(record.symbol for record in records)
    for record in records:
        if counts[record.symbol] > 1:
            record.ambiguities.append(
                {
                    "kind": "duplicate_symbol",
                    "symbol": record.symbol,
                    "count": counts[record.symbol],
                }
            )


def assign_extended_ids(records: list[FastChemRecord]) -> None:
    for index, record in enumerate(records, start=1):
        record.record_id = f"FC-X-{index:04d}"
        record.record_group = "extended_gas"


def coefficients_match_raw_lines(document: dict[str, Any], lines: list[str]) -> bool:
    """Stored coefficient tokens equal the raw file lines named by the locator."""

    locator = document.get("source_locator") or {}
    numbers = locator.get("coefficient_lines") or []
    stored = document.get("coefficients_as_published")
    if not isinstance(stored, list) or len(stored) != len(numbers):
        return False
    for tokens, line_number in zip(stored, numbers):
        if not isinstance(line_number, int) or line_number < 1 or line_number > len(lines):
            return False
        if not isinstance(tokens, list):
            return False
        if lines[line_number - 1].split() != tokens:
            return False
    return True


def _quantized(value: Decimal) -> Decimal:
    if value == value.to_integral_value():
        return Decimal(int(value))
    return value.normalize()


def composition_key(
    counts: dict[str, Decimal] | dict[str, int],
    charge: int,
) -> tuple[tuple[tuple[str, Decimal], ...], int]:
    items = tuple(
        sorted(
            (element, _quantized(Decimal(str(count))))
            for element, count in counts.items()
            if _quantized(Decimal(str(count))) != 0
        )
    )
    return items, int(charge)


def phase_classes(token: object) -> frozenset[str]:
    if token is None:
        return frozenset()
    text = str(token).strip()
    if not text or text.casefold() in {"not_parsed", "none", "null"}:
        return frozenset()
    if text.casefold() == "ref":
        return frozenset({"gas", "condensed"})
    found: set[str] = set()
    unknown = False
    for part in text.split(","):
        word = part.strip().casefold()
        if not word or word in _PHASE_QUALIFIERS or "h2o" in word:
            continue
        if word in _GAS_PHASE_WORDS:
            found.add("gas")
        elif word in _CONDENSED_PHASE_WORDS:
            found.add("condensed")
        else:
            unknown = True
    if unknown:
        return frozenset()
    return frozenset(found)


def _parse_chemical_formula(
    formula: str,
) -> tuple[dict[str, Decimal], int] | None:
    text = formula.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    if not text:
        return None
    if text.casefold() in {"e", "e-", "electron"}:
        return None
    text = _TRAILING_PHASE_RE.sub("", text).strip()
    bracket = re.fullmatch(r"\[(.+)\](\+\+|--|\+|-|\+\d+|-\d+|\d+\+|\d+-)?", text)
    if bracket:
        text = bracket.group(1)
        charge = _charge_from_suffix(bracket.group(2) or "")
        if charge is None:
            return None
    else:
        text, charge = _strip_charge_suffix(text)
    if "[" in text or "]" in text or "(" in text or ")" in text:
        return None
    counts: dict[str, Decimal] = {}
    pos = 0
    if not text:
        return None
    while pos < len(text):
        match = _ELEMENT_RE.match(text, pos)
        if match is None or match.start() != pos:
            return None
        element = match.group(1)
        if element not in ELEMENT_SYMBOLS:
            return None
        raw_count = match.group(2) or "1"
        try:
            number = _quantized(Decimal(raw_count))
        except Exception:
            return None
        if element == "E":
            if number != number.to_integral_value():
                return None
            charge -= int(number)
        else:
            counts[element] = _quantized(counts.get(element, Decimal(0)) + number)
        pos = match.end()
    if pos != len(text) or not counts:
        return None
    return counts, charge


def _nasa_composition(
    document: dict[str, Any],
) -> tuple[dict[str, Decimal], int] | None:
    slots = document.get("composition")
    if not isinstance(slots, list):
        return None
    counts: dict[str, Decimal] = {}
    charge = 0
    saw_element = False
    for slot in slots:
        if not isinstance(slot, dict) or slot.get("filler"):
            continue
        element = slot.get("element")
        if not element:
            continue
        raw_count = slot.get("count")
        if raw_count is None:
            return None
        number = _quantized(Decimal(str(raw_count)))
        if number == 0:
            continue
        saw_element = True
        if element == "E":
            if number != number.to_integral_value():
                return None
            charge += -int(number)
            continue
        counts[element] = _quantized(counts.get(element, Decimal(0)) + number)
    if not saw_element or not counts:
        return None
    return counts, charge


@dataclass
class PeerSpecies:
    compilation: str
    record_id: str
    formula: str
    phase: str
    key: tuple[tuple[tuple[str, Decimal], ...], int]
    phase_classes: frozenset[str]


def _iter_json_records(directory: Path) -> Iterator[dict[str, Any]]:
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        yield json.loads(path.read_text(encoding="utf-8"))


def _peer_from_nasa_json(compilation: str, directory: Path) -> tuple[list[PeerSpecies], int, int]:
    species: list[PeerSpecies] = []
    bare = 0
    unparsed = 0
    for document in _iter_json_records(directory):
        parsed = _nasa_composition(document)
        if parsed is None:
            slots = document.get("composition")
            elements = []
            if isinstance(slots, list):
                elements = [
                    slot.get("element")
                    for slot in slots
                    if isinstance(slot, dict) and slot.get("element") and not slot.get("filler")
                ]
            if elements and set(elements) == {"E"}:
                bare += 1
            else:
                unparsed += 1
            continue
        counts, charge = parsed
        phase = str(document.get("phase") or "")
        classes = phase_classes(phase)
        species.append(
            PeerSpecies(
                compilation=compilation,
                record_id=str(document.get("record_id") or ""),
                formula=str(document.get("formula") or ""),
                phase=phase,
                key=composition_key(counts, charge),
                phase_classes=classes,
            )
        )
    return species, bare, unparsed


def _peer_from_manifest_entries(
    compilation: str,
    manifest_path: Path,
    *,
    id_field: str,
) -> tuple[list[PeerSpecies], int, int]:
    if not manifest_path.is_file():
        return [], 0, 0
    payload = load_cached_safe_yaml(manifest_path.read_text(encoding="utf-8")) or {}
    species: list[PeerSpecies] = []
    bare = 0
    unparsed = 0
    for entry in payload.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        formula = str(entry.get("formula_normalised") or entry.get("formula") or "").strip()
        published = str(entry.get("formula") or formula).strip()
        charge_field = entry.get("charge")
        parsed = _parse_chemical_formula(formula)
        if parsed is None and formula != published:
            parsed = _parse_chemical_formula(published)
        if parsed is None:
            if published.casefold() in {"e", "e-", "electron"}:
                bare += 1
            else:
                unparsed += 1
            continue
        counts, parsed_charge = parsed
        if charge_field is not None:
            try:
                stated = int(charge_field)
            except (TypeError, ValueError):
                unparsed += 1
                continue
            if stated != parsed_charge:
                # formula_normalised already dropped a suffix, so the field wins
                # when the formula parse has charge 0. A conflict is not a match.
                if parsed_charge == 0:
                    parsed_charge = stated
                else:
                    unparsed += 1
                    continue
        phase = str(entry.get("phase") or "")
        species.append(
            PeerSpecies(
                compilation=compilation,
                record_id=str(entry.get(id_field) or ""),
                formula=published,
                phase=phase,
                key=composition_key(counts, parsed_charge),
                phase_classes=phase_classes(phase),
            )
        )
    return species, bare, unparsed


def load_peer_species(compilations: Path) -> tuple[list[PeerSpecies], dict[str, int], dict[str, int]]:
    burcat, burcat_bare, burcat_unparsed = _peer_from_nasa_json(
        "burcat", compilations / "burcat" / "records"
    )
    glenn, glenn_bare, glenn_unparsed = _peer_from_nasa_json(
        "nasa-glenn", compilations / "nasa-glenn" / "records"
    )
    janaf, janaf_bare, janaf_unparsed = _peer_from_manifest_entries(
        "janaf",
        compilations / "janaf" / "manifest.yaml",
        id_field="table_id",
    )
    atct, atct_bare, atct_unparsed = _peer_from_manifest_entries(
        "atct",
        compilations / "atct" / "manifest.yaml",
        id_field="record_id",
    )
    bare = {
        "burcat": burcat_bare,
        "nasa-glenn": glenn_bare,
        "janaf": janaf_bare,
        "atct": atct_bare,
    }
    unparsed = {
        "burcat": burcat_unparsed,
        "nasa-glenn": glenn_unparsed,
        "janaf": janaf_unparsed,
        "atct": atct_unparsed,
    }
    return [*burcat, *glenn, *janaf, *atct], bare, unparsed


def _fastchem_key(record: FastChemRecord) -> tuple[tuple[tuple[str, Decimal], ...], int] | None:
    counts, charge, notes = _stoich_maps(record.stoichiometry)
    if notes or not counts:
        return None
    return composition_key(counts, charge)


def overlap_rows(
    records: Iterable[FastChemRecord],
    peers: Iterable[PeerSpecies],
) -> list[dict[str, str]]:
    index: dict[tuple[tuple[tuple[str, Decimal], ...], int], list[PeerSpecies]] = defaultdict(list)
    for peer in peers:
        if not peer.phase_classes:
            continue
        index[peer.key].append(peer)
    rows: list[dict[str, str]] = []
    for record in records:
        key = _fastchem_key(record)
        if key is None:
            continue
        own = frozenset({"gas" if record.phase == "gas" else "condensed"})
        for peer in index.get(key, ()):
            if not (own & peer.phase_classes):
                continue
            phase_published = record.phase_as_published or ""
            rows.append(
                {
                    "fastchem_record_id": record.record_id,
                    "symbol": record.symbol,
                    "formula": record.formula,
                    "phase": record.phase,
                    "phase_as_published": phase_published,
                    "peer_compilation": peer.compilation,
                    "peer_record_id": peer.record_id,
                    "peer_formula": peer.formula,
                    "peer_phase": peer.phase,
                    "composition_key": _key_text(key),
                }
            )
    rows.sort(
        key=lambda row: (
            row["fastchem_record_id"],
            row["peer_compilation"],
            row["peer_record_id"],
        )
    )
    return rows


def _key_text(key: tuple[tuple[tuple[str, Decimal], ...], int]) -> str:
    counts, charge = key
    body = ",".join(f"{element}:{_format_count(count)}" for element, count in counts)
    return f"{body}|charge={charge}"


def overlap_summary(
    records: list[FastChemRecord],
    rows: list[dict[str, str]],
    *,
    bare_electron: dict[str, int],
    unparsed_peer_formulas: dict[str, int],
) -> dict[str, Any]:
    by_peer: dict[str, set[str]] = defaultdict(set)
    any_overlap: set[str] = set()
    for row in rows:
        by_peer[row["peer_compilation"]].add(row["fastchem_record_id"])
        any_overlap.add(row["fastchem_record_id"])
    return {
        "fastchem_record_count": len(records),
        "matched_rows": len(rows),
        "fastchem_with_any_overlap": len(any_overlap),
        "matched_species_by_peer": {
            name: len(ids) for name, ids in sorted(by_peer.items())
        },
        "bare_electron_peer_records": bare_electron,
        "unparsed_peer_formulas": unparsed_peer_formulas,
        "phase_class_rule": PHASE_CLASS_RULE,
        "preference": (
            "none; both sides are kept; the scorer's existing authority "
            "ladder is unchanged"
        ),
    }


OVERLAP_COLUMNS = (
    "fastchem_record_id",
    "symbol",
    "formula",
    "phase",
    "phase_as_published",
    "peer_compilation",
    "peer_record_id",
    "peer_formula",
    "peer_phase",
    "composition_key",
)


def write_overlap_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(OVERLAP_COLUMNS),
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def compilation_readme(summary: dict[str, Any]) -> str:
    overlap = summary["overlap"]
    by_peer = overlap["matched_species_by_peer"]
    peer_bits = ", ".join(
        f"{name} {count}" for name, count in sorted(by_peer.items())
    ) or "none"
    return f"""# FastChem v3.1.3 mass-action fits

Frozen tag commit `{TAG_COMMIT}` (v3.1.3, not current master). Compiled and
refitted literature constants, not independent experiments. The harvester
copies the seated files and stores each printed coefficient token. It does
not refit them and does not store a converted twin.

## Fit

File header, gas and condensate files, first line:

```
{FORM_AS_PUBLISHED}
```

Authority for the form, the logarithm, the standard pressure, and the
reaction direction is the seated method papers.

Stock, Kitzmann & Patzer (2022), FastChem 2, arXiv:2206.08247v2,
corpus `stock-2022-fastchem2-methods`, preprint p. 3, Eq. (8):

> {STOCK_EQ8_QUOTE}

Same page, the sentence around Eq. (9):

> {STOCK_EQ9_QUOTE}

Preprint p. 2: "{STOCK_EQ1_QUOTE}" (Eq. 1). The mass-action sentence is
"{STOCK_EQ5_QUOTE}", and Eq. (5) transcribes as `{STOCK_EQ5_TRANSCRIPTION}`.
The paper continues: "{STOCK_EQ5_WHERE}".

Kitzmann, Stock & Patzer (2024), FastChem Cond, arXiv:2309.02337v2,
corpus `kitzmann-2024-fastchem-cond-methods`, preprint p. 16:

> {KITZMANN_PRESSURE_QUOTE}

Eq. (A6):

> {KITZMANN_EQ_A6_QUOTE}

Preprint p. 15: "{KITZMANN_EQ_A1_QUOTE}". "{KITZMANN_DISSOCIATION_QUOTE}"
"{KITZMANN_INTO_ELEMENTS_QUOTE}" (Eq. A2). Eq. (A3) prints
`{KITZMANN_EQ_A3_TRANSCRIPTION}`. "{KITZMANN_JANAF_QUOTE}"

The stored tokens are the printed polynomial. They are not inverted to
force Eq. (5) and Eq. (9), or Eq. (A2) and Eq. (A3), onto one sign.
`reaction_direction_status` is `not_resolved`.

The header's `ln T` is a natural logarithm (`std::log` in tag
`molecule_struct.cpp` / `condensate_struct.cpp`). Column order on the
file is a1, a2, a3, a4, a5. Eq. (8) and Eq. (A6) name that same order
a0, a1, b0, b1, b2. Those paper names are not applied to the stored tokens.

The coefficient-packet SOURCE.md is not the authority above. It says:
{SOURCE_LN_SENTENCE} {SOURCE_DIRECTION_SENTENCE} {SOURCE_NOT_FORMATION_SENTENCE}

Tag `calcNumberDensity` evaluates

`n_species = exp(ln K_adjusted + sum_j nu_j * ln n_j)`

with `nu_j` the printed stoichiometric powers. Electrons may be negative:
`charge = -stoich(e-)`, so `e- -1` is a cation and `e- 1` is an anion.

Standard pressure is 1 bar. Gas code comment: "{GAS_PRESSURE_COMMENT}".
Implemented gas factor `{GAS_PRESSURE_FACTOR}` with `{GAS_SIGMA_RULE}`.
That factor is `kT/1e6`, and 1 bar = 1e6 dyn cm^-2, so the parenthetical
`1e-6 dyn cm-2` is reversed. Condensates use `{CONDENSATE_PRESSURE_FACTOR}`
with `{CONDENSATE_SIGMA_RULE}`. Neither factor is applied to the stored
coefficients.

## Temperature bounds

Gas records print no per-species temperature bound. Solver checks at
100-6000 K are not stored as fit validity. Condensate numbers on the limit
line are successive upper bounds: the reader takes the first limit that is
`>= T`, and the activity helper can refuse `T` above the last limit. No
lower bound is printed, so `T_low_K` is unknown. `sl` is one species with
two coefficient rows, not two species. The file does not label row 0 solid
and row 1 liquid.

## What was ingested

{summary["record_count"]} records: {summary["standard_gas_count"]} standard
gas (`FC-G`), {summary["extended_only_gas_count"]} extended-only gas ions
(`FC-X`), {summary["condensate_count"]} condensates (`FC-C`).
`logK_extended.dat` repeats the standard gas rows with the same headers and
coefficient tokens ({summary["shared_header_or_coefficient_disagreements"]}
disagreements) and adds the ions. Shared rows are stored once, from the
primary gas file. `logK_wo_ions.dat` is named in README.TXT and is not in
the seated packet.

Per-record citations are the text after `#`. Most gas and condensate rows
cite Chase, JANAF thermochemical tables, 1998. The extended ions are mostly
Saha/Kurucz/NIST; one cites Gordon and McBride, NASA/TP--1999-208523.
Two symbols disagree with the printed stoichiometry and both are kept:
`NaCO3(s,l)` is `C 1 Na 2 O 3`, and `F2Si1` (Difluorosilylene) is
`F 2 Si 2`. The formula used for identity is the stoichiometry, not the
symbol. Isomer tags such as `_1`, `_cnn`, `cis`, and `trans` stay on the
symbol; they are not a second formula. No `Na4SiO4` or `Na6Si2O7`.

## Scorer

`compilation_role.kind` is `fit`, `method_class` is `fit`,
`evaluation_class` is `compiled`, and `scoring_eligible` is false.
The record has no closed Quantity: the columns are a mass-action ln K
polynomial, not log10 Kf and not a Gibbs table. A later store rebuild
would write `observations-v2/compilations-fastchem-v3-1-3.yaml` with one
observation per record, quantity unknown, value unavailable because
`source_form` and `coefficients` are set, temperature unknown, evidence
`compilation_assessed`. Gas phase `gas` maps to phase G. Phase
`condensed` is not in the migrator's phase map, so those observations
stay phase-unknown. No scoring row is added, and no authority-ladder
preference was added. Engines are not wired to these coefficients.

## Overlap

`overlap.tsv` lists every FastChem species whose stoichiometry, charge,
and phase class also occur in JANAF, Burcat, NASA Glenn, or ATcT.
{overlap["fastchem_with_any_overlap"]} of {overlap["fastchem_record_count"]}
FastChem records meet at least one peer ({overlap["matched_rows"]} pairs;
species by peer: {peer_bits}). Neither side is deleted. {PHASE_CLASS_RULE}

Unparsed peer rows are not matches and are not dropped from those
compilations: {overlap["unparsed_peer_formulas"]}. ATcT parentheses
(`CH3C(O)OH`, `OS(O)O`) are structural formulas, not a flat element list.
Burcat rows in that count have no usable element composition (comment-only
notes and non-element slot tokens). Leaving them unmatched under-counts
overlap.
"""


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    target = path or (COMPILATION_ROOT / "manifest.yaml")
    payload = load_cached_safe_yaml(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise FastChemParseError(f"{target}: manifest is not a mapping")
    return payload


def iter_record_paths(root: Path | None = None) -> list[Path]:
    records = (root or COMPILATION_ROOT) / "records"
    return sorted(records.glob("FC-*.json"))


def species_header_count(lines: list[str]) -> int:
    """Species lines counted without the record parser. A census, not a parse."""

    return sum(
        1
        for line in lines
        if not line.startswith("#") and ":" in line and "#" in line and line.strip()
    )
