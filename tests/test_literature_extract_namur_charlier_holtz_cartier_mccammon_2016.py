from __future__ import annotations

import csv
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / (
    "data/literature/extracts/"
    "namur-charlier-holtz-cartier-mccammon-2016-reduced-mafic-sulfur-solubility.yaml"
)
SID = "namur-charlier-holtz-cartier-mccammon-2016-reduced-mafic-sulfur-solubility"
CELL_AUDIT = ROOT / (
    "data/literature/extracts/audits/"
    "namur-charlier-holtz-cartier-mccammon-2016-reduced-mafic-sulfur-solubility.cells.csv"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in document["species"]["S"]["context"]
    }


def _assert_render_pins(document: dict) -> None:
    rows = _context(document)
    table1 = rows["namur2016_table1_starting_compositions"]["values"]["table_1"]
    messenger = table1["messenger_data"]["rows"]
    synthetic = table1["synthetic_silicate_compositions"]["rows"]
    mixtures = table1["silicate_metal_sulfide_mixture_ratios"]["rows"]

    # PDF page 3 / printed page 104, Table 1 render.
    assert messenger["NVP"]["Mg_Si"] == "0.309 ± 0.13"
    assert synthetic["high_mg_icp_hct"]["Na2O"] == "2.74"
    assert mixtures["F"]["FeS"] == "0.35"
    assert sum(map(len, (messenger, synthetic, mixtures))) == 12
    messenger_columns = ("Mg_Si", "Al_Si", "Ca_Si", "SiO2", "Al2O3", "MgO", "CaO")
    assert sum(row[column] != "–" for row in messenger.values() for column in messenger_columns) == 14
    assert sum(key != "printed_label" for row in synthetic.values() for key in row) == 30
    assert sum(value != "–" for row in mixtures.values() for value in row.values()) == 20

    table2 = rows["namur2016_table2_scss_regression"]["values"]
    # PDF page 7 / printed page 108, Table 2 render.
    assert table2["coefficients"]["b"]["value_as_printed"] == "−2.54 × 10⁴"
    assert table2["coefficients"]["b"]["one_sigma_as_printed"] == "0.13"
    assert table2["fit_quality"]["p_value"] == "7 × 10⁻⁷⁹"
    assert len(table2["coefficients"]) == 7
    assert len(table2["fit_quality"]) == 5


def _assert_cell_audit() -> None:
    with CELL_AUDIT.open(encoding="utf-8", newline="") as stream:
        cells = list(csv.DictReader(stream))

    assert len(cells) == 103
    assert sum(cell["table"] == "1" for cell in cells) == 75
    assert sum(cell["table"] == "2" for cell in cells) == 28
    assert sum(cell["value_as_printed"] == "–" for cell in cells) == 11
    assert sum(cell["value_as_printed"] != "–" for cell in cells) == 92
    assert {cell["verification"] for cell in cells} == {"(b) render"}


def test_rendered_tables_are_pinned_and_migration_retains_context_only_rows():
    document = _document()
    assert validate_extract_file(EXTRACT, check_fidelity_match=True) == []
    _assert_render_pins(document)
    _assert_cell_audit()

    mutated_value = deepcopy(document)
    _context(mutated_value)["namur2016_table1_starting_compositions"]["values"]["table_1"]["messenger_data"]["rows"]["NVP"]["Mg_Si"] = "0.310 ± 0.13"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated_value)

    mutated_count = deepcopy(document)
    del _context(mutated_count)["namur2016_table1_starting_compositions"]["values"]["table_1"]["synthetic_silicate_compositions"]["rows"]["EH"]
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated_count)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    contexts = [
        row
        for work_rows in result.context_by_work.values()
        for row in work_rows
        if row.get("source_id") == SID
    ]
    assert len(contexts) == 3
    assert result.observations == {}
    assert all(
        not any(
            key in row
            for key in (
                "T_C",
                "T_K",
                "point_conditions",
                "total_pressure_Pa",
                "duration",
                "capsule",
            )
        )
        for row in contexts
    )
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert not any(row.identity.quantity.is_unknown for row in result.observations.values())
    assert not any(row.identity.species.phase.is_unknown for row in result.observations.values())
    assert sum(row.value.kind.value == "unavailable" for row in result.observations.values()) == 0
    assert {
        (row["locator"]["page"], row["locator"].get("table"))
        for row in contexts
        if row["observation_id"] in {
            "namur2016_table1_starting_compositions",
            "namur2016_table2_scss_regression",
        }
    } == {(3, "1"), (7, "2")}
