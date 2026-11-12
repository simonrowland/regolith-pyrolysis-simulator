#!/usr/bin/env python3
"""Validate literature extract YAML files (t-508 / literature_extract.v1).

Fail-loud rules (non-exhaustive; see data/literature/extracts/SCHEMA.md):

* ``schema_version`` must be ``literature_extract.v1``
* ``source_id`` must match the filename stem (companion tables instead use
  the source-prefixed stem and a linked parent extract)
* source citation + extraction method/date/worker + review_status required
* observation ``type`` in the closed set
* every observation has a locator with at least one location key
* equipment-metadata fields, if present, are objects with value+locator
* bare scalar equipment fields refused; null values refused
* empty / null-only ``values`` payloads refused (pointer rows must carry
  explicit structured content)
* observation_id unique within the file (cross-species)
* fidelity_samples required on extracts that carry any observation
  (ENFORCED_FOR_NEW — pre-policy pilot allowlist is exempt; see
  ``_fidelity_pre_policy_allowlist.yaml``)
* fidelity sample structure: path-based *or* structured line-item form
  (species, observable, T/index, value, locator)
* source fidelity is a typed, counted warning at this chokepoint
  (``uncovered_observation`` / ``unattested_sample`` /
  ``auto_migrated_pilot_pin`` / ``source_blind_pin`` /
  ``ambiguous_observation_id`` / ``missing_observation_id`` /
  ``non_finite_evidence``). A draft
  corpus is not rejected for those warnings. Promoting the extract
  (``review_status: reviewed``, graduation, load, emit, or the index)
  refuses while any remain. Certification opens a source artifact
  (paper, OCR, or quotes — not the extract and not program source) and
  requires each resolved extract number as a complete token: sign and
  exponent belong to the token, so 0.24 is not -0.24 or 0.24e-5, and
  1.0 is not 1. A sentence period does not extend that token, so 1
  matches "the value is 1." A CSV or TSV source, including a table
  artifact with that suffix, is parsed completely before the locator is
  applied. A cell certifies a stored value only when the whole trimmed,
  unquoted cell parses to that number, or equals the stored string.
  The comparison is decimal.Decimal of the trimmed, unquoted cell against
  repr of a stored float or str of a stored int. The prose numeric grammar
  is not a gate on a cell, so 91. is 91, 0.9100 is 0.91, 1e-999 is not 0,
  and 9007199254740993 is not 9007199254740992. A cell NaN, Infinity, or
  InvalidOperation does not certify a number. A non-finite measured value
  (NaN or ±Inf stored as a float) is ``non_finite_evidence``: it refuses
  certification of that observation, is never omitted, and is not cleared
  by attesting a finite sibling. A substring does not certify, so a cell 1/2 is
  not 1 and a cell 1,000 is not 1. A line_range selects only records that lie entirely
  inside it. A span that cuts through a quoted cell is source-blind and
  is not re-parsed from the sliced lines. 1 K is not 1 KPa.
  A MinerU markdown table is one ``<table>`` line, so a line_range cannot
  isolate a row. ``table_row`` and ``table_column`` (the same indexes as
  ``table_row_number`` and ``table_column_number``) select one cell after
  the HTML table is parsed, rowspan and colspan included. The stored
  number must be that whole trimmed cell. A cited span that includes a
  line of an HTML table and has no ``table_row`` or ``table_column``
  refuses with ``html_table_requires_cell_address``. A value on a prose
  line in that span still certifies. A value present only in the table
  does not. A superscript cell certifies only when that cell is a
  complete power and nothing else: an optional sign, a base number, an
  optional × or x or ·, then 10 and the superscript exponent, or the
  base raised to that exponent. ``10<sup>3</sup>`` certifies ``1e3``.
  ``1.5×10<sup>3</sup>`` certifies 1500 / ``1.5e3``. A leading minus
  applies to the whole power, so ``-10<sup>2</sup>`` is ``-100`` and
  not ``100``. ``10<sup>3</sup>2``
  and any other leading or trailing content are ``superscript_in_cell``,
  as is a plain number. A value in another row
  does not certify. Formula and species labels are normalized before every
  comparison, including a label with no ASCII digit. Markup folds
  inside each whitespace token and does not delete a separator. ``_{n}``,
  a bare ``_`` digit, Unicode subscript digits, ``$...$``, and a
  ``\\mathrm`` / ``\\text`` / ``\\rm`` command are removed inside the
  token. A space inside ``$...$`` stays a separator, so ``$H_{2} O_{2}$``
  is ``H2`` and ``O2``, and ``$SiO_{2} 100$`` is ``SiO2`` and ``100``.
  A caret is a superscript. A token that contains ``^`` is not unwrapped
  and does not certify as a plain formula or a plain number, so
  ``$O^{2-}$`` is not ``O2`` and ``$SiO^{2}$`` is not ``SiO2``.
  A run immediately followed by ``^``, ``<sup>``, or a Unicode
  superscript certifies only a label that includes that same charge, so
  ``Fe`` is not ``Fe^{2+}``.
  A subscript digit inside a formula token is not a number: ``SiO_{2}``
  and ``SiO₂`` do not certify 2, and ``Fe_{2}O_{3}`` does not certify 2.
  ``SiO_{2} 100`` still certifies 100. A formula label matches one whole
  run of element symbols, digits, parentheses, square brackets, and the
  charge marks ·, +, and -. ``SO4`` is not ``Fe₂(SO₄)₃``. ``Fe2(SO4)3``
  is. ``CaO·SiO2`` certifies only as that whole run, so ``SiO2`` does not.
  A token with no internal space, including
  ``${\\mathrm{{Al}}}_{2}{\\mathrm{O}}_{3}$``, goes through
  ``printed_formula``. A layout space is removed
  only between an element symbol or a closing parenthesis and the digit
  count that follows it. ``SiO 2`` is ``SiO2``. A space after a count
  stays, so ``H2 O2``, ``SiO2 Al2O3``, and ``Fe 2 O 3`` (folded to
  ``Fe2 O3``) stay separate tokens. ``CO`` is not ``CO₂`` and ``SiO₂`` is
  not ``SiO₂₂``. A thin space (U+2009), narrow no-break space (U+202F),
  or no-break space (U+00A0) in an unsigned digit run is typography.
  An ASCII space is not, so ``1 100`` certifies 1 and 100 and does not
  certify 1100. The first group is one to three digits and each group
  after it is exactly three, so ``1 650`` and ``1 650`` are 1650. The
  run starts at the beginning of the text or after whitespace or
  punctuation, never after a letter, so ``SiO2 100`` stays ``SiO2``
  and ``100``. ``1e0 001``, ``12 34``, ``1.5 000``, and ``1 550e3`` are
  not one number. A short group leaves the separated run unjoined, so
  ``12\\u200934\\u2009567`` is not ``34567`` and ``1\\u2009650\\u20091``
  is not ``1650``. The formula-label path does not apply this fold.
  A comma is
  not that separator. A numeric leaf does not match a formula by the
  formula fold.
  A leading decimal is a number, so .24 is not a label
  and is not the token -0.24, 10.24, or 0.24. A float spelling is kept
  only when it parses back to the stored number, so
  ``format(0.24000000000000002, ".16g")`` is 0.24 and does not certify
  it. A sample value within ``rel_tol`` of a different stored
  number does not certify that stored number; the opened source must
  contain the resolved leaf. Unicode numeric signs are folded onto that
  grammar before the match, so a Unicode minus is a minus and −0.24 is
  not 0.24. An observation id that names more than one scored row is not
  collapsed into one coverage entry; certification refuses it. A scored
  row whose ``observation_id`` is not a non-empty string is not omitted
  from that decision; certification refuses it.
  A recorded locator that is not opened is source-blind. An equipment
  pin does not cover a measured payload. Coverage holds only when every
  measured leaf of the observation's evidence has a matching pin of that
  address or of an ancestor. Evidence is ``values`` plus each scored
  numeric condition the temperature payload copies beside it
  (``T_range_K``, ``T_K``, ``T_C``, ``temperature_C``, ``temp_C``,
  ``point_conditions``). A locator inside a condition is provenance, not
  a leaf. A coverable measured leaf is a finite int or float (not a
  boolean), or a string that contains any digit [0-9] unless that string
  is an admitted annotation: a direct child of ``values`` other than
  ``reason``, or a ``reason`` whose parent condition state has a non-value
  ``StateTag``, at any depth. A ``reason`` beside ``tag: value``, or in a
  mapping with no state tag, stays a leaf at the observation root and
  nested under ``values``. A nested ``locator.note`` stays a leaf. A non-finite float
  is not coverable and is not dropped: it is ``non_finite_evidence``.
  A quantity name, a unit on its
  own, or another label with no digit is not a leaf and may be attested
  as text. A string the numeric parser cannot read, such as ``0.91Pa``
  or ``0.0614K``, is still a leaf: it is uncovered unless the opened
  source carries that printed value. Zero recognised leaves in a block
  that still contains a digit outside an annotation string is a refusal,
  not coverage. Repeating a
  pin of one address does not cover a sibling, even when the numbers
  are equal, and a ``values`` pin does not cover a sibling condition.
  A descriptive label such as ``quantity: pressure`` does not cover a
  sibling measurement.
* structured fidelity samples resolve rows in ``observations[]`` or the
  sibling ``context[]`` container (d-032); an id matching rows in both
  containers is an ambiguity error, not a silent pick
* no silent acceptance of unknown top-level observation types
* sibling ``context`` container rows (unscored experiment context, d-032):
  observation-shaped contract, but a scored observation ``type`` parked
  there is refused
* absolute machine-local path values refused on every path-valued key
  (parsed, so quoted scalars and YAML aliases count; PATH_VALUE_KEYS)

Usage::

  python tools/validate_literature_extracts.py
  python tools/validate_literature_extracts.py path/to/extract.yaml
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal, DecimalException
from functools import lru_cache
import math
import os
import re
import subprocess
import sys
import unicodedata
from collections.abc import (
    MutableMapping,
    MutableSequence,
    MutableSet,
    Set as AbstractSet,
)
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from simulator.battery.extract_context import (
    SUPPORTED_EXTRACT_CONTEXT_PATH,
    iter_unsupported_context_container_paths,
)
from simulator.chemistry.formula_text import fold_subscript_digits
from simulator.state_types import StateTag
from tools.mineru_table_draft import (
    Cell,
    TableGeometryError,
    parse_table_html,
    printed_formula_tokens,
)

EXTRACTS_DIR = ROOT / "data" / "literature" / "extracts"
SCHEMA_VERSION = "literature_extract.v1"
TABLE_SCHEMA_VERSION = "literature_extract_table.v1"
U0_MANIFEST = ROOT / "data" / "vapour_rail_u0_manifest.yaml"
FIDELITY_POLICY_PATH = EXTRACTS_DIR / "_fidelity_pre_policy_allowlist.yaml"
FIDELITY_POLICY_SCHEMA = "literature_extract_fidelity_policy.v1"
FIDELITY_GRADUATION_LEDGER_PATH = EXTRACTS_DIR / "_fidelity_graduation_ledger.yaml"
FIDELITY_GRADUATION_LEDGER_SCHEMA = (
    "literature_extract_fidelity_graduation_ledger.v1"
)
LAB_PARAMETER_VOCABULARY_PATH = ROOT / "data" / "literature" / "lab_parameter_vocabulary.yaml"


@lru_cache(maxsize=512)
def _load_extract_yaml_cached(path: str, mtime_ns: int, size: int) -> Any:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_extract_yaml(path: Path) -> Any:
    """Load YAML with an in-process cache invalidated by file stat changes."""
    resolved = path.resolve()
    metadata = resolved.stat()
    parsed = _load_extract_yaml_cached(
        str(resolved), metadata.st_mtime_ns, metadata.st_size
    )
    return deepcopy(parsed)

# Scored physics series. The first six are read by the rail/scoring machinery;
# the d-032 additions (owner ruling 2026-09-21) are physics quantities the rail
# could score, admitted from the invented-type cleanup with near-spellings
# collapsed (see SCHEMA.md "Observation types"). The set stays CLOSED:
# model_derived / model_comparison / quoted_comparator remain refused while
# the engine-reference vs validation-evidence boundary is an open owner
# question, and unscored experiment context lives in the sibling ``context``
# container instead of inventing type names here.
OBSERVATION_TYPES = frozenset(
    {
        "psat_series",
        "gibbs_table",
        "activity_coefficient",
        "alpha",
        "rate_series",
        "transition_point",
        "partial_pressure",
        "mass_loss",
        "heat_capacity_and_heat_content_series",
        "interaction_parameter",
        "concentration_series",
        "volatility_series",
        "phase_transition",
        "gas_speciation",
        "sulfur_solubility_and_sulfate_capacity_series",
        "chloride_capacity",
        "composition_series",
        "equilibrium_phase_composition",
        "distribution_ratio",
    }
)

REVIEW_STATUSES = frozenset({"draft", "reviewed", "rejected"})
CONFIDENCE_LEVELS = frozenset({"one_sigma", "two_sigma", "ci95", "not_stated"})

def equipment_fields_from_vocabulary(
    path: Path = LAB_PARAMETER_VOCABULARY_PATH,
) -> frozenset[str]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path}: vocabulary root must be a mapping")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"{path}: vocabulary entries must be a list")
    return frozenset(
        str(row["printed"])
        for row in entries
        if isinstance(row, Mapping) and row.get("printed")
    )


EQUIPMENT_FIELDS = equipment_fields_from_vocabulary()

LOCATOR_KEYS = frozenset(
    {
        "page",
        "published_page",
        "pdf_page_index",
        "table",
        "figure",
        "paragraph",
        "section",
        "equation",
        "line_range",
        "note",
        "source_path",
        "quotes_path",
        "record",
        "source_sha256",
    }
)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ABS_PATH_RE = re.compile(r"^(/|[A-Za-z]:\\|\\\\)")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _valid_source_sha256(value: Any) -> bool:
    """True when value is lowercase, 64-character SHA-256 hex."""
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


# Unicode minus and the other dash/plus signs are not ASCII "+" or "-".
# The citation grammar below only sees ASCII signs, so a raw scan of
# "−0.24" (U+2212) yields the token "0.24" and certifies the opposite
# sign. Fold these onto ASCII before that grammar runs. Plus-minus is
# absent here on purpose: it is neither "+" nor "-" and stays in the
# grammar as its own sign.
_UNICODE_MINUS_SIGNS = (
    "\u2212"  # MINUS SIGN
    "\u2010"  # HYPHEN
    "\u2011"  # NON-BREAKING HYPHEN
    "\u2012"  # FIGURE DASH
    "\u2013"  # EN DASH
    "\u2014"  # EM DASH
    "\u2015"  # HORIZONTAL BAR
    "\ufe63"  # SMALL HYPHEN-MINUS
    "\uff0d"  # FULLWIDTH HYPHEN-MINUS
    "\u02d7"  # MODIFIER LETTER MINUS SIGN
    "\u207b"  # SUPERSCRIPT MINUS
    "\u208b"  # SUBSCRIPT MINUS
    "\u2796"  # HEAVY MINUS SIGN
)
_UNICODE_PLUS_SIGNS = (
    "\uff0b"  # FULLWIDTH PLUS SIGN
    "\ufe62"  # SMALL PLUS SIGN
    "\u207a"  # SUPERSCRIPT PLUS
    "\u208a"  # SUBSCRIPT PLUS
    "\u2795"  # HEAVY PLUS SIGN
)
_NUMERIC_SIGN_FOLD = str.maketrans(
    {ord(ch): "-" for ch in _UNICODE_MINUS_SIGNS}
    | {ord(ch): "+" for ch in _UNICODE_PLUS_SIGNS}
)
# Plus-minus stays a distinct sign so "±0.24" is not the token "0.24",
# and so it does not compare equal to an ASCII minus or plus.
_DISTINCT_NUMERIC_SIGNS = "\u00b1\u2213"
# "-" is first or last in each class so it is literal, not a range.
_TOKEN_SIGN_CLASS = "-" + "+" + _DISTINCT_NUMERIC_SIGNS
_TOKEN_LOOKBEHIND_CLASS = "A-Za-z0-9_." + "+" + _DISTINCT_NUMERIC_SIGNS + "-"
# Sign and exponent are part of one token. The lookbehind also rejects a
# preceding sign so the match cannot start on the digits of -0.24. The
# optional exponent group consumes 0.24e-5 whole, so a shorter spelling is
# not a second match. The mantissa is digits, a fraction, or a leading
# decimal: ".24" is a number. Without that alternative the string ".91"
# is not a measured leaf, and the non-numeric substring path treats ".24"
# as text inside "-.24". Callers scan text only through
# ``_citation_token_matches``, which folds Unicode signs and typographic
# digit groups first and drops
# any span ``_span_is_complete_printed_value`` rejects. The grammar is not
# the boundary: a comma is not a hard edge here, and a yielded match is
# still not a token of ``1,000``. A period is not a hard edge either.
# ``(?:\.\d+)?`` consumes a decimal only when a digit follows the dot, so
# a sentence period stays outside the match. The shared predicate then
# accepts that period and rejects one a digit follows. Listing '.' in the
# lookahead would drop ``1`` from ``the value is 1.`` before the predicate
# could accept it. A second scanner would let one path drop a sign or a
# leading decimal that another path keeps.
_NUMERIC_TOKEN_RE = re.compile(
    f"(?<![{_TOKEN_LOOKBEHIND_CLASS}])"
    f"[{_TOKEN_SIGN_CLASS}]?"
    r"(?:\d+(?:\.\d+)?|\.\d+)"
    f"(?:[eE][{_TOKEN_SIGN_CLASS}]?"
    r"\d+)?"
    r"(?![A-Za-z0-9_])"
)


def _fold_numeric_signs(text: str) -> str:
    """Map Unicode minus and plus onto the ASCII signs the token grammar sees.

    One code point becomes one code point, so token spans stay aligned.
    Plus-minus is not folded: it remains its own sign.
    """
    return text.translate(_NUMERIC_SIGN_FOLD)


# Thin space, narrow no-break space, and no-break space. One separator
# between groups. An ASCII space is two measurements: ``1 100`` stays
# ``1 100``. A comma is not this separator: ``1,000`` stays ``1,000``.
_DIGIT_GROUP_SEPARATORS = "\u2009\u202f\u00a0"
# The whole separated run, including a short group. A well-formed prefix
# is not deleted while a later separator remains.
_DIGIT_GROUP_RUN_RE = re.compile(
    r"\d+(?:[" + _DIGIT_GROUP_SEPARATORS + r"]\d+)+"
)
# First group is 1–3 digits. Every group after it is exactly three.
_DIGIT_GROUP_WELL_FORMED_RE = re.compile(
    r"\d{1,3}(?:[" + _DIGIT_GROUP_SEPARATORS + r"]\d{3})+"
)


def _digit_run_may_start(text: str, start: int) -> bool:
    """True when a grouped digit run may begin at ``start``.

    The previous character is the start of the text, whitespace, or
    punctuation. A letter never starts a run. A digit, decimal point,
    exponent marker, or sign does not either.
    """
    if start <= 0:
        return True
    previous = text[start - 1]
    if previous in "0123456789.eE+-" or previous.isalpha():
        return False
    return previous.isspace() or unicodedata.category(previous).startswith("P")


def _fold_digit_groups(text: str) -> str:
    """Join one unsigned digit run on a typographic separator.

    A thin space, narrow no-break space, or no-break space joins groups.
    An ASCII space does not, so ``1 100`` stays ``1 100``. The run starts
    at the beginning of the text or after whitespace or punctuation, never
    after a letter, so ``SiO2 100`` stays ``SiO2 100``. The first group is
    one to three digits. Each following group is exactly three. The whole
    separated run must be that shape. A short group is not deleted, and a
    well-formed piece of the same run is not joined on its own, so
    ``12\\u200934\\u2009567`` stays and is not ``34567``, and
    ``1\\u2009650\\u20091`` is not ``1650``. A decimal point may follow the
    run. ``12 34``, ``1e0 001``, ``1.5 000``, ``1 550e3``, and a signed
    run stay apart. A second call keeps the same spans. Text with none of
    those separators is returned unchanged.
    """
    if not any(separator in text for separator in _DIGIT_GROUP_SEPARATORS):
        return text
    pieces: list[str] = []
    cursor = 0
    for match in _DIGIT_GROUP_RUN_RE.finditer(text):
        if match.end() < len(text) and text[match.end()] in "eE":
            continue
        if not _digit_run_may_start(text, match.start()):
            continue
        grouped = match.group(0)
        if _DIGIT_GROUP_WELL_FORMED_RE.fullmatch(grouped) is None:
            continue
        pieces.append(text[cursor : match.start()])
        for separator in _DIGIT_GROUP_SEPARATORS:
            grouped = grouped.replace(separator, "")
        pieces.append(grouped)
        cursor = match.end()
    pieces.append(text[cursor:])
    return "".join(pieces)


def _citation_token_matches(text: str) -> list[re.Match[str]]:
    """Numeric tokens after signs and digit groups are folded.

    Spelling equality and string alignment read this list. A sign cannot
    be dropped on one of those paths and kept on another. A match whose
    span fails ``_span_is_complete_printed_value`` is not a token, so
    ``1`` is not a token of ``1,000``. Typographic digit groups of one
    number are joined first, so ``1 650`` is the token ``1650``. An ASCII
    space is not joined, so ``1 100`` is the tokens ``1`` and ``100``,
    and ``12 34`` is two tokens. A second fold does not move those spans.
    This grammar is
    not the coverage membership test: a string that contains a digit is
    a measured leaf even when this list is empty.
    """
    folded = _fold_digit_groups(_fold_numeric_signs(text))
    return [
        match
        for match in _NUMERIC_TOKEN_RE.finditer(folded)
        if _span_is_complete_printed_value(folded, match.start(), match.end())
    ]


_ASCII_DIGITS = "0123456789"
# Signs that belong to the number when they sit immediately before it.
# Unicode plus and minus are folded onto ASCII before this set is used.
# Plus-minus stays distinct, so it is listed here and not folded.
_ATTACHED_VALUE_SIGNS = frozenset("+-") | frozenset(_DISTINCT_NUMERIC_SIGNS)
# Micro sign, Greek mu, and degree sign. Degree is not str.isalpha.
_VALUE_LETTER_SIGNS = frozenset("µμ°")


def _string_has_ascii_digit(text: str) -> bool:
    """True when ``text`` contains a digit [0-9].

    This is the coverage membership test for a string leaf. It does not
    ask whether the numeric grammar can read the string.
    """
    return any(ch in _ASCII_DIGITS for ch in text)


def _parser_reads_every_digit(text: str) -> bool:
    """True when every ASCII digit of ``text`` sits inside a citation token.

    The numeric grammar still compares values it can read. A digit that
    sits outside every token, as in ``0.91Pa`` or ``91.``, is not read.
    """
    folded = _fold_digit_groups(_fold_numeric_signs(text))
    if not _string_has_ascii_digit(folded):
        return False
    covered = [False] * len(folded)
    for match in _citation_token_matches(folded):
        for index in range(match.start(), match.end()):
            covered[index] = True
    return all(
        covered[index] or folded[index] not in _ASCII_DIGITS
        for index in range(len(folded))
    )


def _extends_printed_value(text: str, index: int, nxt: str, *, sign_attaches: bool) -> bool:
    """True when ``text[index]`` continues a printed value.

    An extending character is an ASCII letter or digit, a Unicode letter,
    or µ/°. A '.' or ',' extends only when the next character is a digit.
    A numeric sign extends only on the left (``sign_attaches``) and only
    when it sits on a digit or a leading decimal, because that sign
    belongs to the number.
    """
    ch = text[index]
    if ch.isascii() and ch.isalnum():
        return True
    if ch.isalpha() or ch in _VALUE_LETTER_SIGNS:
        return True
    if ch in ".,":
        return len(nxt) == 1 and nxt in _ASCII_DIGITS
    if sign_attaches and ch in _ATTACHED_VALUE_SIGNS:
        return len(nxt) == 1 and (nxt in _ASCII_DIGITS or nxt == ".")
    return False


def _span_is_complete_printed_value(text: str, start: int, end: int) -> bool:
    """The only boundary predicate. Every matcher accepts a span through it.

    Invariant: a digit-bearing value matches source text only as a complete
    token. The whole stored value, number plus any attached or spaced unit,
    occupies ``[start, end)``, and neither neighbour extends that span.
    ``1 K`` is not ``1 KPa``. ``1`` is not ``1,000``. ``1,000`` is not
    ``1,000,000``. ``1`` is not ``-1`` or ``1µ``. ``1`` is ``the value is 1.``
    A whitespace token that contains ``^`` holds a superscript. No span
    inside that token certifies a plain formula or a plain number.
    A formula subscript count is not a measurement: ``_{n}`` and a bare
    ``_`` digit do not certify as that number, so ``SiO_{2}`` does not
    certify 2. ``SiO_{2} 100`` still certifies 100.
    This predicate is the prose boundary only. A CSV or TSV source does
    not call it. The table is parsed first, and the whole cell is compared.
    """
    if not (0 <= start < end <= len(text)):
        return False
    if _token_holds_superscript(text, start, end):
        return False
    if _span_is_formula_subscript(text, start):
        return False
    if start > 0 and _extends_printed_value(
        text, start - 1, text[start], sign_attaches=True
    ):
        return False
    if end < len(text) and _extends_printed_value(
        text,
        end,
        text[end + 1] if end + 1 < len(text) else "",
        sign_attaches=False,
    ):
        return False
    return True


def _span_is_formula_subscript(text: str, start: int) -> bool:
    """True when a numeric span opens a formula subscript count.

    ``_{12}`` and ``_12`` are the formula's count. Those digits are not
    a measurement. A later token still is, so ``SiO_{2} 100`` keeps
    ``100``. A Unicode subscript digit is not an ASCII number, so it is
    not this span.
    """
    if start <= 0:
        return False
    if text[start - 1] == "_":
        return True
    return start >= 2 and text[start - 1] == "{" and text[start - 2] == "_"


def _token_holds_superscript(text: str, start: int, end: int) -> bool:
    """True when the whitespace token covering ``[start, end)`` contains ``^``."""
    left = start
    while left > 0 and not text[left - 1].isspace():
        left -= 1
    right = end
    limit = len(text)
    while right < limit and not text[right].isspace():
        right += 1
    return "^" in text[left:right]


def _printed_value_carried(leaf: str, text: str) -> bool:
    """True when the folded ``leaf`` occurs in the folded ``text`` whole.

    Signs and typographic digit groups are folded on both sides. This is
    the numeric boundary. A formula label does not use it: parentheses
    would split ``Fe2(SO4)3``. Acceptance is
    ``_span_is_complete_printed_value`` on that span. ``0.91Pa`` is not
    ``10.91Pa`` or ``0.91Pascal``, ``91.`` is not ``91.0``, ``1 K`` is not
    ``1 KPa``, ``1,000`` is not ``1,000,000``, and ``12 34`` is not ``1234``.
    A thin space, narrow no-break space, or no-break space joins an
    unsigned digit run, so ``1 650`` is ``1650``. An ASCII space does not,
    so ``1 100`` is not ``1100``. ``SiO2 100`` is not ``SiO2100``.
    """
    leaf = _fold_digit_groups(_fold_numeric_signs(leaf))
    text = _fold_digit_groups(_fold_numeric_signs(text))
    if not leaf:
        return False
    start = 0
    while True:
        index = text.find(leaf, start)
        if index < 0:
            return False
        if _span_is_complete_printed_value(text, index, index + len(leaf)):
            return True
        start = index + 1


# Certification opens these as tables. A companion ``literature_extract_table``
# document stays an extract, not a source. The cell is the token. Prose
# boundary matching is not applied inside a cell, and a delimiter is never
# an extending character because the table is parsed before comparison.
_TABULAR_SOURCE_DELIMITERS = {
    ".csv": ",",
    ".tsv": "\t",
}


def _tabular_delimiter(path: Path) -> str | None:
    """Comma or tab when ``path`` is a tabular source, else None."""
    return _TABULAR_SOURCE_DELIMITERS.get(path.suffix.lower())


def _table_records(
    text: str, delimiter: str
) -> list[tuple[int, int, list[str]]]:
    """Parse the complete table. Each row is ``(start_line, end_line, cells)``.

    Lines are 1-based and inclusive, the same numbering as ``str.splitlines``.
    A quoted cell that contains a newline occupies every physical line it
    spans. ``csv.Error`` means the table was not parsed. Callers refuse
    that and do not search the raw text or a sliced line.
    """
    lines = text.splitlines(keepends=True)
    index = 0

    class _Lines:
        def __iter__(self) -> _Lines:
            return self

        def __next__(self) -> str:
            nonlocal index
            if index >= len(lines):
                raise StopIteration
            line = lines[index]
            index += 1
            return line

    reader = csv.reader(_Lines(), delimiter=delimiter)
    records: list[tuple[int, int, list[str]]] = []
    previous = 0
    for row in reader:
        if index <= previous:
            raise csv.Error("table record did not consume a line")
        records.append((previous + 1, index, list(row)))
        previous = index
    return records


def _cited_table_cells(
    text: str,
    locator: Mapping[str, Any],
    delimiter: str,
) -> tuple[list[str] | None, str | None]:
    """Cells the locator cites, after the complete table has been parsed.

    Invariant: a table certifies from parsed cells only. ``line_range``
    selects records that lie entirely inside it. A span that includes some
    but not all lines of a record cuts through a quoted cell. That span is
    source-blind, and the sliced lines are not parsed as their own table.
    Without ``line_range`` every cell of the table is cited.
    """
    records = _table_records(text, delimiter)
    if "line_range" not in locator or locator.get("line_range") is None:
        return [cell for _start, _end, row in records for cell in row], None
    span = _parse_line_range(locator.get("line_range"))
    if span is None:
        return None, "line_range is not a line number or start-end span"
    start, end = span
    line_count = len(text.splitlines())
    if end > line_count:
        return None, (
            f"line_range {start}-{end} is outside the artifact "
            f"({line_count} lines)"
        )
    for rec_start, rec_end, _row in records:
        overlaps = not (end < rec_start or start > rec_end)
        covers = start <= rec_start and rec_end <= end
        if overlaps and not covers:
            return None, (
                f"line_range {start}-{end} cuts through a quoted table cell "
                f"on lines {rec_start}-{rec_end}"
            )
    cells = [
        cell
        for rec_start, rec_end, row in records
        if start <= rec_start and rec_end <= end
        for cell in row
    ]
    return cells, None


def _finite_decimal(text: str) -> Decimal | None:
    """One finite Decimal, or None when ``text`` is not that number.

    InvalidOperation, Overflow, and a non-finite value (NaN, Infinity)
    are refusals. Callers treat None as "this number does not certify".
    """
    try:
        parsed = Decimal(text)
    except DecimalException:
        return None
    if not parsed.is_finite():
        return None
    return parsed


def _whole_cell_number(cell: str) -> Decimal | None:
    """Parse the entire trimmed cell as one finite Decimal, or return None.

    The cell is the token. ``Decimal`` parses it after trim and the
    table reader's unquote. The prose numeric grammar is not a gate, so
    a trailing dot is the number: ``91.`` is ``91``. Value comparison is
    ``Decimal``, never a float conversion: ``0.9100`` is ``0.91``,
    ``1e-999`` is not ``0``, and ``9007199254740993`` is not
    ``9007199254740992``. ``1/2``, ``1,000``, and ``0.91Pa`` do not parse.
    A typographic digit group does: a thin space or a no-break space makes
    1650, and an ASCII space does not, so ``1 550`` is not ``1550`` and
    ``12 34`` is not ``1234``. NaN, Infinity, and an exponent Decimal
    rejects are refusals.
    This is not a list of spellings of a stored leaf.
    """
    folded = _fold_digit_groups(_fold_numeric_signs(cell.strip()))
    return _finite_decimal(folded)


def _stored_number_decimal(leaf: int | float) -> Decimal | None:
    """Exact Decimal of a stored int or float.

    A float uses ``repr`` (shortest round-trip). An int uses ``str``.
    Neither path does float arithmetic on the cell. A non-finite float
    and a bool are refusals.
    """
    if isinstance(leaf, bool):
        return None
    if isinstance(leaf, float):
        text = repr(leaf)
    elif isinstance(leaf, int):
        text = str(leaf)
    else:
        return None
    return _finite_decimal(text)


def _cell_certifies_leaf(leaf: Any, cell: str) -> bool:
    """True when the whole trimmed, unquoted cell is ``leaf``.

    Invariant: every numeric table cell is decided here. The whole cell
    certifies a stored number only when ``Decimal`` of that cell equals
    ``Decimal`` of the stored spelling (``repr`` of a float, ``str`` of an
    int), including the sign of zero. ``0.9100`` is ``0.91``. ``91.`` is
    ``91``. ``1.0`` is ``1``. ``1e-999`` is not ``0``.
    ``9007199254740993`` is not ``9007199254740992``. ``1/2`` is not ``1``.
    ``1,000`` is not ``1``.
    A cell that is not one finite number does not certify. A string
    matches only that string. Spellings are not enumerated. ``Decimal``
    equality treats negative zero as zero, so the sign bit is compared
    when the value is zero: ``-0.0`` is not ``0.0``.
    """
    folded = _fold_numeric_signs(cell.strip())
    if isinstance(leaf, str):
        return folded == _fold_numeric_signs(leaf)
    if isinstance(leaf, bool):
        return folded.lower() == ("true" if leaf else "false")
    if isinstance(leaf, (int, float)):
        parsed = _whole_cell_number(cell)
        stored = _stored_number_decimal(leaf)
        if parsed is None or stored is None:
            return False
        if parsed != stored:
            return False
        # Decimal equality collapses the sign of zero. The sign is part
        # of the value, so a negative zero does not certify a positive zero.
        if parsed.is_zero():
            return parsed.as_tuple().sign == stored.as_tuple().sign
        return True
    return False


def _leaf_in_table(leaf: Any, cells: Sequence[str]) -> bool:
    """True when one whole cell is ``leaf``.

    Comparison is ``_cell_certifies_leaf`` only. A numeric cell is an
    exact ``Decimal``, so ``0.9100`` is ``0.91`` and ``1e-999`` is not
    ``0``. The prose matcher is not used, so a digit inside ``1/2`` or
    ``note 1`` is not that stored number.
    """
    return any(_cell_certifies_leaf(leaf, cell) for cell in cells)


# One MinerU table is a single ``<table>...</table>`` line. Nested tables
# are not a MinerU shape this matcher reads.
_HTML_TABLE_RE = re.compile(
    r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL
)
# 1-based grid coordinates, header rows included. The longer names are the
# cell keys already written on locators; they are the same indexes.
_HTML_ROW_KEYS = ("table_row", "table_row_number")
_HTML_COLUMN_KEYS = ("table_column", "table_column_number")
# Formula-bearing label keys only. Every other key, and every number, keeps
# the ordinary token or whole-cell comparison.
_FORMULA_LABEL_KEYS = frozenset({"formula", "species"})
# The only cross-token join. A newline is not one of these spaces, and a
# preceding measurement (``6.98 Fe``) is not an element chain. Markup
# folds inside a token and never deletes a separator. A space joins only
# an element symbol, or a closing parenthesis, to the digit count that
# follows. ``SiO 2`` becomes ``SiO2``. ``(OH) 2`` becomes ``(OH)2``.
# A space after a count is never joined: ``H2 O2`` and ``Fe 2 O 3``
# (which becomes ``Fe2 O3``) stay separate tokens.
_FORMULA_INTERNAL_SPACES = " \u00a0\u2009\u202f"
_FORMULA_ELEMENT_COUNT_SPACE_RE = re.compile(
    "(?<![A-Za-z])"
    "((?:[A-Z][a-z]?)+)"
    "[" + _FORMULA_INTERNAL_SPACES + "]+"
    r"(?=\d{1,3}(?!\d))"
)
_FORMULA_PAREN_COUNT_SPACE_RE = re.compile(
    r"(\))"
    "[" + _FORMULA_INTERNAL_SPACES + "]+"
    r"(?=\d{1,3}(?!\d))"
)


def _html_table_grid(
    table_html: str,
) -> tuple[list[list[Cell]] | None, str | None]:
    """One table from ``parse_table_html``, which owns rowspan and colspan.

    Certification reads ``Cell.text`` and ``Cell.superscript_spans``. The
    text keeps formula markup. The draft's split numeric value is not used.
    """
    try:
        grid = parse_table_html(table_html)
    except TableGeometryError as exc:
        return None, str(exc)
    except Exception:
        return None, "html_table_unparsed: HTML table could not be parsed"
    if not grid:
        return None, "html_table_unparsed: HTML table has no rows"
    return grid, None


_EXPLICIT_SCIENCE_RE = re.compile(
    r"([+\-]?(?:\d+(?:\.\d+)?|\.\d+))[eE]([+\-]?\d+)"
)
_EXPLICIT_CARET_RE = re.compile(
    r"([+\-]?(?:\d+(?:\.\d+)?|\.\d+))\s*\^\s*([+\-]?\d+)"
)
_EXPLICIT_TIMES_RE = re.compile(
    r"([+\-]?(?:\d+(?:\.\d+)?|\.\d+))\s*×\s*10\s*\^\s*([+\-]?\d+)"
)
# × U+00D7, ASCII x, · U+00B7. The optional group is mantissa times 10.
# Without it the prefix is the base of base**exponent.
_SUPERSCRIPT_TIMES = "\u00d7x\u00b7"
_SUPERSCRIPT_CELL_RE = re.compile(
    r"([+\-]?(?:\d+(?:\.\d+)?|\.\d+))(?:\s*(["
    + _SUPERSCRIPT_TIMES
    + r"])\s*10)?"
)


def _power_product(base: Decimal, exponent: int) -> Decimal | None:
    try:
        value = base ** exponent
    except (ArithmeticError, DecimalException):
        return None
    if not value.is_finite():
        return None
    return value


def _explicit_power_decimal(text: str) -> Decimal | None:
    """Value of one explicit power spelling, or None.

    ``1e3``, ``10^3``, and ``1×10^3`` are powers. ``1000`` and ``103``
    are plain numbers.
    """
    token = _fold_numeric_signs(text.strip())
    scientific = _EXPLICIT_SCIENCE_RE.fullmatch(token)
    if scientific:
        return _finite_decimal(f"{scientific.group(1)}e{scientific.group(2)}")
    caret = _EXPLICIT_CARET_RE.fullmatch(token)
    if caret:
        base = _finite_decimal(caret.group(1))
        if base is None:
            return None
        return _power_product(base, int(caret.group(2)))
    times = _EXPLICIT_TIMES_RE.fullmatch(token)
    if times:
        mantissa = _finite_decimal(times.group(1))
        if mantissa is None:
            return None
        factor = _power_product(Decimal(10), int(times.group(2)))
        if factor is None:
            return None
        try:
            value = mantissa * factor
        except (ArithmeticError, DecimalException):
            return None
        if not value.is_finite():
            return None
        return value
    return None


def _superscript_power_decimal(
    text: str, spans: tuple[tuple[int, int], ...]
) -> Decimal | None:
    """One complete power cell, or None.

    The cell is an optional sign, a base number, an optional × or x or
    middle dot, then 10, and one superscript exponent. Or that base raised
    to the exponent. The sign applies to the whole printed value, so
    ``-10<sup>2</sup>`` is ``-100`` and ``-1.5×10<sup>3</sup>`` is
    ``-1500``. ``10<sup>3</sup>`` is ``10**3``, which is ``1e3``.
    ``1.5×10<sup>3</sup>`` is ``1.5 * 10**3``. A trailing or leading extra,
    including ``10<sup>3</sup>2``, is not a power.
    """
    if len(spans) != 1:
        return None
    start, end = spans[0]
    if not (0 <= start < end <= len(text)):
        return None
    if text[end:].strip():
        return None
    exponent = _fold_numeric_signs(text[start:end].strip())
    if not re.fullmatch(r"[+\-]?\d+", exponent):
        return None
    prefix = _fold_numeric_signs(text[:start].strip())
    sign = Decimal(1)
    if prefix[:1] in "+-":
        if prefix[0] == "-":
            sign = Decimal(-1)
        prefix = prefix[1:].strip()
    parsed = _SUPERSCRIPT_CELL_RE.fullmatch(prefix)
    if parsed is None:
        return None
    base = _finite_decimal(parsed.group(1))
    if base is None:
        return None
    power = int(exponent)
    if parsed.group(2) is None:
        value = _power_product(base, power)
    else:
        factor = _power_product(Decimal(10), power)
        if factor is None:
            return None
        try:
            value = base * factor
        except (ArithmeticError, DecimalException):
            return None
    if value is None or not value.is_finite():
        return None
    if sign < 0:
        value = -value
    if not value.is_finite():
        return None
    return value


def _superscript_leaf_certifies(
    leaf: Any, text: str, spans: tuple[tuple[int, int], ...]
) -> bool:
    """True when ``leaf`` spells the cell's power and equals it.

    An int is a plain number, so ``103``, ``1000``, and ``1500`` do not
    certify. A float certifies when it is that power, so ``1e3`` certifies
    ``10<sup>3</sup>`` and ``1.5e3`` certifies ``1.5×10<sup>3</sup>``.
    A string certifies only as ``1e3``, ``10^3``, or ``1×10^3`` (the same
    value). Extra characters in the cell are not a power.
    """
    power = _superscript_power_decimal(text, spans)
    if power is None:
        return False
    if isinstance(leaf, str):
        spelled = _explicit_power_decimal(leaf)
        return spelled is not None and spelled == power
    if isinstance(leaf, bool) or not isinstance(leaf, float):
        return False
    stored = _stored_number_decimal(leaf)
    return stored is not None and stored == power


def _parse_positive_index(raw: Any) -> int | None:
    """1-based index. Bool, float, zero, and a non-digit string are refusals."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw if raw >= 1 else None
    if isinstance(raw, str) and re.fullmatch(r"[0-9]+", raw.strip()):
        number = int(raw.strip())
        return number if number >= 1 else None
    return None


