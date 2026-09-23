"""Converted values keep their number and carry the printed original.

A Celsius reading the loader turns into kelvin, an extractor Kelvin twin of
that reading, and a hand conversion recorded on a located value are derived.
A printed number is not restamped, and a note whose arithmetic does not
reproduce the stored number is not given a conversion.
"""

from __future__ import annotations

import copy
import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import (
    AXIS_TEMPERATURE_K,
    REPO_ROOT,
    _located_from_plain,
    _value_or_point_from_plain,
    migrate,
    select_declared_source,
)
from simulator.battery.records import as_decimal
from simulator.battery.waypoints import WaypointAuthority, pressure_boundary
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree

_CONVERSION_NOTE = re.compile(r"unit conversion|\bconverted\b", re.IGNORECASE)


def _magnitudes(state_value: object) -> list[Decimal] | None:
    try:
        if isinstance(state_value, dict):
            kind = state_value.get("kind")
            if kind == "point":
                return [Decimal(str(state_value["point"]))]
            if kind == "interval":
                return [
                    Decimal(str(state_value["interval_low"])),
                    Decimal(str(state_value["interval_high"])),
                ]
            if kind == "bound":
                return [Decimal(str(state_value["bound_value"]))]
            return None
        if isinstance(state_value, (str, int, float, Decimal)):
            return [Decimal(str(state_value))]
    except (ArithmeticError, ValueError, KeyError):
        return None
    return None


def test_celsius_only_point_keeps_kelvin_and_records_the_printed_degree() -> None:
    selection = select_declared_source(AXIS_TEMPERATURE_K, None, {"T_C": 1600})
    assert selection.amount == Decimal("1873.15")
    assert selection.unit_trail == "celsius_to_kelvin"
    assert selection.field_name == "T_C"


def test_exact_kelvin_twin_uses_the_printed_celsius() -> None:
    selection = select_declared_source(
        AXIS_TEMPERATURE_K, None, {"T_K": "1573.15", "T_C": 1300}
    )
    assert selection.amount == Decimal("1573.15")
    assert selection.unit_trail == "celsius_to_kelvin"
    assert selection.field_name == "T_C"


def test_author_plus_273_convention_is_not_rewritten() -> None:
    """1370 C with a stored 1643 K is the author's +273, not +273.15."""

    selection = select_declared_source(
        AXIS_TEMPERATURE_K, None, {"T_K": "1643.0", "T_C": 1370}
    )
    assert selection.amount == Decimal("1643.0")
    assert selection.field_name == "T_K"
    assert selection.unit_trail.startswith("identity")


def test_published_kelvin_is_not_demoted_by_a_celsius_twin() -> None:
    selection = select_declared_source(
        AXIS_TEMPERATURE_K,
        None,
        {"T_K_as_published": 2000, "T_C": Decimal("1726.85")},
    )
    assert selection.amount == Decimal("2000")
    assert selection.field_name == "T_K_as_published"
    assert selection.unit_trail.startswith("identity")


def _observation(values: dict) -> dict:
    extract = copy.deepcopy(FIXTURE_EXTRACT)
    row = extract["species"]["Na"]["observations"][0]
    row["values"] = values
    row["units"] = "K"
    extract["fidelity_samples"] = []
    return extract


def test_parent_celsius_lands_as_a_conversion(tmp_path: Path) -> None:
    extract = _observation(
        {
            "quantity": "pure_Psat",
            "method_class": "measured_direct",
            "admission_status": "admitted",
            "T_C": 1600,
            "P_atm": 1,
        }
    )
    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    located = result.observations["fixture-source::na_psat"].point_conditions["temperature_K"]
    assert located.state.value == Decimal("1873.15")
    assert located.inference is not None
    assert located.inference.relation == "celsius_to_kelvin"
    assert dict(located.inference.parameters)["original"].state.value == Decimal("1600")


def test_series_kelvin_twin_lands_as_a_conversion(tmp_path: Path) -> None:
    extract = copy.deepcopy(FIXTURE_EXTRACT)
    row = extract["species"]["Na"]["observations"][0]
    row["values"]["series"] = [{"T_C": 1300, "T_K": "1573.15", "pressure_atm": 1}]
    row["fidelity_samples"] = extract["fidelity_samples"]
    extract["fidelity_samples"][0]["value"] = [{"T_C": 1300, "T_K": "1573.15", "pressure_atm": 1}]
    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    point = next(
        obs
        for oid, obs in result.observations.items()
        if oid.startswith("fixture-source::na_psat::")
    )
    located = point.point_conditions["temperature_K"]
    assert located.state.value == Decimal("1573.15")
    assert located.inference is not None
    assert located.inference.relation == "celsius_to_kelvin"
    original = dict(located.inference.parameters)["original"].state.value
    assert original == Decimal("1300")


def test_series_plus_273_keeps_the_stored_kelvin(tmp_path: Path) -> None:
    extract = copy.deepcopy(FIXTURE_EXTRACT)
    extract["species"]["Na"]["observations"][0]["values"]["series"] = [
        {"T_C": 1370, "T_K": "1643.0", "pressure_atm": 1}
    ]
    extract["fidelity_samples"][0]["value"] = [
        {"T_C": 1370, "T_K": "1643.0", "pressure_atm": 1}
    ]
    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    point = next(
        obs
        for oid, obs in result.observations.items()
        if oid.startswith("fixture-source::na_psat::")
    )
    located = point.point_conditions["temperature_K"]
    assert located.state.value == Decimal("1643.0")
    assert located.inference is None


def _located(value: object, note: str, **extra: object):
    payload = {
        "state": {"tag": "value", "value": value},
        "locator": {"page": 3, "note": note},
    }
    payload.update(extra)
    cast = _value_or_point_from_plain if isinstance(value, dict) else as_decimal
    return _located_from_plain(payload, cast)


