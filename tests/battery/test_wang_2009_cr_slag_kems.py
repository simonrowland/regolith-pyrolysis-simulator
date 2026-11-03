"""Render-checked extraction and migration pins for Wang et al. 2009 Tables 3–5."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root


SID = "wang-2009-cr-slag-kems-original"
ROOT = Path(__file__).resolve().parents[2]
CORPUS = regolith_corpus_root()
SOURCE = CORPUS / "raw" / SID / f"{SID}.pdf"
EXTRACT = ROOT / "data/literature/extracts" / f"{SID}.yaml"


def _page_text(page_number: int) -> str:
    return subprocess.run(
        [
            "pdftotext",
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            "-layout",
            str(SOURCE),
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _by_id(extract: dict, species: str, observation_id: str) -> dict:
    return next(
        row
        for row in extract["species"][species]["observations"]
        if row["observation_id"] == observation_id
    )


def _assert_render_pins(extract: dict, page2: str, page4: str, page5: str) -> None:
    # The expected strings below were checked against rendered PDF pages 2, 4, and 5.
    page5 = page5.replace("\x01", "−").replace("\x02", "±")
    page2 = re.sub(r"\s+", " ", page2)
    page4_flat = re.sub(r"\s+", " ", page4)
    page5_flat = re.sub(r"\s+", " ", page5)
    table4_text = page5.split("Table 4.", 1)[1].split("Table 5.", 1)[0]
    table5_text = page5.split("Table 5.", 1)[1].split("Copyright", 1)[0]
    sample_rows = re.compile(r"^\s*(\d{1,2})\s+0\.\d{3}\s+[-−]?\d", re.MULTILINE)
    assert [int(value) for value in sample_rows.findall(table4_text)] == list(range(1, 21))
    assert [int(value) for value in sample_rows.findall(table5_text)] == list(range(1, 21))

    assert "Table 1." in page2
    assert re.search(r"\b1\s+0\.062\b", page2)
    assert "1 0.066 0.712 0.099 0.013 0.111" in page4_flat
    assert re.search(r"\b1\s+0\.062\b", table4_text)
    assert "−36066.0 ± 2827.4" in page5_flat
    assert "−41668.5 ± 1781.7" in page5_flat
    assert "0.010" in page5_flat and "0.005" in page5_flat and "0.018" in page5_flat

    cro_a = _by_id(extract, "CrO", "wang_2009_table4_cro_sample_01_temperature_fit")
    cro2_a = _by_id(extract, "CrO2", "wang_2009_table4_cro2_sample_01_temperature_fit")
    activity_1 = _by_id(
        extract,
        "Cr2O3",
        "wang_2009_table5_cr2o3_sample_01_activities_at_1950_2035_k",
    )
    activity_20 = _by_id(
        extract,
        "Cr2O3",
        "wang_2009_table5_cr2o3_sample_20_activities_at_1950_2035_k",
    )
    table5_fit_1 = _by_id(
        extract,
        "Cr2O3",
        "wang_2009_table5_cr2o3_sample_01_log_activity_temperature_fit",
    )
    assert cro_a["values"]["A_as_printed"] == "-36066.0"
    assert cro_a["values"]["A_uncertainty_as_printed"] == "2827.4"
    assert cro_a["locator"]["page"] == 5
    assert cro_a["locator"]["table"] == "4"
    assert cro_a["locator"]["table_row_number"] == 3
    assert cro_a["values"]["cr2o3_mole_fraction_as_printed_table4"] == "0.062"
    assert cro_a["values"]["composition_mol"]["Cr2O3"] == "0.066"
    assert cro2_a["values"]["A_as_printed"] == "-41668.5"
    assert table5_fit_1["values"]["cr2o3_mole_fraction_as_printed_table5"] == "0.062"
    assert table5_fit_1["values"]["composition_mol"]["Cr2O3"] == "0.066"
    assert activity_1["values"]["points"][0]["activity"] == "0.010"
    assert activity_1["values"]["points"][1]["activity"] == "0.005"
    assert activity_20["values"]["points"][1]["activity"] == "0.018"
    assert activity_1["values"]["points"][0]["locator"]["page"] == 5
    assert activity_1["values"]["points"][0]["locator"]["table"] == "5"
    assert activity_1["values"]["points"][0]["locator"]["table_row_number"] == 2
    assert activity_1["values"]["points"][0]["locator"]["table_column_number"] == 5


def test_render_pins_and_printed_table_row_counts() -> None:
    if not SOURCE.is_file():
        pytest.skip(f"source PDF is absent: {SOURCE}")

    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert len(extract["species"]["CrO"]["observations"]) == 20
    assert len(extract["species"]["CrO2"]["observations"]) == 20
    assert len(extract["species"]["Cr2O3"]["observations"]) == 40
    page2 = _page_text(2)
    page4 = _page_text(4)
    page5 = _page_text(5)
    _assert_render_pins(extract, page2, page4, page5)

    # Mutation proof: flipping the printed Table 4 sign must fail the pin.
    mutated = deepcopy(extract)
    mutated_row = _by_id(
        mutated, "CrO", "wang_2009_table4_cro_sample_01_temperature_fit"
    )
    mutated_row["values"]["A_as_printed"] = "36066.0"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated, page2, page4, page5)


def test_isolated_migration_keeps_conditions_and_only_refuses_fit_coefficients() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(ROOT, EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues

    rows = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == SID
    ]
    assert len(rows) == 100
    assert sum(row.value.kind is ValueKind.POINT for row in rows) == 40
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in rows) == 60

    fit_ids = {
        row["observation_id"]
        for species in extract["species"].values()
        for row in species["observations"]
        if "temperature_fit" in row["observation_id"]
    }
    unknown_quantity_ids = {
        observation.observation_id.split("::", 1)[1]
        for observation in rows
        if not observation.identity.quantity.is_value
    }
    assert unknown_quantity_ids == fit_ids
    assert all(observation.identity.species.phase.is_value for observation in rows)

    for observation in rows:
        identity = observation.identity
        assert identity.composition.is_value
        assert identity.total_pressure_Pa is None or identity.total_pressure_Pa.is_unknown
        assert identity.fO2_Pa is None or identity.fO2_Pa.is_unknown
        conditions = observation.point_conditions or {}
        assert conditions["composition"].state.is_value
        assert conditions["composition"].locator.page == 4
        assert conditions["composition"].locator.table == "3"
        assert conditions["fO2_Pa"].state.is_unknown
        pressure_condition = conditions.get("total_pressure_Pa")
        assert pressure_condition is None or pressure_condition.state.is_unknown
        temperature = conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.locator.page == 5
        assert temperature.locator.table in {"4", "5"}
        assert identity.composition.value == conditions["composition"].state.value
        if not identity.quantity.is_value:
            assert identity.temperature_K.is_unknown
            assert temperature.state.value.kind is ValueKind.INTERVAL
            assert str(temperature.state.value.interval_low) == "1950"
            assert str(temperature.state.value.interval_high) == "2035"

    activities = [
        observation for observation in rows if observation.identity.quantity.is_value
    ]
    assert len(activities) == 40
    for observation in activities:
        identity = observation.identity
        assert identity.quantity.value is Quantity.ACTIVITY
        assert identity.species.formula == "Cr2O3"
        assert identity.species.phase.value is Phase.L
        assert identity.temperature_K.is_value
        assert (
            identity.total_pressure_Pa is None
            or identity.total_pressure_Pa.is_unknown
        )
        assert identity.fO2_Pa is None or identity.fO2_Pa.is_unknown
        reference = identity.reference_state
        assert reference is not None and reference.is_value
        assert reference.value.endmember.formula == "Cr2O3"
        assert reference.value.endmember.phase.is_value
        assert reference.value.endmember.phase.value is Phase.CR

    first_three = sorted(activities, key=lambda row: row.observation_id)[:3]
    assert len(first_three) == 3
    for observation in first_three:
        assert observation.identity.composition.is_value
        assert (
            observation.identity.total_pressure_Pa is None
            or observation.identity.total_pressure_Pa.is_unknown
        )
        assert (
            observation.identity.fO2_Pa is None
            or observation.identity.fO2_Pa.is_unknown
        )

