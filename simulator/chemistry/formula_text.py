"""Unicode subscript digits as plain digits, and the certification sign class.

This is the one map from subscript digits to ASCII digits. A caller that
also removes spaces or braces keeps that rule itself.

``UNICODE_MINUS_SIGNS`` and ``UNICODE_PLUS_SIGNS`` are the one class of
signs that fold onto ASCII ``-`` and ``+``. Superscript and subscript plus
and minus are in that class. Charge detection uses these same characters.
Plus-minus is not here: it stays a distinct numeric sign.
``is_superscripted`` is the one superscript test for a formula token or
cell: a ``<sup>`` tag, a caret, or a Unicode name that contains
SUPERSCRIPT or MODIFIER LETTER SMALL / MODIFIER LETTER CAPITAL.
"""

from __future__ import annotations

import re
import unicodedata

_SUBSCRIPT_DIGITS = "₀₁₂₃₄₅₆₇₈₉"
_ASCII_DIGITS = "0123456789"
_SUBSCRIPT_DIGIT_FOLD = str.maketrans(_SUBSCRIPT_DIGITS, _ASCII_DIGITS)

# Signs the certification grammar folds onto ASCII "-".
UNICODE_MINUS_SIGNS = (
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
# Signs the certification grammar folds onto ASCII "+".
# Superscript and subscript plus are in this string.
UNICODE_PLUS_SIGNS = (
    "\uff0b"  # FULLWIDTH PLUS SIGN
    "\ufe62"  # SMALL PLUS SIGN
    "\u207a"  # SUPERSCRIPT PLUS
    "\u208a"  # SUBSCRIPT PLUS
    "\u2795"  # HEAVY PLUS SIGN
)


# A ``<sup>`` tag, empty or not. The slash matches the closing tag.
_SUP_TAG_RE = re.compile(r"<\s*/?\s*sup\b", re.IGNORECASE)


def is_superscripted(text: str) -> bool:
    """True when a formula token or cell carries a superscript.

    A ``<sup>`` tag, a caret, a character whose Unicode name contains
    SUPERSCRIPT, or a MODIFIER LETTER SMALL / MODIFIER LETTER CAPITAL.
    The character test is ``unicodedata.name``, not a hand list. An empty
    tag counts. Callers decide this on the raw token or cell, before
    superscript spans are stripped.
    """
    if "^" in text or _SUP_TAG_RE.search(text):
        return True
    for character in text:
        try:
            name = unicodedata.name(character)
        except ValueError:
            continue
        if (
            "SUPERSCRIPT" in name
            or "MODIFIER LETTER SMALL" in name
            or "MODIFIER LETTER CAPITAL" in name
        ):
            return True
    return False


def fold_subscript_digits(text: str) -> str:
    """Replace Unicode subscript digits with ASCII digits.

    Every other character stays, including spaces and braces.
    """
    return text.translate(_SUBSCRIPT_DIGIT_FOLD)