def test_bar_note_records_the_printed_pressure_without_changing_pascals() -> None:
    located = _located(
        {"kind": "point", "point": "30000"},
        "Printed total pressure 0.3 bar; unit conversion only. H2 partial pressure is not added to derive a total.",
    )
    assert located.state.value.point == Decimal("30000")
    assert located.inference is not None
    assert located.inference.relation == "bar_to_Pa"
    assert dict(located.inference.parameters)["original"].state.value == Decimal("0.3")


def test_note_that_does_not_reproduce_the_number_stays_printed() -> None:
    """10^-6 mbar is 0.0001 Pa. The stored 0.1 Pa is not that conversion."""

    located = _located(
        {"kind": "point", "point": "0.1"},
        "Printed 10^-6 mbar; unit conversion only",
    )
    assert located.state.value.point == Decimal("0.1")
    assert located.inference is None


def test_already_si_note_stays_printed() -> None:
    located = _located(
        {"kind": "point", "point": "250"},
        "Each temperature held 250 s; unit conversion only",
    )
    assert located.state.value.point == Decimal("250")
    assert located.inference is None


def test_interval_note_records_both_printed_ends() -> None:
    located = _located(
        {"kind": "interval", "interval_low": "0.000005", "interval_high": "0.00004"},
        "Printed approximately 5-40 mg; unit conversion only.",
    )
    assert located.state.value.interval_low == Decimal("0.000005")
    assert located.state.value.interval_high == Decimal("0.00004")
    assert located.inference is not None
    assert located.inference.relation == "mg_to_kg"
    params = dict(located.inference.parameters)
    assert params["original_low"].state.value == Decimal("5")
    assert params["original_high"].state.value == Decimal("40")


def test_structured_as_published_builds_the_conversion() -> None:
    located = _located(
        {"kind": "point", "point": "0.097"},
        "unstructured aside",
        as_published={"value": "97", "units": "mm"},
    )
    assert located.state.value.point == Decimal("0.097")
    assert located.inference is not None
    assert located.inference.relation == "mm_to_m"
    assert dict(located.inference.parameters)["original"].state.value == Decimal("97")


def test_structured_as_published_that_disagrees_is_refused() -> None:
    with pytest.raises(ValueError, match="does not reproduce"):
        _located(
            {"kind": "point", "point": "0.097"},
            "Printed 97 mm ID; unit conversion only",
            as_published={"value": "96", "units": "mm"},
        )


def test_converted_run_pressure_is_not_stamped_printed() -> None:
    from dataclasses import replace

    from simulator.battery.records import Bench, BenchIdentity, BenchIdentityBasis
    from tests.battery import factories

    pressure = _located(
        {"kind": "point", "point": "30000"},
        "Printed total pressure 0.3 bar; unit conversion only.",
    )
    assert pressure.state.value.point == Decimal("30000")
    assert pressure.inference is not None
    experiment = replace(
        factories.tabulation_experiment(),
        pressure_environment=replace(
            factories.tabulation_experiment().pressure_environment,
            total_pressure_Pa=pressure,
        ),
    )
    bench = Bench(
        id="work-1::bench::one",
        work_id="work-1",
        identity=BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),
    )
    result = pressure_boundary(experiment, bench)
    assert result.selected is not None
    assert result.selected.route == "printed_run_pressure"
    assert result.selected.authority is WaypointAuthority.DERIVED
    assert result.selected.value.point == Decimal("30000")


def test_works_conversion_notes_keep_every_stored_number() -> None:
    """Every hand-converted works value either gains a checked derivation or stays printed.

    Identity notes (the printed unit is already the stored unit) and notes whose
    arithmetic does not reproduce the stored number stay printed. Nothing else
    in this set may remain a printed conversion, and no magnitude may move.
    """

    works = REPO_ROOT / "data" / "literature" / "works"
    recovered = 0
    left = []
    seen = 0
    for path in sorted(works.glob("*.yaml")):
        if path.name == "ALIASES.yaml":
            continue
        text = path.read_text(encoding="utf-8")
        if not _CONVERSION_NOTE.search(text):
            continue
        doc = yaml.safe_load(text)

        def walk(node: object) -> None:
            nonlocal recovered, seen
            if isinstance(node, dict):
                state = node.get("state")
                locator = node.get("locator")
                note = str((locator or {}).get("note") or "") if isinstance(locator, dict) else ""
                if (
                    isinstance(state, dict)
                    and state.get("tag") == "value"
                    and _CONVERSION_NOTE.search(note)
                ):
                    seen += 1
                    before = _magnitudes(state.get("value"))
                    if before is None:
                        left.append((path.name, note[:80]))
                        return
                    raw_value = state.get("value")
                    cast = (
                        _value_or_point_from_plain
                        if isinstance(raw_value, dict)
                        else as_decimal
                    )
                    located = _located_from_plain(node, cast)
                    after = located.state.value
                    if before is not None:
                        if getattr(after, "kind", None) == ValueKind.POINT:
                            after_amounts = [after.point]
                        elif getattr(after, "kind", None) == ValueKind.INTERVAL:
                            after_amounts = [after.interval_low, after.interval_high]
                        elif getattr(after, "kind", None) == ValueKind.BOUND:
                            after_amounts = [after.bound_value]
                        else:
                            after_amounts = [after]
                        assert after_amounts == before, (path.name, note, before, after_amounts)
                    if located.inference is not None and not str(located.inference.relation).startswith(
                        "identity"
                    ):
                        recovered += 1
                    else:
                        left.append((path.name, note[:80]))
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(doc)
    assert seen > 0
    assert recovered > 0
    assert recovered + len(left) == seen
