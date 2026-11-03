"""Render and migration pins for Matzen et al. (2017), Tables 1 and 2."""

from __future__ import annotations

import copy
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import PhaseRole, ValueKind
from simulator.battery.migrate import migrate
from literature_extract_test_utils import isolated_root
from tests.helpers.corpus_paths import regolith_corpus_root

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/matzen-2017-ni-olivine-composition.yaml"
PINS = {
    "matzen2017_run_49_glass_sio2": "48.71",
    "matzen2017_run_49_glass_tio2": "1.68",
    "matzen2017_run_49_glass_nio": "0.070",
    "matzen2017_run_70_glass_mgo": "21.89",
}
COMPOSITION_UNKNOWN_REASON = (
    "This paper does not print the full starting melt composition for this run. "
    "The Methods section says it uses starting materials described in Matzen et al. "
    "(2013), Table 1. The approximately 12, 15, and 21 wt.% MgO values are target "
    "liquid compositions only; Table 2 reports analyzed run-product phase "
    "compositions, which are this observation's output rather than its starting input."
)
COMPOSITION_UNKNOWN_PINS = (
    "matzen2017_run_49_glass_sio2",
    "matzen2017_run_59_glass_mgo",
    "matzen2017_run_65_glass_nio",
    "matzen2017_run_70_glass_mgo",
)


def _assert_render_pins(document: dict, pins: dict[str, str] = PINS) -> None:
    observations = {
        row["observation_id"]: row
        for item in document["species"].values()
        for row in item.get("observations", [])
    }
    for observation_id, amount in pins.items():
        assert observations[observation_id]["values"]["component_amount"] == amount


def _assert_composition_unknown_pins(document: dict) -> None:
    observations = {
        row["observation_id"]: row
        for item in document["species"].values()
        for row in item.get("observations", [])
    }
    for observation_id in COMPOSITION_UNKNOWN_PINS:
        composition = observations[observation_id]["values"]["composition"]
        assert composition["state"] == {
            "tag": "unknown",
            "reason": COMPOSITION_UNKNOWN_REASON,
        }
        assert composition["locator"] == {"page": 5, "pdf_page_index": 4}


def test_matzen_render_and_composition_pins_fail_when_mutated() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)
    _assert_composition_unknown_pins(document)

    changed_extract = copy.deepcopy(document)
    row = next(
        row
        for item in changed_extract["species"].values()
        for row in item.get("observations", [])
        if row["observation_id"] == "matzen2017_run_49_glass_sio2"
    )
    row["values"]["component_amount"] = "48.72"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_extract)

    changed_render_pin = dict(PINS, matzen2017_run_49_glass_sio2="48.72")
    with pytest.raises(AssertionError):
        _assert_render_pins(document, changed_render_pin)

    changed_composition = copy.deepcopy(document)
    composition_row = next(
        row
        for item in changed_composition["species"].values()
        for row in item.get("observations", [])
        if row["observation_id"] == "matzen2017_run_59_glass_mgo"
    )
    del composition_row["values"]["composition"]
    with pytest.raises((AssertionError, KeyError)):
        _assert_composition_unknown_pins(changed_composition)


def test_matzen_rows_migrate_as_points_with_run_axes(tmp_path: Path) -> None:
    corpus = regolith_corpus_root()
    if not (corpus / "raw/matzen-2017-ni-olivine-composition/matzen-2017-ni-olivine-composition.pdf").is_file():
        pytest.skip("Matzen 2017 source PDF is absent")

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    result = migrate(isolated_root(tmp_path, document), write=False)
    rows = list(result.observations.values())
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert all(row.value.kind is ValueKind.POINT for row in rows)
    assert all(row.value.point is not None for row in rows)
    assert all(
        row.identity.temperature_K is not None
        and row.identity.temperature_K.is_value
        and row.identity.temperature_K.value is not None
        for row in rows
    )
    assert all(
        (condition := row.point_conditions.get("total_pressure_Pa")) is not None
        and condition.state.is_value
        and condition.locator is not None
        for row in rows
    )
    assert all(
        row.identity.composition is not None
        and row.identity.composition.is_unknown
        and row.identity.composition.reason == COMPOSITION_UNKNOWN_REASON
        for row in rows
    )

    by_id = {row.observation_id.rsplit("::", 1)[1]: row for row in rows}
    sio2 = by_id["matzen2017_run_49_glass_sio2"]
    assert sio2.identity.species.formula == "SiO2"
    assert sio2.identity.phase_role.value is PhaseRole.GLASS
    assert sio2.identity.temperature_K.value == Decimal("1703.15")
    assert sio2.point_conditions["total_pressure_Pa"].state.value == Decimal("1500000000")
    assert sio2.value.point == Decimal("48.71")

    for observation_id in COMPOSITION_UNKNOWN_PINS:
        composition = by_id[observation_id].identity.composition
        assert composition is not None and composition.is_unknown
        assert composition.reason == COMPOSITION_UNKNOWN_REASON

    nio = by_id["matzen2017_run_65_glass_nio"]
    assert nio.identity.species.formula == "NiO"
    assert nio.point_conditions["fO2_log"].state.value == Decimal("-8.63")
    assert nio.point_conditions["total_pressure_Pa"].state.value == Decimal("100000")
