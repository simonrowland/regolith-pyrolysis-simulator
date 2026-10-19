from __future__ import annotations

import copy
import csv
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "tranell-2000-silica-activity-titanium"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
AUDIT = ROOT / "data/literature/extracts/audits" / f"{SOURCE_ID}.cells.csv"

# Independent transcription of the published cells from the 600 dpi renders
# of physical PDF pages 16–19. Rows and columns here are not read from the
# extract builder or its audit CSV.
TABLE_I = (
    ("1", "1.25", "7.30"), ("2", "0.99", "7.55"),
    ("3", "0.77", "7.78"), ("4", "0.53", "7.34"),
    ("5", "1.21", "14.40"), ("6", "1.00", "14.20"),
    ("7", "0.70", "14.60"), ("8", "0.56", "14.80"),
    ("9", "1.21", "20.85"), ("10", "1.00", "22.00"),
    ("11", "0.75", "22.20"), ("12", "0.56", "21.70"),
    ("13", "1.35", "52.35"), ("14", "1.00", "51.10"),
)
TABLES = {
    "II": {
        "page": 17,
        "columns": (
            "XSi GDMS (wt ppm)", "XSi ICP (wt ppm)",
            "XTi GDMS (wt ppm)", "XTi ICP (wt ppm)",
            "XMo GDMS (wt ppm)", "XMo ICP (wt ppm)",
            "Cu in slag (wt ppm)", "Mo in slag (wt ppm)",
            "aSi in copper", "aSiO2 in slag", "Ti3+/Ti4+ in slag",
        ),
        "rows": (
            ("1", ("132.0", "70", "0.008", "0.7", "22.9", "-", "1104", "51", "4.11E-06", "0.035", "0.204")),
            ("2", ("280.5", "255", "0.005", "0.6", "30.6", "-", "894", "90", "1.09E-05", "0.093", "0.303")),
            ("3", ("247.5", "245", "0.003", "0.1", "26.6", "-", "1489", "81", "1.00E-05", "0.086", "0.455")),
            ("4", ("225.5", "240", "0.002", "0.1", "28.3", "-", "1060", "110", "9.48E-06", "0.081", "0.632")),
            ("5", ("71.5", "65", "0.002", "0.2", "25.5", "-", "950", "68", "2.78E-06", "0.024", "0.180")),
            ("6", ("110.5", "107", "0.006", "0.3", "26.7", "-", "791", "69", "4.43E-06", "0.038", "0.218")),
            ("7", ("286.5", "295", "0.006", "0.2", "30.2", "-", "884", "77", "1.18E-05", "0.101", "0.400")),
            ("8", ("300.0", "280", "0.007", "0.4", "29.5", "-", "1276", "120", "1.18E-05", "0.101", "0.469")),
            ("9", ("79.5", "130", "0.008", "0.5", "29.1", "-", "780", "152", "3.24E-06", "0.028", "0.189")),
            ("10", ("149.5", "160", "0.003", "0.3", "24.3", "-", "1145", "38", "6.30E-06", "0.054", "0.242")),
            ("11", ("321.8", "290", "0.007", "0.4", "25.6", "-", "1320", "49", "1.25E-05", "0.106", "0.413")),
            ("12", ("328.0", "290", "0.050", "0.3", "27.2", "-", "1850", "98", "1.26E-05", "0.107", "0.516")),
            ("Reference*", ("0.08", "-", "0.003", "-", "<0.008", "-", "-", "-", "-", "-", "-")),
            ("1 (24h)", ("73", "-", "0.002", "-", "33", "-", "-", "-", "2.97E-06", "0.025", "0.176")),
            ("2 (24h)", ("234", "-", "0.005", "-", "16.6", "-", "-", "-", "9.53E-06", "0.081", "0.242")),
            ("5 (24h)", ("52", "-", "0.004", "-", "29", "-", "-", "-", "2.12E-06", "0.018", "0.140")),
            ("6 (24h)", ("102", "-", "0.005", "-", "32", "-", "-", "-", "4.15E-06", "0.035", "0.220")),
        ),
    },
    "III": {
        "page": 18,
        "columns": (
            "pO2 (atm)", "XSi GDMS (wt ppm)", "XSi ICP (wt ppm)",
            "XTi GDMS (wt ppm)", "XTi ICP (wt ppm)",
            "XMo GDMS (wt ppm)", "XMo ICP (wt ppm)",
            "aSi in copper (average)", "aSiO2 in slag (average)",
            "Ti3+/Ti4+ in slag",
        ),
        "rows": (
            ("1", ("4.20E-12", "29", "27.9", "0.07", "5.47", "185", "212", "1.18E-06", "0.042", "0.086")),
            ("2", ("2.23E-11", "69", "91.8", "0.18", "0.06", "120", "200", "2.81E-06", "0.535", "0.239")),
            ("3", ("1.42E-11", "124", "103", "0.32", "0.77", "97", "183", "5.05E-06", "0.612", "0.520")),
            ("4", ("1.45E-11", "233", "106", "0.02", "0.36", "94", "196", "9.49E-06", "(1.175)", "0.658")),
            ("5", ("9.72E-12", "46", "34.1", "0.13", "0.13", "133", "171", "1.87E-06", "0.155", "0.123")),
            ("6", ("7.66E-12", "*", "85.5", "*", "0.68", "*", "216", "3.48E-06", "0.228", "0.234")),
            ("7", ("3.44E-12", "*", "224", "*", "0.67", "*", "202", "9.12E-06", "0.268", "0.485")),
            ("8", ("1.34E-11", "*", "251", "*", "0.68", "*", "237", "1.02E-05", "(1.170)", "0.732")),
            ("9", ("1.02E-12", "68", "28.9", "0.95", "109", "504", "330", "2.77E-06", "0.024", "0.218")),
            ("10", ("3.76E-12", "90", "60.6", "0.13", "0.54", "90", "159", "3.67E-06", "0.214", "0.171")),
            ("11", ("5.03E-12", "197", "71.8", "0.38", "1.61", "500", "258", "8.02E-06", "0.343", "0.354")),
            ("12", ("9.03E-12", "310", "84.1", "0.8", "3.62", "162", "196", "1.26E-05", "0.973", "0.467")),
            ("13", ("7.94E-13", "805", "118", "0.15", "10.1", "205", "291", "3.28E-05", "0.227", "0.453")),
            ("14", ("2.95E-12", "374", "91", "0.08", "3.31", "179", "322", "1.52E-05", "0.432", "0.452")),
        ),
    },
    "IV": {
        "page": 19,
        "columns": (
            "pO2 (atm)", "XSi GDMS (wt ppm)", "XSi ICP (wt ppm)",
            "XTi GDMS (wt ppm)", "XTi ICP (wt ppm)",
            "XMo GDMS (wt ppm)", "XMo ICP (wt ppm)",
            "aSi in copper", "aSiO2 in slag", "Ti3+/Ti4+ in slag",
        ),
        "rows": (
            ("1", ("5E-12", "*", "60", "*", "1.9", "*", "200", "2.44E-06", "0.104", "0.092")),
            ("2", ("5E-12", "150", "115", "0.004", "<0.5", "34.2", "200", "5.40E-06", "0.230", "0.262")),
            ("3", ("5E-12", "148", "130", "0.006", "<0.5", "20.1", "170", "5.66E-06", "0.242", "0.530")),
            ("4", ("5E-12", "430", "235", "0.006", "<0.5", "24.4", "131", "1.35E-05", "0.578", "0.691")),
            ("5", ("5E-12", "64.9", "55", "0.006", "<0.5", "16.4", "125", "2.44E-06", "0.104", "0.101")),
            ("6", ("5E-12", "*", "155", "*", "0.7", "*", "220", "6.31E-06", "0.270", "0.283")),
            ("7", ("5E-12", "269", "180", "0.008", "<0.5", "17.5", "165", "9.14E-06", "0.390", "0.402")),
            ("8", ("5E-12", "420", "310", "0.03", "0.9", "24.3", "190", "1.49E-05", "0.635", "0.685")),
            ("9", ("5E-12", "114", "90", "0.03", "0.6", "24", "210", "4.15E-06", "0.177", "0.188")),
            ("10", ("5E-12", "*", "70", "*", "12", "*", "230", "2.85E-06", "0.122", "0.296")),
            ("11", ("5E-12", "356", "240", "0.07", "0.7", "32.9", "180", "1.21E-05", "0.518", "0.483")),
            ("12", ("5E-12", "526", "355", "0.06", "1", "56.1", "415", "1.79E-05", "0.766", "0.690")),
            ("12(2h)", ("5E-12", "*", "180", "*", "1.4", "*", "185", "-", "-", "0.567")),
            ("12(6h)", ("5E-12", "*", "190", "*", "3.7", "*", "195", "-", "-", "0.562")),
            ("12(18h)", ("5E-12", "*", "360", "*", "2.3", "*", "170", "-", "-", "0.568")),
        ),
    },
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _audit_cells() -> dict[tuple[str, str, str], str]:
    with AUDIT.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    result = {}
    for row in rows:
        key = (row["table"], row["row"], row["column"])
        assert key not in result, key
        result[key] = row["printed"]
    return result


def _put_cell(cells: dict[tuple[str, str, str], str], key: tuple[str, str, str], value: str) -> None:
    assert key not in cells or cells[key] == value, (key, cells.get(key), value)
    cells[key] = value


def _extract_cells(document: dict) -> dict[tuple[str, str, str], str]:
    cells: dict[tuple[str, str, str], str] = {}
    for species, block in document["species"].items():
        for observation in block["observations"]:
            locator = observation.get("locator", {})
            table = str(locator.get("table", ""))
            if table not in TABLES:
                continue
            values = observation["values"]
            points = values.get("points")
            if isinstance(points, list):
                items = [(point, point.get("row_as_printed")) for point in points]
            else:
                items = [(observation, values.get("row_as_printed"))]
            for item, row_label in items:
                if not row_label:
                    continue
                item_locator = item.get("locator", locator)
                column = item_locator.get("column")
                if column == "aSi (in copper)":
                    column = TABLES[table]["columns"][8 if table == "II" else 7]
                elif column == "aSiO2 (in slag)":
                    column = TABLES[table]["columns"][9 if table == "II" else 8]
                printed = item.get("value_as_printed") or item.get("value_as_published")
                if printed is None:
                    printed = values.get("activity_as_printed")
                if column and printed is not None:
                    _put_cell(cells, (table, row_label, column), str(printed))
                conditions = item.get("point_conditions", observation.get("point_conditions", {}))
                oxygen = conditions.get("oxygen_partial_pressure", {})
                oxygen_locator = oxygen.get("locator", {})
                if table in {"III", "IV"} and oxygen_locator.get("column") == "pO2":
                    p_column = TABLES[table]["columns"][0]
                    _put_cell(cells, (table, row_label, p_column), str(oxygen["value"]))

    for species_block in document["species"].values():
        for row in species_block.get("context", []):
            values = row.get("values", {})
            if row.get("observation_id") == "tranell_2000_table_i_master_slags_context":
                for item in values["rows"]:
                    _put_cell(cells, ("I", item["slag"], "CaO/SiO2 (wt%)"), item["CaO_SiO2_mass_ratio_wt_as_printed"])
                    _put_cell(cells, ("I", item["slag"], "TiO2 (wt%)"), item["TiO2_wt_pct_as_printed"])
            elif row.get("observation_id") == "tranell_2000_table_ii_unreacted_copper_reference_context":
                for column, printed in values["values_as_printed"].items():
                    _put_cell(cells, ("II", "Reference*", column), printed)
            elif row.get("observation_id") == "tranell_2000_table_iv_ti_icp_upper_bounds_context":
                for item in values["values_as_printed"]:
                    _put_cell(cells, ("IV", item["row"], "XTi ICP (wt ppm)"), item["XTi_ICP_wt_ppm"])
    return cells


def _assert_rendered_grid_matches(document: dict, audit_cells: dict, tables: dict = TABLES) -> None:
    extracted = _extract_cells(document)
    for slag, ratio, titania in TABLE_I:
        assert audit_cells[("I", slag, "CaO/SiO2 (wt%)")] == ratio
        assert audit_cells[("I", slag, "TiO2 (wt%)")] == titania
        assert extracted[("I", slag, "CaO/SiO2 (wt%)")] == ratio
        assert extracted[("I", slag, "TiO2 (wt%)")] == titania
    for table, definition in tables.items():
        for row_label, row_values in definition["rows"]:
            for column, printed in zip(definition["columns"], row_values, strict=True):
                assert audit_cells[(table, row_label, column)] == printed
                key = (table, row_label, column)
                if printed in {"-", "*"}:
                    assert extracted.get(key, printed) == printed, (key, extracted.get(key))
                else:
                    assert extracted[key] == printed, (key, extracted.get(key), printed)
    _assert_typed_numeric_cells_match(document, tables)


def _printed_numeric_cell(table: str, row: str, column: str, tables: dict) -> Decimal | None:
    definition = tables[table]
    column_index = definition["columns"].index(column)
    row_values = next(values for label, values in definition["rows"] if label == row)
    printed = row_values[column_index]
    if printed in {"-", "*"} or printed.startswith("<"):
        return None
    return Decimal(printed.strip("()"))


def _canonical_table_column(table: str, column: str, tables: dict = TABLES) -> str:
    if column == "aSi (in copper)":
        return tables[table]["columns"][8 if table == "II" else 7]
    if column == "aSiO2 (in slag)":
        return tables[table]["columns"][9 if table == "II" else 8]
    return column


def _table_column(table: str, item: dict, tables: dict = TABLES) -> str:
    column = item.get("locator", {}).get("column")
    return _canonical_table_column(table, column, tables)


def _assert_typed_numeric_cells_match(document: dict, tables: dict) -> None:
    for block in document["species"].values():
        for observation in block["observations"]:
            table = str(observation.get("locator", {}).get("table", ""))
            if table not in tables:
                continue
            values = observation["values"]
            points = values.get("points")
            if isinstance(points, list):
                numeric_items = [(point, point.get("value")) for point in points]
            elif "activity" in values and values.get("row_as_printed"):
                numeric_items = [(observation, values["activity"])]
            else:
                continue
            for item, actual in numeric_items:
                row = item.get("row_as_printed") or values.get("row_as_printed")
                column = _table_column(table, item if item is not observation else observation, tables)
                expected = _printed_numeric_cell(table, row, column, tables)
                assert expected is not None, (table, row, column, actual)
                assert isinstance(actual, (str, int, float))
                assert Decimal(str(actual)) == expected, (table, row, column, actual, expected)


def _assert_physical_locators(value: object) -> None:
    if isinstance(value, dict):
        if "page" in value and "pdf_page_index" in value:
            assert value["page"] == value["pdf_page_index"] + 1
        for child in value.values():
            _assert_physical_locators(child)
    elif isinstance(value, list):
        for child in value:
            _assert_physical_locators(child)


def _assert_row_conditions(observation: dict, row_label: str, table: str) -> None:
    conditions = observation.get("point_conditions", {})
    sample = observation.get("sample") or observation.get("values", {}).get("sample")
    assert sample == f"Slag {row_label.split()[0].split('(')[0]}"
    temperature = conditions["temperature_K"]
    assert temperature["state"] == {"tag": "value", "value": "1873"}
    assert temperature["locator"]["page"] == TABLES[table]["page"]
    assert temperature["locator"]["pdf_page_index"] + 1 == temperature["locator"]["page"]
    assert conditions["composition"]["state"]["tag"] == "unknown"
    assert conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"
    expected_hours = (
        2 if "(2h)" in row_label
        else 6 if "(6h)" in row_label
        else 18 if "(18h)" in row_label
        else 8 if table == "II" and "(24h)" not in row_label
        else 24
    )
    assert conditions["equilibration_time_h"]["state"] == {
        "tag": "value", "value": str(expected_hours)
    }
    assert conditions["atmosphere"]["state"]["tag"] == "value"
    oxygen = conditions["oxygen_partial_pressure"]
    assert oxygen["units"] == "atm"
    assert oxygen["state"]["tag"] == "value"
    if table == "IV":
        assert oxygen["approximate"] is True


def _assert_migrated_numeric_values(document: dict, migrated_rows: list) -> None:
    checked = 0
    for block in document["species"].values():
        for observation in block["observations"]:
            table = str(observation.get("locator", {}).get("table", ""))
            if table not in TABLES:
                continue
            values = observation["values"]
            points = values.get("points")
            if isinstance(points, list):
                items = points
            elif "activity" in values and values.get("row_as_printed"):
                items = [observation]
            else:
                continue
            expected = []
            for item in items:
                row = item.get("row_as_printed") or values.get("row_as_printed")
                column = _table_column(table, item)
                amount = _printed_numeric_cell(table, row, column, TABLES)
                assert amount is not None, (table, row, column)
                expected.append(amount)
            prefix = f"{SOURCE_ID}::{observation['observation_id']}"
            actual = [
                row.value.point
                for row in migrated_rows
                if row.observation_id == prefix or row.observation_id.startswith(prefix + "::")
            ]
            assert len(actual) == len(expected), (observation["observation_id"], len(actual), len(expected))
            assert all(amount is not None for amount in actual)
            assert sorted(actual) == sorted(expected), (observation["observation_id"], actual, expected)
            checked += len(actual)
    assert checked == 367


def test_rendered_tables_bind_every_cell_and_migrate_conditions() -> None:
    document = _document()
    audit_cells = _audit_cells()
    _assert_physical_locators(document)
    _assert_rendered_grid_matches(document, audit_cells)

    # Mutation checks: a wrong independent render transcription and a wrong
    # extract cell both fail the same full-grid assertion.
    changed_grid = copy.deepcopy(TABLES)
    row = list(changed_grid["III"]["rows"][0][1])
    row[2] = "28.0"
    changed_grid["III"]["rows"] = (("1", tuple(row)), *changed_grid["III"]["rows"][1:])
    try:
        _assert_rendered_grid_matches(document, audit_cells, changed_grid)
    except AssertionError:
        pass
    else:
        raise AssertionError("mutating a render-grid cell did not fail")

    changed_extract = copy.deepcopy(document)
    assay = next(
        observation
        for observation in changed_extract["species"]["Si"]["observations"]
        if observation.get("observation_id") == "tranell_2000_table_iii_xsi_icp"
    )
    point = next(point for point in assay["values"]["points"] if point["row_as_printed"] == "1")
    point["value"] = "28.0"
    point["value_as_printed"] = "28.0"
    try:
        _assert_rendered_grid_matches(changed_extract, audit_cells)
    except AssertionError:
        pass
    else:
        raise AssertionError("mutating an extract cell did not fail")

    numeric_only_mutation = copy.deepcopy(document)
    numeric_only_assay = next(
        observation
        for observation in numeric_only_mutation["species"]["Si"]["observations"]
        if observation.get("observation_id") == "tranell_2000_table_iii_xsi_icp"
    )
    numeric_only_point = next(
        point for point in numeric_only_assay["values"]["points"]
        if point["row_as_printed"] == "1"
    )
    numeric_only_point["value"] = "28.0"
    try:
        _assert_rendered_grid_matches(numeric_only_mutation, audit_cells)
    except AssertionError:
        pass
    else:
        raise AssertionError("changing only the typed numeric value did not fail")

    source_species = {
        observation["observation_id"]: formula
        for formula, block in document["species"].items()
        for observation in block["observations"]
    }
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert not result.validation.hard_issues
    rows = list(result.observations.values())
    assert len(rows) == 367
    _assert_migrated_numeric_values(document, rows)

    for row in rows:
        local_id = row.observation_id.split(f"{SOURCE_ID}::", 1)[1]
        expected_species = next(
            formula
            for observation_id, formula in sorted(
                source_species.items(), key=lambda item: len(item[0]), reverse=True
            )
            if local_id.startswith(observation_id)
        )
        assert row.identity.species.formula == expected_species
        assert row.identity.temperature_K.is_value
        assert row.point_conditions is not None
        conditions = row.point_conditions
        assert conditions["temperature_K"].state.is_value
        assert conditions["composition"].state.is_unknown
        assert conditions["total_pressure_Pa"].state.is_unknown
        assert conditions["fO2_Pa"].state.is_value
        if row.identity.quantity.value in {Quantity.ACTIVITY, Quantity.VALENCE_RATIO}:
            assert row.identity.fO2_Pa.is_value

    for formula, block in document["species"].items():
        for observation in block["observations"]:
            table = observation["locator"]["table"]
            points = observation["values"].get("points")
            if points:
                for point in points:
                    _assert_row_conditions(point, point["row_as_printed"], table)
            elif observation["values"].get("row_as_printed"):
                _assert_row_conditions(observation, observation["values"]["row_as_printed"], table)

    composition_observations = [
        observation
        for block in document["species"].values()
        for observation in block["observations"]
        if observation["values"].get("quantity") == "equilibrium_phase_composition"
    ]
    assert sum(len(observation["values"]["points"]) for observation in composition_observations) == 238
    for observation in composition_observations:
        values = observation["values"]
        material = (
            "slag"
            if observation["observation_id"].endswith(("cu_in_slag", "mo_in_slag"))
            else "copper button"
        )
        assert observation["phase"] == "l"
        assert values["basis_as_published"].endswith(f"in {material}; wt ppm")
        for point in values["points"]:
            assert point["sample_material_as_published"] == material
            assert point["method_as_printed"] in {"GDMS", "ICP-AES", "X-ray fluorescence"}
            assert "water" in point["quench_statement"]["text"]
            assert "ex-situ" in point["quench_statement"]["text"]
            assert point["quench_statement"]["locator"]["page"] == 2

    migrated_compositions = [
        row for row in rows
        if row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    ]
    assert len(migrated_compositions) == 238
    assert all(row.identity.species.phase.is_value and row.identity.species.phase.value.value == "l"
               for row in migrated_compositions)
    migrated_titanium_redox = [
        row for row in rows if row.identity.quantity.value is Quantity.VALENCE_RATIO
    ]
    assert len(migrated_titanium_redox) == 45
    assert all(row.identity.species.phase.is_unknown for row in migrated_titanium_redox)
    assert all(
        "phase_as_measured" not in point
        for observation in document["species"]["Ti"]["observations"]
        if observation["values"].get("quantity") == "valence_ratio"
        for point in observation["values"]["points"]
    )
