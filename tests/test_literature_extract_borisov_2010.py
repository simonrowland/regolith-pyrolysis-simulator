from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/borisov-2010-silica-ferric-ferrous.yaml"
MAPPING = ROOT / "data/literature/extracts/audits/borisov-2010-silica-ferric-ferrous.mapping.yaml"
SOURCE_ID = "borisov-2010-silica-ferric-ferrous"


def test_borisov_2010_table4_runs_keep_printed_values_and_conditions():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_rows = document["species"]["Fe"]["observations"][0]["values"]["rows"]
    mapping = yaml.safe_load(MAPPING.read_text(encoding="utf-8"))
    expected = {
        "DAFL-9": ("1424", "3.44", "DAFL"),
        "DAFL-10": ("1540", "2.45", "DAFL"),
        "DAF-1": ("1500", "2.08", "DAF"),
        "DAS20F-1": ("1500", "1.82", "DAS20F"),
        "DAS40F-1": ("1500", "1.65", "DAS40F"),
        "DAS60F-1": ("1500", "1.73", "DAS60F"),
        "DAS80F-1": ("1500", "1.78", "DAS80F"),
        "DAF-2": ("1398", "2.94", "DAF"),
        "DAS20F-2": ("1398", "2.56", "DAS20F"),
        "DAS40F-2": ("1398", "2.29", "DAS40F"),
        "DAS60F-2": ("1398", "2.68", "DAS60F"),
        "DAF-5": ("1571", "1.58", "DAF"),
        "DAS20F-5": ("1571", "1.44", "DAS20F"),
        "DAS40F-5": ("1571", "1.41", "DAS40F"),
        "DAS60F-5": ("1571", "1.36", "DAS60F"),
        "DAS80F-5": ("1571", "1.38", "DAS80F"),
        "DAS90F-5": ("1571", "1.53", "DAS90F"),
        "DAF-6": ("1450", "2.39", "DAF"),
        "DAS20F-6": ("1450", "2.11", "DAS20F"),
        "DAS40F-6": ("1450", "2.01", "DAS40F"),
        "DAS60F-6": ("1450", "2.00", "DAS60F"),
        "DAS80F-6": ("1450", "2.15", "DAS80F"),
    }

    assert len(source_rows) == 22
    assert mapping["source_id"] == SOURCE_ID
    table4 = next(table for table in mapping["tables"] if table["table"] == "4")
    assert table4["printed_data_rows"] == table4["extracted_run_rows"] == 22
    assert len(mapping["render_check"]["verified_cells"]) >= 15
    assert {
        row["run_id_as_printed"]: (
            row["T_C"],
            row["Fe3_over_Fe2"],
            row["composition_as_printed"],
        )
        for row in source_rows
    } == expected

    migrator = Migrator(ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = [
        row
        for row in migrator.result.observations.values()
        if row.observation_id.startswith(f"{SOURCE_ID}::")
    ]
    assert len(migrated) == 22
    assert migrator.result.validation is not None
    assert len(migrator.result.validation.hard_issues) == 0

    for source_row in source_rows:
        composition = source_row["point_conditions"]["composition"]
        assert composition["locator"]["pdf_page_index"] == 1
        assert composition["state"]["value"]["components"] == document[
            "species"
        ]["Fe"]["observations"][0]["values"]["composition_maps"][
            source_row["composition_as_printed"]
        ]
        observation = next(
            row for row in migrated if row.locator.record == source_row["run_id_as_printed"]
        )
        conditions = observation.point_conditions
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value is Quantity.VALENCE_RATIO
        assert observation.value.point == Decimal(source_row["value"])
        assert conditions["T_C"].state.value == Decimal(source_row["T_C"])
        assert conditions["T_C"].locator is not None
        assert conditions["composition"].locator is not None
        assert conditions["composition"].locator.pdf_page_index == 1
        assert (
            conditions["composition"].locator.record
            == source_row["composition_as_printed"]
        )

    assert validate_extract_file(EXTRACT, check_fidelity_match=True) == []
