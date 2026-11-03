from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shi-2025-mno-activity-eaf-spinel.yaml"
SOURCE_ID = "shi-2025-mno-activity-eaf-spinel"

TABLE1_RENDER = [
    ("1", ["18.97", "23.06", "13.64", "7.420", "28.24", "8.670"]),
    ("2", ["14.46", "17.58", "23.64", "7.420", "28.24", "8.670"]),
    ("3", ["12.21", "14.84", "28.64", "7.420", "28.24", "8.670"]),
    ("4", ["9.940", "12.09", "33.64", "7.420", "28.24", "8.670"]),
]
TABLE2_RENDER = [
    ("1673–1633", "121.0"), ("1633–1593", "77.74"), ("1593–1553", "49.51"),
    ("1553–1513", "31.34"), ("1513–1473", "19.62"), ("1473–1433", "11.90"),
    ("1433–1393", "6.940"), ("1393–1353", "3.863"), ("1353–1313", "2.062"),
    ("1313–1273", "0.4219"),
]
TABLE3_RENDER = [
    ("1", "13.64", "0.3691"), ("2", "23.64", "0.7203"), ("3", "28.64", "0.8020"),
    ("4", "33.64", "0.9374"), ("5", "36.56", "1.000"),
]
TABLE4_MATRIX_RENDER = [
    ("1", ["51.93", "12.00", "14.84", "12.02", "2.544", "1.686", "4.989"]),
    ("2", ["51.58", "12.06", "14.44", "14.29", "0.8600", "2.846", "3.996"]),
    ("3", ["53.66", "11.83", "14.36", "14.48", "0.3770", "2.681", "2.610"]),
    ("4", ["53.08", "11.68", "13.96", "18.06", "0.07800", "1.170", "1.970"]),
]
TABLE4_SPINEL_RENDER = [
    ("1", ["49.25", "-", "-", "34.23", "10.28", "2.131", "4.110"]),
    ("2", ["48.42", "-", "-", "34.30", "6.473", "5.420", "5.390"]),
    ("3", ["50.55", "-", "-", "33.45", "5.360", "5.935", "4.700"]),
    ("4", ["48.89", "-", "-", "34.60", "4.670", "6.410", "5.430"]),
]
TABLE5_RENDER = [
    ("quenching", "48.89", "34.60", "4.670", "6.410", "5.430"),
    ("slow cooling", "48.24", "35.18", "4.506", "6.572", "5.502"),
]
TABLE6_RENDER = [
    ("1", "13.64", "0.001530", "0.04137"), ("2", "23.64", "0.001170", "0.03164"),
    ("3", "28.64", "0.001052", "0.02845"), ("4", "33.64", "0.0007829", "0.02117"),
]


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_grid(actual, expected):
    assert actual == expected


def _context_rows(document, table, prefix):
    return [
        row for body in document["species"].values() for row in body.get("context", [])
        if row.get("locator", {}).get("table") == str(table)
        and row["observation_id"].startswith(prefix)
    ]


def _table4_matrix_grid(document):
    by_row = {}
    for body in document["species"].values():
        for row in body.get("observations", []):
            if row.get("locator", {}).get("table") != "4":
                continue
            no = str(row["locator"]["row"])
            by_row.setdefault(no, {})[str(row["values"]["formula"])] = str(row["values"]["component_amount"])
    return [(no, [by_row[no][name] for name in ("O", "Ca", "Si", "Al", "Mg", "Fe", "Mn")]) for no in sorted(by_row)]


def _migrate():
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator


def _record(migrator, local_id):
    exact_id = f"{SOURCE_ID}::{local_id}"
    prefix = exact_id + "::"
    return next(row for row in migrator.result.observations.values() if row.observation_id == exact_id or row.observation_id.startswith(prefix))


def test_all_six_printed_tables_match_page_renders_and_row_counts():
    document = _document()
    contexts1 = _context_rows(document, "1", "shi2025_table1_initial_slag_row_")
    assert [(row["values"]["row_as_printed"], [row["values"]["components_as_printed"][key] for key in ("CaO", "SiO2", "Al2O3", "MgO", "FeO", "MnO")]) for row in contexts1] == TABLE1_RENDER

    table2 = next(row for row in _context_rows(document, "2", "shi2025_table2_") if row["observation_id"].endswith("context"))
    assert [tuple(cell) for cell in table2["values"]["rows_as_printed"]] == TABLE2_RENDER

    table3 = [
        (str(row["locator"]["row"]), row["values"]["points"][0]["alumina_content_mol_percent"], row["values"]["points"][0]["activity"])
        for row in document["species"]["Al2O3"]["observations"]
    ]
    assert table3 == TABLE3_RENDER

    assert _table4_matrix_grid(document) == TABLE4_MATRIX_RENDER
    spinel_rows = _context_rows(document, "4", "shi2025_table4_row_")
    table4_spinel = sorted(
        [(str(row["values"]["row_as_printed"][0]), [str(x) for x in row["values"]["row_as_printed"][2:]]) for row in spinel_rows],
        key=lambda row: int(row[0]),
    )
    assert table4_spinel == TABLE4_SPINEL_RENDER

    table5 = next(row for row in _context_rows(document, "5", "shi2025_table5_"))
    assert [tuple(row) for row in table5["values"]["rows_as_printed"]] == TABLE5_RENDER

    table6_activity = {row["locator"]["row"]: row for row in document["species"]["MnO"]["observations"] if row["locator"].get("table") == "6"}
    table6_x = {row["locator"]["row"]: row for row in document["species"]["Mn"]["observations"] if row["locator"].get("table") == "6"}
    table6 = [
        (no, table6_activity[no]["values"]["points"][0]["alumina_content_mol_percent"], table6_x[no]["values"]["component_amount"], table6_activity[no]["values"]["points"][0]["activity"])
        for no, *_ in TABLE6_RENDER
    ]
    assert table6 == TABLE6_RENDER

    row_counts = {1: len(contexts1), 2: len(table2["values"]["rows_as_printed"]), 3: len(table3), 4: len(spinel_rows) + len(TABLE4_MATRIX_RENDER), 5: len(table5["values"]["rows_as_printed"]), 6: len(table6)}
    assert row_counts == {1: 4, 2: 10, 3: 5, 4: 8, 5: 2, 6: 4}
    assert sum(row_counts.values()) == 33


