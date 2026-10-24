"""Render-grid pins and migration checks for Cacciatore and Zajacz (2026)."""
from __future__ import annotations

import copy
import csv
import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import PhaseRole, ValueKind
from simulator.battery.migrate import migrate
from literature_extract_test_utils import isolated_root
from tests.helpers.corpus_paths import regolith_corpus_root

ROOT = Path(__file__).resolve().parents[1]
SID = "cacciatore-zajacz-2026-v-sc-oxybarometer"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
SUPPLEMENT_EXTRACT = ROOT / f"data/literature/extracts/{SID}-supplement.yaml"
GRID = ROOT / f"tests/fixtures/{SID}-table3.csv"


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


def _assert_fO2_control_glosses(document: dict) -> None:
    for observation in _rows(document).values():
        experiment = observation["experiment"].lower()
        atmosphere = observation["values"]["point_conditions"]["atmosphere"]["state"]["value"]
        if "-h2l-" in experiment:
            assert atmosphere == "H2L (hydrogen loading)"
        elif "-h2-" in experiment:
            assert atmosphere == "H2 (hydrogen membrane)"
        elif "-sb-" in experiment:
            assert atmosphere == "solid buffer"
        else:
            raise AssertionError(f"unrecognised fO2 control in {experiment}")


def _series_letters(series: str) -> tuple[str, ...] | None:
    return {
        "A&B": ("a", "b"),
        "A": ("a",),
        "B": ("b",),
        "A_2": ("a2",),
        "B_2": ("b2",),
    }.get(series)


def _run_key(observation: dict) -> tuple[str, str]:
    experiment = observation["experiment"].removeprefix(f"{SID}-")
    suffix = re.search(r"([ab])2?(-f)?$", experiment)
    assert suffix, experiment
    letter = suffix.group(1) + ("2" if experiment.endswith("2-f") else "")
    sample = re.sub(r"-[ab]2?(-f)?$", "", experiment).replace("-", "_").lower()
    if experiment.endswith("-f"):
        sample += "_f"
    return sample, letter


def _assert_fO2_log_uncertainties(document: dict, supplement: dict) -> None:
    table = next(
        context
        for item in supplement["species"].values()
        for context in item.get("context", [])
        if context.get("locator", {}).get("table") == "Table_2_Exp_cond"
    )
    uncertainty_by_run = {}
    for record in table["values"]["rows"][1:]:
        cells = record["cells"]
        series = _series_letters(cells.get("B"))
        if series:
            for letter in series:
                uncertainty_by_run[(cells["A"].lower(), letter)] = cells["J"]

    for observation in _rows(document).values():
        expected = uncertainty_by_run[_run_key(observation)]
        note = observation["values"]["point_conditions"]["fO2_log"]["state"]["note"]
        assert f"±{expected} uncertainty" in note, observation["observation_id"]


def _assert_pressure_uncertainties(document: dict, supplement: dict) -> None:
    table = next(
        context
        for item in supplement["species"].values()
        for context in item.get("context", [])
        if context.get("locator", {}).get("table") == "Table_2_Exp_cond"
    )
    uncertainty_by_run = {}
    for record in table["values"]["rows"][1:]:
        cells = record["cells"]
        if cells.get("I") != "nd":
            continue
        series = _series_letters(cells.get("B"))
        if series:
            for letter in series:
                uncertainty_by_run[(cells["A"].lower(), letter)] = (
                    cells["L"],
                    cells["M"],
                )

    checked = 0
    for observation in _rows(document).values():
        expected = uncertainty_by_run.get(_run_key(observation))
        if expected is None:
            continue
        pressure_uncertainty, duration = expected
        note = observation["values"]["point_conditions"]["total_pressure_Pa"]["state"]["note"]
        match = re.search(r"±([\d.]+) bar", note)
        assert match and match.group(1) == pressure_uncertainty, observation["observation_id"]
        assert match.group(1) != duration, observation["observation_id"]
        checked += 1

    assert checked == 231


def test_table3_render_grid_and_both_mutations_fail() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    with GRID.open(encoding="utf-8", newline="") as handle:
        grid = list(csv.DictReader(handle))
    assert len(grid) == 682
    _assert_table3_grid(document, grid)
    _assert_fO2_control_glosses(document)
    supplement = yaml.safe_load(SUPPLEMENT_EXTRACT.read_text(encoding="utf-8"))
    _assert_fO2_log_uncertainties(document, supplement)
    _assert_pressure_uncertainties(document, supplement)

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

    changed_control = copy.deepcopy(document)
    _rows(changed_control)[f"{SID}_e1_0_ii_h2_a_f_glass_sio2"]["values"]["point_conditions"]["atmosphere"]["state"]["value"] = "H2 (hydrogen loading)"
    with pytest.raises(AssertionError):
        _assert_fO2_control_glosses(changed_control)
    changed_h2l = copy.deepcopy(document)
    _rows(changed_h2l)[f"{SID}_e4_0_iii_h2l_a_f_glass_sio2"]["values"]["point_conditions"]["atmosphere"]["state"]["value"] = "H2L (hydrogen membrane)"
    with pytest.raises(AssertionError):
        _assert_fO2_control_glosses(changed_h2l)


def test_fO2_log_note_uses_printed_uncertainty() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    supplement = yaml.safe_load(SUPPLEMENT_EXTRACT.read_text(encoding="utf-8"))
    _assert_fO2_log_uncertainties(document, supplement)


def test_total_pressure_note_uses_printed_uncertainty() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    supplement = yaml.safe_load(SUPPLEMENT_EXTRACT.read_text(encoding="utf-8"))
    _assert_pressure_uncertainties(document, supplement)

    changed_document = copy.deepcopy(document)
    observation = _rows(changed_document)[
        f"{SID}_e3_0_iv_sb_a_f_glass_sio2"
    ]
    observation["values"]["point_conditions"]["total_pressure_Pa"]["state"]["note"] = (
        "Table 2 prints 2034 bar ±48 bar; converted to Pa for the d-223 total_pressure_Pa identity."
    )
    with pytest.raises(AssertionError):
        _assert_pressure_uncertainties(changed_document, supplement)


def test_table3_glass_rows_migrate_with_sample_conditions(tmp_path: Path) -> None:
    corpus = regolith_corpus_root()
    if not (corpus / f"raw/{SID}/{SID}.pdf").is_file():
        pytest.skip("Cacciatore and Zajacz source PDF is absent")

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    with GRID.open(encoding="utf-8", newline="") as handle:
        grid = list(csv.DictReader(handle))
    result = migrate(isolated_root(tmp_path, document), write=False)
    observations = list(result.observations.values())
    assert len(observations) == 682
    assert result.validation is not None
    assert not result.validation.hard_issues
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
