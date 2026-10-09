"""b-685: polymorph_from_extract accepts typed-unknown; closed tokens unchanged.

PIN commit 6ec189d50 captured the pre-fix stringify/unrecognised behaviour.
This file now asserts the accepting contract.
"""

from __future__ import annotations

from simulator.battery.enums import Phase, Polymorph, StateTag
from simulator.battery.migrate import make_species, polymorph_from_extract
from simulator.battery.polymorph_dictionary import unrecognised_polymorph_spelling


_TYPED_UNKNOWN = {"tag": "unknown", "reason": "source does not state polymorph"}
_TYPED_NA = {"tag": "not_applicable", "reason": "not crystal"}
_REASON = "source does not state polymorph"


def test_b685_typed_unknown_mapping_is_accepted() -> None:
    """Typed-unknown Mapping is State.unknown(reason), not an unrecognised blob."""

    raw = polymorph_from_extract({"condensed_form": {"polymorph": dict(_TYPED_UNKNOWN)}})
    assert raw is not None
    assert raw.is_unknown
    assert raw.value is None
    assert raw.reason == _REASON
    assert raw.tag is StateTag.UNKNOWN
    assert unrecognised_polymorph_spelling(raw.reason) is None

    species = make_species("SiO2", Phase.CR, polymorph=raw)
    assert species.polymorph is not None
    assert species.polymorph.is_unknown
    assert species.polymorph.reason == _REASON
    assert "unrecognised polymorph" not in (species.polymorph.reason or "")


def test_b685_typed_not_applicable_mapping_is_accepted_on_crystal() -> None:
    raw = polymorph_from_extract({"condensed_form": {"polymorph": dict(_TYPED_NA)}})
    assert raw is not None
    assert raw.is_not_applicable
    assert raw.reason == "not crystal"

    species = make_species("MgO", Phase.CR, polymorph=raw)
    assert species.polymorph is not None
    assert species.polymorph.is_not_applicable
    assert species.polymorph.reason == "not crystal"


def test_b685_typed_value_mapping_coerces_closed_token() -> None:
    raw = polymorph_from_extract(
        {"condensed_form": {"polymorph": {"tag": "value", "value": "periclase"}}}
    )
    assert raw is not None
    assert raw.is_value
    assert raw.value is Polymorph.PERICLASE

    species = make_species("MgO", Phase.CR, polymorph=raw)
    assert species.polymorph is not None
    assert species.polymorph.is_value
    assert species.polymorph.value is Polymorph.PERICLASE


def test_b685_closed_token_string_still_coerces() -> None:
    """Closed plain-string tokens are unchanged by the reader path."""

    raw = polymorph_from_extract({"condensed_form": {"polymorph": "periclase"}})
    assert raw is not None
    assert raw.is_value
    assert raw.value == "periclase"

    species = make_species("MgO", Phase.CR, polymorph=raw)
    assert species.polymorph is not None
    assert species.polymorph.is_value
    assert species.polymorph.value is Polymorph.PERICLASE


def test_b685_unrecognised_value_mapping_degrades() -> None:
    raw = polymorph_from_extract(
        {"condensed_form": {"polymorph": {"tag": "value", "value": "not-a-real-form"}}}
    )
    assert raw is not None
    assert raw.is_unknown
    assert unrecognised_polymorph_spelling(raw.reason) == "not-a-real-form"


def test_b685_absent_or_empty_polymorph_returns_none() -> None:
    assert polymorph_from_extract({}) is None
    assert polymorph_from_extract({"condensed_form": {}}) is None
    assert polymorph_from_extract({"condensed_form": {"polymorph": ""}}) is None
    assert polymorph_from_extract({"condensed_form": {"polymorph": None}}) is None
