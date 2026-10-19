from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import Migrator
from tests.literature_extract_test_helpers import iter_locators


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "leister-1999-fe-v-redox-2000c"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"

# Transcribed independently from the 600 dpi render of Table 2, physical page 3.
TABLE_2_RENDER = [
    ("33Na2O · 67SiO2", "1600", "air", "30", "97", "3"),
    ("33Na2O · 67SiO2", "1600", "argon", "30", "94", "6"),
    ("33Na2O · 67SiO2", "1800", "argon", "30", "86", "14"),
    ("33Na2O · 67SiO2", "1800", "argon", "160", "70", "30"),
    ("33Na2O · 67SiO2", "2000", "argon", "30", "80", "20"),
    ("15Na2O · 85SiO2", "1500", "air", "960", "89", "11"),
    ("15Na2O · 85SiO2", "1550", "air", "960", "87", "13"),
    ("15Na2O · 85SiO2", "1600", "air", "720", "86", "14"),
    ("15Na2O · 85SiO2", "1700", "air", "360", "82", "17"),
    ("15Na2O · 85SiO2", "1600", "argon", "300", "73", "27"),
    ("15Na2O · 85SiO2", "1800", "argon", "145", "65", "35"),
    ("15Na2O · 85SiO2", "2000", "argon", "35", "52", "48"),
]

# Transcribed independently from the 600 dpi render of Table 3, physical page 5.
TABLE_3_RENDER = [
    ("33Na2O · 67SiO2", "1600", "air", "45", "95", "5", "n.d."),
    ("33Na2O · 67SiO2", "1600", "argon", "45", "64", "36", "n.d."),
    ("33Na2O · 67SiO2", "1800", "argon", "45", "44", "56", "n.d."),
    ("33Na2O · 67SiO2", "2000", "argon", "45", "39", "61", "n.d."),
    ("15Na2O · 85SiO2", "1600", "reduced", "60", "<0.5", "28", "72"),
    ("15Na2O · 85SiO2", "1600", "air", "45", "84", "14", "n.d."),
    ("15Na2O · 85SiO2", "1600", "argon", "45", "70", "30", "n.d."),
    ("15Na2O · 85SiO2", "1600", "argon", "245", "54", "46", "n.d."),
    ("15Na2O · 85SiO2", "1800", "argon", "145", "51", "49", "n.d."),
    ("15Na2O · 85SiO2", "2000", "argon", "40", "22", "78", "n.d."),
]


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text())


def _table_grid(document: dict, table_number: str) -> list[tuple[str, ...]]:
    columns = {"2": ("fe3", "fe2"), "3": ("v5", "v4", "v3")}[table_number]
    rows: dict[str, dict[str, str]] = {}
    for species, body in document["species"].items():
        for observation in body.get("observations", []):
            if observation["locator"].get("table") != table_number:
                continue
            column = next(
                name for name in columns if observation["observation_id"].endswith(name + "_valence_fraction")
            )
            for point in observation["values"]["points"]:
                row_number = point["locator"]["row"]
                conditions = point["point_conditions"]
                row = rows.setdefault(
                    row_number,
                    {
                        "sample": point["sample_as_published"],
                        "temperature": point["T_C"],
                        "atmosphere": conditions["atmosphere"]["state"]["value"],
                        "holding_time": conditions["holding_time_min"]["state"]["value"],
                    },
                )
                row[column] = point["value_as_printed"]
    return [
        tuple(row[key] for key in ("sample", "temperature", "atmosphere", "holding_time", *columns))
        for _, row in sorted(rows.items(), key=lambda item: int(item[0]))
    ]


def _migrate() -> Migrator:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator


def test_render_transcribed_tables_bind_values_to_samples_and_conditions() -> None:
    document = _document()
    assert _table_grid(document, "2") == TABLE_2_RENDER
    assert _table_grid(document, "3") == TABLE_3_RENDER

    for locator in iter_locators(document):
        assert locator["page"] == locator["pdf_page_index"] + 1
        assert "published_page" in locator

    for species, body in document["species"].items():
        for observation in body.get("observations", []):
            for point in observation["values"]["points"]:
                assert point["T_C"]
                assert point["point_conditions"]["T_C"]["state"]["value"] == point["T_C"]
                for condition in point["point_conditions"].values():
                    assert "state" in condition and "locator" in condition
                assert point["sample_id"].endswith(f"row{int(point['locator']['row']):02d}")
                assert point["quench_statement"]["preservation"] in {
                    "asserted_preserved",
                    "asserted_altered",
                    "not_stated",
                }
                expected_quench_page = 2 if species == "Fe" else 7
                assert point["quench_statement"]["locator"]["page"] == expected_quench_page


def test_render_and_extract_cell_mutations_fail_table_binding() -> None:
    document = _document()

    changed_render = deepcopy(TABLE_2_RENDER)
    changed_render[0] = (*changed_render[0][:-1], "4")
    with pytest.raises(AssertionError):
        assert _table_grid(document, "2") == changed_render

    changed_extract = deepcopy(document)
    changed_extract["species"]["Fe"]["observations"][0]["values"]["points"][0][
        "value_as_printed"
    ] = "96"
    with pytest.raises(AssertionError):
        assert _table_grid(changed_extract, "2") == TABLE_2_RENDER


def test_isolated_migration_preserves_species_temperature_conditions_and_scored_values() -> None:
    document = _document()
    migrator = _migrate()
    rows = list(migrator.result.observations.values())
    assert len(rows) == 44
    assert not migrator.result.validation.hard_issues
    assert all(row.value.kind is ValueKind.POINT for row in rows)
    assert all(row.identity.quantity.value is Quantity.VALENCE_RATIO for row in rows)

    expected_formulae = {
        observation["observation_id"]: species
        for species, body in document["species"].items()
        for observation in body.get("observations", [])
    }
    for row in rows:
        source_observation_id = row.observation_id.split("::", maxsplit=2)[1]
        assert row.identity.species.formula == expected_formulae[source_observation_id]
        assert row.identity.temperature_K.is_value
        assert row.point_conditions is not None
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["temperature_K"].locator is not None
        assert row.point_conditions["atmosphere"].state.is_value
        assert row.point_conditions["atmosphere"].locator is not None
        assert row.point_conditions["holding_time_min"].state.is_value
        assert row.point_conditions["holding_time_min"].locator is not None

    assert sum(point["value_as_printed"] == "n.d." for point in document["species"]["V"]["observations"][2]["values"]["points"]) == 9
    bound = document["species"]["V"]["observations"][0]["values"]["points"][4]
    assert bound["value"]["kind"] == "bound"
    assert bound["value"]["bound_operator"] == "<"
    assert bound["value_as_printed"] == "<0.5"
