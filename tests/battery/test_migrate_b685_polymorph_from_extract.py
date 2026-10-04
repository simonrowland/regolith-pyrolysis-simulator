"""PIN b-685: polymorph_from_extract current stringify / unrecognised behaviour.

Captures tip behaviour before the typed-unknown accepting change. Never squash.
"""

from __future__ import annotations

from simulator.battery.enums import Phase, Polymorph
from simulator.battery.migrate import make_species, polymorph_from_extract
from simulator.battery.polymorph_dictionary import unrecognised_polymorph_spelling


_TYPED_UNKNOWN = {"tag": "unknown", "reason": "source does not state polymorph"}
_TYPED_NA = {"tag": "not_applicable", "reason": "not crystal"}
_REASON = "source does not state polymorph"


def test_pin_b685_typed_unknown_mapping_is_stringified_to_unrecognised() -> None:
    """Current defect: Mapping {tag: unknown, reason} becomes State.of(str(mapping))."""

    raw = polymorph_from_extract({"condensed_form": {"polymorph": dict(_TYPED_UNKNOWN)}})
    assert raw is not None
    assert raw.is_value
    assert raw.value == str(_TYPED_UNKNOWN)

    species = make_species("SiO2", Phase.CR, polymorph=raw)
    assert species.polymorph is not None
    assert species.polymorph.is_unknown
    assert species.polymorph.value is None
    reason = species.polymorph.reason or ""
    assert reason.startswith("unrecognised polymorph ")
    # Spelling recovers the stringified mapping (literal_eval of the quoted blob).
    spelling = unrecognised_polymorph_spelling(reason)
    # Spelling is the stringified mapping (str(dict)), not a re-parsed dict.
    assert spelling == str(_TYPED_UNKNOWN)


def test_pin_b685_typed_not_applicable_mapping_is_stringified_on_crystal() -> None:
    """Same stringify path for not_applicable mappings on a crystal phase."""

    raw = polymorph_from_extract({"condensed_form": {"polymorph": dict(_TYPED_NA)}})
    assert raw is not None
    assert raw.is_value
    assert raw.value == str(_TYPED_NA)

    species = make_species("MgO", Phase.CR, polymorph=raw)
    assert species.polymorph is not None
    assert species.polymorph.is_unknown
    assert "unrecognised polymorph" in (species.polymorph.reason or "")


def test_pin_b685_closed_token_string_still_coerces() -> None:
    """Closed plain-string tokens are unchanged by the reader path."""

    raw = polymorph_from_extract({"condensed_form": {"polymorph": "periclase"}})
    assert raw is not None
    assert raw.is_value
    assert raw.value == "periclase"

    species = make_species("MgO", Phase.CR, polymorph=raw)
    assert species.polymorph is not None
    assert species.polymorph.is_value
    assert species.polymorph.value is Polymorph.PERICLASE


def test_pin_b685_absent_or_empty_polymorph_returns_none() -> None:
    assert polymorph_from_extract({}) is None
    assert polymorph_from_extract({"condensed_form": {}}) is None
    assert polymorph_from_extract({"condensed_form": {"polymorph": ""}}) is None
    assert polymorph_from_extract({"condensed_form": {"polymorph": None}}) is None
