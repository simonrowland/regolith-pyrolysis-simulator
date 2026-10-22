"""Pins Haskin Table 1.8 values and migrates typed metal/glass analyses."""
from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import PhaseRole, ValueKind
from simulator.battery.migrate import migrate
from literature_extract_test_utils import isolated_root
from tests.helpers.corpus_paths import regolith_corpus_root

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/haskin-1990-lunar-molten-regolith-electrolysis-nasa-report.yaml"
PINS = {
    "haskin-1990-lunar-molten-regolith-electrolysis-nasa-report_a12_1_metal-1_fe": "92.13",
    "haskin-1990-lunar-molten-regolith-electrolysis-nasa-report_a12_1_glass_sio2": "49.48",
    "haskin-1990-lunar-molten-regolith-electrolysis-nasa-report_a12_1cb_glass_feo": "0.69",
}


def _assert_render_pins(document: dict, pins: dict[str, str] = PINS) -> None:
    rows = {
        row["observation_id"]: row
        for item in document["species"].values()
        for row in item.get("observations", [])
    }
    for observation_id, amount in pins.items():
        assert rows[observation_id]["values"]["component_amount"] == amount


def test_table1_8_render_pins_and_both_mutations_fail() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)
    changed_extract = copy.deepcopy(document)
    row = next(
        row for item in changed_extract["species"].values()
        for row in item.get("observations", [])
        if row["observation_id"] == "haskin-1990-lunar-molten-regolith-electrolysis-nasa-report_a12_1_metal-1_fe"
    )
    row["values"]["component_amount"] = "92.14"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_extract)
    changed_pin = dict(PINS)
    changed_pin["haskin-1990-lunar-molten-regolith-electrolysis-nasa-report_a12_1_glass_sio2"] = "49.49"
    with pytest.raises(AssertionError):
        _assert_render_pins(document, changed_pin)


def test_metal_and_glass_rows_migrate_as_points_with_temperature(tmp_path: Path) -> None:
    corpus = regolith_corpus_root()
    if not (corpus / "raw/haskin-1990-lunar-molten-regolith-electrolysis-nasa-report/haskin-1990-lunar-molten-regolith-electrolysis-nasa-report.pdf").is_file():
        pytest.skip("Haskin report PDF is absent")
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    result = migrate(isolated_root(tmp_path, document), write=False)
    rows = list(result.observations.values())
    assert len(rows) == 38
    assert all(row.value.kind is ValueKind.POINT and row.value.point is not None for row in rows)
    assert all(row.identity.temperature_K.value == Decimal("1623.15") for row in rows)
    assert all(row.identity.phase_role.value in (PhaseRole.METAL, PhaseRole.GLASS) for row in rows)
    assert all(row.identity.species.formula for row in rows)
    by_id = {row.observation_id.rsplit("::", 1)[1]: row for row in rows}
    metal = by_id["haskin-1990-lunar-molten-regolith-electrolysis-nasa-report_a12_1_metal-1_fe"]
    assert metal.identity.species.formula == "Fe"
    assert metal.identity.phase_role.value is PhaseRole.METAL
    assert metal.value.point == Decimal("92.13")
    glass = by_id["haskin-1990-lunar-molten-regolith-electrolysis-nasa-report_a12_1_glass_sio2"]
    assert glass.identity.species.formula == "SiO2"
    assert glass.identity.phase_role.value is PhaseRole.GLASS
    assert glass.value.point == Decimal("49.48")