def test_independent_render_grid_and_extract_cell_mutations_fail():
    document = _document()
    actual = _table4_matrix_grid(document)
    changed_render = deepcopy(TABLE4_MATRIX_RENDER)
    changed_render[0] = (changed_render[0][0], ["51.94", *changed_render[0][1][1:]])
    with pytest.raises(AssertionError):
        _assert_grid(actual, changed_render)

    mutated = deepcopy(document)
    row = next(
        item for body in mutated["species"].values() for item in body.get("observations", [])
        if item["observation_id"] == "shi2025_table4_row_1_matrix_o"
    )
    row["values"]["component_amount"] = "51.94"
    with pytest.raises(AssertionError):
        _assert_grid(_table4_matrix_grid(mutated), TABLE4_MATRIX_RENDER)


def test_isolated_migration_maps_conditions_values_and_schema_refusal():
    migrator = _migrate()
    result = migrator.result
    assert len(result.observations) == 42
    assert not result.validation.hard_issues
    assert not result.registry_issues

    unknown_quantity_ids = {
        row.observation_id.rsplit("::T=", 1)[0].removeprefix(f"{SOURCE_ID}::")
        for row in result.observations.values()
        if row.identity.quantity.is_unknown
    }
    assert unknown_quantity_ids == {"shi2025_prose_mn_spinel_precipitation_ratio_80_32"}
    assert all(not row.identity.species.phase.is_unknown for row in result.observations.values())
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in result.observations.values()) == 1

    # Render pins from Table 3 (page 3), Table 4 (page 4), and Table 6 (page 6).
    assert _record(migrator, "shi2025_table3_row_1_al2o3_activity").value.point == Decimal("0.3691")
    assert _record(migrator, "shi2025_table6_row_1_mno_activity").value.point == Decimal("0.04137")
    assert _record(migrator, "shi2025_table6_row_4_mno_activity").value.point == Decimal("0.02117")
    table3_row1 = _record(migrator, "shi2025_table3_row_1_al2o3_activity")
    assert dict(table3_row1.identity.composition.value.components) == {
        "CaO": Decimal("0.1897"),
        "SiO2": Decimal("0.2306"),
        "Al2O3": Decimal("0.1364"),
        "MgO": Decimal("0.0742"),
        "FeO": Decimal("0.2824"),
        "MnO": Decimal("0.0867"),
    }

    # Table 4 prints mol%; canonical storage is mole fraction, so the migrated
    # component amount must be the printed percent divided by 100.
    matrix_o1 = _record(migrator, "shi2025_table4_row_1_matrix_o")
    assert matrix_o1.value.point == Decimal("0.5193")
    assert matrix_o1.locator.page == 4 and matrix_o1.locator.table == "4"
    assert _record(migrator, "shi2025_table4_row_2_matrix_o").value.point == Decimal("0.5158")
    assert _record(migrator, "shi2025_table4_row_4_matrix_o").value.point == Decimal("0.5308")

    # The printed initial recipes are retained as located sample context; no
    # post-equilibrium bulk composition or total pressure is invented.
    for local_id, table, page in (
        ("shi2025_table6_row_1_mno_activity", "6", 6),
        ("shi2025_table4_row_1_matrix_o", "4", 4),
        ("shi2025_table3_row_5_al2o3_activity", "3", 3),
    ):
        row = _record(migrator, local_id)
        assert row.identity.temperature_K.is_value and row.identity.temperature_K.value == Decimal("1673")
        assert row.identity.fO2_Pa.is_value and row.identity.fO2_Pa.value == Decimal("1.01325e-8")
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.point_conditions["total_pressure_Pa"].state.is_unknown
        assert row.locator.page == page and row.locator.table == table
    activity = _record(migrator, "shi2025_table6_row_1_mno_activity")
    assert activity.identity.composition.is_unknown
    assert _record(migrator, "shi2025_table3_row_5_al2o3_activity").identity.composition.is_unknown
    printed_start = activity.point_conditions["printed_composition"]
    assert printed_start.state.is_value
    assert printed_start.locator.table == "1" and printed_start.locator.row == "1"
