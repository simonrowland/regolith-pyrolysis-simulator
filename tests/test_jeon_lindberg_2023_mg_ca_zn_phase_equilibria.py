from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.battery.test_migrate import _migrate_real_extract

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/jeon-lindberg-2023-mg-ca-zn-oxide-equilibria.yaml"

# Table 1 prints a composition only on the first row of a run of blanks.
# A blank cell means the same as the nearest printed cell above.
DITTO = "table blank = same as above"
ROW_12_RATIO = (
    ("CaO", Decimal("0.1")),
    ("MgO", Decimal("0.2")),
    ("ZnO", Decimal("0.7")),
)
# The methods text groups Experimental No. 11 to 16 and points at Table 1.
# It does not list their compositions and does not say they share one slag.
# Table 1 prints 0.3CaO-0.2MgO-0.5ZnO on row 11 and 0.1CaO-0.2MgO-0.7ZnO on
# row 12, so rows 13-16 are dittos of row 12, not a text assignment.
METHODS_SENTENCE = (
    "The initial compositions were set in triangle area on expected phase "
    "diagrams as shown in Table 1 (Experimental No. 11 to 16)."
)

# Independently transcribed from the 600 dpi physical page 5, Table 2.
RENDER_GRID = (
    (1, "Monoxide", "MgO", 85.4),
    (1, "Monoxide", "ZnO", 14.6),
    (1, "Zincite", "MgO", 0.0),
    (1, "Zincite", "ZnO", 100.0),
    (7, "Monoxide#1", "CaO", 1.3),
    (7, "Monoxide#1", "MgO", 81.3),
    (7, "Monoxide#1", "ZnO", 17.4),
    (7, "Monoxide#2", "CaO", 93.4),
)


def _document() -> dict:
    loaded = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block.get("observations", [])
    }


def _source_row(row: str) -> str:
    return f"source locator: Table 1 row {row}"


def _assert_ditto(block: dict, source_row: str) -> None:
    inference = block["inference"]
    assert inference["relation"] == "extract_inference"
    assert inference["inputs"] == [DITTO, _source_row(source_row)]


def _assert_migrated_ditto(located, source_row: str) -> None:
    assert located.inference is not None
    assert located.inference.relation == "extract_inference"
    assert located.inference.inputs == (DITTO, _source_row(source_row))


def _assert_conversion_only(located, original: Decimal) -> None:
    """Printed Celsius becomes kelvin and does not carry a table-blank ditto."""

    inference = located.inference
    assert inference is not None
    assert inference.relation == "celsius_to_kelvin"
    assert inference.output_unit == "K"
    assert DITTO not in inference.inputs
    printed = dict(inference.parameters)["original"]
    assert printed.state.value == original
    assert printed.inference is None


def _assert_ditto_and_conversion(located, source_row: str, original: Decimal) -> None:
    """A blank temperature keeps the ditto and the Celsius-to-kelvin conversion."""

    inference = located.inference
    assert inference is not None
    assert inference.relation == "extract_inference"
    assert inference.inputs == (DITTO, _source_row(source_row))
    assert inference.output_unit == "K"
    printed = dict(inference.parameters)["original"]
    assert printed.state.value == original
    conversion = printed.inference
    assert conversion is not None
    assert conversion.relation == "celsius_to_kelvin"
    assert conversion.output_unit == "K"
    assert dict(conversion.parameters)["original"].state.value == original


def _assert_migrated_temperature(located, sample: str) -> None:
    if sample == "13":
        _assert_conversion_only(located, Decimal("1550"))
    elif sample == "14":
        _assert_ditto_and_conversion(located, "13", Decimal("1550"))
    elif sample == "15":
        _assert_conversion_only(located, Decimal("1600"))
    elif sample == "16":
        _assert_ditto_and_conversion(located, "15", Decimal("1600"))
    else:
        raise AssertionError(sample)


def _assert_grid(document: dict, grid=RENDER_GRID) -> None:
    rows = _rows(document)
    for sample, phase, species, expected in grid:
        row = next(
            row for row in rows.values()
            if row["point_conditions"]["sample_id"]["state"]["value"] == str(sample)
            and row["phase_as_published"] == phase
            and row["values"]["component_as_published"] == species
        )
        assert row["values"]["value"] == expected
        assert row["locator"]["page"] == 5
        assert row["locator"]["pdf_page_index"] == 4
        assert row["locator"]["published_page"] == 12739


def test_table2_grid_matches_600dpi_render_and_rejects_cell_mutations() -> None:
    document = _document()
    _assert_grid(document)
    changed_grid = list(RENDER_GRID)
    changed_grid[0] = (1, "Monoxide", "MgO", 85.3)
    with pytest.raises(AssertionError):
        _assert_grid(document, changed_grid)
    changed_extract = deepcopy(document)
    row = _rows(changed_extract)["jeon23_s1_r3_mgo"]
    row["values"]["value"] = 85.3
    with pytest.raises(AssertionError):
        _assert_grid(changed_extract)


