"""Measured chromium concentrations and deferred figures from Avarmaa et al. (2022)."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT
from simulator.battery.records import ValueKind


EXTRACT = REPO_ROOT / "data/literature/extracts/avarmaa-2022-cr-don-smelting.yaml"
TABLE1_RENDER_ROWS = [
    ("1350", "5.0", "-7.25", "-3.17", {"CO": "8", "CO2": "55", "SO2": "40", "Ar": "300"}),
    ("1400", "2.5", "-6.74", "-3.41", {"CO": "8", "CO2": "55", "SO2": "40", "Ar": "300"}),
    ("1400", "5.0", "-6.91", "-3.15", {"CO": "9.9", "CO2": "55", "SO2": "40", "Ar": "300"}),
    ("1400", "10.0", "-7.18", "-2.67", {"CO": "16", "CO2": "55", "SO2": "40", "Ar": "300"}),
    ("1450", "5.0", "-6.67", "-3.03", {"CO": "13.3", "CO2": "55", "SO2": "40", "Ar": "300"}),
]


def _migrate(path: Path):
    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _assert_table1_render_rows(document: dict) -> None:
    table = next(
        row for row in document["species"]["Cr"]["context"]
        if row["observation_id"] == "avarmaa_2022_table_1_experimental_conditions"
    )
    rows = table["values"]["rows"]
    actual = [
        (
            row["temperature_C"], row["target_Fe_in_matte_wt_percent"],
            row["log_pO2_atm_as_printed"], row["log_pS2_atm_as_printed"],
            row["gas_flow_mL_min"],
        )
        for row in rows
    ]
    assert actual == TABLE1_RENDER_ROWS
    assert table["values"]["SO2_partial_pressure_atm_as_printed"] == "0.1 ± 0.01"


def test_printed_slag_mean_and_matte_range_migrate_with_species_and_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrated = _migrate(EXTRACT)
    rows = {
        row.observation_id.split("::", 1)[1]: row
        for row in migrated.observations.values()
        if row.source_id == document["source_id"]
    }
    assert set(rows) == {
        "avarmaa_2022_average_slag_chromium_at_1400C",
    }
    slag = rows["avarmaa_2022_average_slag_chromium_at_1400C"]
    assert slag.value.kind is ValueKind.POINT
    assert slag.value.point == Decimal("0.66")
    assert slag.identity.species.formula == "Cr"
    assert slag.identity.temperature_K.value == Decimal("1673.15")
    assert slag.point_conditions["log_fO2"].state.tag.value == "unknown"
    assert not migrated.validation.hard_issues
    assert not migrated.registry_issues

    context_range = next(
        row for row in document["species"]["Cr"]["context"]
        if row["observation_id"] == "avarmaa_2022_matte_chromium_range"
    )
    assert context_range["values"]["range_as_published"] == "30-100"


def test_mutated_printed_mean_fails_the_render_pin(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    target = document["species"]["Cr"]["observations"][0]
    target["values"]["value"] = "0.67"
    mutated = tmp_path / EXTRACT.name
    mutated.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    migrated = _migrate(mutated)
    row = next(
        row for row in migrated.observations.values()
        if row.observation_id.endswith("::avarmaa_2022_average_slag_chromium_at_1400C")
    )
    with pytest.raises(AssertionError):
        assert row.value.point == Decimal("0.66")


def test_figures_are_point_free_and_include_all_six_records() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    figures = [
        row for row in document["species"]["Cr"]["context"]
        if row.get("type") == "figure_record"
    ]
    assert len(figures) == 7  # Figure 3 has separate panels a and b.
    assert {row["locator"]["figure"] for row in figures} == {"1", "2", "3", "4", "5", "6"}
    for figure in figures:
        assert figure["digitisation"] == "deferred to a figure pass"
        assert figure["numeric_points"] == "none"
        assert figure["series"]
        assert all(series["marker_count"] > 0 for series in figure["series"])


def test_table1_matches_independent_render_transcription() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table1_render_rows(document)


def test_table1_cell_mutation_fails_render_pin() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table = next(
        row for row in document["species"]["Cr"]["context"]
        if row["observation_id"] == "avarmaa_2022_table_1_experimental_conditions"
    )
    table["values"]["rows"][2]["log_pO2_atm_as_printed"] = "-6.90"
    with pytest.raises(AssertionError):
        _assert_table1_render_rows(document)
