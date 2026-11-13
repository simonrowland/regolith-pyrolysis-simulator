"""Unicode subscript digits as plain digits, and the one charge-sign set.

This is the one map from subscript digits to ASCII digits. A caller that
also removes spaces or braces keeps that rule itself. ``FORMULA_CHARGE_SIGNS``
is the one set of formula charge signs.
"""

from __future__ import annotations

_SUBSCRIPT_DIGITS = "₀₁₂₃₄₅₆₇₈₉"
_ASCII_DIGITS = "0123456789"
_SUBSCRIPT_DIGIT_FOLD = str.maketrans(_SUBSCRIPT_DIGITS, _ASCII_DIGITS)

# ASCII plus and hyphen-minus, minus sign, en dash, figure dash, small
# hyphen-minus, fullwidth plus and hyphen-minus, superscript plus and
# minus, subscript plus and minus. Not a second superscript alphabet.
FORMULA_CHARGE_SIGNS = (
    "+-"
    "\u2212"
    "\u2013"
    "\u2012"
    "\ufe63"
    "\uff0b"
    "\uff0d"
    "\u207a\u207b"
    "\u208a\u208b"
)


def fold_subscript_digits(text: str) -> str:
    """Replace Unicode subscript digits with ASCII digits.

    Every other character stays, including spaces and braces.
    """
    return text.translate(_SUBSCRIPT_DIGIT_FOLD)
