"""Source pins and isolated migration for Frandsen (1933)."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.identity import celsius_to_kelvin, profile_for
from simulator.battery.migrate import Migrator, REPO_ROOT
from tools.validate_literature_extracts import validate_extract_document


EXTRACT = REPO_ROOT / (
    "data/literature/extracts/"
    "frandsen-1933-phosphorus-pentoxide-calorimetry.yaml"
)
SID = EXTRACT.stem

RENDER_PINS = (
    (
        "frandsen_1933_phosphorus_pentoxide_calorimetry_p012_t04_r18_c09",
        "27400", "27,400", 12, "table", "2",
    ),
    (
        "frandsen_1933_phosphorus_pentoxide_calorimetry_p012_t04_r18_c08",
        "45950", "45,950", 12, "table", "2",
    ),
    (
        "frandsen_1933_phosphorus_pentoxide_calorimetry_p019_t06_r03_c11",
        "237", "237", 19, "table", "4",
    ),
    (
        "frandsen_1933_phosphorus_pentoxide_calorimetry_vapor_heat_content_358C_eq15",
        "19900", "19,900 ± 1,300", 20, "equation", "15",
    ),
    (
        "frandsen_1933_phosphorus_pentoxide_calorimetry_crystal_heat_content_358C_eq19",
        "11070", "11,070 ± 170", 20, "equation", "19",
    ),
    (
        "frandsen_1933_phosphorus_pentoxide_calorimetry_p023_t07_r03_c07",
        "43890", "43,890", 23, "table", "5",
    ),
    (
        "frandsen_1933_phosphorus_pentoxide_calorimetry_vapor_cp_eq17",
        "36.8", "36.8 ± 1.0", 16, "equation", "17",
    ),
    (
        "frandsen_1933_phosphorus_pentoxide_calorimetry_heat_of_sublimation_eq21",
        "8800", "8,800 ± 1,300", 21, "equation", "21",
    ),
    (
        "frandsen_1933_phosphorus_pentoxide_calorimetry_heat_solution_metaphosphoric_acid_eq26",
        "42000", "42,000 ± 2,100", 24, "equation", "26",
    ),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _rows_by_id(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for body in document["species"].values()
        for container in ("observations", "context")
        for row in body.get(container, [])
    }


def _assert_render_pins(document: dict) -> None:
    rows = _rows_by_id(document)
    for observation_id, value, printed_value, page, locator_key, locator_value in RENDER_PINS:
        row = rows[observation_id]
        assert str(row["values"]["value"]) == value
        assert row["values"]["value_as_published"] == printed_value
        assert row["locator"]["page"] == page
        assert str(row["locator"][locator_key]) == locator_value


def test_rendered_cells_and_printed_table_row_counts() -> None:
    document = _document()
    _assert_render_pins(document)
    rows = [
        row
        for body in document["species"].values()
        for container in ("observations", "context")
        for row in body.get(container, [])
    ]
    target_rows = [
        row
        for row in rows
        if "value" in row.get("values", {})
        and row.get("locator", {}).get("table") in {"2", "4", "5"}
    ]
    by_table = Counter(row["locator"]["table"] for row in target_rows)
    assert by_table == {"2": 26, "4": 20, "5": 5}
    table2_quantities = Counter(
        row["values"].get("quantity", row["values"].get("quantity_as_printed"))
        for row in target_rows
        if row["locator"]["table"] == "2"
    )
    assert table2_quantities == {"H_minus_H298": 13, "heat of solution": 13}
    observations = document["species"]["P2O5"]["observations"]
    contexts = document["species"]["P2O5"]["context"]
    assert len(observations) == 37
    assert all(row["values"]["admission_status"] == "admitted" for row in observations)
    assert len(contexts) == 63
    assert sum(row["type"] == "calorimeter_run_inputs" for row in contexts) == 38
    assert sum(row["type"] == "calorimetric_result" for row in contexts) == 25
    assert all(
        row["values"].get("status") == "typed_refusal"
        and row["values"].get("admission_status") == "typed_refusal"
        for row in contexts
        if row["type"] == "calorimetric_result"
    )

    mutated = deepcopy(document)
    mutated_rows = _rows_by_id(mutated)
    mutated_rows[RENDER_PINS[0][0]]["values"]["value"] = "27401"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)


def test_extract_document_validates_against_literature_schema() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=SID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []


def test_equation_reaction_locators_and_context_lineage_after_migration() -> None:
    document = _document()
    source_observations = document["species"]["P2O5"]["observations"]
    source_context = document["species"]["P2O5"]["context"]
    rows_by_id = _rows_by_id(document)

    equation_rows = [
        row
        for row in source_observations + source_context
        if row.get("values", {}).get("equation_as_printed")
    ]
    assert len(equation_rows) == 11

    orthophosphoric_acid = rows_by_id[
        "frandsen_1933_phosphorus_pentoxide_calorimetry_heat_solution_orthophosphoric_acid_eq25"
    ]
    assert orthophosphoric_acid["values"]["reaction_as_printed"] == (
        "P2O5(cr) + 3 H2O(l) → 2 H3PO4(aq)"
    )

    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    retained_context_ids = {
        context["context_id"].split("::context::", 1)[1]
        for work_context in migrator.result.context_by_work.values()
        for context in work_context
        if context["context_id"].startswith(f"{SID}::context::")
    }
    for row in equation_rows:
        supporting_ids = row["values"]["supporting_context_row_ids"]
        assert supporting_ids
        assert set(supporting_ids) <= retained_context_ids
        assert any(
            rows_by_id[context_id]["type"] == "calorimeter_run_inputs"
            for context_id in supporting_ids
        )

    for observation_id in (
        "frandsen_1933_phosphorus_pentoxide_calorimetry_vapor_heat_content_358C_eq15",
        "frandsen_1933_phosphorus_pentoxide_calorimetry_crystal_heat_content_358C_eq19",
    ):
        source = rows_by_id[observation_id]
        expected_locator = (20, 19, 54)
        assert tuple(
            source["locator"][key]
            for key in ("page", "pdf_page_index", "published_page")
        ) == expected_locator
        assert tuple(
            source["point_conditions"]["temperature_C"]["locator"][key]
            for key in ("page", "pdf_page_index", "published_page")
        ) == expected_locator
        assert tuple(
            source["uncertainty"]["locator"][key]
            for key in ("page", "pdf_page_index", "published_page")
        ) == expected_locator
        migrated = migrator.result.observations[f"{SID}::{observation_id}"]
        assert migrated.locator is not None
        assert (
            migrated.locator.page,
            migrated.locator.pdf_page_index,
            migrated.locator.published_page,
        ) == expected_locator
        assert migrated.point_conditions["temperature_C"].locator is not None
        assert (
            migrated.point_conditions["temperature_C"].locator.page,
            migrated.point_conditions["temperature_C"].locator.pdf_page_index,
            migrated.point_conditions["temperature_C"].locator.published_page,
        ) == expected_locator


def test_isolated_migration_preserves_conditions_and_refuses_unsupported_units() -> None:
    document = _document()
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    source_observations = document["species"]["P2O5"]["observations"]
    source_context = document["species"]["P2O5"]["context"]
    assert len(result.observations) == len(source_observations) == 37
    assert not result.validation.hard_issues
    assert not result.registry_issues
    unavailable = sum(
        row.value.kind is ValueKind.UNAVAILABLE for row in result.observations.values()
    )
    assert unavailable == 37

    migrated_context = [
        context
        for rows in result.context_by_work.values()
        for context in rows
        if context["context_id"].startswith(f"{SID}::context::")
    ]
    assert len(migrated_context) == len(source_context) == 63
    assert {row["context_id"].split("::context::", 1)[1] for row in migrated_context} == {
        row["observation_id"] for row in source_context
    }

    migrated_by_id = {
        key.split("::", 1)[1]: row
        for key, row in result.observations.items()
        if row.source_id == SID
    }
    source_rows = {
        row["observation_id"]: row for row in source_observations
    }
    assert len(migrated_by_id) == len(source_rows) == 37

    for observation_id, source in source_rows.items():
        migrated = migrated_by_id[observation_id]
        assert migrated.admission.status.value == "admitted", observation_id
        source_conditions = source.get("point_conditions") or {}
        assert source_conditions, observation_id
        assert "temperature_C" in source_conditions or "temperature_K" in source_conditions
        assert migrated.point_conditions is not None
        assert set(source_conditions) <= set(migrated.point_conditions), observation_id
        for name, condition in source_conditions.items():
            assert condition.get("state", {}).get("tag")
            assert condition.get("locator"), (observation_id, name)
            migrated_condition = migrated.point_conditions[name]
            assert migrated_condition.state.tag.value == condition["state"]["tag"]
            assert migrated_condition.locator is not None

        temperature_name = next(
            name for name in ("temperature_K", "temperature_C") if name in source_conditions
        )
        temperature = migrated.point_conditions.get(temperature_name)
        assert temperature is not None, observation_id
        assert temperature.locator is not None
        assert migrated.identity.temperature_K.is_value, observation_id
        raw_temperature = float(source_conditions[temperature_name]["state"]["value"])
        expected_temperature_K = (
            float(celsius_to_kelvin(raw_temperature))
            if temperature_name == "temperature_C"
            else raw_temperature
        )
        assert float(migrated.identity.temperature_K.value) == pytest.approx(
            expected_temperature_K
        )

    unsupported_pressure_ids = [
        row["observation_id"]
        for row in source_observations + source_context
        if row["locator"].get("table") in {"4", "5"}
    ]
    for observation_id in unsupported_pressure_ids:
        source = _rows_by_id(document)[observation_id]
        assert "total_pressure_Pa" not in source.get("point_conditions", {})

    table2_solution = [
        row for row in source_context
        if row["locator"].get("table") == "2"
        and row["values"].get("quantity_as_printed") == "heat of solution"
    ]
    assert len(table2_solution) == 13
    assert all("total_pressure_Pa" not in row["point_conditions"] for row in table2_solution)

    table2_vapor = [
        row for row in source_observations
        if row["locator"].get("table") == "2"
    ]
    table2_inputs = [
        row for row in source_context
        if row["type"] == "calorimeter_run_inputs"
        and row["locator"].get("table") == "2"
    ]
    assert len(table2_vapor) == len(table2_inputs) == 13
    for row in table2_vapor + table2_inputs:
        pressure = row["point_conditions"]["total_pressure_Pa"]
        assert pressure["state"] == {"tag": "value", "value": 101325}
        assert pressure["locator"]["page"] == 11
        assert "one atmosphere" in pressure["locator"]["note"]
    vapor_at_sublimation = next(
        row for row in source_observations
        if row["observation_id"].endswith("vapor_heat_content_358C_eq15")
    )
    pressure = vapor_at_sublimation["point_conditions"]["total_pressure_Pa"]
    assert pressure["state"] == {"tag": "value", "value": 101325}
    assert pressure["locator"]["page"] == 11

    vapor_cp = next(
        row for row in source_context
        if row["observation_id"].endswith("vapor_cp_eq17")
    )
    assert vapor_cp["point_conditions"]["total_pressure_Pa"]["state"]["value"] == 101325
    assert vapor_cp["point_conditions"]["total_pressure_Pa"]["locator"]["page"] == 16
    sublimation = next(
        row for row in source_context
        if row["observation_id"].endswith("heat_of_sublimation_eq21")
    )
    assert sublimation["point_conditions"]["total_pressure_Pa"]["state"]["value"] == 101325
    assert sublimation["point_conditions"]["total_pressure_Pa"]["locator"]["page"] == 20


def test_profile_identity_axes_are_bound_to_three_migrated_rows() -> None:
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = migrator.result.observations
    selected_ids = (
        "frandsen_1933_phosphorus_pentoxide_calorimetry_p012_t04_r18_c09",
        "frandsen_1933_phosphorus_pentoxide_calorimetry_p019_t06_r03_c11",
        "frandsen_1933_phosphorus_pentoxide_calorimetry_crystal_cp_25C_eq20",
    )

    for observation_id in selected_ids:
        row = observations[f"{SID}::{observation_id}"]
        profile = profile_for(row.identity)
        assert "temperature_K" in profile.required
        assert row.identity.temperature_K is not None
        assert row.identity.composition is not None
        assert row.identity.total_pressure_Pa is not None
        assert row.identity.fO2_Pa is not None
        axes = {
            name: getattr(row.identity, name).tag.value
            for name in (
                "composition",
                "total_pressure_Pa",
                "fO2_Pa",
                "temperature_K",
            )
        }
        print(
            f"{observation_id}: required={sorted(profile.required)}; axes={axes}"
        )
