"""Tests for the b-190 / b-205 three-value comparability vocabulary.

WHY THIS FILE EXISTS. The vocabulary shipped with zero tests, and an adversarial
review made the consequence concrete: a one-line ``return MATCH`` inside
``verdict_from_declarations`` would have been completely silent, as would
deleting the recorded row key at either consumer. An instrument nobody tests is
not an instrument.

These tests pin the INVARIANT, not the current counts:

    an absent declaration on EITHER side yields `undeterminable`,
    and `undeterminable` is never silently promoted to a pass.
"""

from __future__ import annotations

import doctest

import pytest

from simulator import comparability_verdict as cv
from simulator.comparability_verdict import (
    MATCH,
    MISMATCH,
    UNDETERMINABLE,
    verdict_from_declarations,
    verdict_from_membership,
)


def test_module_doctests_pass():
    result = doctest.testmod(cv, verbose=False)
    assert result.failed == 0, f"{result.failed} doctest failures"


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("liquid", "liquid", MATCH),
        ("LIQUID", "  liquid ", MATCH),          # casefold + strip
        ("pure_solid", "liquid", MISMATCH),
        ("pure_solid", None, UNDETERMINABLE),    # engine declares nothing
        (None, "liquid", UNDETERMINABLE),        # measurement declares nothing
        ("unstated", "liquid", UNDETERMINABLE),  # explicit non-declaration
        ("", "liquid", UNDETERMINABLE),
        (None, None, UNDETERMINABLE),
    ],
)
def test_declaration_pairs(left, right, expected):
    assert verdict_from_declarations(left, right) == expected


@pytest.mark.parametrize(
    "declared,expected",
    [
        ("silicate_melt", MATCH),
        ("Silicate_Melt", MATCH),                # capitalisation is a records
        ("  silicate_melt  ", MATCH),            # artefact, not a mismatch
        ("molten_metal", MISMATCH),
        ("some_unlisted_class", MISMATCH),       # declared-but-unlisted != unknown
        (None, UNDETERMINABLE),
        ("", UNDETERMINABLE),
    ],
)
def test_membership(declared, expected):
    assert verdict_from_membership(declared, {"silicate_melt"}) == expected


def test_unhashable_declaration_is_not_a_match():
    """A list/dict declaration must not raise, and must not pass."""

    assert verdict_from_membership([], {"silicate_melt"}) == MISMATCH
    assert verdict_from_membership({}, {"silicate_melt"}) == MISMATCH


def test_undeterminable_is_not_a_pass_and_not_a_fail():
    """The whole point of the third state.

    If someone collapses `undeterminable` into either boolean, this fails.
    """

    verdict = verdict_from_declarations("pure_solid", None)
    assert verdict == UNDETERMINABLE
    assert verdict != MATCH
    assert verdict != MISMATCH
    assert verdict in cv.COMPARABILITY_VERDICTS


def test_vocabulary_is_closed():
    assert cv.COMPARABILITY_VERDICTS == {MATCH, MISMATCH, UNDETERMINABLE}


def test_every_returned_verdict_is_in_the_closed_vocabulary():
    """COMPARABILITY_VERDICTS was defined and never enforced. Enforce it."""

    probes = [
        ("liquid", "liquid"), ("a", "b"), (None, "x"), ("x", None),
        ("unstated", "unstated"), ("", ""), (1, 1), (1, 2),
    ]
    for left, right in probes:
        assert verdict_from_declarations(left, right) in cv.COMPARABILITY_VERDICTS
    for declared in ("silicate_melt", "molten_metal", None, "", 7, [], {}):
        assert verdict_from_membership(declared, {"silicate_melt"}) in (
            cv.COMPARABILITY_VERDICTS
        )


def test_rail_verdict_separates_untyped_from_comparable():
    """b-190's site: the boolean says True for BOTH; the verdict must not.

    This is the defect in one assertion. `rail_system_class_comparability`
    deliberately keeps returning True for an untyped observation (silent
    exclusion would hide coverage drift), so the ONLY thing distinguishing
    "verified comparable" from "never typed" is this verdict.
    """

    from simulator.diagnostic_helpers.extract_reproduction import (
        RAIL_COMPARABLE_SYSTEM_CLASSES,
        RAIL_INCOMPARABLE_SYSTEM_CLASSES,
    )

    comparable = sorted(RAIL_COMPARABLE_SYSTEM_CLASSES)[0]
    incomparable = sorted(RAIL_INCOMPARABLE_SYSTEM_CLASSES)[0]

    assert verdict_from_membership(comparable, RAIL_COMPARABLE_SYSTEM_CLASSES) == MATCH
    assert (
        verdict_from_membership(incomparable, RAIL_COMPARABLE_SYSTEM_CLASSES)
        == MISMATCH
    )
    assert verdict_from_membership(None, RAIL_COMPARABLE_SYSTEM_CLASSES) == (
        UNDETERMINABLE
    )