def test_isolated_migration_preserves_every_point_species_temperature_and_conditions(tmp_path: Path) -> None:
    result = _migrate_real_extract(
        tmp_path, "jeon-lindberg-2023-mg-ca-zn-oxide-equilibria.yaml"
    )
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.observations) == 102
    source_rows = _rows(_document())
    migrated = {
        item.observation_id.rsplit("::", 1)[-1]: item
        for item in result.observations.values()
    }
    assert set(migrated) == set(source_rows)
    expected_species = {
        row["observation_id"]: species
        for species, block in _document()["species"].items()
        for row in block.get("observations", [])
    }
    for key, item in migrated.items():
        assert item.value.kind.value == "point"
        assert item.identity.species.formula == expected_species[key]
        assert item.identity.temperature_K.is_value
        assert item.point_conditions["sample_id"].state.is_value
        assert item.point_conditions["atmosphere"].state.is_value
        sample = str(item.point_conditions["sample_id"].state.value)
        time = item.point_conditions["equilibration_time_hours"]
        assert time.state.is_value
        temperature = item.point_conditions["temperature_K"].state.value
        if sample in {"13", "14", "15", "16"}:
            # The earlier oracle required these four samples to stay unknown.
            # Table 1 leaves the cells blank, and the methods sentence does
            # not assign them a slag, so the blank is a ditto of the nearest
            # printed row. Identity follows the printed row-12 profile: the
            # ratio is the point composition, and this quantity does not
            # keep composition on the identity.
            assert item.identity.composition.is_not_applicable
            composition = item.point_conditions["composition"]
            assert composition.state.is_value
            assert composition.state.value.components == ROW_12_RATIO
            assert composition.state.value.basis == "printed mole ratio"
            _assert_migrated_ditto(composition, "12")
            printed = item.point_conditions["initial_composition_as_published"]
            assert printed.state.value == "0.1CaO-0.2MgO-0.7ZnO"
            _assert_migrated_ditto(printed, "12")
            _assert_migrated_ditto(item.point_conditions["atmosphere"], "7")
            assert item.point_conditions["atmosphere"].state.value == "Air"
            if sample in {"13", "14"}:
                assert time.state.value == Decimal("10")
                _assert_migrated_ditto(time, "11")
                assert temperature == Decimal("1823.15")
            else:
                assert time.state.value == Decimal("6")
                assert temperature == Decimal("1873.15")
                if sample == "15":
                    assert time.inference is None
                else:
                    _assert_migrated_ditto(time, "15")
            source_temperature = source_rows[key]["values"]["T_C"]
            if sample == "14":
                assert source_temperature["value"] == 1550
                _assert_ditto(source_temperature, "13")
            elif sample == "16":
                assert source_temperature["value"] == 1600
                _assert_ditto(source_temperature, "15")
            elif sample == "13":
                assert source_temperature == 1550
            else:
                assert source_temperature == 1600
            _assert_migrated_temperature(
                item.point_conditions["temperature_K"], sample
            )
        elif sample == "12":
            composition = item.point_conditions["composition"]
            assert composition.state.value.components == ROW_12_RATIO
            assert composition.inference is None
            assert temperature == Decimal("1773.15")
        assert not item.notices
        if item.uncertainty.kind.value == "printed":
            assert item.uncertainty.verbatim["reported_status"] == "printed"
            assert item.uncertainty.verbatim["as_published"] != "± 0"
    assert migrated["jeon23_s1_r3_mgo"].value.point == Decimal("85.4")
    assert migrated["jeon23_s1_r3_zno"].value.point == Decimal("14.6")
    assert migrated["jeon23_s7_r15_cao"].value.point == Decimal("1.3")

    # Dropping the blank-temperature inference must turn this pin red.
    # The printed Celsius and the kelvin conversion stay.
    mutated = deepcopy(_document())
    blank_ids: list[str] = []
    for row in _rows(mutated).values():
        sample = row["point_conditions"]["sample_id"]["state"]["value"]
        if sample not in {"14", "16"}:
            continue
        temperature = row["values"]["T_C"]
        assert isinstance(temperature, dict)
        del temperature["inference"]
        blank_ids.append(row["observation_id"])
    assert set(blank_ids) == {
        row["observation_id"]
        for row in _rows(_document()).values()
        if row["point_conditions"]["sample_id"]["state"]["value"] in {"14", "16"}
    }
    dropped = _migrate_real_extract(
        tmp_path / "dropped-temperature-inference",
        "jeon-lindberg-2023-mg-ca-zn-oxide-equilibria.yaml",
        extract_doc=mutated,
    )
    dropped_rows = {
        item.observation_id.rsplit("::", 1)[-1]: item
        for item in dropped.observations.values()
    }
    for key in blank_ids:
        item = dropped_rows[key]
        sample = str(item.point_conditions["sample_id"].state.value)
        located = item.point_conditions["temperature_K"]
        original = Decimal("1550") if sample == "14" else Decimal("1600")
        _assert_conversion_only(located, original)
        with pytest.raises(AssertionError):
            _assert_ditto_and_conversion(
                located, "13" if sample == "14" else "15", original
            )


