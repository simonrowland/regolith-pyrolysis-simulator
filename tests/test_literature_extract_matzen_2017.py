"""Render and migration pins for Matzen et al. (2017), Tables 1 and 2."""

from __future__ import annotations

import copy
import os
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import PhaseRole, ValueKind
from simulator.battery.migrate import migrate
from literature_extract_test_utils import isolated_root

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/matzen-2017-ni-olivine-composition.yaml"
DEFAULT_CORPUS = Path("/Users/simonrowland/Repos/regolith-corpus")
PINS = {
    "matzen2017_run_49_glass_sio2": "48.71",
    "matzen2017_run_49_glass_tio2": "1.68",
    "matzen2017_run_49_glass_nio": "0.070",
    "matzen2017_run_70_glass_mgo": "21.89",
}


def _assert_render_pins(document: dict, pins: dict[str, str] = PINS) -> None:
    observations = {
        row["observation_id"]: row
        for item in document["species"].values()
        for row in item.get("observations", [])
    }
    for observation_id, amount in pins.items():
        assert observations[observation_id]["values"]["component_amount"] == amount


def test_matzen_table_2_render_pins_and_both_mutations_fail() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)

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


def test_matzen_rows_migrate_as_points_with_run_axes(tmp_path: Path) -> None:
    corpus = Path(os.environ.get("REGOLITH_CORPUS_ROOT", DEFAULT_CORPUS))
    if not (corpus / "raw/matzen-2017-ni-olivine-composition/matzen-2017-ni-olivine-composition.pdf").is_file():
        pytest.skip("Matzen 2017 source PDF is absent")

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    result = migrate(isolated_root(tmp_path, document), write=False)
    rows = list(result.observations.values())
    assert len(rows) == 180
    assert all(row.value.kind is ValueKind.POINT for row in rows)
    assert all(row.value.point is not None for row in rows)

    by_id = {row.observation_id.rsplit("::", 1)[1]: row for row in rows}
    sio2 = by_id["matzen2017_run_49_glass_sio2"]
    assert sio2.identity.species.formula == "SiO2"
    assert sio2.identity.phase_role.value is PhaseRole.GLASS
    assert sio2.identity.temperature_K.value == Decimal("1703.15")
    assert sio2.point_conditions["total_pressure_Pa"].state.value == Decimal("1500000000")
    assert sio2.value.point == Decimal("48.71")

    nio = by_id["matzen2017_run_65_glass_nio"]
    assert nio.identity.species.formula == "NiO"
    assert nio.point_conditions["fO2_log"].state.value == Decimal("-8.63")
    assert nio.point_conditions["total_pressure_Pa"].state.value == Decimal("100000")
