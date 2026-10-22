"""Independent text/render pins and migration checks for Borisov and Borisovskiy (2024)."""
from __future__ import annotations

import copy
import os
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import PhaseRole, ValueKind
from simulator.battery.migrate import migrate
from literature_extract_test_utils import isolated_root

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/borisov-borisovskiy-2024-ti-zircon.yaml"
DEFAULT_CORPUS = Path("/Users/simonrowland/Repos/regolith-corpus")
PINS = {
    "borisov-borisovskiy-2024-ti-zircon_t8_16_glass_sio2": "56.28",
    "borisov-borisovskiy-2024-ti-zircon_t15_12_glass_tio2": "15.24",
    "borisov-borisovskiy-2024-ti-zircon_t25_17_glass_zro2": "6.71",
}


def _assert_render_pins(document: dict, pins: dict[str, str] = PINS) -> None:
    rows = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }
    for observation_id, value in pins.items():
        assert rows[observation_id]["values"]["component_amount"] == value


def test_table1_render_pins_and_both_mutations_fail() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)

    changed_extract = copy.deepcopy(document)
    row = next(
        row
        for species in changed_extract["species"].values()
        for row in species.get("observations", [])
        if row["observation_id"] == "borisov-borisovskiy-2024-ti-zircon_t15_12_glass_tio2"
    )
    row["values"]["component_amount"] = "15.25"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_extract)

    changed_pin = dict(PINS, **{"borisov-borisovskiy-2024-ti-zircon_t8_16_glass_sio2": "56.29"})
    with pytest.raises(AssertionError):
        _assert_render_pins(document, changed_pin)


def test_glass_rows_migrate_as_points_with_printed_conditions(tmp_path: Path) -> None:
    corpus = Path(os.environ.get("REGOLITH_CORPUS_ROOT", DEFAULT_CORPUS))
    if not (corpus / "raw/borisov-borisovskiy-2024-ti-zircon/borisov-borisovskiy-2024-ti-zircon.pdf").is_file():
        pytest.skip("Borisov and Borisovskiy source PDF is absent")

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    result = migrate(isolated_root(tmp_path, document), write=False)
    rows = list(result.observations.values())
    assert len(rows) == 198
    assert all(row.value.kind is ValueKind.POINT and row.value.point is not None for row in rows)
    assert all(row.identity.temperature_K.value is not None for row in rows)
    assert all(row.point_conditions["total_pressure_Pa"].state.value == Decimal("101325") for row in rows)
    assert all(row.point_conditions["atmosphere"].state.value == "air" for row in rows)
    formula_by_id = {"sio2": "SiO2", "tio2": "TiO2", "al2o3": "Al2O3", "mgo": "MgO", "cao": "CaO", "zro2": "ZrO2"}
    for row in rows:
        expected_formula = formula_by_id[row.observation_id.rsplit("_glass_", 1)[1]]
        assert row.identity.species.formula == expected_formula
    by_id = {row.observation_id.rsplit("::", 1)[1]: row for row in rows}
    row = by_id["borisov-borisovskiy-2024-ti-zircon_t15_12_glass_tio2"]
    assert row.identity.species.formula == "TiO2"
    assert row.identity.phase_role.value is PhaseRole.GLASS
    assert row.identity.temperature_K.value == Decimal("1575.15")
    assert row.point_conditions["total_pressure_Pa"].state.value == Decimal("101325")
    assert row.value.point == Decimal("15.24")
