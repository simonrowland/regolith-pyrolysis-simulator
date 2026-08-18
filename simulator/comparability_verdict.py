"""Three-value comparability vocabulary shared by b-190 and b-205.

THE DEFECT THIS CLOSES. Both tickets are the same bug wearing different clothes:
a comparison whose *basis* is undeclared on one side was reported as comparable,
because the only available verdict was a boolean and ``False`` was reserved for a
*known* mismatch. An unknown basis therefore had to borrow ``True``, and an
untyped row became indistinguishable from a verified-compatible one.

    b-190  ``rail_system_class_comparability`` returns ``(True, None, None)`` for
           an observation with no ``system_class`` -- untyped reads as comparable.
    b-205  the melt-activity bench scores measurements against engines whose
           ``activity_standard_state`` is ``None`` -- 402 of 402 points for
           imcc-published and imcc-ext -- with no record that the basis was
           never established.

THE INVARIANT. ``undeterminable`` is NOT a pass and NOT a fail. It is strictly
weaker than either: a known mismatch is a fact about the world, while an
undeterminable verdict is a fact about our *records*. Collapsing it into either
boolean destroys that distinction, which is the whole point of the third state.

    An absent declaration on EITHER side yields ``undeterminable``.
    Only two present declarations can produce ``match`` or ``mismatch``.

DIAGNOSTIC ONLY (owner ruling 2026-08-18). These verdicts are reported and
counted. Nothing refuses on them. Instrument before gate: a gating decision needs
the counts first, and the counts do not exist until this ships.
"""

from __future__ import annotations

from typing import Any, Collection

MATCH = "match"
MISMATCH = "mismatch"
UNDETERMINABLE = "undeterminable"

#: Closed vocabulary. One verdict per scored row, at both consumer sites.
COMPARABILITY_VERDICTS: frozenset[str] = frozenset({MATCH, MISMATCH, UNDETERMINABLE})

#: Sentinel values that mean "nothing was declared here". Kept DELIBERATELY
#: MINIMAL: only what the call sites actually emit (``None`` from an engine that
#: declares no standard state, ``"unstated"`` from the measured-basis parser).
#: An earlier version also swallowed ``"unknown"`` and ``"not_stated"``, which
#: made this helper disagree with ``rail_system_class_comparability`` on the
#: same string -- that gate fail-closes an unlisted-but-declared class to False
#: while this returned ``undeterminable``. Two answers for one input is worse
#: than a strict reading, so a declared-but-odd token is now a MISMATCH here
#: too. Add a sentinel only when a producer actually emits it.
_ABSENT: frozenset[Any] = frozenset({None, "", "unstated"})


def _declared(value: Any) -> bool:
    """True when ``value`` carries an actual declaration."""

    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _ABSENT
    return True


def verdict_from_declarations(left: Any, right: Any) -> str:
    """Compare two declared bases (the b-205 shape).

    Either side undeclared -> ``undeterminable``. Both declared -> equality.

    >>> verdict_from_declarations("liquid", "liquid")
    'match'
    >>> verdict_from_declarations("pure_solid", "liquid")
    'mismatch'
    >>> verdict_from_declarations("pure_solid", None)
    'undeterminable'
    >>> verdict_from_declarations("unstated", "liquid")
    'undeterminable'
    """

    if not _declared(left) or not _declared(right):
        return UNDETERMINABLE
    if isinstance(left, str) and isinstance(right, str):
        return MATCH if left.strip().lower() == right.strip().lower() else MISMATCH
    return MATCH if left == right else MISMATCH


def verdict_from_membership(declared: Any, allowed: Collection[Any]) -> str:
    """Test one declaration against an allowed set (the b-190 shape).

    Undeclared -> ``undeterminable``; that is the entire point of the ticket.
    A declared value outside ``allowed`` is a genuine ``mismatch``, including a
    value that is simply unlisted -- do not invent comparability for it.

    >>> verdict_from_membership("silicate_melt", {"silicate_melt"})
    'match'
    >>> verdict_from_membership("molten_metal", {"silicate_melt"})
    'mismatch'
    >>> verdict_from_membership(None, {"silicate_melt"})
    'undeterminable'
    """

    if not _declared(declared):
        return UNDETERMINABLE
    if isinstance(declared, str):
        # Casefold like the sibling helper. Without this "Silicate_Melt" is a
        # mismatch against {"silicate_melt"} -- a false mismatch driven by
        # capitalisation, which is exactly the kind of records-artefact this
        # vocabulary exists to keep out of the verdict.
        needle = declared.strip().lower()
        return MATCH if any(
            isinstance(item, str) and item.strip().lower() == needle
            for item in allowed
        ) else MISMATCH
    try:
        return MATCH if declared in allowed else MISMATCH
    except TypeError:
        # Unhashable declaration (list/dict). It is a declaration of SOMETHING,
        # but not one this vocabulary can test, so it cannot be a match.
        return MISMATCH


__all__ = [
    "MATCH",
    "MISMATCH",
    "UNDETERMINABLE",
    "COMPARABILITY_VERDICTS",
    "verdict_from_declarations",
    "verdict_from_membership",
]
