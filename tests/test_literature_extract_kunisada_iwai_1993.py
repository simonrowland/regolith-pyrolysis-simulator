from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kunisada-iwai-1993-na-silicate-sulfide-capacity.yaml"
TABLE_ID = "kunisada_iwai_1993_table1_slag_compositions"


def _table_record(document: dict) -> dict:
    return next(
        row
        for row in document["species"]["S"]["context"]
        if row["observation_id"] == TABLE_ID
    )


def test_table1_cells_and_row_count_are_pinned_to_page_44_render() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table = _table_record(document)
    rows = table["values"]["rows_as_printed"]

    assert len(rows) == 57
    assert [int(row[0]) for row in rows] == (
        list(range(1, 15))
        + list(range(101, 112))
        + list(range(201, 212))
        + list(range(301, 312))
        + list(range(401, 411))
    )
    assert table["locator"]["page"] == 2
    assert table["locator"]["published_page"] == 44
    assert table["locator"]["table"] == "1"

    # These cells were checked against the 600-dpi render of printed page 44.
    by_row = {row[0]: row for row in rows}
    assert by_row["1"][1] == "29.1"
    assert by_row["4"][7] == "0.102"
    assert by_row["208"][1] == "31.1"
    assert by_row["408"][7] == "0.120"
    assert by_row["1"][3:7] == ["", "", "", ""]


def test_unprinted_total_pressure_stays_unknown_and_figures_are_not_digitized() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = document["species"]["S"]["context"]
    experiment = next(row for row in context if row["type"] == "method")
    pressure = experiment["values"]["total_pressure_Pa"]
    assert pressure["state"]["tag"] == "unknown"
    assert "does not report total experimental pressure" in pressure["state"]["reason"]

    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 6
    assert all(row["values"]["points"] == [] for row in figures)


def test_isolated_migration_preserves_render_checked_table_and_method_locators() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert not migrator.result.validation.hard_issues
    assert migrator.result.observations == {}
    contexts = {
        row["observation_id"]: row
        for context_rows in migrator.result.context_by_work.values()
        for row in context_rows
    }
    assert contexts[TABLE_ID]["locator"]["page"] == 2
    assert contexts[TABLE_ID]["locator"]["table"] == "1"
    assert contexts["kunisada_iwai_1993_experimental_conditions"]["locator"][
        "page"
    ] == 1
    assert contexts["kunisada_iwai_1993_capacity_definition_and_gas_potentials"][
        "locator"
    ]["page"] == 2