def _assert_table1_ditto(document: dict) -> None:
    """Rows 13-16 borrow every blank Table 1 cell from the nearest printed row."""

    rows = _rows(document)
    experiments = {item["experiment_id"]: item["sample"] for item in document["experiments"]}
    row_11 = experiments["jeon23_sample_11"]["initial_composition"]
    assert row_11["state"]["value"]["components"] == [
        ["CaO", "0.3"],
        ["MgO", "0.2"],
        ["ZnO", "0.5"],
    ]
    assert "inference" not in row_11
    row_12 = experiments["jeon23_sample_12"]["initial_composition"]
    assert row_12["locator"]["row"] == "12"
    assert "inference" not in row_12
    expected_experiment = {
        "13": {"temperature_C": 1550, "time_hours": ("10", "11"), "temperature_source": None},
        "14": {"temperature_C": ("1550", "13"), "time_hours": ("10", "11"), "temperature_source": "13"},
        "15": {"temperature_C": 1600, "time_hours": 6, "temperature_source": None},
        "16": {"temperature_C": ("1600", "15"), "time_hours": ("6", "15"), "temperature_source": "15"},
    }
    for sample, expected in expected_experiment.items():
        block = experiments[f"jeon23_sample_{sample}"]
        composition = block["initial_composition"]
        assert composition["locator"]["row"] == "12"
        _assert_ditto(composition, "12")
        _assert_ditto(block["atmosphere"], "7")
        assert block["atmosphere"]["value"] == "Air"
        temperature = block["temperature_C"]
        if expected["temperature_source"] is None:
            assert temperature == expected["temperature_C"]
        else:
            assert temperature["value"] == int(expected["temperature_C"][0])
            _assert_ditto(temperature, expected["temperature_source"])
        time = block["time_hours"]
        if isinstance(expected["time_hours"], tuple):
            assert time["value"] == int(expected["time_hours"][0])
            _assert_ditto(time, expected["time_hours"][1])
        else:
            assert time == expected["time_hours"]
            assert not isinstance(time, dict)
    for row in rows.values():
        sample = row["point_conditions"]["sample_id"]["state"]["value"]
        if sample not in {"13", "14", "15", "16"}:
            continue
        printed = row["point_conditions"]["initial_composition_as_published"]
        composition = row["point_conditions"]["composition"]
        assert printed["state"]["value"] == "0.1CaO-0.2MgO-0.7ZnO"
        assert printed["locator"]["row"] == "12"
        assert composition["locator"]["row"] == "12"
        assert composition["state"]["value"]["components"] == [
            ["CaO", "0.1"],
            ["MgO", "0.2"],
            ["ZnO", "0.7"],
        ]
        _assert_ditto(printed, "12")
        _assert_ditto(composition, "12")
        atmosphere = row["point_conditions"]["atmosphere"]
        assert atmosphere["locator"]["row"] == "7"
        _assert_ditto(atmosphere, "7")
        time = row["point_conditions"]["equilibration_time_hours"]
        temperature = row["values"]["T_C"]
        if sample in {"13", "14"}:
            assert time["state"]["value"] == 10
            assert time["locator"]["row"] == "11"
            _assert_ditto(time, "11")
        elif sample == "15":
            assert time["state"]["value"] == 6
            assert time["locator"]["row"] == "15"
            assert "inference" not in time
            assert temperature == 1600
        else:
            assert time["state"]["value"] == 6
            assert time["locator"]["row"] == "15"
            _assert_ditto(time, "15")
        if sample == "13":
            assert temperature == 1550
        elif sample == "14":
            assert temperature["value"] == 1550
            _assert_ditto(temperature, "13")
        elif sample == "16":
            assert temperature["value"] == 1600
            _assert_ditto(temperature, "15")


def test_table_row_count_and_table1_condition_disposition() -> None:
    document = _document()
    rows = _rows(document)
    assert len(rows) == 102
    assert {row["point_conditions"]["sample_id"]["state"]["value"] for row in rows.values()} == {str(i) for i in range(1, 17)}
    assert "Experimental No. 11 to 16" in METHODS_SENTENCE
    _assert_table1_ditto(document)

    changed = deepcopy(document)
    changed_row = next(
        row for row in _rows(changed).values()
        if row["point_conditions"]["sample_id"]["state"]["value"] == "13"
    )
    changed_row["point_conditions"]["composition"]["inference"]["inputs"] = ["silent value"]
    with pytest.raises(AssertionError):
        _assert_table1_ditto(changed)

    context = document["species"]["SiO2"]["context"]
    footnote = next(row for row in context if row["observation_id"] == "jeon23_table2_p2o5_bound_context")
    assert footnote["values"]["bound_as_published"] == "<0.1 mol%"
    assert footnote["values"]["temperature_as_published"] == "1000 °C"
    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 11
    assert {row["locator"]["figure"] for row in figures} == {str(i) for i in range(1, 12)}
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("points" not in row["values"] for row in figures)
    assert all(row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 for row in figures)