def _locator_has_html_cell_address(locator: Mapping[str, Any]) -> bool:
    return any(key in locator for key in (*_HTML_ROW_KEYS, *_HTML_COLUMN_KEYS))


def _one_html_index(
    locator: Mapping[str, Any], keys: tuple[str, ...], label: str
) -> tuple[int | None, str | None]:
    """The shared index, or an error. ``(None, None)`` means the keys are absent."""
    present = [key for key in keys if key in locator]
    if not present:
        return None, None
    parsed: list[int] = []
    for key in present:
        value = _parse_positive_index(locator.get(key))
        if value is None:
            return None, (
                f"html_cell_address_invalid: {key} is not a 1-based cell index"
            )
        parsed.append(value)
    if any(item != parsed[0] for item in parsed):
        return None, f"html_cell_address_invalid: {label} keys disagree"
    return parsed[0], None


def _html_cell_address(
    locator: Mapping[str, Any],
) -> tuple[tuple[int, int] | None, str | None]:
    """``(row, column)`` when the locator cites one HTML cell."""
    if not _locator_has_html_cell_address(locator):
        return None, None
    row, row_error = _one_html_index(locator, _HTML_ROW_KEYS, "table row")
    if row_error:
        return None, row_error
    column, column_error = _one_html_index(
        locator, _HTML_COLUMN_KEYS, "table column"
    )
    if column_error:
        return None, column_error
    if row is None or column is None:
        return None, (
            "html_cell_address_incomplete: table_row and table_column "
            "are both required"
        )
    return (row, column), None


def _html_table_line_bounds(text: str, match: re.Match[str]) -> tuple[int, int]:
    """1-based inclusive lines this ``<table>`` match occupies."""
    start_line = text.count("\n", 0, match.start()) + 1
    end_line = text.count("\n", 0, max(match.start(), match.end() - 1)) + 1
    return start_line, end_line


def _prose_line_span(
    text: str, locator: Mapping[str, Any]
) -> tuple[int, int] | None:
    """1-based inclusive lines a prose locator cites.

    Absent ``line_range`` is the opened text. A bad range is None; callers
    that already refused that range do not need this.
    """
    if "line_range" not in locator or locator.get("line_range") is None:
        count = len(text.splitlines())
        if count == 0:
            return 1, 0
        return 1, count
    return _parse_line_range(locator.get("line_range"))


def _cited_span_includes_html_table(text: str, locator: Mapping[str, Any]) -> bool:
    """True when a cited line holds an HTML ``<table>``."""
    span = _prose_line_span(text, locator)
    if span is None:
        return False
    start, end = span
    if end < start:
        return False
    for match in _HTML_TABLE_RE.finditer(text):
        table_start, table_end = _html_table_line_bounds(text, match)
        if not (end < table_start or start > table_end):
            return True
    return False


def _cited_prose_outside_html_tables(text: str, locator: Mapping[str, Any]) -> str:
    """Cited lines that do not hold an HTML table."""
    span = _prose_line_span(text, locator)
    if span is None:
        return ""
    start, end = span
    lines = text.splitlines()
    if end < start or start > len(lines):
        return ""
    end = min(end, len(lines))
    table_lines: set[int] = set()
    for match in _HTML_TABLE_RE.finditer(text):
        table_start, table_end = _html_table_line_bounds(text, match)
        if end < table_start or start > table_end:
            continue
        table_lines.update(
            range(max(start, table_start), min(end, table_end) + 1)
        )
    kept = [
        lines[index]
        for index in range(start - 1, end)
        if (index + 1) not in table_lines
    ]
    return "\n".join(kept)


def _cited_html_cell(
    text: str, locator: Mapping[str, Any]
) -> tuple[str | None, tuple[tuple[int, int], ...], str | None]:
    """The one cited cell, after the complete table is parsed.

    The second item is the cell's superscript spans. ``line_range`` selects
    tables that lie entirely inside it. A span that cuts a table is
    source-blind. More than one table in the cited span is source-blind.
    The sliced line is not searched as prose.
    """
    address, address_error = _html_cell_address(locator)
    if address_error:
        return None, (), address_error
    if address is None:
        return None, (), (
            "html_cell_address_incomplete: table_row and table_column "
            "are both required"
        )
    row, column = address
    span: tuple[int, int] | None = None
    if "line_range" in locator and locator.get("line_range") is not None:
        span = _parse_line_range(locator.get("line_range"))
        if span is None:
            return None, (), "line_range is not a line number or start-end span"
        start, end = span
        line_count = len(text.splitlines())
        if end > line_count:
            return None, (), (
                f"line_range {start}-{end} is outside the artifact "
                f"({line_count} lines)"
            )
    chosen: list[str] = []
    for match in _HTML_TABLE_RE.finditer(text):
        start_line, end_line = _html_table_line_bounds(text, match)
        if span is None:
            chosen.append(match.group(0))
            continue
        range_start, range_end = span
        overlaps = not (range_end < start_line or range_start > end_line)
        covers = range_start <= start_line and end_line <= range_end
        if overlaps and not covers:
            return None, (), (
                f"line_range {range_start}-{range_end} cuts through an HTML "
                f"table on lines {start_line}-{end_line}"
            )
        if covers:
            chosen.append(match.group(0))
    if not chosen:
        return None, (), "html_table_absent: cited span has no HTML table"
    if len(chosen) > 1:
        return None, (), (
            f"html_table_ambiguous: cited span has {len(chosen)} HTML tables"
        )
    grid, grid_error = _html_table_grid(chosen[0])
    if grid_error or not grid:
        return None, (), grid_error or "html_table_unparsed: HTML table has no rows"
    width = len(grid[row - 1]) if row <= len(grid) else 0
    if row > len(grid) or column > width:
        return None, (), (
            f"html_cell_outside_table: table_row {row} table_column {column} "
            f"is outside the table ({len(grid)} rows, {width} columns)"
        )
    cited = grid[row - 1][column - 1]
    return cited.text, cited.superscript_spans, None


