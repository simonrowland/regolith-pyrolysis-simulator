from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.battery.test_migrate import _migrate_real_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/cheng-2021-fe-ca-si-o-phase-equilibria.yaml"


def _document() -> dict:
    loaded = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _observation(document: dict, observation_id: str) -> dict:
    matches = [
        observation
        for block in document["species"].values()
        for observation in block.get("observations", [])
        if observation.get("observation_id") == observation_id
    ]
    assert len(matches) == 1
    return matches[0]


RENDER_PINS = (
    # 600 dpi PDF p. 16, Table 5, experiment 1 liquid.
    ("cheng21_t5_e001_liq_feo1p5", 87.5, 16),
    # 600 dpi PDF p. 20, Table 5, experiment 98 C2F solid.
    ("cheng21_t5_e098_c2f_feo1p5", 49.5, 20),
    # 600 dpi PDF p. 22, Table 5, experiment 119 Liq 1.
    ("cheng21_t5_e119_liq_1_sio2", 65.5, 22),
    # 600 dpi PDF p. 11, Table 3, initial mixture A.
    ("cheng21_table3_a_liq_cao", 27.6, 11),
)


def _assert_render_pins(document: dict) -> None:
    for observation_id, expected, pdf_page in RENDER_PINS:
        observation = _observation(document, observation_id)
        assert observation["values"]["value"] == expected
        assert observation["locator"]["page"] == pdf_page


def test_cheng_table_rows_and_600_dpi_render_pins() -> None:
    document = _document()
    _assert_render_pins(document)

    table5_rows = {
        observation["values"]["phase_row_id"]
        for block in document["species"].values()
        for observation in block["observations"]
        if observation["observation_id"].startswith("cheng21_t5_")
        and "phase_row_id" in observation["values"]
    }
    assert len(table5_rows) == 309  # 306 numeric phase rows and 3 printed n/a rows.
    numeric_table5_rows = {
        observation["values"]["phase_row_id"]
        for block in document["species"].values()
        for observation in block["observations"]
        if observation["observation_id"].startswith("cheng21_t5_")
        and "phase_row_id" in observation["values"]
        and "composition_mol" in observation["values"]
    }
    assert len(numeric_table5_rows) == 306

    for block in document["species"].values():
        for observation in block["observations"]:
            if observation.get("uncertainty"):
                assert float(observation["uncertainty"]["value"]) > 0

    paired = [
        observation
        for block in document["species"].values()
        for observation in block["observations"]
        if observation["observation_id"].startswith("cheng21_t5_e119_")
        and observation["phase"] in {"Liq 1", "Liq 2"}
    ]
    assert {observation["phase"] for observation in paired} == {"Liq 1", "Liq 2"}
    assert {
        observation["point_conditions"]["tie_line_id"]["state"]["value"]
        for observation in paired
    } == {"cheng21_t5_e119::tie-line"}


def test_cheng_render_pin_rejects_a_changed_cell() -> None:
    document = deepcopy(_document())
    _observation(document, "cheng21_t5_e001_liq_feo1p5")["values"]["value"] = 87.4
    with pytest.raises(AssertionError):
        _assert_render_pins(document)


def test_cheng_isolated_migration_carries_temperature_and_conditions(tmp_path: Path) -> None:
    result = _migrate_real_extract(
        tmp_path, "cheng-2021-fe-ca-si-o-phase-equilibria.yaml"
    )
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.observations) == 990
    assert sum(
        observation.value.kind.value == "unavailable"
        for observation in result.observations.values()
    ) == 3

    for observation in result.observations.values():
        conditions = observation.point_conditions
        assert conditions is not None
        for key in ("temperature_K", "fO2_Pa"):
            assert conditions[key].state.is_value
            assert conditions[key].locator is not None
