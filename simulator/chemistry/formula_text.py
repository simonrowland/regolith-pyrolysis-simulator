"""Unicode subscript digits as plain digits.

This is the one map from subscript digits to ASCII digits. A caller that
also removes spaces or braces keeps that rule itself.
"""

from __future__ import annotations

_SUBSCRIPT_DIGITS = "₀₁₂₃₄₅₆₇₈₉"
_ASCII_DIGITS = "0123456789"
_SUBSCRIPT_DIGIT_FOLD = str.maketrans(_SUBSCRIPT_DIGITS, _ASCII_DIGITS)


def fold_subscript_digits(text: str) -> str:
    """Replace Unicode subscript digits with ASCII digits.

    Every other character stays, including spaces and braces.
    """
    return text.translate(_SUBSCRIPT_DIGIT_FOLD)