def _html_blind_code(message: str) -> str:
    head = message.split(":", 1)[0].strip()
    if head.startswith("html_"):
        return head
    return "cited_span_missing"


# Corpus-relative roots that hold a paper, OCR, or quotes. Reference
# transcriptions under docs/references/ remain repository-relative. The
# extract tree and gate implementation are absent: a pin must not certify
# against its own YAML or this file.
_SOURCE_ARTIFACT_PREFIXES = (
    "raw/",
    "corpus/",
    "text/",
    "tables/",
    "supporting/",
    "docs/references/",
)
_PROGRAM_SOURCE_SUFFIXES = frozenset({".py", ".pyi"})
# path segments: bare key or key[index_or_id]
_PATH_SEGMENT_RE = re.compile(r"^([^.\[]+)(?:\[([^\]]*)\])?$")

# Path-valued keys that must stay corpus- or repository-relative (never
# machine-local absolute). This frozenset is the ONE definition of which keys
# carry paths: the walk below, the refusal, and the tests
# (tests/test_absolute_path_ratchet.py plants an absolute value on each member
# and checks the set against the extract tree) all read it; there is no second
# list. Membership comes from a survey of all 273 extracts on green 61ec839da
# (ROR-b713-r2): every key with a path-shaped value (corpus/raw/tables/text/
# data/docs prefix or a file extension, no whitespace) is a member.
# Deliberately out: table_transcription (printed table text, e.g. a unit
# token "/K" is not a path), url (http/https), path and metadata_path
# (fidelity dot-paths), record (row identifiers), new_corpus_tables (table
# numbers), note / derived_from (prose that mentions paths).
PATH_VALUE_KEYS = frozenset(
    {
        "provenance_path",
        "source_path",
        "source_pdf",
        "source_pdf_path",
        "original_scan",  # Gibson & Hubbard anchor aliased into source_path
        "table_csv",  # 110 tables/... values
        "table_file",
        "table_path",
        "quotes_path",
        "source_csv",
        "corpus_transcription",
        "corpus_provenance",
        "transcription",  # string form; dict form (page/table/title) is walked
        "repaired_table_path",
        "verified_data_path",
        "corpus_tables",  # directory string or list of csv paths
        "table_provenance_root",
        "chapter_pdf",
        "corpus_asset",
        "image_asset_as_published",  # published image filenames are relative asset paths
        "provenance_file",
        "audit_file",
        "audit_files",
        "audit_csv",  # figure-read audit table (shornikov 2025)
        "numeric_points",  # figure-audit csv, or the sentinels none / not_digitized
        "calibration_audit",
    }
)


def _is_absolute_path(value: Any) -> bool:
    return isinstance(value, str) and bool(_ABS_PATH_RE.match(value))


def _walk_path_values(
    node: Any, *, path: str = ""
) -> list[tuple[str, str, str]]:
    """Return (key, dotted-path, value) for PATH_VALUE_KEYS strings.

    Walks the *parsed* document, so a quoted scalar and a YAML alias
    (``source_path: *scan``) count exactly like an unquoted scalar, and an
    anchor reused N times yields N hits (one per place a consumer reads it).
    A path key's value may be a string, a list (each string element checked,
    e.g. ``corpus_tables: [tables/x/t1.csv, ...]``), or a mapping (walked,
    e.g. the ``transcription`` page/table dict).
    """
    hits: list[tuple[str, str, str]] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            key_s = str(key)
            child = f"{path}.{key_s}" if path else key_s
            if key_s in PATH_VALUE_KEYS:
                if isinstance(value, str):
                    hits.append((key_s, child, value))
                elif isinstance(value, (list, tuple)):
                    for i, item in enumerate(value):
                        if isinstance(item, str):
                            hits.append((key_s, f"{child}[{i}]", item))
            hits.extend(_walk_path_values(value, path=child))
    elif isinstance(node, (list, tuple)):
        for i, item in enumerate(node):
            hits.extend(_walk_path_values(item, path=f"{path}[{i}]"))
    return hits


def _walk_absolute_path_values(
    node: Any, *, path: str = ""
) -> list[tuple[str, str, str]]:
    """Return absolute strings found under the owning PATH_VALUE_KEYS set."""
    return [hit for hit in _walk_path_values(node, path=path) if _is_absolute_path(hit[2])]


def _absolute_path_value_errors(doc: Any, label: str) -> list[str]:
    """Refuse every absolute path value in one parsed extract (fails closed).

    One error per distinct (key, value): an anchor expanded at many aliases is
    reported once with its expansion count, so the message stays readable.
    """
    hits = _walk_absolute_path_values(doc)
    by_value: dict[tuple[str, str], list[str]] = {}
    for key, dotted, value in hits:
        by_value.setdefault((key, value), []).append(dotted)
    errors: list[str] = []
    for (key, value), where in by_value.items():
        more = f" (+{len(where) - 1} more via YAML aliases/repeats)" if len(where) > 1 else ""
        errors.append(
            f"{label}: {where[0]} must be repository-relative "
            f"(absolute/machine-local path refused): {value!r}{more}"
        )
    return errors


def _private_path_value_errors(doc: Any, label: str) -> list[str]:
    """Refuse repository-private paths under every path-valued extract key."""
    hits = [hit for hit in _walk_path_values(doc) if hit[2].startswith("docs-private/")]
    by_value: dict[tuple[str, str], list[str]] = {}
    for key, dotted, value in hits:
        by_value.setdefault((key, value), []).append(dotted)
    errors: list[str] = []
    for (key, value), where in by_value.items():
        more = f" (+{len(where) - 1} more via YAML aliases/repeats)" if len(where) > 1 else ""
        errors.append(
            f"{label}: {where[0]} must be repository-portable "
            f"(docs-private path refused for {key}): {value!r}{more}"
        )
    return errors


def _docs_private_prose_errors(doc: Any, label: str) -> list[str]:
    """Refuse private-workspace references in extract prose, not path fields."""
    errors: list[str] = []

    def walk(node: Any, field_path: str = "") -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                key_s = str(key)
                child_path = f"{field_path}.{key_s}" if field_path else key_s
                if key_s in PATH_VALUE_KEYS and isinstance(value, str):
                    continue
                if key_s in PATH_VALUE_KEYS and isinstance(value, (list, tuple)):
                    for index, item in enumerate(value):
                        if not isinstance(item, str):
                            walk(item, f"{child_path}[{index}]")
                    continue
                walk(value, child_path)
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                walk(value, f"{field_path}[{index}]")
        elif isinstance(node, str) and "docs-private" in node:
            errors.append(
                f"{label}:{field_path}: [docs-private-prose] private path reference "
                "is forbidden in prose"
            )

    walk(doc)
    return errors


class ExtractValidationError(Exception):
    """One or more extract validation failures."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__(f"{len(self.errors)} extract validation error(s)")


def _is_extract_path(path: Path) -> bool:
    if path.suffix not in {".yaml", ".yml"}:
        return False
    if path.name.startswith("_"):
        return False
    if path.name.upper().startswith("SCHEMA"):
        return False
    return True


def discover_extracts(directory: Path | None = None) -> list[Path]:
    d = directory or EXTRACTS_DIR
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and _is_extract_path(p))


# ---------------------------------------------------------------------------
# Fidelity policy (ENFORCED_FOR_NEW + shrink-only allowlist)
# ---------------------------------------------------------------------------


def load_fidelity_graduation_ledger(
    path: Path | None = None,
) -> tuple[set[str], list[str]]:
    """Load the canonical append-only graduation history."""
    p = path or FIDELITY_GRADUATION_LEDGER_PATH
    if not p.is_file():
        return set(), [f"{p}: missing canonical fidelity graduation ledger"]
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return set(), [f"{p}: failed to load YAML: {exc}"]
    if not isinstance(doc, Mapping):
        return set(), [f"{p}: root must be a mapping"]
    errors: list[str] = []
    if doc.get("schema_version") != FIDELITY_GRADUATION_LEDGER_SCHEMA:
        errors.append(
            f"{p}: schema_version must be {FIDELITY_GRADUATION_LEDGER_SCHEMA!r}, "
            f"got {doc.get('schema_version')!r}"
        )
    graduated = doc.get("graduated_pre_policy_source_ids")
    if not isinstance(graduated, list) or not all(
        isinstance(x, str) for x in graduated
    ):
        errors.append(
            f"{p}: graduated_pre_policy_source_ids must be a list of strings"
        )
        return set(), errors
    graduated_set = set(graduated)
    if len(graduated) != len(graduated_set):
        errors.append(f"{p}: graduated_pre_policy_source_ids contains duplicates")
    return graduated_set, errors


def load_committed_fidelity_graduation_history(
    path: Path | None = None,
) -> tuple[set[str], list[str]]:
    """Return every graduation id recorded anywhere in the ledger's Git history.

    Candidate policy and ledger files are mutable together, so current-state
    comparison cannot prove append-only behavior.  Git ancestry is the external
    prior state used by the normal validator/CI path.  The union makes an id
    irreversible even if a later commit deletes the whole ledger entry.
    """
    p = (path or FIDELITY_GRADUATION_LEDGER_PATH).resolve()
    try:
        relative = p.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return set(), [f"{p}: canonical graduation ledger must be inside repository"]
    try:
        shallow = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if shallow.returncode != 0:
            detail = shallow.stderr.strip() or f"git rev-parse exited {shallow.returncode}"
            return set(), [f"{p}: cannot inspect canonical Git history: {detail}"]
        if shallow.stdout.strip() == "true":
            return set(), [
                f"{p}: cannot prove append-only graduation history from a shallow "
                f"clone; fetch full Git ancestry for validation"
            ]
        log = subprocess.run(
            ["git", "log", "--format=%H", "--", relative],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return set(), [f"{p}: cannot inspect canonical Git history: {exc}"]
    if log.returncode != 0:
        detail = log.stderr.strip() or f"git log exited {log.returncode}"
        return set(), [f"{p}: cannot inspect canonical Git history: {detail}"]

    historical: set[str] = set()
    errors: list[str] = []
    for commit in filter(None, (line.strip() for line in log.stdout.splitlines())):
        shown = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        # A deletion commit names the path but has no file at that tree. Older
        # versions still appear later in the log and retain the tombstones.
        if shown.returncode != 0:
            continue
        try:
            doc = yaml.safe_load(shown.stdout)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{p}@{commit}: failed to parse historical ledger: {exc}")
            continue
        ids = doc.get("graduated_pre_policy_source_ids") if isinstance(doc, Mapping) else None
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            errors.append(
                f"{p}@{commit}: historical graduated_pre_policy_source_ids "
                f"must be a list of strings"
            )
            continue
        historical.update(ids)
    return historical, errors


def load_fidelity_policy(
    path: Path | None = None,
    *,
    graduated_ledger_path: Path | None = None,
    prior_graduated_source_ids: set[str] | None = None,
    artifact_root: Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Load the fidelity policy file.

    Returns ``(policy_doc, errors)``. On hard load failure, policy_doc is empty
    and errors is non-empty.

    Shrink-only + no re-activation: the closed set is partitioned into
    ``active_pre_policy_source_ids`` ∪ ``graduated_pre_policy_source_ids``
    (disjoint). Graduating moves an id from active → graduated; re-adding a
    graduated id to active is refused.
    """
    p = path or FIDELITY_POLICY_PATH
    ledger_path = graduated_ledger_path or p.with_name(
        FIDELITY_GRADUATION_LEDGER_PATH.name
    )
    if not p.is_file():
        return {}, [f"{p}: missing fidelity policy file (t-510 ENFORCED_FOR_NEW)"]
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {}, [f"{p}: failed to load YAML: {exc}"]
    errors: list[str] = []
    if not isinstance(doc, Mapping):
        return {}, [f"{p}: root must be a mapping"]
    if doc.get("schema_version") != FIDELITY_POLICY_SCHEMA:
        errors.append(
            f"{p}: schema_version must be {FIDELITY_POLICY_SCHEMA!r}, "
            f"got {doc.get('schema_version')!r}"
        )
    if doc.get("policy") != "ENFORCED_FOR_NEW":
        errors.append(
            f"{p}: policy must be 'ENFORCED_FOR_NEW', got {doc.get('policy')!r}"
        )
    closed = doc.get("closed_set_source_ids")
    active = doc.get("active_pre_policy_source_ids")
    graduated = doc.get("graduated_pre_policy_source_ids")
    if not isinstance(closed, list) or not all(isinstance(x, str) for x in closed):
        errors.append(f"{p}: closed_set_source_ids must be a list of strings")
        closed_set: set[str] = set()
    else:
        closed_set = set(closed)
        if len(closed) != len(closed_set):
            errors.append(f"{p}: closed_set_source_ids contains duplicates")
    if not isinstance(active, list) or not all(isinstance(x, str) for x in active):
        errors.append(f"{p}: active_pre_policy_source_ids must be a list of strings")
        active_set: set[str] = set()
    else:
        active_set = set(active)
        if len(active) != len(active_set):
            errors.append(f"{p}: active_pre_policy_source_ids contains duplicates")
    # graduated is optional in older snapshots only if empty-equivalent; require list.
    if graduated is None:
        graduated = []
        # Normalize so callers see a stable key (do not mutate caller's view
        # beyond the returned dict copy below).
    if not isinstance(graduated, list) or not all(isinstance(x, str) for x in graduated):
        errors.append(f"{p}: graduated_pre_policy_source_ids must be a list of strings")
        graduated_set: set[str] = set()
    else:
        graduated_set = set(graduated)
        if len(graduated) != len(graduated_set):
            errors.append(f"{p}: graduated_pre_policy_source_ids contains duplicates")
    ledger_set, ledger_errors = load_fidelity_graduation_ledger(ledger_path)
    errors.extend(ledger_errors)
    if prior_graduated_source_ids is not None:
        prior_graduated = set(prior_graduated_source_ids)
        history_errors: list[str] = []
    elif ledger_path.resolve() == FIDELITY_GRADUATION_LEDGER_PATH.resolve():
        prior_graduated, history_errors = load_committed_fidelity_graduation_history(
            ledger_path
        )
        errors.extend(history_errors)
    else:
        prior_graduated = set()
        history_errors = []
    # Shrink-only: active ⊆ closed. Additions beyond closed are refused here.
    extras = active_set - closed_set
    if extras:
        errors.append(
            f"{p}: active_pre_policy_source_ids not ⊆ closed_set_source_ids "
            f"(shrink-only; illegal additions: {sorted(extras)})"
        )
    grad_extras = graduated_set - closed_set
    if grad_extras:
        errors.append(
            f"{p}: graduated_pre_policy_source_ids not ⊆ closed_set_source_ids "
            f"(illegal ids: {sorted(grad_extras)})"
        )
    # No re-activation: graduated ids cannot return to active.
    reactivated = active_set & graduated_set
    if reactivated:
        errors.append(
            f"{p}: active_pre_policy_source_ids ∩ graduated_pre_policy_source_ids "
            f"non-empty (re-activation refused: {sorted(reactivated)})"
        )
    if not ledger_errors:
        missing_tombstones = ledger_set - graduated_set
        if missing_tombstones:
            errors.append(
                f"{p}: graduated_pre_policy_source_ids deleted entries from the "
                f"canonical graduation ledger (append-only tombstones missing: "
                f"{sorted(missing_tombstones)})"
            )
        unrecorded_graduations = graduated_set - ledger_set
        if unrecorded_graduations:
            errors.append(
                f"{p}: graduated_pre_policy_source_ids contains entries absent from "
                f"the canonical graduation ledger (append there in the same change: "
                f"{sorted(unrecorded_graduations)})"
            )
        ledger_reactivated = active_set & ledger_set
        if ledger_reactivated:
            errors.append(
                f"{p}: active_pre_policy_source_ids re-activates ids recorded in the "
                f"canonical graduation ledger: {sorted(ledger_reactivated)}"
            )
    if not history_errors:
        deleted_history = prior_graduated - ledger_set
        if deleted_history:
            errors.append(
                f"{ledger_path}: canonical graduation ledger deleted ids present "
                f"in prior Git history (append-only violation: "
                f"{sorted(deleted_history)})"
            )
        history_reactivated = active_set & prior_graduated
        if history_reactivated:
            errors.append(
                f"{p}: active_pre_policy_source_ids re-activates ids from prior "
                f"canonical graduation history: {sorted(history_reactivated)}"
            )
    # Partition: every closed id is either still exempt or graduated.
    # Prevents silent shrink-without-tombstone that would allow later re-add.
    if closed_set and not (extras or grad_extras or reactivated):
        missing = closed_set - active_set - graduated_set
        if missing:
            errors.append(
                f"{p}: closed-set ids missing from both active and graduated "
                f"(graduate explicitly when removing from active: {sorted(missing)})"
            )
        stray = (active_set | graduated_set) - closed_set
        if stray:
            # Already covered by ⊆ checks; keep for completeness if those skipped.
            pass
    out = dict(doc)
    if "graduated_pre_policy_source_ids" not in out:
        out["graduated_pre_policy_source_ids"] = list(graduated)
    # Promotion: graduating an id certifies that extract. A missing file is not
    # certified here (synthetic policy tests graduate ids that have no extract).
    # The same chokepoint refuses a stored review_status of reviewed.
    if not errors:
        for source_id in sorted(graduated_set):
            extract_path = EXTRACTS_DIR / f"{source_id}.yaml"
            if not extract_path.is_file():
                continue
            try:
                extract_doc = load_extract_yaml(extract_path)
            except Exception as exc:  # noqa: BLE001 — surface as a promotion refusal
                errors.append(
                    f"{p}: cannot certify graduated extract {source_id}: {exc}"
                )
                continue
            if not isinstance(extract_doc, Mapping):
                errors.append(
                    f"{p}: graduated extract {source_id} is not a mapping"
                )
                continue
            errors.extend(
                certify_extract_source_fidelity(
                    extract_doc,
                    label=source_id,
                    artifact_root=artifact_root,
                )
            )
    return out, errors


def pre_policy_source_ids(policy: Mapping[str, Any] | None = None) -> set[str]:
    """Return the active pre-policy allowlist (empty if policy unavailable)."""
    if policy is None:
        policy, errs = load_fidelity_policy()
        if errs:
            return set()
    active = policy.get("active_pre_policy_source_ids") or []
    return {str(x) for x in active if isinstance(x, str)}


# ---------------------------------------------------------------------------
# Fidelity sample resolution + match (t-510 gate)
# ---------------------------------------------------------------------------


def _values_equal(
    expected: Any,
    actual: Any,
    *,
    rel_tol: float | None = None,
) -> bool:
    """Type-strict structural equality for fidelity pins.

    Default is exact equality (no float tolerance, no bool/int coercion).
    ``rel_tol`` is only applied when the sample explicitly requests it (and
    both sides are non-bool numbers).
    """
    if expected is None or actual is None:
        return expected is actual
    # bool is a subclass of int — never let False==0 / True==1 pass.
    if type(expected) is bool or type(actual) is bool:
        return type(expected) is bool and type(actual) is bool and expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if type(expected) is not type(actual):
            return False
        if rel_tol is not None:
            return math.isclose(
                float(expected), float(actual), rel_tol=float(rel_tol), abs_tol=0.0
            )
        return expected == actual
    if isinstance(expected, str) and isinstance(actual, str):
        return expected == actual
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        if type(expected) is not type(actual) or len(expected) != len(actual):
            return False
        unmatched_actual_keys = list(actual.keys())
        for expected_key, expected_value in expected.items():
            match_index = next(
                (
                    i
                    for i, actual_key in enumerate(unmatched_actual_keys)
                    if type(expected_key) is type(actual_key)
                    and _values_equal(expected_key, actual_key)
                ),
                None,
            )
            if match_index is None:
                return False
            actual_key = unmatched_actual_keys.pop(match_index)
            if not _values_equal(
                expected_value, actual[actual_key], rel_tol=rel_tol
            ):
                return False
        return True
    if isinstance(expected, (list, tuple)) and isinstance(actual, (list, tuple)):
        if type(expected) is not type(actual) or len(expected) != len(actual):
            return False
        return all(
            _values_equal(a, b, rel_tol=rel_tol) for a, b in zip(expected, actual)
        )
    if isinstance(expected, AbstractSet) and isinstance(actual, AbstractSet):
        if type(expected) is not type(actual) or len(expected) != len(actual):
            return False
        unmatched_actual = list(actual)
        for expected_item in expected:
            match_index = next(
                (
                    i
                    for i, actual_item in enumerate(unmatched_actual)
                    if _values_equal(expected_item, actual_item, rel_tol=rel_tol)
                ),
                None,
            )
            if match_index is None:
                return False
            unmatched_actual.pop(match_index)
        return True
    return type(expected) is type(actual) and expected == actual


def _path_is_observation_evidence(path: str) -> bool:
    """True when a path-based sample pins observation or context evidence.

    Required shape::

        species.<id>.(observations|context)[<id|index>].(values|equipment|<condition>)[....]

    ``<condition>`` is a scored numeric condition (``_SCORED_NUMERIC_CONDITIONS``).
    """
    if not path or not isinstance(path, str):
        return False
    parts = path.split(".")
    if len(parts) < 4:
        return False
    if parts[0] != "species":
        return False
    obs_seg = _PATH_SEGMENT_RE.match(parts[2])
    if (
        not obs_seg
        or obs_seg.group(1) not in ("observations", "context")
        or obs_seg.group(2) is None
    ):
        return False
    leaf_seg = _PATH_SEGMENT_RE.match(parts[3])
    if not leaf_seg:
        return False
    name = leaf_seg.group(1)
    return name in ("values", "equipment") or name in _SCORED_NUMERIC_CONDITIONS


def _sample_expected_value(sample: Mapping[str, Any]) -> Any:
    if "value" in sample and "draft_value" in sample:
        raise KeyError("sample must use exactly one of value or draft_value")
    if "value" in sample:
        return sample["value"]
    if "draft_value" in sample:
        return sample["draft_value"]
    raise KeyError("sample missing value/draft_value")


def _is_null_or_empty_pin(value: Any) -> bool:
    """True when a fidelity pin carries no observed content (null / empty-only)."""
    return not _has_payload_leaf(value)


def _mutable_container_ids(value: Any, seen: set[int] | None = None) -> set[int]:
    """Return identities of every reachable mutable mapping/sequence, cycle-safe."""
    if seen is None:
        seen = set()
    value_id = id(value)
    if value_id in seen:
        return set()
    seen.add(value_id)
    identities: set[int] = set()
    if isinstance(value, Mapping):
        if isinstance(value, MutableMapping):
            identities.add(value_id)
        for child in value.values():
            identities.update(_mutable_container_ids(child, seen))
    elif isinstance(value, (list, tuple)):
        if isinstance(value, MutableSequence):
            identities.add(value_id)
        for child in value:
            identities.update(_mutable_container_ids(child, seen))
    elif isinstance(value, AbstractSet):
        if isinstance(value, MutableSet):
            identities.add(value_id)
        for child in value:
            identities.update(_mutable_container_ids(child, seen))
    return identities


