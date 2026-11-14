"""Raster spelling for a compilation formula. Not a parser.

The only edits are alphabetic case and a published digit that the raster
shows as the element letter (0→O, 1→l). A letter never becomes a digit, and
a different element is never substituted. ``usgs_b1259._ocr_formula_variants``
is a different mass-gated OCR rule and is not this function.
"""

from __future__ import annotations

import re

from simulator.reference_data.janaf import flatten_subscripts

# Formatting commands only. \alpha, \beta, \gamma, \cdot stay, so a phase prefix
# cannot be rewritten into an element.
_LATEX_FORMAT = re.compile(r"\\(?:mathrm|mathbf|textrm|text|left|right|displaystyle)")
_UNICODE_SCRIPTS = str.maketrans(
    {
        "₀": "0",
        "₁": "1",
        "₂": "2",
        "₃": "3",
        "₄": "4",
        "₅": "5",
        "₆": "6",
        "₇": "7",
        "₈": "8",
        "₉": "9",
        "⁰": "0",
        "¹": "1",
        "²": "2",
        "³": "3",
        "⁴": "4",
        "⁵": "5",
        "⁶": "6",
        "⁷": "7",
        "⁸": "8",
        "⁹": "9",
    }
)


def compilation_formula_text(value: str) -> str:
    """Flatten subscripts and strip math-format markers. Does not parse."""

    text = str(value or "").translate(_UNICODE_SCRIPTS)
    text = text.replace("$", "")
    text = _LATEX_FORMAT.sub("", text)
    # Bare "_" is a subscript mark ("SiO_2"). Brace form is flatten_subscripts.
    text = text.replace("_", "")
    return flatten_subscripts(text)


def aligned_raster_formula(published: str, raster: str) -> str | None:
    """Flattened raster spelling, or None when the edit is not allowed.

    Equal strings, length changes, letter→digit, and element substitutions
    return None. The caller records the locator; this function does not.
    """

    left = compilation_formula_text(published)
    right = compilation_formula_text(raster)
    if not left or not right or left == right or len(left) != len(right):
        return None
    for published_char, raster_char in zip(left, right):
        if published_char == raster_char:
            continue
        if (
            published_char.isalpha()
            and raster_char.isalpha()
            and published_char.lower() == raster_char.lower()
        ):
            continue
        if published_char == "0" and raster_char in {"O", "o"}:
            continue
        if published_char == "1" and raster_char in {"l", "L"}:
            continue
        return None
    return right
