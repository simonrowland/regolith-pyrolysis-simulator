"""Render-grid pins and migration checks for Cacciatore and Zajacz (2026)."""
from __future__ import annotations

import copy
import csv
import os
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import PhaseRole, ValueKind
from simulator.battery.migrate import migrate
from literature_extract_test_utils import isolated_root

ROOT = Path(__file__).resolve().parents[1]
SID = "cacciatore-zajacz-2026-v-sc-oxybarometer"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
GRID = ROOT / f"tests/fixtures/{SID}-table3.csv"
DEFAULT_CORPUS = Path("/Users/simonrowland/Repos/regolith-corpus")


def _rows(document: dict) -> dict[str, dict]:
    return {
        observation["observation_id"]: observation
        for item in document["species"].values()
        for observation in item.get("observations", [])
    }


def _assert_table3_grid(document: dict, grid: list[dict[str, str]]) -> None:
    observations = _rows(document)
    for expected in grid:
        observation_id = (
            f"{SID}_{expected['sample'].lower()}_glass_{expected['component'].lower()}"
        )
        values = observations[observation_id]["values"]
        assert values["component_amount"] == expected["value"]
        assert values["sigma"] == expected["sigma"]


def test_table3_render_grid_and_both_mutations_fail() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    with GRID.open(encoding="utf-8", newline="") as handle:
        grid = list(csv.DictReader(handle))
    assert len(grid) == 682
    _assert_table3_grid(document, grid)

    changed_extract = copy.deepcopy(document)
    observation = _rows(changed_extract)[
        f"{SID}_e1_0_ii_h2_a_f_glass_sio2"
    ]
    observation["values"]["component_amount"] = "52.09"
    with pytest.raises(AssertionError):
        _assert_table3_grid(changed_extract, grid)

    changed_grid = copy.deepcopy(grid)
    changed_grid[0]["value"] = "52.09"
    with pytest.raises(AssertionError):
        _assert_table3_grid(document, changed_grid)


def test_table3_glass_rows_migrate_with_sample_conditions(tmp_path: Path) -> None:
    corpus = Path(os.environ.get("REGOLITH_CORPUS_ROOT", DEFAULT_CORPUS))
    if not (corpus / f"raw/{SID}/{SID}.pdf").is_file():
        pytest.skip("Cacciatore and Zajacz source PDF is absent")

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    with GRID.open(encoding="utf-8", newline="") as handle:
        grid = list(csv.DictReader(handle))
    result = migrate(isolated_root(tmp_path, document), write=False)
    observations = list(result.observations.values())
    assert len(observations) == 682
    assert all(row.value.kind is ValueKind.POINT and row.value.point is not None for row in observations)

    expected_by_id = {
        f"{SID}_{item['sample'].lower()}_glass_{item['component'].lower()}": item
        for item in grid
    }
    for row in observations:
        observation_id = row.observation_id.rsplit("::", 1)[1]
        expected = expected_by_id[observation_id]
        assert row.identity.species.formula == expected["component"]
        assert row.identity.phase_role.value is PhaseRole.GLASS
        assert row.identity.temperature_K.value == Decimal(expected["T_C"]) + Decimal("273.15")
        assert row.point_conditions["fO2_log"].state.value == Decimal(expected["logfO2_bar"])
        assert row.point_conditions["total_pressure_Pa"].state.value == Decimal(expected["P_bar"]) * Decimal("100000")
        assert row.value.point == Decimal(expected["value"])


def test_table3_source_test_skips_when_corpus_is_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(tmp_path / "absent-corpus"))
    with pytest.raises(pytest.skip.Exception):
        test_table3_glass_rows_migrate_with_sample_conditions(tmp_path)