def _shares_mutable_identity(expected: Any, actual: Any) -> bool:
    """True when pin and body graphs share a mutable object at any depth."""
    return bool(
        _mutable_container_ids(expected) & _mutable_container_ids(actual)
    )


_STRUCTURED_SAMPLE_SELECTOR_KEYS = frozenset(
    {"species", "observation_id", "observable", "field", "value_key", "index", "T_K", "T"}
)


def _sample_addressing_keys(sample: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return populated path and structured selector keys for one sample."""
    path_keys = [key for key in ("path", "field_path") if key in sample]
    structured_keys = [
        key
        for key in _STRUCTURED_SAMPLE_SELECTOR_KEYS
        if key in sample and sample[key] is not None
    ]
    return path_keys, structured_keys


def _sample_mode_errors(sample: Mapping[str, Any]) -> list[str]:
    """Return ambiguity errors for aliases/selectors with precedence semantics."""
    path_keys, structured_keys = _sample_addressing_keys(sample)
    errors: list[str] = []
    for key in (
        "path",
        "field_path",
        "locator",
        "source_locator",
        *sorted(_STRUCTURED_SAMPLE_SELECTOR_KEYS),
    ):
        if key not in sample:
            continue
        value = sample[key]
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"selector {key} must be non-null and non-empty")
    if len(path_keys) > 1:
        errors.append("use exactly one path addressing key (path or field_path)")
    if path_keys and structured_keys:
        errors.append("do not mix path and structured addressing modes")
    for left, right in (
        ("field", "value_key"),
        ("T_K", "T"),
        ("value", "draft_value"),
        ("locator", "source_locator"),
    ):
        if left in sample and right in sample:
            errors.append(f"use exactly one of {left} or {right}")
    temperature_keys = [key for key in ("T_K", "T") if key in sample]
    if "index" in sample and temperature_keys:
        errors.append("use index or a temperature selector, not both")
    return errors


def _validated_rel_tol(sample: Mapping[str, Any]) -> tuple[float | None, str | None]:
    """Validate explicit numeric tolerance without bool/NaN/inf/crash bypasses."""
    if "rel_tol" not in sample:
        return None, None
    rel_tol = sample["rel_tol"]
    if isinstance(rel_tol, bool) or not isinstance(rel_tol, (int, float)):
        return None, f"rel_tol must be a real number (not bool), got {rel_tol!r}"
    rel_tol_f = float(rel_tol)
    if not math.isfinite(rel_tol_f) or rel_tol_f < 0.0 or rel_tol_f >= 1.0:
        return None, (
            f"rel_tol must be finite and satisfy 0 <= rel_tol < 1, got {rel_tol!r}"
        )
    return rel_tol_f, None


def resolve_field_path(doc: Mapping[str, Any], path: str) -> Any:
    """Resolve a dotted path with optional ``[key]`` selectors into ``doc``.

    Examples::

        species.Fe.observations[fe_alpha_1].values.alpha
        species.Fe.observations[0].values
    """
    if not path or not isinstance(path, str):
        raise KeyError("empty path")
    cur: Any = doc
    for raw_seg in path.split("."):
        m = _PATH_SEGMENT_RE.match(raw_seg)
        if not m:
            raise KeyError(f"malformed path segment {raw_seg!r} in {path!r}")
        key, bracket = m.group(1), m.group(2)
        if not isinstance(cur, Mapping) or key not in cur:
            raise KeyError(f"missing key {key!r} while resolving {path!r}")
        cur = cur[key]
        if bracket is None:
            continue
        # Bracket: integer index into list, or observation_id match in list,
        # or key into mapping.
        if isinstance(cur, Mapping):
            if bracket not in cur:
                raise KeyError(
                    f"missing bracket key {bracket!r} while resolving {path!r}"
                )
            cur = cur[bracket]
            continue
        if isinstance(cur, list):
            # Prefer integer index when bracket is a bare integer.
            if re.fullmatch(r"-?\d+", bracket):
                idx = int(bracket)
                if idx < 0 or idx >= len(cur):
                    raise KeyError(
                        f"index {idx} out of range while resolving {path!r}"
                    )
                cur = cur[idx]
                continue
            # Match observation_id (or other id field) in a list of mappings.
            found = None
            for item in cur:
                if isinstance(item, Mapping) and str(item.get("observation_id")) == bracket:
                    found = item
                    break
            if found is None:
                raise KeyError(
                    f"no list item with observation_id={bracket!r} "
                    f"while resolving {path!r}"
                )
            cur = found
            continue
        raise KeyError(
            f"cannot apply bracket {bracket!r} to {type(cur).__name__} "
            f"while resolving {path!r}"
        )
    return cur


def _find_observation(
    doc: Mapping[str, Any],
    *,
    species: str,
    observation_id: str | None = None,
    observable: str | None = None,
) -> Mapping[str, Any]:
    """Locate one observation-shaped row under ``species``.

    Structured pins resolve against both the scored ``observations[]`` list and
    the sibling ``context[]`` container (d-032), so a pin survives a row moving
    between the two. The observation_id namespace is file-unique across both
    containers (enforced under ``validate_extract_document``), so an id matching
    exactly one row is unambiguous; an id matching rows in both containers is a
    typed ambiguity error, never a silent pick.
    """
    species_map = doc.get("species")
    if not isinstance(species_map, Mapping) or species not in species_map:
        raise KeyError(f"species {species!r} not in extract")
    block = species_map[species]
    if not isinstance(block, Mapping):
        raise KeyError(f"species {species!r} block is not a mapping")
    obs_list = block.get("observations") or []
    if not isinstance(obs_list, list):
        raise KeyError(f"species {species!r} observations is not a list")
    ctx_list = block.get("context") or []
    if not isinstance(ctx_list, list):
        raise KeyError(f"species {species!r} context is not a list")
    containers = (("observations", obs_list), ("context", ctx_list))
    if observation_id:
        matches = [
            (container, row)
            for container, rows in containers
            for row in rows
            if isinstance(row, Mapping) and row.get("observation_id") == observation_id
        ]
        if not matches:
            raise KeyError(
                f"observation_id {observation_id!r} not found under species "
                f"{species!r} (searched observations[] and context[])"
            )
        if len(matches) > 1:
            where = ", ".join(f"{container}[]" for container, _row in matches)
            raise KeyError(
                f"ambiguous observation_id {observation_id!r} under species "
                f"{species!r}: {len(matches)} rows match ({where}); "
                f"observation_id must be unique across observations[] and context[]"
            )
        row = matches[0][1]
        if observable and row.get("type") != observable:
            raise KeyError(
                f"observation_id {observation_id!r} has type "
                f"{row.get('type')!r}, not requested observable {observable!r}"
            )
        return row
    if observable:
        matches = [
            row
            for _container, rows in containers
            for row in rows
            if isinstance(row, Mapping) and row.get("type") == observable
        ]
        if not matches:
            raise KeyError(
                f"no observation with type={observable!r} under species {species!r} "
                f"(searched observations[] and context[])"
            )
        if len(matches) > 1:
            raise KeyError(
                f"ambiguous type={observable!r} under species {species!r} "
                f"({len(matches)} matches across observations[] and context[]; "
                f"set observation_id)"
            )
        return matches[0]
    raise KeyError("structured sample needs observation_id or observable")


# Point temperature keys, in the order a structured ``T_K`` / ``T`` selector
# searches. A present key that is not the target does not hide a later key.
_SERIES_TEMPERATURE_KEYS = ("T_K", "T", "temperature_K", "temp_K")


def _series_temperature_index(points: Sequence[Any], target: float) -> int | None:
    """Index of the first series point whose temperature key matches ``target``."""
    for index, point in enumerate(points):
        if not isinstance(point, Mapping):
            continue
        for key in _SERIES_TEMPERATURE_KEYS:
            if key not in point:
                continue
            try:
                seen = float(point[key])
            except (TypeError, ValueError):
                continue
            if math.isclose(seen, target, rel_tol=0.0, abs_tol=1e-6):
                return index
    return None


def resolve_fidelity_sample(
    doc: Mapping[str, Any],
    sample: Mapping[str, Any],
) -> Any:
    """Resolve the extract value a fidelity sample claims to pin.

    Supports:

    * **path-based** (pilot): ``path`` / ``field_path`` → walk the extract
    * **structured** (preferred for OCR): ``species`` + ``observation_id``
      and/or ``observable``, optional ``field`` / ``value_key`` into
      ``values``, optional ``index`` into a series list, optional ``T_K``
      match against a series point's temperature key. The addressed row may
      live in ``observations[]`` or the sibling ``context[]`` container
      (d-032); an id matching rows in both is an ambiguity error.
    """
    mode_errors = _sample_mode_errors(sample)
    if mode_errors:
        raise KeyError("; ".join(mode_errors))
    path_keys, _structured_keys = _sample_addressing_keys(sample)
    path = sample.get(path_keys[0]) if path_keys else None
    if path:
        return resolve_field_path(doc, str(path))

    species = sample.get("species")
    if not species:
        raise KeyError(
            "fidelity sample needs path/field_path or structured species+observable"
        )
    obs = _find_observation(
        doc,
        species=str(species),
        observation_id=(
            str(sample["observation_id"]) if sample.get("observation_id") else None
        ),
        observable=str(sample["observable"]) if sample.get("observable") else None,
    )
    values = obs.get("values")
    field = sample.get("field") or sample.get("value_key")
    index = sample.get("index")
    t_k = sample.get("T_K") if "T_K" in sample else sample.get("T")

    cur: Any = values
    if index is not None:
        if not isinstance(cur, list):
            raise KeyError(
                f"index={index!r} requires values to be a list, "
                f"got {type(cur).__name__}"
            )
        idx = int(index)
        if idx < 0 or idx >= len(cur):
            raise KeyError(f"index {idx} out of range (len={len(cur)})")
        cur = cur[idx]
    elif t_k is not None:
        if not isinstance(cur, list):
            raise KeyError(
                f"T_K={t_k!r} match requires values to be a list of points"
            )
        target = float(t_k)
        found_index = _series_temperature_index(cur, target)
        if found_index is None:
            raise KeyError(f"no series point with T≈{target} under species {species!r}")
        cur = cur[found_index]

    if field:
        if not isinstance(cur, Mapping) or field not in cur:
            raise KeyError(f"field {field!r} not found in resolved values")
        return cur[field]
    return cur


def check_fidelity_sample_matches(
    doc: Mapping[str, Any],
    sample: Mapping[str, Any],
    *,
    label: str = "sample",
) -> list[str]:
    """Return error strings if sample expected value does not match the extract.

    Empty list ⇒ match. Used by the parameterized t-510 gate test and available
    for tranche review tooling.

    Rejects null/empty pins, shared sample/body object identity (YAML aliases),
    and type-coerced near-matches (bool/int, float isclose drift).
    """
    errors: list[str] = []
    if "value" not in sample and "draft_value" not in sample:
        return [f"{label}: sample missing value/draft_value"]
    try:
        expected = _sample_expected_value(sample)
    except KeyError as exc:
        return [f"{label}: invalid fidelity sample value selector: {exc}"]
    if _is_null_or_empty_pin(expected):
        return [
            f"{label}: fidelity sample value must be a non-null observed pin "
            f"(null/empty-only pins refused)"
        ]
    try:
        actual = resolve_fidelity_sample(doc, sample)
    except KeyError as exc:
        return [f"{label}: cannot resolve sample against extract: {exc}"]
    if _is_null_or_empty_pin(actual):
        return [
            f"{label}: resolved extract value must be non-null and non-empty "
            f"(content-free pins do not satisfy the fidelity gate)"
        ]
    # Mutable containers that share identity at any depth co-mutate under edits.
    if _shares_mutable_identity(expected, actual):
        return [
            f"{label}: fidelity sample value shares mutable object identity with "
            f"extract body at some nesting depth (YAML alias); rewrite pin as an "
            f"independent literal"
        ]
    rel_tol_f, rel_tol_error = _validated_rel_tol(sample)
    if rel_tol_error:
        return [f"{label}: {rel_tol_error}"]
    if not _values_equal(expected, actual, rel_tol=rel_tol_f):
        errors.append(
            f"{label}: fidelity sample value mismatch: "
            f"sample={expected!r} extract={actual!r}"
        )
    return errors


def check_all_fidelity_samples_match(
    doc: Mapping[str, Any],
    *,
    label: str = "<document>",
) -> list[str]:
    """Assert every fidelity sample still matches extract content."""
    samples = doc.get("fidelity_samples")
    if not samples:
        return []
    if not isinstance(samples, list):
        return [f"{label}: fidelity_samples must be a list"]
    errors: list[str] = []
    for i, sample in enumerate(samples):
        sp = f"{label}:fidelity_samples[{i}]"
        if not isinstance(sample, Mapping):
            errors.append(f"{sp}: each sample must be a mapping")
            continue
        errors.extend(check_fidelity_sample_matches(doc, sample, label=sp))
    return errors


def _usable_observation_id(observation: Mapping[str, Any]) -> str | None:
    """The id coverage can name, or None when the row cannot be addressed.

    Same rule as schema validation: a non-empty string. A missing key,
    a blank string, or a non-string is not an id. Callers refuse those
    scored rows. They do not drop them from certification.
    """
    observation_id = observation.get("observation_id")
    if isinstance(observation_id, str) and observation_id:
        return observation_id
    return None


def _fidelity_sample_observation_key(
    doc: Mapping[str, Any], sample: Mapping[str, Any]
) -> tuple[str, str] | None:
    """Return the scored observation addressed by a fidelity sample, if any."""
    path_keys, structured_keys = _sample_addressing_keys(sample)
    if path_keys:
        if len(path_keys) != 1:
            return None
        path = sample.get(path_keys[0])
        if not isinstance(path, str) or not _path_is_observation_evidence(path):
            return None
        parts = path.split(".")
        if len(parts) < 4 or parts[0] != "species":
            return None
        species_match = _PATH_SEGMENT_RE.match(parts[1])
        observation_match = _PATH_SEGMENT_RE.match(parts[2])
        if (
            not species_match
            or species_match.group(2) is not None
            or not observation_match
            or observation_match.group(1) != "observations"
            or observation_match.group(2) is None
        ):
            return None
        species_id = species_match.group(1)
        try:
            observation = resolve_field_path(doc, ".".join(parts[:3]))
        except KeyError:
            return None
        if not isinstance(observation, Mapping):
            return None
        observation_id = _usable_observation_id(observation)
        if observation_id is None:
            return None
        return species_id, observation_id

    if not structured_keys or not isinstance(sample.get("species"), str):
        return None
    species_id = sample["species"]
    try:
        observation = _find_observation(
            doc,
            species=species_id,
            observation_id=(
                str(sample["observation_id"]) if sample.get("observation_id") else None
            ),
            observable=str(sample["observable"]) if sample.get("observable") else None,
        )
    except KeyError:
        return None
    species_map = doc.get("species")
    if not isinstance(species_map, Mapping):
        return None
    block = species_map.get(species_id)
    observations = block.get("observations") if isinstance(block, Mapping) else None
    if not isinstance(observations, list) or not any(
        observation is row for row in observations
    ):
        return None
    observation_id = _usable_observation_id(observation)
    if observation_id is None:
        return None
    return species_id, observation_id


# Auto-migrated pilot pins copied the first stored payload. They are not a
# source check. Do not rewrite them; the re-audit is a separate campaign.
_PILOT_MIGRATION_NOTE = (
    "auto fidelity sample from first observation payload (pilot migration)"
)
_FIDELITY_WARNING_KINDS = (
    "uncovered_observation",
    "unattested_sample",
    "auto_migrated_pilot_pin",
    "source_blind_pin",
    "ambiguous_observation_id",
    "missing_observation_id",
    "non_finite_evidence",
)
_ARTIFACT_PATH_KEYS = ("source_path", "quotes_path")
_FIDELITY_DETAIL_CAP = 3
# Same bracket rule as resolve_field_path: a bare integer is a list position.
_INTEGER_SELECTOR_RE = re.compile(r"-?\d+")


@dataclass(frozen=True)
class FidelityWarning:
    """One counted source-fidelity hole. ``count`` is observations or samples."""

    kind: str
    count: int
    label: str
    detail: str = ""
    reason_codes_by_pin: tuple[frozenset[str], ...] = ()

    def render(self) -> str:
        text = f"{self.label}: fidelity warning {self.kind} count={self.count}"
        if self.detail:
            text = f"{text}: {self.detail}"
        return text


@dataclass(frozen=True)
class _LocatorSourceBlindReason:
    detail: str
    reason_codes: frozenset[str] = frozenset()


# A pin is unverified, not failed, only when every code is in this set.
# cited_token_absent and every other content refusal stay failures.
_MOUNT_ABSENCE_CODES = frozenset({
    "corpus_root_unset",
    "corpus_prefix_unmounted",
})


def _mount_absence_only(reason_codes: frozenset[str]) -> bool:
    """True when every code is a missing mount or an unmounted prefix."""
    return bool(reason_codes) and reason_codes <= _MOUNT_ABSENCE_CODES


@dataclass
class CorpusMountTally:
    """Corpus pins observed while counting fidelity warnings for one walk.

    ``verified_opens`` are attestation-complete pins whose top-level prefix
    directory exists and which opened with a matching digest and token.
    ``unverified_pins`` are pins whose source-blind codes are all
    mount-absence codes. A pin that also carries any other code, including
    ``cited_token_absent``, stays on the warning list. ``unverified_by_prefix``
    counts unmounted-prefix pins by the missing directory.
    """

    verified_opens: int = 0
    unverified_pins: int = 0
    unverified_by_prefix: dict[str, int] = field(default_factory=dict)


def _note_reason(reasons: list[str], extra: list[int], text: str) -> None:
    if len(reasons) < _FIDELITY_DETAIL_CAP:
        reasons.append(text)
    else:
        extra[0] += 1


def _finish_detail(reasons: list[str], extra: int) -> str:
    text = "; ".join(reasons)
    if extra:
        text = f"{text}; and {extra} more"
    return text


# Observation fields the scored temperature payload copies beside ``values``
# (simulator/battery/migrate.py, the select_declared_source temperature read).
# This is the only condition set coverage walks. A new scored condition is
# added here, not as another branch in the coverage loop.
_SCORED_NUMERIC_CONDITIONS = (
    "T_range_K",
    "T_K",
    "T_C",
    "temperature_C",
    "temp_C",
    "point_conditions",
)
# A locator cites a condition. Its page numbers are not measured leaves.
_CONDITION_PROVENANCE_KEYS = frozenset({"locator"})
# Closed keys whose string values are extractor-authored annotation, not
# transcriptions. Numeric values under these keys remain measured leaves.
ANNOTATION_KEYS = frozenset(
    {
        "attribution",
        "note",
        "reason",
        "schema_gap",
        "composition_derivation",
        "parents_not_printed",
    }
)
# Condition-state refusal tags. StateTag is the schema owner. ``value`` is
# the printed-evidence case and is not in this set.
_NON_VALUE_STATE_TAGS = frozenset(
    member.value for member in StateTag if member is not StateTag.VALUE
)


def _is_annotation_string(
    prefix: tuple[Any, ...],
    key: Any,
    value: Any,
    parent: Mapping[str, Any],
) -> bool:
    """True for an extractor annotation string the evidence walk skips.

    A string under an annotation key other than ``reason`` is annotation
    only as a direct child of ``values``. A ``reason`` string is annotation
    only when ``parent`` is a condition state whose ``tag`` is a non-value
    ``StateTag``, at any depth. ``tag: value`` and a mapping with no state
    tag stay leaves, at the observation root and nested under ``values``.
    Numbers stay leaves. A nested ``locator.note`` is not a direct child
    of ``values``, so it stays a leaf.
    """
    if not isinstance(value, str) or key not in ANNOTATION_KEYS:
        return False
    if key != "reason":
        return prefix == ("values",)
    tag = parent.get("tag")
    if isinstance(tag, StateTag):
        token = tag.value
    elif isinstance(tag, str):
        token = tag
    else:
        return False
    return token in _NON_VALUE_STATE_TAGS


def _scored_observations(
    doc: Mapping[str, Any],
) -> tuple[
    dict[tuple[str, str], Mapping[str, Any]],
    list[tuple[str, str, int]],
    list[tuple[str, int]],
]:
    """Unique scored rows, ambiguous ids, and rows with no usable id.

    Context rows are unscored (d-032) and are not part of this coverage set.
    Coverage keys are ``(species_id, observation_id)``. A second row with the
    same id used to overwrite the first, while a path pin resolves the first
    row, so an attested pin of the first value covered the last row. An id
    that names more than one row is omitted from the unique map and returned
    as ``(species_id, observation_id, row_count)``. Certification refuses
    those ids. They are not a covered observation.

    A scored row whose ``observation_id`` is not a non-empty string used to
    disappear from the required set, so a reviewed document with no samples
    certified without opening a source. Those rows are returned as
    ``(species_id, row_index)``. A non-mapping entry in ``observations``
    has no id and is returned the same way. Certification refuses them.
    """
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    missing: list[tuple[str, int]] = []
    species_map = doc.get("species") or {}
    if isinstance(species_map, Mapping):
        for species_id, block in species_map.items():
            observations = (
                block.get("observations") if isinstance(block, Mapping) else None
            )
            if not isinstance(observations, list):
                continue
            for row_index, observation in enumerate(observations):
                if not isinstance(observation, Mapping):
                    missing.append((str(species_id), row_index))
                    continue
                observation_id = _usable_observation_id(observation)
                if observation_id is None:
                    missing.append((str(species_id), row_index))
                    continue
                grouped.setdefault(
                    (str(species_id), observation_id), []
                ).append(observation)
    unique: dict[tuple[str, str], Mapping[str, Any]] = {}
    ambiguous: list[tuple[str, str, int]] = []
    for key in sorted(grouped):
        rows = grouped[key]
        if len(rows) == 1:
            unique[key] = rows[0]
        else:
            ambiguous.append((key[0], key[1], len(rows)))
    return unique, ambiguous, missing


def _scored_evidence_leaves(
    observation: Mapping[str, Any],
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Coverable leaves and non-finite leaves of one scored observation.

    One walk. ``values`` and each scored numeric condition are the evidence.
    A ``locator`` inside a condition is provenance. The first list is what
    a pin can cover. The second list is NaN and ±Inf. Those addresses are
    never omitted and are never coverable.
    """
    coverable: list[tuple[Any, ...]] = []
    non_finite: list[tuple[Any, ...]] = []

    def take(value: Any, prefix: tuple[Any, ...], skip_keys: frozenset[str]) -> None:
        child_coverable, child_non_finite = _classify_measured_leaves(
            value, prefix, skip_keys=skip_keys
        )
        coverable.extend(child_coverable)
        non_finite.extend(child_non_finite)

    if "values" in observation:
        take(observation.get("values"), ("values",), frozenset())
    for key in _SCORED_NUMERIC_CONDITIONS:
        if key not in observation or observation.get(key) is None:
            continue
        take(
            observation.get(key),
            (key,),
            _CONDITION_PROVENANCE_KEYS,
        )
    return coverable, non_finite


def _scored_measured_leaves(observation: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    """Coverable measured leaves of ``values`` and each scored condition.

    Addresses sit in one evidence tree. ``("values", "alpha")`` and
    ``("T_range_K", 0)`` are different leaves even when the numbers are
    equal. A ``locator`` inside a condition is provenance and is not a
    leaf. An annotation string other than ``reason`` is skipped only as a
    direct child of ``values``. A ``reason`` is skipped only when its
    parent condition state has a non-value ``StateTag``, at any depth.
    ``tag: value`` and a mapping with no state tag stay leaves, at the
    observation root and nested under ``values``. A nested ``locator.note``
    stays a leaf, as does a number under an annotation key. Non-finite
    floats are not in this list;
    ``_scored_evidence_leaves`` records them.
    """
    coverable, _non_finite = _scored_evidence_leaves(observation)
    return coverable


def _coverable_leaves_are_pinned(
    needed: Sequence[tuple[Any, ...]],
    covered: AbstractSet[tuple[Any, ...]],
    observation: Mapping[str, Any],
) -> bool:
    """True when every coverable leaf is pinned, or there is no coverable leaf.

    An empty leaf list is pinned only when the evidence has no digit-bearing
    string outside ``ANNOTATION_KEYS``.
    A non-finite quantity is not a coverable leaf. Callers refuse it on
    its own; this predicate does not certify the observation.
    """
    if not needed:
        return not _observation_has_digit_bearing_string(observation)
    return set(needed) <= set(covered)


def _observation_has_digit_bearing_string(observation: Mapping[str, Any]) -> bool:
    """True when scored evidence contains a non-annotation string with a digit.

    The walk is the same evidence the measured-leaf walker uses. A locator
    inside a condition is provenance. This is the backstop for a recognizer
    that returns no leaves: the coverage decision refuses that block. A
    ``reason`` in a non-value condition state is not evidence. A nested
    ``locator.note``, a ``tag: value`` reason, and a reason in a mapping
    with no state tag are evidence.
    """

    def contains(
        value: Any, prefix: tuple[Any, ...], skip_keys: frozenset[str]
    ) -> bool:
        if isinstance(value, str):
            return _string_has_ascii_digit(value)
        if isinstance(value, Mapping):
            return any(
                contains(child, prefix + (key,), skip_keys)
                for key, child in value.items()
                if key not in skip_keys
                and not _is_annotation_string(prefix, key, child, value)
            )
        if isinstance(value, (list, tuple)):
            return any(
                contains(child, prefix + (index,), skip_keys)
                for index, child in enumerate(value)
            )
        return False

    if "values" in observation and contains(
        observation.get("values"), ("values",), frozenset()
    ):
        return True
    for key in _SCORED_NUMERIC_CONDITIONS:
        if key not in observation or observation.get(key) is None:
            continue
        if contains(observation.get(key), (key,), _CONDITION_PROVENANCE_KEYS):
            return True
    return False


def _path_selector_names_observation_id(sample: Mapping[str, Any]) -> bool:
    """False when the observation bracket is a list position, not an id.

    ``resolve_field_path`` prefers a bare integer bracket as a list index.
    ``observations[0]`` follows whichever row currently sits there, so the pin
    does not identify an observation. Structured samples have no path bracket;
    ``_fidelity_sample_observation_key`` decides those.
    """
    path_keys, _structured = _sample_addressing_keys(sample)
    if len(path_keys) != 1:
        return True
    path = sample.get(path_keys[0])
    if not isinstance(path, str):
        return False
    parts = path.split(".")
    if len(parts) < 3:
        return False
    match = _PATH_SEGMENT_RE.match(parts[2])
    if not match or match.group(1) != "observations" or match.group(2) is None:
        return False
    return _INTEGER_SELECTOR_RE.fullmatch(match.group(2)) is None


def _sample_attestation_defects(sample: Mapping[str, Any]) -> list[str]:
    """Missing reviewer, ISO date, or exactly one artifact locator."""
    defects: list[str] = []
    reviewer = sample.get("reviewed_by")
    if not isinstance(reviewer, str) or not reviewer.strip():
        defects.append("reviewed_by is required for source attestation")
    reviewed_on = sample.get("reviewed_on")
    if not isinstance(reviewed_on, str) or not _ISO_DATE_RE.fullmatch(reviewed_on):
        defects.append("reviewed_on must be an ISO YYYY-MM-DD date")
    else:
        try:
            date.fromisoformat(reviewed_on)
        except ValueError:
            defects.append("reviewed_on is not a calendar date")
    locator_keys = [key for key in ("locator", "source_locator") if key in sample]
    locator = sample.get(locator_keys[0]) if len(locator_keys) == 1 else None
    malformed_locator_keys = _malformed_locator_artifact_keys(locator)
    if len(locator_keys) != 1 or (
        not _locator_ok(locator) and not malformed_locator_keys
    ):
        defects.append(
            "exactly one valid locator/source_locator is required for source attestation"
        )
    return defects


def _malformed_locator_artifact_keys(locator: Any) -> list[str]:
    """Return declared artifact keys that are not non-empty strings."""
    if not isinstance(locator, Mapping):
        return []
    return [
        key
        for key in (*_ARTIFACT_PATH_KEYS, "record")
        if key in locator
        and (not isinstance(locator[key], str) or not locator[key].strip())
    ]


def _sample_addresses_scored_evidence(sample: Mapping[str, Any]) -> bool:
    """True when the pin's address is ``values`` or a scored numeric condition.

    This is not coverage. The fourth path segment names the container.
    An equipment path may be a legal sample and still must not contribute.
    Structured samples resolve through ``values`` only; a condition needs
    a path pin.
    """
    path_keys, structured_keys = _sample_addressing_keys(sample)
    if path_keys:
        if len(path_keys) != 1:
            return False
        path = sample.get(path_keys[0])
        if not isinstance(path, str):
            return False
        parts = path.split(".")
        if len(parts) < 4:
            return False
        leaf = _PATH_SEGMENT_RE.match(parts[3])
        if not leaf:
            return False
        name = leaf.group(1)
        return name == "values" or name in _SCORED_NUMERIC_CONDITIONS
    return bool(structured_keys)


# Sentinel: a bracket that does not resolve. A resolved node may itself be None.
_ADDRESS_MISS = object()


def _classify_measured_leaves(
    value: Any,
    prefix: tuple[Any, ...] = (),
    *,
    skip_keys: frozenset[str] = frozenset(),
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Split measured leaves into coverable addresses and non-finite addresses.

    Invariant: every float is accounted for. A finite int or float (not a
    boolean) and a string that contains any digit [0-9] are coverable unless
    the string is an admitted annotation: a direct child of ``values``
    other than ``reason``, or a ``reason`` whose parent condition state
    has a non-value ``StateTag``, at any depth. ``tag: value`` and a
    mapping with no state tag stay leaves, at the observation root and
    nested under ``values``. A nested ``locator.note`` stays a leaf.
    Numbers under annotation keys remain leaves. A
    non-finite float (NaN, +Inf, -Inf) is the second list. It is never
    omitted and never coverable. A label with no digit is neither. The
    address is the path of mapping keys and list indexes from ``value``.
    A container is not a leaf. ``()`` is a measured scalar payload.
    Membership of a string does not consult the numeric grammar:
    ``0.91Pa`` is coverable because it contains digits. ``skip_keys``
    drops a provenance mapping (a condition ``locator``) and does not
    apply when walking ``values``.
    """
    if isinstance(value, bool):
        return [], []
    if isinstance(value, int):
        return [prefix], []
    if isinstance(value, float):
        if math.isfinite(value):
            return [prefix], []
        return [], [prefix]
    if isinstance(value, str):
        if _string_has_ascii_digit(value):
            return [prefix], []
        return [], []
    if isinstance(value, Mapping):
        coverable: list[tuple[Any, ...]] = []
        non_finite: list[tuple[Any, ...]] = []
        for key, child in value.items():
            if key in skip_keys or _is_annotation_string(
                prefix, key, child, value
            ):
                continue
            child_coverable, child_non_finite = _classify_measured_leaves(
                child, prefix + (key,), skip_keys=skip_keys
            )
            coverable.extend(child_coverable)
            non_finite.extend(child_non_finite)
        return coverable, non_finite
    if isinstance(value, (list, tuple)):
        coverable = []
        non_finite = []
        for index, child in enumerate(value):
            child_coverable, child_non_finite = _classify_measured_leaves(
                child, prefix + (index,), skip_keys=skip_keys
            )
            coverable.extend(child_coverable)
            non_finite.extend(child_non_finite)
        return coverable, non_finite
    return [], []


def _measured_leaf_addresses(
    value: Any,
    prefix: tuple[Any, ...] = (),
    *,
    skip_keys: frozenset[str] = frozenset(),
) -> list[tuple[Any, ...]]:
    """Coverable measured leaves under ``value``.

    This is the coverable half of ``_classify_measured_leaves``. A
    non-finite float is not returned here and is not dropped: the same
    walk records it, and certification refuses it.
    """
    coverable, _non_finite = _classify_measured_leaves(
        value, prefix, skip_keys=skip_keys
    )
    return coverable


def _step_bracket(cur: Any, bracket: str, address: list[Any]) -> Any:
    """Apply one bracket the way ``resolve_field_path`` does.

    A mapping bracket stays a string key. A list bracket is an integer index.
    ``_ADDRESS_MISS`` means the bracket does not resolve.
    """
    if isinstance(cur, Mapping):
        if bracket not in cur:
            return _ADDRESS_MISS
        address.append(bracket)
        return cur[bracket]
    if isinstance(cur, list) and _INTEGER_SELECTOR_RE.fullmatch(bracket):
        index = int(bracket)
        if 0 <= index < len(cur):
            address.append(index)
            return cur[index]
    return _ADDRESS_MISS


def _path_evidence_address(doc: Mapping[str, Any], path: Any) -> tuple[Any, ...] | None:
    """Evidence-tree address of a path pin.

    The first element is ``values`` or a scored condition key.
    ``(container,)`` is that whole container. None covers nothing.
    """
    if not isinstance(path, str):
        return None
    parts = path.split(".")
    if len(parts) < 4:
        return None
    container_segment = _PATH_SEGMENT_RE.match(parts[3])
    if container_segment is None:
        return None
    container = container_segment.group(1)
    if container != "values" and container not in _SCORED_NUMERIC_CONDITIONS:
        return None
    try:
        observation = resolve_field_path(doc, ".".join(parts[:3]))
    except KeyError:
        return None
    if not isinstance(observation, Mapping) or container not in observation:
        return None
    cur: Any = observation.get(container)
    address: list[Any] = [container]
    bracket = container_segment.group(2)
    if bracket is not None:
        cur = _step_bracket(cur, bracket, address)
        if cur is _ADDRESS_MISS:
            return None
    for raw in parts[4:]:
        segment = _PATH_SEGMENT_RE.match(raw)
        if segment is None:
            return None
        key = segment.group(1)
        if not isinstance(cur, Mapping) or key not in cur:
            return None
        cur = cur[key]
        address.append(key)
        bracket = segment.group(2)
        if bracket is not None:
            cur = _step_bracket(cur, bracket, address)
            if cur is _ADDRESS_MISS:
                return None
    return tuple(address)


def _structured_values_address(
    doc: Mapping[str, Any], sample: Mapping[str, Any]
) -> tuple[Any, ...] | None:
    """Values-relative address of a structured pin. ``()`` is the whole payload."""
    species = sample.get("species")
    if not isinstance(species, str):
        return None
    try:
        observation = _find_observation(
            doc,
            species=species,
            observation_id=(
                str(sample["observation_id"]) if sample.get("observation_id") else None
            ),
            observable=(
                str(sample["observable"]) if sample.get("observable") else None
            ),
        )
    except KeyError:
        return None
    if not isinstance(observation, Mapping) or "values" not in observation:
        return None
    cur: Any = observation.get("values")
    address: list[Any] = []
    if "index" in sample and sample.get("index") is not None:
        try:
            index = int(sample.get("index"))
        except (TypeError, ValueError):
            return None
        if isinstance(cur, list) and 0 <= index < len(cur):
            cur = cur[index]
            address.append(index)
        else:
            return None
    elif "T_K" in sample or "T" in sample:
        target = sample.get("T_K") if "T_K" in sample else sample.get("T")
        if not isinstance(cur, list):
            return None
        try:
            wanted = float(target)
        except (TypeError, ValueError):
            return None
        found = _series_temperature_index(cur, wanted)
        if found is None:
            return None
        cur = cur[found]
        address.append(found)
    field = sample.get("field") or sample.get("value_key")
    if field is not None:
        if not isinstance(cur, Mapping) or field not in cur:
            return None
        address.append(field)
    return tuple(address)


def _sample_evidence_address(
    doc: Mapping[str, Any], sample: Mapping[str, Any]
) -> tuple[Any, ...] | None:
    """Address of this pin in the scored evidence tree.

    ``("values",)`` pins the whole payload. ``("T_range_K",)`` pins that
    whole condition. None means the pin does not resolve to scored evidence,
    so it covers nothing. Structured samples address ``values`` only. The
    walk uses the same selectors as ``resolve_fidelity_sample``: path
    brackets, structured ``index``, ``T_K`` / ``T``, and one ``field`` key.
    """
    path_keys, _structured = _sample_addressing_keys(sample)
    if path_keys:
        return _path_evidence_address(doc, sample.get(path_keys[0]))
    inner = _structured_values_address(doc, sample)
    if inner is None:
        return None
    return ("values",) + inner


def _parse_line_range(raw: Any) -> tuple[int, int] | None:
    """1-based inclusive span. A bare line number is that one line."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return (raw, raw) if raw >= 1 else None
    if isinstance(raw, str):
        text = raw.strip()
        if re.fullmatch(r"\d+", text):
            number = int(text)
            return (number, number) if number >= 1 else None
        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", text)
        if not match:
            return None
        start, end = int(match.group(1)), int(match.group(2))
        if start >= 1 and end >= start:
            return start, end
        return None
    if (
        isinstance(raw, (list, tuple))
        and len(raw) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) for item in raw)
    ):
        start, end = int(raw[0]), int(raw[1])
        if start >= 1 and end >= start:
            return start, end
    return None


def _source_artifact_path_refusal(path: Path, root: Path) -> str | None:
    """Refuse resolved paths outside source roots, prefixes, or suffix rules."""
    if not path.is_relative_to(root):
        return "corpus_path_outside_root"
    relative = path.relative_to(root).as_posix()
    if not any(relative.startswith(prefix) for prefix in _SOURCE_ARTIFACT_PREFIXES):
        return "artifact_not_source"
    if path.suffix.lower() in _PROGRAM_SOURCE_SUFFIXES:
        return "artifact_not_source"
    if path.suffix.lower() in {".yaml", ".yml"} and not relative.startswith(
        "tables/"
    ):
        return "artifact_not_source"
    return None


def _corpus_top_prefix(relative: str) -> str:
    """First directory of a corpus-relative artifact path, or empty."""
    parts = Path(relative).parts
    if not parts or parts[0] in {".", ".."}:
        return ""
    return parts[0]


def _open_source_artifact(
    raw: str,
    *,
    repository_root: Path,
    corpus_root: Path | None,
    expected_sha256: Any,
) -> tuple[Path | None, bytes | None, str | None, str | None]:
    """Resolve and open one source artifact, returning path, bytes, hash, refusal.

    Corpus artifacts are relative to ``REGOLITH_CORPUS_ROOT`` and must match
    the digest recorded on the attesting locator. Existing files under any
    allowed prefix in ``artifact_root`` remain repository-local; the root is
    only a lookup location and never disables corpus resolution.
    """
    text = raw.strip()
    candidate = Path(text)
    if not text or "://" in text or _ABS_PATH_RE.match(text):
        return None, None, None, "corpus_path_outside_root"
    if candidate.is_absolute():
        return None, None, None, "corpus_path_outside_root"

    repository_root_resolved = repository_root.expanduser().resolve()
    repository_path = (repository_root_resolved / candidate).resolve()
    refusal = _source_artifact_path_refusal(
        repository_path, repository_root_resolved
    )
    if refusal:
        return None, None, None, refusal
    relative = repository_path.relative_to(repository_root_resolved).as_posix()

    is_corpus = False
    resolved = repository_path
    corpus_root_resolved: Path | None = None
    if not repository_path.is_file():
        if corpus_root is None:
            return None, None, None, "corpus_root_unset"
        corpus_root_resolved = corpus_root.expanduser().resolve()
        corpus_path = (corpus_root_resolved / relative).resolve()
        refusal = _source_artifact_path_refusal(corpus_path, corpus_root_resolved)
        if refusal:
            return None, None, None, refusal
        resolved = corpus_path
        is_corpus = True
    if not resolved.is_file():
        if is_corpus and corpus_root_resolved is not None:
            prefix = _corpus_top_prefix(relative)
            # A sparse mount has no directory for this prefix. The pin is
            # unverified here; a later mount of the directory verifies it.
            if prefix and not (corpus_root_resolved / prefix).is_dir():
                return None, None, None, "corpus_prefix_unmounted"
        return (
            None,
            None,
            None,
            "corpus_artifact_missing" if is_corpus else "artifact_missing",
        )
    try:
        blob = resolved.read_bytes()
    except OSError:
        return (
            None,
            None,
            None,
            "corpus_artifact_unreadable" if is_corpus else "artifact_unreadable",
        )
    digest = hashlib.sha256(blob).hexdigest()
    if is_corpus:
        if expected_sha256 is None:
            return resolved, blob, digest, "artifact_digest_missing"
        if not _valid_source_sha256(expected_sha256):
            return resolved, blob, digest, "artifact_digest_invalid"
        if digest != expected_sha256:
            return resolved, blob, digest, "artifact_digest_mismatch"
    return resolved, blob, digest, None


def _text_is_extract_document(text: str) -> bool:
    """True when the opened bytes are an extract or companion table.

    A copy renamed into ``text/`` still parses as the extract and would
    otherwise supply its own pins as source evidence.
    """
    head = text[:8192]
    if (
        SCHEMA_VERSION not in head
        and TABLE_SCHEMA_VERSION not in head
    ):
        return False
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    return isinstance(loaded, Mapping) and loaded.get("schema_version") in {
        SCHEMA_VERSION,
        TABLE_SCHEMA_VERSION,
    }


def _cited_text(
    full: str, locator: Mapping[str, Any]
) -> tuple[str | None, str | None]:
    """Prose span. A table does not use this; it is parsed whole first."""
    if "line_range" not in locator or locator.get("line_range") is None:
        return full, None
    span = _parse_line_range(locator.get("line_range"))
    if span is None:
        return None, "line_range is not a line number or start-end span"
    start, end = span
    lines = full.splitlines()
    if end > len(lines):
        return None, (
            f"line_range {start}-{end} is outside the artifact ({len(lines)} lines)"
        )
    return "\n".join(lines[start - 1 : end]), None


@dataclass(frozen=True)
class _PinnedLeaf:
    """One scalar the opened source must contain."""

    value: Any
    formula_label: bool


def _string_is_plain_number(text: str) -> bool:
    """True when the whole trimmed string is one finite number.

    A formula key that stores a number stays on the numeric comparison.
    ``SiO2`` is not a number. ``2`` and ``6.98`` are.
    """
    return _finite_decimal(_fold_numeric_signs(text.strip())) is not None


def _sample_root_is_formula_label(sample: Mapping[str, Any]) -> bool:
    """True when the addressed value itself is a formula or species label."""
    path_keys, _structured = _sample_addressing_keys(sample)
    if path_keys:
        raw = sample.get(path_keys[0])
        if not isinstance(raw, str):
            return False
        match = _PATH_SEGMENT_RE.match(raw.split(".")[-1])
        return bool(match) and match.group(1) in _FORMULA_LABEL_KEYS
    for key in ("field", "value_key"):
        name = sample.get(key)
        if isinstance(name, str) and name in _FORMULA_LABEL_KEYS:
            return True
    return False


def _pinned_leaves(value: Any, *, formula_label: bool) -> list[_PinnedLeaf]:
    """Scalar leaves. A mapping key named formula or species marks that child.

    The flag does not pass into a child mapping. A list keeps the flag of
    the value that contains it. A number, including a numeric string, is
    never a formula label.
    """
    if isinstance(value, str):
        if not value.strip():
            return []
        return [
            _PinnedLeaf(
                value,
                formula_label and not _string_is_plain_number(value),
            )
        ]
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return [_PinnedLeaf(value, False)]
    if isinstance(value, Mapping):
        leaves: list[_PinnedLeaf] = []
        for key, child in value.items():
            child_label = isinstance(key, str) and key in _FORMULA_LABEL_KEYS
            leaves.extend(_pinned_leaves(child, formula_label=child_label))
        return leaves
    if isinstance(value, (list, tuple)):
        leaves = []
        for child in value:
            leaves.extend(_pinned_leaves(child, formula_label=formula_label))
        return leaves
    return []


def _numeric_spellings(leaf: int | float) -> set[str]:
    """Spellings whose complete token may certify this number.

    Only a spelling that preserves the stored number is kept. Rounding
    ``0.24000000000000002`` to ``0.24`` is not that number. ``str(leaf)``
    is the shortest round trip. ``format(leaf, ".16g")`` is kept only when
    it parses back to the same float, so an exact alternate of ``1e-20``
    can remain and a rounded neighbour cannot. ``-0.0`` and ``0.0`` compare
    equal, so the sign is checked separately. A float spelling still needs
    a decimal point or an exponent: ``format(1.0, ".16g")`` is ``1`` and
    does not certify 1.0.
    """
    if isinstance(leaf, int):
        return {str(leaf)}
    if not math.isfinite(leaf):
        return set()
    exact: set[str] = set()
    for spelling in (str(leaf), format(leaf, ".16g")):
        if "." not in spelling and "e" not in spelling.lower():
            continue
        try:
            parsed = float(spelling)
        except ValueError:
            continue
        if parsed != leaf:
            continue
        if math.copysign(1.0, parsed) != math.copysign(1.0, leaf):
            continue
        exact.add(spelling)
    return exact


def _spelling_is_complete_token(spelling: str, text: str) -> bool:
    """True when ``spelling`` is one whole numeric token in ``text``.

    The span has to pass ``_span_is_complete_printed_value`` and equal a
    citation token. ``1`` is not a token of ``1,000``. A canonical
    spelling has no digit-group separator, so this path does not join
    groups. The text join lives with the token scan.
    """
    folded = _fold_numeric_signs(spelling)
    if not _printed_value_carried(folded, text):
        return False
    return any(match.group(0) == folded for match in _citation_token_matches(text))


def _string_leaf_in_text(leaf: str, text: str) -> bool:
    """Substring match that does not slice a numeric token.

    A label with no digit stays a substring. A digit-bearing leaf matches
    only when the whole stored value, number plus any attached or spaced
    unit, passes ``_span_is_complete_printed_value``. ``1 K`` is not
    ``1 KPa``, ``1,000`` is not ``1,000,000``, and ``0.91Pa`` is not
    ``10.91Pa``. A leaf the parser reads also needs each of those numbers
    to be the same complete token, so ``0.24`` is not ``-0.24`` or
    ``0.24e-5``. A leading decimal is a number, so ``.24`` is not ``-.24``
    or ``10.24``. Unicode signs are folded before either string is
    searched, so ``−0.24`` is ``-0.24``. A digitless leaf does not join
    digit groups. A digit-bearing leaf does, so its token spans stay
    aligned with the citation scan.
    """
    leaf = _fold_numeric_signs(leaf)
    text = _fold_numeric_signs(text)
    if not _string_has_ascii_digit(leaf):
        return leaf in text
    leaf = _fold_digit_groups(leaf)
    text = _fold_digit_groups(text)
    if not _printed_value_carried(leaf, text):
        return False
    if not _parser_reads_every_digit(leaf):
        return True
    leaf_tokens = _citation_token_matches(leaf)
    if not leaf_tokens:
        return True
    placed = {
        (match.start(), match.end()): match.group(0)
        for match in _citation_token_matches(text)
    }
    start = 0
    while True:
        index = text.find(leaf, start)
        if index < 0:
            return False
        if not _span_is_complete_printed_value(text, index, index + len(leaf)):
            start = index + 1
            continue
        if all(
            placed.get((index + token.start(), index + token.end()))
            == token.group(0)
            for token in leaf_tokens
        ):
            return True
        start = index + 1


def _leaf_in_text(leaf: Any, text: str) -> bool:
    """True when the opened artifact contains this pinned scalar.

    Numeric leaves match a complete token only. Sign and exponent belong to
    the token, so 0.24 does not match -0.24, 0.24e-5, or 10.24, and 1.0 does
    not match 1. A leading decimal is a token, so .24 does not match -.24.
    The whole stored value has to pass ``_span_is_complete_printed_value``,
    so 1 does not match 1,000, 1 K does not match 1 KPa, and 1,000 does not
    match 1,000,000. A float matches only a spelling that parses back to
    that stored number, so 0.24000000000000002 does not match 0.24. A
    Unicode minus is a minus, so −0.24 does not match 0.24 and does match
    -0.24. This is a cited-text grep, not a unit conversion.
    """
    if isinstance(leaf, bool):
        token = "true" if leaf else "false"
        return (
            re.search(rf"(?i)(?<![A-Za-z0-9_]){token}(?![A-Za-z0-9_])", text)
            is not None
        )
    if isinstance(leaf, int):
        return _spelling_is_complete_token(str(leaf), text)
    if isinstance(leaf, float):
        if not math.isfinite(leaf):
            return False
        return any(
            _spelling_is_complete_token(spelling, text)
            for spelling in _numeric_spellings(leaf)
        )
    if isinstance(leaf, str):
        return _string_leaf_in_text(leaf, text)
    return False


def _normalize_formula_label(text: str) -> str:
    """Fold formula markup inside each whitespace token.

    Invariant: folding does not change how many whitespace-separated
    tokens ``text`` has, except to join a space between an element
    symbol or a closing parenthesis and its own count. ``$...$``,
    ``\\mathrm``, ``\\text``, ``\\rm``, braces, and ``_{n}`` are removed
    inside the token. A space inside ``$...$`` is a separator.
    A caret is a superscript: a token that contains ``^`` is left as it
    is, and no plain formula or plain number certifies from that token.
    ``printed_formula_tokens`` applies ``printed_formula`` to each other
    token and does not cross a space. Unicode subscript digits then become
    plain digits. ``SiO 2`` becomes ``SiO2`` and ``(OH) 2`` becomes
    ``(OH)2``. ``$H_{2} O_{2}$`` stays ``H2`` and ``O2``.
    ``$SiO_{2} 100$`` stays ``SiO2`` and ``100``. ``Fe 2 O 3`` becomes
    ``Fe2 O3``. ``H2 O2`` and ``SiO2 Al2O3`` stay two tokens.
    ``6.98 Fe_{2}O_{3}`` stays ``6.98 Fe2O3``. ``page 2`` stays ``page 2``.
    ``${\\mathrm{{Al}}}_{2}{\\mathrm{O}}_{3}$`` becomes ``Al2O3``.
    """
    normalized = printed_formula_tokens(text)
    normalized = fold_subscript_digits(normalized)
    previous = None
    while previous != normalized:
        previous = normalized
        normalized = _FORMULA_ELEMENT_COUNT_SPACE_RE.sub(r"\1", normalized)
        normalized = _FORMULA_PAREN_COUNT_SPACE_RE.sub(r"\1", normalized)
    return normalized


def _strip_wrapping_math(text: str) -> str:
    """Remove one ``$...$`` pair that wraps the whole trimmed cell."""
    stripped = text.strip()
    if len(stripped) >= 2 and stripped.startswith("$") and stripped.endswith("$"):
        return stripped[1:-1].strip()
    return stripped


def _formula_cell_matches(leaf: str, cell: str) -> bool:
    """True when the whole cell is this formula label after markup is folded.

    A numeric string does not match here. A longer cell that merely
    contains the label does not match.
    """
    if _string_is_plain_number(leaf):
        return False
    left = _normalize_formula_label(_strip_wrapping_math(cell))
    right = _normalize_formula_label(_strip_wrapping_math(leaf))
    return bool(right) and left == right


# A formula run is element symbols, digits, parentheses, square brackets,
# and the charge marks · U+00B7, +, and -. Whitespace or any other
# character ends the run. Parentheses do not.
_FORMULA_RUN_MARKS = "()[]+-\u00b7"


def _formula_run_char(ch: str) -> bool:
    """True when ``ch`` stays inside one formula run."""
    return (ch.isascii() and ch.isalnum()) or ch in _FORMULA_RUN_MARKS


# Superscript digits and signs. A run followed by one of these, by ``^``,
# or by ``<sup>`` is a charged or powered species, not the bare formula.
_UNICODE_SUPERSCRIPTS = (
    "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079\u207a\u207b"
)
_UNICODE_SUPERSCRIPT_FOLD = str.maketrans(
    _UNICODE_SUPERSCRIPTS, "0123456789+-"
)


def _superscript_suffix_at(text: str, index: int) -> tuple[str, int] | None:
    """Canonical ``^{body}`` and the index after it, or None.

    The marker is ``^``, a ``<sup>`` element, or a Unicode superscript.
    ``^{2+}``, ``<sup>2+</sup>``, and ``²⁺`` share one body. The bare run
    in front of the marker is not the label.
    """
    if index >= len(text):
        return None
    if text[index] == "^":
        if index + 1 < len(text) and text[index + 1] == "{":
            close = text.find("}", index + 2)
            if close < 0:
                return "^{", index + 2
            body = text[index + 2 : close].translate(_UNICODE_SUPERSCRIPT_FOLD)
            return "^{" + body + "}", close + 1
        return "^{", index + 1
    if text[index] == "<" and text[index : index + 5].lower() == "<sup>":
        tail = text[index:]
        close = tail.lower().find("</sup>")
        if close < 0:
            return "^{", index + 5
        body = tail[5:close].translate(_UNICODE_SUPERSCRIPT_FOLD)
        return "^{" + body + "}", index + close + 6
    if text[index] in _UNICODE_SUPERSCRIPTS:
        end = index + 1
        while end < len(text) and text[end] in _UNICODE_SUPERSCRIPTS:
            end += 1
        body = text[index:end].translate(_UNICODE_SUPERSCRIPT_FOLD)
        return "^{" + body + "}", end
    return None


def _canonical_formula_unit(text: str) -> str:
    """One run plus its superscript suffix, or ``text`` when it is not one.

    ``Fe^{2+}``, ``Fe<sup>2+</sup>``, and ``Fe²⁺`` are ``Fe^{2+}``. A
    compound with no superscript is unchanged.
    """
    if not text or not _formula_run_char(text[0]):
        return text
    end = 1
    limit = len(text)
    while end < limit and _formula_run_char(text[end]):
        end += 1
    suffix = _superscript_suffix_at(text, end)
    if suffix is None:
        return text
    canonical, suffix_end = suffix
    if suffix_end != limit:
        return text
    return text[:end] + canonical


def _formula_label_in_text(leaf: str, text: str) -> bool:
    """True when the folded label equals one whole formula run.

    The run is a maximal span of element symbols, digits, parentheses,
    square brackets, and the charge marks ·, +, and -. Whitespace, either
    end of the text, or any other character delimits it. The stored label
    matches only that whole run. ``SO4`` is not ``Fe2(SO4)3``. ``SiO2``
    is not ``CaO·SiO2``. ``CO`` is not ``CO2``. A run immediately followed
    by ``^``, ``<sup>``, or a Unicode superscript is a charged or powered
    species. It certifies only a label that includes that same charge or
    power, so ``Fe`` is not ``Fe^{2+}``, ``SiO2`` is not ``SiO2^{2+}``,
    and ``O2`` is not ``O^{2-}``. A numeric string does not match here.
    This path does not fold digit groups, so ``SiO2 100`` is ``SiO2``
    and is not ``SiO2100``.
    """
    if _string_is_plain_number(leaf):
        return False
    normalized = _normalize_formula_label(leaf)
    if not normalized.strip():
        return False
    label_unit = _canonical_formula_unit(normalized)
    folded = _normalize_formula_label(text)
    start = 0
    limit = len(folded)
    while start < limit:
        if not _formula_run_char(folded[start]):
            start += 1
            continue
        end = start + 1
        while end < limit and _formula_run_char(folded[end]):
            end += 1
        suffix = _superscript_suffix_at(folded, end)
        if suffix is not None:
            canonical, suffix_end = suffix
            if folded[start:end] + canonical == label_unit:
                return True
            start = suffix_end
            continue
        if folded[start:end] == label_unit:
            return True
        start = end
    return False


def _pinned_leaf_in_text(leaf: _PinnedLeaf, text: str) -> bool:
    """Formula labels compare only after the formula fold.

    A raw substring would accept CO inside CO₂. Other leaves keep the
    ordinary token match.
    """
    if leaf.formula_label and isinstance(leaf.value, str):
        return _formula_label_in_text(leaf.value, text)
    return _leaf_in_text(leaf.value, text)


def _pinned_leaf_in_cells(leaf: _PinnedLeaf, cells: Sequence[str]) -> bool:
    """Whole-cell match, then a formula fold only for a formula label."""
    if any(_cell_certifies_leaf(leaf.value, cell) for cell in cells):
        return True
    if leaf.formula_label and isinstance(leaf.value, str):
        return any(_formula_cell_matches(leaf.value, cell) for cell in cells)
    return False


def _certification_scalar_leaves(
    sample: Mapping[str, Any],
    doc: Mapping[str, Any] | None,
) -> tuple[list[_PinnedLeaf], str | None]:
    """Scalar leaves the opened source must contain.

    The sample spelling and the resolved extract value are both required.
    ``rel_tol`` can make those numbers differ. Dropping the resolved leaf
    would certify a stored number the source was never searched for.
    ``formula`` and ``species`` strings carry the formula-label mark.
    """
    try:
        expected = _sample_expected_value(sample)
    except KeyError as exc:
        return [], f"locator was not opened: {exc}"
    formula_label = _sample_root_is_formula_label(sample)
    leaves = _pinned_leaves(expected, formula_label=formula_label)
    if doc is not None:
        try:
            resolved = resolve_fidelity_sample(doc, sample)
        except KeyError:
            resolved = None
        if resolved is not None:
            leaves.extend(_pinned_leaves(resolved, formula_label=formula_label))
    if not leaves:
        return [], "locator was not opened: pinned value has no scalar leaf to find"
    return leaves, None


def _locator_source_blind_reason(
    sample: Mapping[str, Any],
    *,
    artifact_root: Path,
    corpus_root: Path | None,
    doc: Mapping[str, Any] | None = None,
    corpus_mount: CorpusMountTally | None = None,
) -> _LocatorSourceBlindReason | None:
    """Return a source-blind reason when the locator evidence is inadequate.

    No source-blind reason means a candidate was opened and contained the
    certified leaves: the sample value and resolved extract value. A tolerant
    sample match does not stand in for the stored number. Separate declared
    keys must each match; a record's filename alternate is considered only
    when the earlier candidate could not be opened. One locator digest can
    attest at most one corpus artifact.
    """
    keys = [key for key in ("locator", "source_locator") if key in sample]
    if len(keys) != 1 or not isinstance(sample.get(keys[0]), Mapping):
        return _LocatorSourceBlindReason(
            "locator was not opened: attestation locator is missing"
        )
    locator = sample[keys[0]]
    malformed_keys = _malformed_locator_artifact_keys(locator)
    if malformed_keys:
        return _LocatorSourceBlindReason(
            "locator source-blind: locator_key_malformed "
            f"({', '.join(malformed_keys)} must be non-empty strings)",
            frozenset({"locator_key_malformed"}),
        )
    candidate_groups = [
        (key, [raw.strip()])
        for key in _ARTIFACT_PATH_KEYS
        if isinstance((raw := locator.get(key)), str) and raw.strip()
    ]
    record = locator.get("record")
    if isinstance(record, str) and record.strip():
        text = record.strip()
        token = text.split()[0]
        candidates = [text] if token == text else [text, token]
        candidate_groups.append(("record", candidates))
    if not candidate_groups:
        return _LocatorSourceBlindReason(
            "locator was not opened: no source_path, quotes_path, or file record "
            "points at a text artifact"
        )
    leaves, leaf_error = _certification_scalar_leaves(sample, doc)
    if leaf_error:
        return _LocatorSourceBlindReason(leaf_error)
    reasons: list[str] = []
    reason_codes: set[str] = set()
    unmounted_prefixes: list[str] = []

    def note_corpus_mount(opened_corpus: bool, codes: frozenset[str]) -> None:
        if corpus_mount is None:
            return
        if opened_corpus and not codes:
            corpus_mount.verified_opens += 1
            return
        if not _mount_absence_only(codes):
            return
        corpus_mount.unverified_pins += 1
        for prefix in sorted(set(unmounted_prefixes)):
            corpus_mount.unverified_by_prefix[prefix] = (
                corpus_mount.unverified_by_prefix.get(prefix, 0) + 1
            )
    resolved_corpus_root = (
        corpus_root.expanduser().resolve() if corpus_root is not None else None
    )
    corpus_artifact_keys = 0
    for _key, candidates in candidate_groups:
        candidate_reasons: list[str] = []
        candidate_reason_codes: set[str] = set()
        key_matched = False
        resolved_to_corpus = False
        for raw in candidates:
            path, blob, _digest, open_refusal = _open_source_artifact(
                raw,
                repository_root=artifact_root,
                corpus_root=corpus_root,
                expected_sha256=locator.get("source_sha256"),
            )
            if path is not None and blob is not None:
                # The first opened record candidate decides this key. Discard
                # refusals from candidates that did not open.
                candidate_reasons.clear()
                candidate_reason_codes.clear()
            if (
                path is not None
                and blob is not None
                and resolved_corpus_root is not None
                and path.is_relative_to(resolved_corpus_root)
            ):
                resolved_to_corpus = True
            if open_refusal:
                if open_refusal == "corpus_prefix_unmounted":
                    prefix = _corpus_top_prefix(raw)
                    if prefix:
                        unmounted_prefixes.append(prefix)
                candidate_reasons.append(
                    f"locator was not opened: {open_refusal} ({raw})"
                )
                candidate_reason_codes.add(open_refusal)
                if path is not None and blob is not None:
                    break
                continue
            if path is None or blob is None:
                candidate_reasons.append(
                    f"locator was not opened: artifact_not_opened ({raw})"
                )
                candidate_reason_codes.add("artifact_not_opened")
                continue
            if path.suffix.lower() == ".pdf" or blob.startswith(b"%PDF"):
                candidate_reasons.append(
                    f"{path.name} is a PDF; certification opens a text or OCR artifact"
                )
                candidate_reason_codes.add("artifact_is_pdf")
                break
            try:
                text = blob.decode("utf-8")
            except UnicodeError:
                candidate_reasons.append(f"{path.name} is not UTF-8 text")
                candidate_reason_codes.add("artifact_not_utf8")
                break
            if _text_is_extract_document(text):
                candidate_reasons.append(
                    "locator was not opened: "
                    f"{path.name} is an extract document, not an independent source"
                )
                candidate_reason_codes.add("artifact_is_extract_document")
                break
            delimiter = _tabular_delimiter(path)
            if delimiter is None and _locator_has_html_cell_address(locator):
                # One parsed cell. A miss is source-blind, not a prose search
                # of the same line.
                cell, superscript_spans, span_error = _cited_html_cell(
                    text, locator
                )
                if cell is None:
                    candidate_reasons.append(
                        span_error or "cited span was not opened"
                    )
                    candidate_reason_codes.add(
                        _html_blind_code(span_error or "cited span was not opened")
                    )
                    break
                if superscript_spans:
                    missing = [
                        leaf
                        for leaf in leaves
                        if not _superscript_leaf_certifies(
                            leaf.value, cell, superscript_spans
                        )
                    ]
                    if missing:
                        rendered = ", ".join(
                            repr(leaf.value) for leaf in missing[:3]
                        )
                        candidate_reasons.append(
                            "superscript_in_cell: a superscript cell certifies "
                            f"only a complete explicit power ({rendered})"
                        )
                        candidate_reason_codes.add("superscript_in_cell")
                        break
                    key_matched = True
                    break
                missing = [
                    leaf
                    for leaf in leaves
                    if not _pinned_leaf_in_cells(leaf, (cell,))
                ]
            elif delimiter is None:
                cited, span_error = _cited_text(text, locator)
                if cited is None:
                    candidate_reasons.append(
                        span_error or "cited span was not opened"
                    )
                    candidate_reason_codes.add("cited_span_missing")
                    break
                if _cited_span_includes_html_table(text, locator):
                    # Table lines are not prose. A value that survives only
                    # inside the table needs a cell address. A value on a
                    # prose line in the same span still certifies.
                    prose = _cited_prose_outside_html_tables(text, locator)
                    missing = [
                        leaf
                        for leaf in leaves
                        if not _pinned_leaf_in_text(leaf, prose)
                    ]
                    if missing:
                        candidate_reasons.append(
                            "html_table_requires_cell_address: cited span "
                            "includes an HTML table and has no table_row "
                            "or table_column"
                        )
                        candidate_reason_codes.add(
                            "html_table_requires_cell_address"
                        )
                        break
                    key_matched = True
                    break
                missing = [
                    leaf
                    for leaf in leaves
                    if not _pinned_leaf_in_text(leaf, cited)
                ]
            else:
                # Parse the complete table, then select. A sliced line is not a table.
                try:
                    cells, span_error = _cited_table_cells(text, locator, delimiter)
                except csv.Error as exc:
                    candidate_reasons.append(
                        f"opened {path.name} but the table could not be parsed ({exc})"
                    )
                    candidate_reason_codes.add("cited_table_unparsed")
                    break
                if cells is None:
                    candidate_reasons.append(
                        span_error or "cited span was not opened"
                    )
                    candidate_reason_codes.add("cited_span_missing")
                    break
                missing = [
                    leaf
                    for leaf in leaves
                    if not _pinned_leaf_in_cells(leaf, cells)
                ]
            if not missing:
                key_matched = True
                break
            rendered = ", ".join(repr(leaf.value) for leaf in missing[:3])
            candidate_reasons.append(
                f"opened {path.name} but the resolved extract value is not in the "
                f"cited text ({rendered})"
            )
            candidate_reason_codes.add("cited_token_absent")
            break
        if resolved_to_corpus:
            corpus_artifact_keys += 1
            if corpus_artifact_keys > 1:
                note_corpus_mount(False, frozenset({"multiple_corpus_artifacts"}))
                return _LocatorSourceBlindReason(
                    "locator source-blind: multiple_corpus_artifacts",
                    frozenset({"multiple_corpus_artifacts"}),
                )
        if not key_matched:
            reasons.extend(candidate_reasons)
            reason_codes.update(candidate_reason_codes)
    if reasons:
        codes = frozenset(reason_codes)
        note_corpus_mount(False, codes)
        return _LocatorSourceBlindReason(
            "locator source-blind: " + "; ".join(reasons),
            codes,
        )
    note_corpus_mount(corpus_artifact_keys > 0, frozenset())
    return None


def fidelity_source_warnings(
    doc: Mapping[str, Any],
    *,
    label: str = "<document>",
    artifact_root: Path | None = None,
    corpus_mount: CorpusMountTally | None = None,
) -> list[FidelityWarning]:
    """Count source-fidelity holes. Empty means the certification invariant holds.

    Invariant: a scored measured leaf is certified only when its observation
    id is one non-empty string naming exactly one scored row, and the opened
    source contains that resolved extract leaf as a complete token of the
    same sign and the same digits. The whole value, including a spaced or
    attached unit, must match, and neither neighbour may extend the span:
    an ASCII letter or digit, a Unicode letter, µ, or °, or a dot or comma
    followed by a digit. A sentence period does not extend the token, so
    ``1`` matches ``the value is 1.``. ``1 K`` is not ``1 KPa`` and ``1``
    is not ``1,000``. A CSV or TSV source is parsed completely before the
    locator is applied. A cell certifies a stored value only when the whole
    trimmed, unquoted cell parses to that number, or equals the stored
    string. ``0.9100`` is ``0.91``. The numbers are ``Decimal`` values,
    so ``1e-999`` is not ``0`` and ``9007199254740993`` is not
    ``9007199254740992``. A trailing dot is the number, so ``91.`` is
    ``91``. The prose numeric grammar is not a gate on a cell. NaN,
    Infinity, and InvalidOperation do not certify. A substring does not
    certify, so a cell ``1/2`` is not ``1`` and a cell ``1,000`` is not
    ``1``. A
    ``line_range`` selects records that lie entirely inside it. A span that
    cuts through a quoted cell is source-blind and is not re-parsed from
    the sliced lines. A MinerU ``<table>`` on a text artifact is parsed
    whole when the locator carries ``table_row`` and ``table_column``
    (the same indexes as ``table_row_number`` and ``table_column_number``).
    The stored value must be that whole trimmed cell after rowspan and
    colspan expansion. A cited span that includes a table line and has
    neither address refuses with ``html_table_requires_cell_address``.
    A value on a prose line in that span still certifies. A value in
    another row is source-blind. Formula
    and species labels fold markup inside each whitespace token. A space
    inside ``$...$`` stays a separator, so ``$H_{2} O_{2}$`` is not
    ``H2O2``. A token that contains ``^`` holds a superscript and does
    not certify as a plain formula or a plain number. A run immediately
    followed by ``^``, ``<sup>``, or a Unicode superscript certifies
    only a label with that same charge.
    A subscript digit inside a formula token is not a number, so
    ``SiO_{2}`` and ``SiO₂`` do not certify 2. A neighbouring
    measurement still does. A formula label equals one whole run of
    element symbols, digits, parentheses, square brackets, and the
    charge marks ·, +, and -. ``SO4`` is not ``Fe2(SO4)3``. ``SiO2``
    is not ``CaO·SiO2``.
    ``printed_formula`` unwraps one token, including a
    ``\\mathrm`` / ``\\text`` / ``\\rm`` command, so
    ``${\\mathrm{{Al}}}_{2}{\\mathrm{O}}_{3}$`` is ``Al2O3``. A layout
    space is removed only between an element
    or a closing parenthesis and the count that follows it, before every
    comparison. ``SiO 2`` is ``SiO2``. A space after a count stays, so
    ``Fe 2 O 3`` is ``Fe2 O3`` and does not certify ``Fe2O3``. A digitless
    label is a complete token, a preceding number is not joined, and two
    formulas stay two tokens. A superscript cell certifies only when it
    is a complete power and nothing else. ``10<sup>3</sup>`` certifies
    ``1e3``. ``1.5×10<sup>3</sup>`` certifies 1500 / ``1.5e3``.
    A leading minus applies to the whole power, so ``-10<sup>2</sup>``
    is ``-100``.
    ``10<sup>3</sup>2`` and a plain number are ``superscript_in_cell``.
    An unsigned digit run joins on a thin space, narrow no-break space,
    or no-break space. An ASCII space does not. The first group is one
    to three digits, then groups of three, and the run starts at the
    beginning of the text or after whitespace or punctuation, never after
    a letter. ``1 650`` and ``1 650`` are ``1650``. ``1 100`` is 1 and
    100, not ``1100``. ``SiO2 100`` is not ``SiO2100``. ``1e0 001``,
    ``12 34``, ``1.5 000``, and ``1 550e3`` are not one number. A short
    group leaves the separated run unjoined, so ``12\\u200934\\u2009567``
    is not ``34567`` and ``1\\u2009650\\u20091`` is not ``1650``. The
    formula-label path does not apply this fold. A comma is not that
    separator. ``1 K`` is not ``1 KPa``.
    A leading decimal is a numeric token.
    A spelling that rounds the stored float to a different number is not
    that token. The sample's own spelling is not a substitute: a ``rel_tol``
    match of a different number does not certify the stored leaf. A scored
    row with no usable observation id is a typed refusal, never omitted.
    A Unicode minus is a minus. Duplicate observation ids are a typed
    refusal, never one coverage entry.

    Every measured leaf of a scored observation's evidence is covered only
    by a matching sample that names that observation and whose address is
    that leaf or an ancestor of it. Evidence is ``values`` plus each
    present scored numeric condition (``_SCORED_NUMERIC_CONDITIONS``): the
    fields the scored temperature payload copies beside ``values``. A
    locator inside a condition is provenance, not a leaf. Digit-bearing
    strings are extractor-authored annotation, and not evidence, only as a
    direct child of ``values`` other than ``reason``, or as a ``reason``
    whose parent condition state has a non-value ``StateTag``, at any
    depth. A ``reason`` beside ``tag: value``, or in a mapping with no
    state tag, stays evidence at the observation root and nested under
    ``values``. A nested ``locator.note`` stays evidence. Numbers under
    annotation keys remain leaves. Repeating a pin
    of one address does not cover a sibling, even when the numbers are
    equal, and a ``values`` pin does not cover a sibling condition. A
    measured leaf is a finite int or float (not a boolean), or a string
    that contains any digit [0-9] outside ``ANNOTATION_KEYS``. A non-finite
    float (NaN, +Inf, -Inf)
    is ``non_finite_evidence``. It is never omitted and never contributes
    "no required address": a pin of a finite sibling does not certify
    the observation, and a pin of the non-finite value does not either.
    A descriptive label with no digit is
    not a leaf and does not cover a sibling measurement. A string the
    numeric parser cannot read is uncovered unless the opened source
    carries that printed value; annotation strings are the only skipped
    strings. Several samples may
    each pin part of the evidence. Equipment pins and a numeric
    observation index do not contribute. Evidence with no measured leaf
    is covered by any matching evidence pin only when that evidence also
    contains no non-annotation digit-bearing string. Zero recognised leaves
    in a block with such a string is a refusal. A pilot pin or an
    unattested sample can still supply addresses toward
    ``uncovered_observation``; attestation is its own count. Certification
    refuses while any of these counts is non-zero: a pilot pin or an
    unattested sample anywhere in the file, an attestation-complete pin
    whose locator was not opened, or an observation id that names more
    than one scored row. An opened locator must be a paper, OCR, quotes,
    or reference transcription and must contain each pinned number as a
    complete token (sign and exponent included; Unicode signs folded onto
    that token). The extract document and program source are not artifacts.
    Drift stays a match error. ``source_blind_pin`` is that unopened or
    absent-value count, including a resolved extract leaf the cited text
    does not contain. A scored row with no usable observation id is
    ``missing_observation_id`` and is not dropped from this count. These
    warnings do not reject a draft corpus.
    """
    samples = doc.get("fidelity_samples")
    if samples is None:
        samples = []
    elif not isinstance(samples, list):
        samples = []

    observation_map, ambiguous_ids, missing_ids = _scored_observations(doc)
    required = set(observation_map)
    # Leaf addresses each observation still needs. This is the only coverage
    # write: a pin adds an address only when its own address is that leaf or
    # an ancestor. Value equality does not transfer coverage to a sibling,
    # and a values pin does not transfer coverage to a condition.
    evidence = {
        key: _scored_evidence_leaves(observation)
        for key, observation in observation_map.items()
    }
    needed_addresses = {key: evidence[key][0] for key in evidence}
    non_finite_addresses = {key: evidence[key][1] for key in evidence}
    covered_addresses: dict[tuple[str, str], set[tuple[Any, ...]]] = {
        key: set() for key in required
    }
    addressed: set[tuple[str, str]] = set()
    unattested = 0
    unattested_reasons: list[str] = []
    unattested_extra = [0]
    pilot = 0
    pilot_reasons: list[str] = []
    pilot_extra = [0]
    source_blind = 0
    source_blind_reasons: list[str] = []
    source_blind_extra = [0]
    source_blind_reason_codes: list[frozenset[str]] = []
    root = artifact_root if artifact_root is not None else ROOT
    configured_corpus_root = os.environ.get("REGOLITH_CORPUS_ROOT", "").strip()
    corpus_root = (
        Path(configured_corpus_root).expanduser() if configured_corpus_root else None
    )
    for index, sample in enumerate(samples):
        sample_label = f"{label}:fidelity_samples[{index}]"
        if not isinstance(sample, Mapping):
            unattested += 1
            _note_reason(
                unattested_reasons,
                unattested_extra,
                f"{sample_label}: each sample must be a mapping",
            )
            continue
        if sample.get("note") == _PILOT_MIGRATION_NOTE:
            pilot += 1
            _note_reason(
                pilot_reasons,
                pilot_extra,
                f"{sample_label}: auto-migrated pilot sample cannot be "
                "source-attested; replace it with a source-reviewed sample",
            )
        defects = _sample_attestation_defects(sample)
        if defects:
            unattested += 1
            for defect in defects:
                _note_reason(
                    unattested_reasons,
                    unattested_extra,
                    f"{sample_label}: {defect}",
                )
        else:
            # Attestation-complete pins are the ones that would otherwise certify
            # without opening the source. Unattested pins stay on that count.
            blind = _locator_source_blind_reason(
                sample,
                artifact_root=root,
                corpus_root=corpus_root,
                doc=doc,
                corpus_mount=corpus_mount,
            )
            if blind:
                source_blind += 1
                source_blind_reason_codes.append(blind.reason_codes)
                _note_reason(
                    source_blind_reasons,
                    source_blind_extra,
                    f"{sample_label}: {blind.detail}",
                )
        match_errors = check_fidelity_sample_matches(doc, sample, label=sample_label)
        observation_key = _fidelity_sample_observation_key(doc, sample)
        if (
            not match_errors
            and observation_key is not None
            and observation_key in required
            and _path_selector_names_observation_id(sample)
            and _sample_addresses_scored_evidence(sample)
        ):
            addressed.add(observation_key)
            pin_address = _sample_evidence_address(doc, sample)
            if pin_address is None:
                continue
            for leaf in needed_addresses[observation_key]:
                if leaf[: len(pin_address)] == pin_address:
                    covered_addresses[observation_key].add(leaf)

    # Invariant: an observation is covered only when every digit-bearing
    # leaf of its evidence has a pin of that address or an ancestor. A
    # repeated pin of one address does not cover an equal sibling, and a
    # values pin does not cover a scored condition. Zero recognised
    # measured leaves in a block that still contains a digit is a refusal,
    # not a pass.
    covered: set[tuple[str, str]] = set()
    for key in observation_map:
        # A non-finite quantity is not covered by any pin, including a pin
        # of that value. The empty-leaf pass must not see it as "no address".
        if non_finite_addresses[key]:
            continue
        needed = needed_addresses[key]
        if not needed:
            if key in addressed and not _observation_has_digit_bearing_string(
                observation_map[key]
            ):
                covered.add(key)
            continue
        if set(needed) <= covered_addresses[key]:
            covered.add(key)

    uncovered_reasons: list[str] = []
    uncovered_extra = [0]
    uncovered_keys: list[tuple[str, str]] = []
    for species_id, observation_id in sorted(required - covered):
        key = (species_id, observation_id)
        if non_finite_addresses[key] and _coverable_leaves_are_pinned(
            needed_addresses[key],
            covered_addresses[key],
            observation_map[key],
        ):
            continue
        uncovered_keys.append(key)
    for species_id, observation_id in uncovered_keys:
        _note_reason(
            uncovered_reasons,
            uncovered_extra,
            f"{label}: fidelity sample coverage missing for observation "
            f"species.{species_id}.observations[{observation_id}]",
        )
    uncovered = len(uncovered_keys)

    warnings: list[FidelityWarning] = []
    if uncovered:
        warnings.append(
            FidelityWarning(
                "uncovered_observation",
                uncovered,
                label,
                _finish_detail(uncovered_reasons, uncovered_extra[0]),
            )
        )
    if unattested:
        warnings.append(
            FidelityWarning(
                "unattested_sample",
                unattested,
                label,
                _finish_detail(unattested_reasons, unattested_extra[0]),
            )
        )
    if pilot:
        warnings.append(
            FidelityWarning(
                "auto_migrated_pilot_pin",
                pilot,
                label,
                _finish_detail(pilot_reasons, pilot_extra[0]),
            )
        )
    if source_blind:
        warnings.append(
            FidelityWarning(
                "source_blind_pin",
                source_blind,
                label,
                _finish_detail(source_blind_reasons, source_blind_extra[0]),
                tuple(source_blind_reason_codes),
            )
        )
    if ambiguous_ids:
        ambiguous_reasons: list[str] = []
        ambiguous_extra = [0]
        for species_id, observation_id, row_count in ambiguous_ids:
            _note_reason(
                ambiguous_reasons,
                ambiguous_extra,
                f"{label}: ambiguous observation_id {observation_id!r} under "
                f"species {species_id} names {row_count} scored rows",
            )
        warnings.append(
            FidelityWarning(
                "ambiguous_observation_id",
                len(ambiguous_ids),
                label,
                _finish_detail(ambiguous_reasons, ambiguous_extra[0]),
            )
        )
    if missing_ids:
        missing_reasons: list[str] = []
        missing_extra = [0]
        for species_id, row_index in missing_ids:
            _note_reason(
                missing_reasons,
                missing_extra,
                f"{label}: scored observation "
                f"species.{species_id}.observations[{row_index}] "
                "has no usable observation_id",
            )
        warnings.append(
            FidelityWarning(
                "missing_observation_id",
                len(missing_ids),
                label,
                _finish_detail(missing_reasons, missing_extra[0]),
            )
        )
    non_finite_keys = sorted(
        key for key, leaves in non_finite_addresses.items() if leaves
    )
    if non_finite_keys:
        non_finite_reasons: list[str] = []
        non_finite_extra = [0]
        for species_id, observation_id in non_finite_keys:
            leaf_count = len(non_finite_addresses[(species_id, observation_id)])
            value_word = "value" if leaf_count == 1 else "values"
            _note_reason(
                non_finite_reasons,
                non_finite_extra,
                f"{label}: non-finite measured value refuses certification of "
                f"species.{species_id}.observations[{observation_id}] "
                f"({leaf_count} {value_word})",
            )
        warnings.append(
            FidelityWarning(
                "non_finite_evidence",
                len(non_finite_keys),
                label,
                _finish_detail(non_finite_reasons, non_finite_extra[0]),
            )
        )
    return warnings


def fidelity_warning_summary(warnings: Sequence[FidelityWarning]) -> str:
    """One visible total line. Extract counts are how many warnings of that kind."""
    parts: list[str] = []
    for kind in _FIDELITY_WARNING_KINDS:
        rows = [warning for warning in warnings if warning.kind == kind]
        total = sum(warning.count for warning in rows)
        if kind in ("unattested_sample", "source_blind_pin"):
            parts.append(f"{kind}={total}")
        else:
            parts.append(f"{kind}={total} ({len(rows)} extracts)")
    return " ".join(parts)


def certify_extract_source_fidelity(
    doc: Mapping[str, Any],
    *,
    label: str = "<document>",
    warnings: Sequence[FidelityWarning] | None = None,
    artifact_root: Path | None = None,
) -> list[str]:
    """Refuse source certification while any fidelity hole remains.

    Invariant: an empty list is the only pass. A scored measured leaf is
    certified only when its observation id is one non-empty string naming
    exactly one scored row, and the opened source contains that resolved
    extract leaf as a complete token of the same sign and the same digits.
    The whole value, including a spaced or attached unit, must match, and
    neither neighbour may extend the span. ``1 K`` is not ``1 KPa`` and
    ``1`` is not ``1,000``. A sentence period does not extend the span, so
    ``1`` matches ``the value is 1.``. A CSV or TSV source is parsed
    completely before the locator is applied. A cell certifies a stored
    value only when the whole trimmed, unquoted cell parses to that
    number, or equals the stored string. ``0.9100`` is ``0.91``. The
    numbers are ``Decimal`` values, so ``1e-999`` is not ``0`` and
    ``9007199254740993`` is not ``9007199254740992``. A trailing dot is
    the number, so ``91.`` is ``91``. The prose numeric grammar is not a
    gate on a cell. NaN, Infinity, and InvalidOperation do not certify.
    A non-finite measured value refuses that observation. A
    substring does not certify, so a cell ``1/2`` is not ``1`` and a cell
    ``1,000`` is not ``1``. A MinerU HTML cell address and a formula or
    species label follow the same whole-cell and complete-token rules,
    after the folds described on ``fidelity_source_warnings``. A span
    that cuts through a quoted cell is source-blind and is not re-parsed
    from the sliced lines. A leading decimal
    is a numeric token. A spelling
    that rounds the stored float to a different number is not that token.
    The sample's own
    spelling is not a substitute: a ``rel_tol`` match of a different number
    does not certify the stored leaf. A scored row with no usable
    observation id refuses; the row is not omitted. A non-finite measured
    value (NaN, +Inf, -Inf) refuses that observation. It is not omitted
    and a finite sibling pin does not clear it. Every measured leaf of
    a scored observation's evidence (``values`` and each scored numeric
    condition) must be covered by a matching, non-pilot, attested sample
    whose address is that leaf or an ancestor, and whose source artifact
    was opened and contains each resolved number. A measured string is
    every non-annotation string that contains a digit [0-9]. A label with
    no digit is not a leaf. A digit-bearing string outside
    ``ANNOTATION_KEYS`` that the numeric parser cannot read is uncovered
    unless the opened source carries that printed value, and zero recognised
    leaves in a block with such a string refuses. A
    Unicode minus is a minus. An observation id that names more than one
    scored row refuses; the rows are not one coverage entry.
    Repeating a pin of one address does not cover a sibling, even when the
    numbers are equal, and a ``values`` pin does not cover a sibling
    condition. A descriptive label does not stand in for a number it does
    not contain. The extract itself and program source do not qualify.
    Equipment pins, unopened locators, and copied values that are absent
    from the artifact refuse. ``warnings`` is accepted for existing callers
    and is not the decision: a supplied empty list cannot hide a hole.
    """
    _ = warnings
    warnings = fidelity_source_warnings(
        doc, label=label, artifact_root=artifact_root
    )
    refusals: list[str] = []
    for warning in warnings:
        if warning.count <= 0:
            continue
        detail = warning.detail or warning.kind
        refusals.append(
            f"{warning.label}: source certification refused: "
            f"{warning.kind} count={warning.count}: {detail}"
        )
    refusals.extend(check_all_fidelity_samples_match(doc, label=label))
    return refusals


def _check_fidelity_gate(
    doc: Mapping[str, Any],
    *,
    label: str = "<document>",
) -> list[str]:
    """Certification refusals for this extract.

    Schema validation does not emit these. A caller that certifies or promotes
    the extract treats a non-empty list as a hard refusal. Older trees that
    only have this helper keep that refusal behaviour.
    """
    return certify_extract_source_fidelity(doc, label=label)


def load_known_species_ids() -> set[str] | None:
    """Return known U0/manifest ids, or None if the manifest is unavailable."""
    if not U0_MANIFEST.is_file():
        return None
    try:
        doc = yaml.safe_load(U0_MANIFEST.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(doc, Mapping):
        return None
    species = doc.get("species")
    ids: set[str] = set()
    if isinstance(species, list):
        for row in species:
            if isinstance(row, Mapping) and row.get("id"):
                ids.add(str(row["id"]))
    elif isinstance(species, Mapping):
        ids.update(str(k) for k in species.keys())
    return ids or None


def _locator_ok(locator: Any) -> bool:
    """True when locator is a mapping with ≥1 known key carrying a non-empty value.

    Extension keys are allowed but do not alone satisfy the rule — at least one
    key from LOCATOR_KEYS must be present and non-empty (SCHEMA.md contract).
    """
    if not isinstance(locator, Mapping):
        return False
    if "source_sha256" in locator and not _valid_source_sha256(
        locator["source_sha256"]
    ):
        return False
    for key, val in locator.items():
        if key not in LOCATOR_KEYS or key == "source_sha256":
            continue
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        return True
    return False


def _has_payload_leaf(values: Any) -> bool:
    """True if values contains at least one non-null, non-empty leaf."""
    if values is None:
        return False
    if isinstance(values, bool):
        return True
    if isinstance(values, (int, float)):
        return True
    if isinstance(values, str):
        return bool(values.strip())
    if isinstance(values, (bytes, bytearray, memoryview)):
        return bool(bytes(values).strip())
    if isinstance(values, Mapping):
        if not values:
            return False
        return any(_has_payload_leaf(v) for v in values.values())
    if isinstance(values, (list, tuple)):
        if not values:
            return False
        return any(_has_payload_leaf(v) for v in values)
    if isinstance(values, AbstractSet):
        if not values:
            return False
        return any(_has_payload_leaf(v) for v in values)
    # Unknown scalar types are accepted only when their own truth-value says
    # they carry content; falsey custom values fail closed.
    return bool(values)


def _check_equipment(
    equipment: Any,
    path: str,
    errors: list[str],
) -> None:
    if equipment is None:
        return
    if not isinstance(equipment, Mapping):
        errors.append(f"{path}: equipment must be a mapping, got {type(equipment).__name__}")
        return
    for field, payload in equipment.items():
        fpath = f"{path}.equipment.{field}"
        if field not in EQUIPMENT_FIELDS:
            errors.append(
                f"{fpath}: unknown equipment field (allowed: {sorted(EQUIPMENT_FIELDS)})"
            )
            continue
        if not isinstance(payload, Mapping):
            errors.append(
                f"{fpath}: equipment field must be an object with value+locator "
                f"(bare {type(payload).__name__} refused; owner rule: no unlocatored equipment)"
            )
            continue
        if "value" not in payload:
            errors.append(
                f"{fpath}: missing value (equipment field must carry value+locator)"
            )
        elif payload.get("value") is None:
            errors.append(f"{fpath}: value must not be null")
        if "locator" not in payload:
            errors.append(
                f"{fpath}: missing locator (validator refuses equipment fields without locators)"
            )
        elif not _locator_ok(payload["locator"]):
            errors.append(
                f"{fpath}: locator must be a mapping with at least one of "
                f"{sorted(LOCATOR_KEYS)}"
            )
        # Truthy inferred (not just identity True) requires a derivation note.
        if payload.get("inferred") and not (
            payload.get("inference") or payload.get("note")
        ):
            errors.append(
                f"{fpath}: inferred geometry requires 'inference' or 'note' stating the derivation"
            )


def _check_uncertainty(uncertainty: Any, path: str, errors: list[str]) -> None:
    if uncertainty is None:
        return
    if isinstance(uncertainty, Mapping):
        level = uncertainty.get("confidence_level")
        if "confidence_level" in uncertainty and (
            not isinstance(level, str) or level not in CONFIDENCE_LEVELS
        ):
            errors.append(
                f"{path}.uncertainty.confidence_level: must be one of {sorted(CONFIDENCE_LEVELS)}"
            )
        if not _has_payload_leaf(uncertainty):
            # Allow {} only when no uncertainty was stated; null-only mappings
            # are migrator artifacts.
            if any(v is None for v in uncertainty.values()) and not _has_payload_leaf(
                {k: v for k, v in uncertainty.items() if v is not None}
            ):
                errors.append(
                    f"{path}: uncertainty mapping is null-only "
                    f"(omit the key if no uncertainty was stated, or retain verbatim text)"
                )
    elif isinstance(uncertainty, str) and not uncertainty.strip():
        errors.append(f"{path}: uncertainty string must be non-empty when present")


def _check_observation(
    obs: Any,
    path: str,
    errors: list[str],
    seen_ids: set[str],
) -> None:
    if not isinstance(obs, Mapping):
        errors.append(f"{path}: observation must be a mapping")
        return
    oid = obs.get("observation_id")
    if not oid or not isinstance(oid, str):
        errors.append(f"{path}: observation_id is required (non-empty string)")
    else:
        if oid in seen_ids:
            errors.append(f"{path}: duplicate observation_id '{oid}' within extract")
        seen_ids.add(oid)
        path = f"{path}[{oid}]"

    otype = obs.get("type")
    if otype not in OBSERVATION_TYPES:
        errors.append(
            f"{path}: type must be one of {sorted(OBSERVATION_TYPES)}, got {otype!r}"
        )

    locator = obs.get("locator")
    if locator is None:
        errors.append(f"{path}: locator is required on every observation")
    elif not _locator_ok(locator):
        errors.append(
            f"{path}: locator must be a mapping with at least one of {sorted(LOCATOR_KEYS)}"
        )

    # Refuse recognized equipment field names parked outside equipment: {}
    for eqf in EQUIPMENT_FIELDS:
        if eqf in obs:
            errors.append(
                f"{path}: equipment field {eqf!r} must live under observation.equipment "
                f"(misplaced top-level key refused)"
            )

    if "values" not in obs or obs["values"] is None:
        errors.append(
            f"{path}: values is required (empty/null-only payloads refused; "
            f"pointer rows must carry explicit structured content)"
        )
    else:
        if not isinstance(obs["values"], (Mapping, list)):
            errors.append(f"{path}: values must be a mapping or list when present")
        elif not _has_payload_leaf(obs["values"]):
            errors.append(
                f"{path}: values is empty or null-only (refuse empty evidence payloads)"
            )
        if not obs.get("units"):
            errors.append(f"{path}: units required when values are present")

    # Uncertainty is first-class: if present, must not be a bare null sink.
    _check_uncertainty(obs.get("uncertainty"), path, errors)

    tr = obs.get("T_range_K")
    if tr is not None:
        if not (isinstance(tr, (list, tuple)) and len(tr) == 2):
            errors.append(f"{path}: T_range_K must be [T_min, T_max]")
        else:
            try:
                t0, t1 = float(tr[0]), float(tr[1])
                if t0 > t1:
                    errors.append(f"{path}: T_range_K min > max")
            except (TypeError, ValueError):
                errors.append(f"{path}: T_range_K bounds must be numeric")

    _check_equipment(obs.get("equipment"), path, errors)


def _check_context_row(
    row: Any,
    path: str,
    errors: list[str],
    seen_ids: set[str],
) -> None:
    """One row of the sibling ``context`` container (d-032 ruling).

    Context rows describe the experiment (apparatus, method, sample,
    conditions, compilation scope); nothing scored reads them. They keep the
    observation-shaped payload contract (id, locator, non-empty values) but
    their ``type`` is a free context-kind label — except that a SCORED
    observation type parked here is refused, because context must never
    become a shadow container for the container the scoring machinery reads.
    """
    if not isinstance(row, Mapping):
        errors.append(f"{path}: context row must be a mapping")
        return
    oid = row.get("observation_id")
    if not oid or not isinstance(oid, str):
        errors.append(f"{path}: observation_id is required (non-empty string)")
    else:
        if oid in seen_ids:
            errors.append(f"{path}: duplicate observation_id '{oid}' within extract")
        seen_ids.add(oid)
        path = f"{path}[{oid}]"

    rtype = row.get("type")
    if not isinstance(rtype, str) or not rtype.strip():
        errors.append(f"{path}: type is required on every context row (non-empty string)")
    elif rtype in OBSERVATION_TYPES:
        errors.append(
            f"{path}: scored observation type {rtype!r} must live under "
            f"observations, not the context container"
        )

    locator = row.get("locator")
    if locator is None:
        errors.append(f"{path}: locator is required on every context row")
    elif not _locator_ok(locator):
        errors.append(
            f"{path}: locator must be a mapping with at least one of "
            f"{sorted(LOCATOR_KEYS)}"
        )

    # Same anti-parking rule as observations.
    for eqf in EQUIPMENT_FIELDS:
        if eqf in row:
            errors.append(
                f"{path}: equipment field {eqf!r} must live under the row's equipment "
                f"(misplaced top-level key refused)"
            )

    if "values" not in row or row["values"] is None:
        errors.append(
            f"{path}: values is required (empty/null-only payloads refused)"
        )
    else:
        if not isinstance(row["values"], (Mapping, list)):
            errors.append(f"{path}: values must be a mapping or list when present")
        elif not _has_payload_leaf(row["values"]):
            errors.append(
                f"{path}: values is empty or null-only (refuse empty evidence payloads)"
            )
        if not row.get("units"):
            errors.append(f"{path}: units required when values are present")

    _check_uncertainty(row.get("uncertainty"), path, errors)

    tr = row.get("T_range_K")
    if tr is not None:
        if not (isinstance(tr, (list, tuple)) and len(tr) == 2):
            errors.append(f"{path}: T_range_K must be [T_min, T_max]")
        else:
            try:
                t0, t1 = float(tr[0]), float(tr[1])
                if t0 > t1:
                    errors.append(f"{path}: T_range_K min > max")
            except (TypeError, ValueError):
                errors.append(f"{path}: T_range_K bounds must be numeric")

    _check_equipment(row.get("equipment"), path, errors)


def _observation_count(doc: Mapping[str, Any]) -> int:
    species = doc.get("species") or {}
    n_obs = 0
    if isinstance(species, Mapping):
        for block in species.values():
            if isinstance(block, Mapping):
                obs = block.get("observations") or []
                if isinstance(obs, list):
                    n_obs += len(obs)
    return n_obs


def _check_fidelity_sample_shape(
    sample: Mapping[str, Any],
    sp: str,
    errors: list[str],
    *,
    doc: Mapping[str, Any] | None = None,
    enforce_observation_paths: bool = False,
) -> None:
    """Validate one fidelity sample's structure (path-based or structured).

    When ``enforce_observation_paths`` is True (non-allowlisted / new extracts),
        path-based samples must pin under
        ``species.*.(observations|context)[...].(values|equipment|<condition>)``
        so metadata-only paths cannot satisfy the gate. ``<condition>`` is
        a scored numeric condition.
    """
    path_keys, structured_keys = _sample_addressing_keys(sample)
    path = sample.get(path_keys[0]) if len(path_keys) == 1 else None
    structured = bool(structured_keys)
    for mode_error in _sample_mode_errors(sample):
        errors.append(f"{sp}: {mode_error}")
    if not path and not structured:
        errors.append(
            f"{sp}: path/field_path OR structured fields "
            f"(species + observation_id/observable) required"
        )
    if structured:
        if not sample.get("species"):
            errors.append(f"{sp}: structured sample requires species")
        if not sample.get("observation_id") and not sample.get("observable"):
            errors.append(
                f"{sp}: structured sample requires observation_id and/or observable"
            )
    if path and enforce_observation_paths:
        if not _path_is_observation_evidence(str(path)):
            errors.append(
                f"{sp}: path-based fidelity sample on new extracts must pin "
                f"observation or context evidence "
                f"(species.*.(observations|context)[id].(values|equipment|<condition>)...); "
                f"got path={path!r} (metadata-only paths refused)"
            )
    if "value" not in sample and "draft_value" not in sample:
        errors.append(f"{sp}: value (or draft_value) is required")
    else:
        try:
            expected = _sample_expected_value(sample)
        except KeyError as exc:
            errors.append(f"{sp}: invalid fidelity sample value selector: {exc}")
            expected = None
        if _is_null_or_empty_pin(expected):
            errors.append(
                f"{sp}: fidelity sample value must be a non-null observed pin "
                f"(null/empty-only pins refused)"
            )
        elif doc is not None:
            # Shared identity + null actual are shape defects (not just match).
            try:
                actual = resolve_fidelity_sample(doc, sample)
            except KeyError as exc:
                errors.append(f"{sp}: cannot resolve sample against extract: {exc}")
                actual = None
            else:
                if _shares_mutable_identity(expected, actual):
                    errors.append(
                        f"{sp}: fidelity sample value shares mutable object identity with "
                        f"extract body at some nesting depth (YAML alias); rewrite pin as "
                        f"an independent literal"
                    )
                if _is_null_or_empty_pin(actual) and not _is_null_or_empty_pin(expected):
                    errors.append(
                        f"{sp}: resolved extract value must be non-null and non-empty "
                        f"(content-free pins do not satisfy the fidelity gate)"
                    )
    _rel_tol, rel_tol_error = _validated_rel_tol(sample)
    if rel_tol_error:
        errors.append(f"{sp}: {rel_tol_error}")
    locator_keys = [key for key in ("locator", "source_locator") if key in sample]
    loc = sample.get(locator_keys[0]) if len(locator_keys) == 1 else None
    note = sample.get("note")
    # Structured (OCR) samples require a real locator; path-based pilot samples
    # may use note as a soft locator (DRAFT migration legacy).
    if locator_keys:
        if len(locator_keys) == 1 and not _locator_ok(loc):
            errors.append(
                f"{sp}: {locator_keys[0]} must be a mapping with at least one of "
                f"{sorted(LOCATOR_KEYS)}"
            )
    elif structured:
        errors.append(
            f"{sp}: locator/source_locator required on structured fidelity samples "
            f"(for source/OCR artifact review)"
        )
    elif not note:
        errors.append(
            f"{sp}: locator/source_locator or note required "
            f"(source-backed sample must be locatable)"
        )


def _check_fidelity_samples(
    doc: Mapping[str, Any],
    label: str,
    errors: list[str],
    *,
    require_samples: bool = True,
    check_match: bool = False,
) -> None:
    """Validate fidelity samples; optionally require presence + content match.

    ``require_samples`` is False for pre-policy (grandfathered) extracts under
    the ENFORCED_FOR_NEW policy. When samples *are* present they are still
    shape-checked (and optionally match-checked). New extracts
    (``require_samples=True``) must pin observation evidence paths.
    """
    n_obs = _observation_count(doc)
    samples = doc.get("fidelity_samples")

    if n_obs == 0:
        # No observations → samples optional; if present, still shape-check.
        if samples is None:
            return
    elif require_samples:
        if samples is None:
            errors.append(
                f"{label}: fidelity_samples required when observations are present "
                f"(t-510 ENFORCED_FOR_NEW: ≥1 located sample; "
                f"pre-policy pilot extracts may be allowlisted)"
            )
            return
        if not isinstance(samples, list) or not samples:
            errors.append(f"{label}: fidelity_samples must be a non-empty list")
            return
    else:
        # Grandfathered: absence is OK.
        if samples is None:
            return
        if not isinstance(samples, list):
            errors.append(f"{label}: fidelity_samples must be a list when present")
            return
        if not samples:
            return

    if not isinstance(samples, list):
        errors.append(f"{label}: fidelity_samples must be a list")
        return

    for i, sample in enumerate(samples):
        sp = f"{label}:fidelity_samples[{i}]"
        if not isinstance(sample, Mapping):
            errors.append(f"{sp}: each sample must be a mapping")
            continue
        _check_fidelity_sample_shape(
            sample,
            sp,
            errors,
            doc=doc,
            enforce_observation_paths=require_samples,
        )
        if check_match:
            errors.extend(check_fidelity_sample_matches(doc, sample, label=sp))


def _check_sweep_gases(doc: Mapping[str, Any], label: str, errors: list[str]) -> None:
    from simulator.battery.migrate import _located_from_plain, _sweep_gas_from_plain
    from simulator.battery.validate import validate_sweep_gas

    experiments = doc.get("experiments", ())
    if isinstance(experiments, Mapping):
        experiments = experiments.values()
    elif not isinstance(experiments, (list, tuple)):
        errors.append(f"{label}: experiments must be a list or mapping")
        return
    for i, experiment in enumerate(experiments):
        if not isinstance(experiment, Mapping):
            continue
        pressure = experiment.get("pressure_environment")
        if not isinstance(pressure, Mapping) or "sweep_gas" not in pressure:
            continue
        path = f"{label}:experiments[{i}].pressure_environment.sweep_gas"
        try:
            located = _located_from_plain(pressure["sweep_gas"], _sweep_gas_from_plain)
            if located.state.is_value:
                if located.locator is None:
                    errors.append(f"{path}: empirical Located value requires a locator")
                errors.extend(f"{issue.path}: {issue.detail}" for issue in
                              validate_sweep_gas(located.state.value, path))
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            errors.append(f"{path}: invalid sweep gas: {exc}")


def _check_gas_ratios(doc: Mapping[str, Any], label: str, errors: list[str]) -> None:
    from simulator.battery.migrate import _located_from_plain, _value_or_point_from_plain
    from simulator.battery.records import GasRatio

    experiments = doc.get("experiments", ())
    if isinstance(experiments, Mapping):
        experiments = experiments.values()
    elif not isinstance(experiments, (list, tuple)):
        return
    for i, experiment in enumerate(experiments):
        if not isinstance(experiment, Mapping):
            continue
        control = experiment.get("fO2_control")
        if not isinstance(control, Mapping) or control.get("gas_ratio") is None:
            continue
        path = f"{label}:experiments[{i}].fO2_control.gas_ratio"
        ratio = control["gas_ratio"]
        try:
            if not isinstance(ratio, Mapping):
                raise ValueError("must be a mapping")
            numerator = ratio.get("numerator_species")
            denominator = ratio.get("denominator_species")
            if not isinstance(numerator, str) or not isinstance(denominator, str):
                raise ValueError("ratio species must be text")
            located = _located_from_plain(ratio["value"], _value_or_point_from_plain)
            GasRatio(
                numerator_species=numerator,
                denominator_species=denominator,
                value=located,
                basis=ratio.get("basis"),
            )
            if located.state.is_value and located.locator is None:
                errors.append(f"{path}.value: empirical Located value requires a locator")
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            errors.append(f"{path}: invalid gas ratio: {exc}")


def validate_extract_document(
    doc: Any,
    *,
    path: Path | None = None,
    expected_source_id: str | None = None,
    known_species_ids: set[str] | None = None,
    collect_warnings: list[str] | None = None,
    fidelity_warnings: list[FidelityWarning] | None = None,
    pre_policy_ids: set[str] | None = None,
    check_fidelity_match: bool = False,
    artifact_root: Path | None = None,
) -> list[str]:
    """Return a list of error strings (empty ⇒ schema-valid).

    Warnings (unknown species ids, and rendered source-fidelity warnings) are
    appended to ``collect_warnings`` when provided. Fidelity holes do not reject
    a draft. ``review_status: reviewed`` is a promotion: those holes become
    errors from ``certify_extract_source_fidelity``. Typed ``FidelityWarning``
    rows are appended to ``fidelity_warnings`` when that list is provided. The
    holes are always computed here.

    ``pre_policy_ids``: source_ids exempt from the fidelity_samples presence
    requirement (ENFORCED_FOR_NEW grandfathering). When None, the on-disk
    allowlist is not consulted and samples are required (unit-test default for
    non-allowlisted fixtures). Pass an empty set to require samples for all;
    pass the loaded allowlist from ``validate_all`` for corpus validation.
    """
    errors: list[str] = []
    label = str(path) if path else "<document>"

    if not isinstance(doc, Mapping):
        return [f"{label}: root must be a mapping"]

    errors.extend(_docs_private_prose_errors(doc, label))

    for key_path in iter_unsupported_context_container_paths(doc):
        rendered_path = ".".join(key_path)
        errors.append(
            f"{label}:{rendered_path}: context container is not lifted by "
            f"the migrator; supported location is {SUPPORTED_EXTRACT_CONTEXT_PATH}"
        )

    if doc.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"{label}: schema_version must be {SCHEMA_VERSION!r}, "
            f"got {doc.get('schema_version')!r}"
        )

    source_id = doc.get("source_id")
    if not source_id or not isinstance(source_id, str):
        errors.append(f"{label}: source_id is required")
    elif expected_source_id is not None and source_id != expected_source_id:
        errors.append(
            f"{label}: source_id {source_id!r} does not match filename stem "
            f"{expected_source_id!r}"
        )

    source = doc.get("source")
    if not isinstance(source, Mapping):
        errors.append(f"{label}: source metadata mapping is required")
    else:
        if not source.get("citation"):
            errors.append(f"{label}: source.citation is required")

    extraction = doc.get("extraction")
    if not isinstance(extraction, Mapping):
        errors.append(f"{label}: extraction metadata mapping is required")
    else:
        for key in ("method", "date", "worker"):
            if not extraction.get(key):
                errors.append(f"{label}: extraction.{key} is required")
        date = extraction.get("date")
        if date is not None and isinstance(date, str) and not _ISO_DATE_RE.match(date):
            errors.append(
                f"{label}: extraction.date must be ISO YYYY-MM-DD, got {date!r}"
            )

    # Every absolute path value, parsed (quoted, aliased, nested), including
    # extraction.provenance_path. b-713 replaced a line-regex count ceiling
    # that missed quoted scalars and YAML aliases.
    errors.extend(_absolute_path_value_errors(doc, label))
    errors.extend(_private_path_value_errors(doc, label))

    review = doc.get("review_status")
    if review not in REVIEW_STATUSES:
        errors.append(
            f"{label}: review_status must be one of {sorted(REVIEW_STATUSES)}, "
            f"got {review!r}"
        )

    sp = doc.get("source_priority")
    if sp is not None and not isinstance(sp, Mapping):
        errors.append(f"{label}: source_priority must be a mapping when present")

    species = doc.get("species")
    if species is None:
        errors.append(f"{label}: species map is required (may be empty {{}})")
    elif not isinstance(species, Mapping):
        errors.append(f"{label}: species must be a mapping")
    else:
        # File-scope observation_id uniqueness (SCHEMA.md: unique within the file).
        seen: set[str] = set()
        for sid, block in species.items():
            spath = f"{label}:species.{sid}"
            if known_species_ids is not None and collect_warnings is not None:
                if str(sid) not in known_species_ids:
                    collect_warnings.append(
                        f"{spath}: unknown species id {sid!r} "
                        f"(not in vapour_rail_u0_manifest; allowed in draft)"
                    )
            if not isinstance(block, Mapping):
                errors.append(f"{spath}: species block must be a mapping")
                continue
            obs_list = block.get("observations")
            if obs_list is None:
                errors.append(f"{spath}: observations list is required")
                continue
            if not isinstance(obs_list, list):
                errors.append(f"{spath}: observations must be a list")
                continue
            for i, obs in enumerate(obs_list):
                _check_observation(obs, f"{spath}.observations[{i}]", errors, seen)
            ctx_list = block.get("context")
            if ctx_list is not None:
                if not isinstance(ctx_list, list):
                    errors.append(f"{spath}: context must be a list")
                else:
                    for i, row in enumerate(ctx_list):
                        _check_context_row(row, f"{spath}.context[{i}]", errors, seen)

    _check_sweep_gases(doc, label, errors)
    _check_gas_ratios(doc, label, errors)

    # ENFORCED_FOR_NEW: require samples unless source_id is pre-policy allowlisted.
    is_pre_policy = False
    if pre_policy_ids is not None and isinstance(source_id, str):
        is_pre_policy = source_id in pre_policy_ids
    _check_fidelity_samples(
        doc,
        label,
        errors,
        require_samples=not is_pre_policy,
        check_match=check_fidelity_match,
    )

    # Chokepoint: count source-fidelity holes on every extract. Drafts warn.
    # review_status reviewed is promotion and refuses the same holes.
    source_warnings = fidelity_source_warnings(
        doc, label=label, artifact_root=artifact_root
    )
    if fidelity_warnings is not None:
        fidelity_warnings.extend(source_warnings)
    if collect_warnings is not None:
        collect_warnings.extend(item.render() for item in source_warnings)
    if review == "reviewed":
        errors.extend(
            certify_extract_source_fidelity(
                doc,
                label=label,
                warnings=source_warnings,
                artifact_root=artifact_root,
            )
        )

    return errors


def validate_extract_file(
    path: Path,
    *,
    known_species_ids: set[str] | None = None,
    collect_warnings: list[str] | None = None,
    fidelity_warnings: list[FidelityWarning] | None = None,
    pre_policy_ids: set[str] | None = None,
    check_fidelity_match: bool = False,
    artifact_root: Path | None = None,
) -> list[str]:
    try:
        doc = load_extract_yaml(path)
    except Exception as exc:  # noqa: BLE001 — surface parse failures as validation errors
        return [f"{path}: failed to load YAML: {exc}"]
    if isinstance(doc, Mapping) and doc.get("schema_version") == TABLE_SCHEMA_VERSION:
        errors: list[str] = []
        errors.extend(_docs_private_prose_errors(doc, str(path)))
        errors.extend(_absolute_path_value_errors(doc, str(path)))
        errors.extend(_private_path_value_errors(doc, str(path)))
        source_id = doc.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            return [f"{path}: companion table source_id is required"]

        if not path.stem.startswith(f"{source_id}-"):
            errors.append(
                f"{path}: companion filename stem must start with source_id "
                f"{source_id!r} followed by '-'"
            )

        parent_filename = f"{source_id}.yaml"
        if Path(parent_filename).name != parent_filename:
            errors.append(
                f"{path}: companion parent extract must be named in the same directory"
            )
            return errors
        parent_path = path.with_name(parent_filename)
        if not parent_path.is_file():
            errors.append(
                f"{path}: companion parent extract does not exist: {parent_path}"
            )
            return errors

        try:
            parent_doc = load_extract_yaml(parent_path)
        except Exception as exc:  # noqa: BLE001 — surface parent parse failures
            errors.append(f"{parent_path}: failed to load YAML: {exc}")
            return errors

        linked = False
        species = parent_doc.get("species") if isinstance(parent_doc, Mapping) else None
        if isinstance(species, Mapping):
            for block in species.values():
                if not isinstance(block, Mapping):
                    continue
                observations = block.get("observations")
                if not isinstance(observations, list):
                    continue
                for observation in observations:
                    if not isinstance(observation, Mapping):
                        continue
                    values = observation.get("values")
                    reference = (
                        values.get("table_transcription")
                        if isinstance(values, Mapping)
                        else None
                    )
                    if (
                        isinstance(reference, str)
                        and not Path(reference).is_absolute()
                    ):
                        if (ROOT / reference).resolve() == path.resolve():
                            linked = True
                            break
                if linked:
                    break
        if not linked:
            errors.append(
                f"{parent_path}: parent extract does not link companion table {path}"
            )
        return errors
    return validate_extract_document(
        doc,
        path=path,
        expected_source_id=path.stem,
        known_species_ids=known_species_ids,
        collect_warnings=collect_warnings,
        fidelity_warnings=fidelity_warnings,
        pre_policy_ids=pre_policy_ids,
        check_fidelity_match=check_fidelity_match,
        artifact_root=artifact_root,
    )


def validate_source_priority_file(
    path: Path | None = None,
    *,
    known_source_ids: set[str] | None = None,
) -> list[str]:
    p = path or (EXTRACTS_DIR / "_source_priority.yaml")
    if not p.is_file():
        return [f"{p}: missing store-level source_priority file"]
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return [f"{p}: failed to load YAML: {exc}"]
    errors: list[str] = []
    if not isinstance(doc, Mapping):
        return [f"{p}: root must be a mapping"]
    if doc.get("schema_version") != "literature_extract_source_priority.v1":
        errors.append(f"{p}: unexpected schema_version {doc.get('schema_version')!r}")
    sp = doc.get("source_priority")
    if not isinstance(sp, Mapping) or not sp:
        errors.append(f"{p}: source_priority map is required")
        return errors
    for family in OBSERVATION_TYPES:
        if family not in sp:
            errors.append(
                f"{p}: source_priority missing required family {family!r} "
                f"(every observable family must have an explicit reviewed order)"
            )
    for family, order in sp.items():
        if family not in OBSERVATION_TYPES:
            errors.append(
                f"{p}: unknown observable family {family!r} "
                f"(allowed: {sorted(OBSERVATION_TYPES)})"
            )
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
            errors.append(
                f"{p}: source_priority.{family} must be a list of source_id strings"
            )
        elif len(order) == 0:
            errors.append(
                f"{p}: source_priority.{family} must be non-empty "
                f"(empty family lists fail closed)"
            )
    return errors


def validate_all(
    paths: Iterable[Path] | None = None,
    *,
    check_priority: bool = True,
    check_fidelity_policy: bool = True,
    check_fidelity_match: bool = False,
    collect_warnings: list[str] | None = None,
    fidelity_warnings: list[FidelityWarning] | None = None,
) -> list[str]:
    errors: list[str] = []
    if check_priority:
        errors.extend(validate_source_priority_file())
    pre_policy: set[str] = set()
    if check_fidelity_policy:
        policy, policy_errs = load_fidelity_policy()
        errors.extend(policy_errs)
        pre_policy = pre_policy_source_ids(policy) if not policy_errs else set()
    files = list(paths) if paths is not None else discover_extracts()
    if not files and paths is None:
        errors.append(f"{EXTRACTS_DIR}: no extract YAML files found")
    known = load_known_species_ids()
    for path in files:
        errors.extend(
            validate_extract_file(
                path,
                known_species_ids=known,
                collect_warnings=collect_warnings,
                fidelity_warnings=fidelity_warnings,
                pre_policy_ids=pre_policy if check_fidelity_policy else None,
                check_fidelity_match=check_fidelity_match,
            )
        )
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Extract YAML paths (default: all under data/literature/extracts/)",
    )
    parser.add_argument(
        "--skip-priority",
        action="store_true",
        help="Do not validate _source_priority.yaml",
    )
    parser.add_argument(
        "--skip-fidelity-policy",
        action="store_true",
        help="Do not load/apply the ENFORCED_FOR_NEW fidelity allowlist",
    )
    parser.add_argument(
        "--check-fidelity-match",
        action="store_true",
        help="Also assert every fidelity sample still matches extract content",
    )
    parser.add_argument(
        "--show-warnings",
        action="store_true",
        help="Print unknown-species-id warnings (non-fatal)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    paths = [p.resolve() for p in args.paths] if args.paths else None
    warnings: list[str] = []
    fidelity: list[FidelityWarning] = []
    errors = validate_all(
        paths,
        check_priority=not args.skip_priority,
        check_fidelity_policy=not args.skip_fidelity_policy,
        check_fidelity_match=args.check_fidelity_match,
        collect_warnings=warnings if args.show_warnings else None,
        fidelity_warnings=fidelity,
    )
    if args.show_warnings and warnings:
        for w in warnings:
            print(f"WARN: {w}", file=sys.stderr)
    summary = fidelity_warning_summary(fidelity)
    if any(item.count for item in fidelity):
        print(
            f"WARN: source fidelity is not certified: {summary}",
            file=sys.stderr,
        )
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        print(f"FAIL: {len(errors)} error(s)", file=sys.stderr)
        return 1
    n = len(paths) if paths is not None else len(discover_extracts())
    print(f"OK: {n} extract file(s) schema-valid; fidelity warnings: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
