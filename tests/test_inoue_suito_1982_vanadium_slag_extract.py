from __future__ import annotations

import csv
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/inoue-suito-1982-vanadium-mgo-saturated-slag.yaml"
RENDER_GRID = ROOT / "tests/fixtures/literature/inoue-suito-1982-table2-render-grid.csv"

TABLE2_COMPONENTS = (
    ("metal_V_cell_1e-4_wt_pct", "V", "metal"),
    ("metal_O_wt_pct", "O", "metal"),
    ("slag_CaO_wt_pct", "CaO", "slag"),
    ("slag_SiO2_wt_pct", "SiO2", "slag"),
    ("slag_FeO_wt_pct", "FeO", "slag"),
    ("slag_Fe2O3_wt_pct", "Fe2O3", "slag"),
    ("slag_MgO_wt_pct", "MgO", "slag"),
    ("slag_V_wt_pct", "V", "slag"),
)


def _read_grid() -> list[dict[str, str]]:
    with RENDER_GRID.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _decimal(value: object) -> Decimal:
    return Decimal(str(value).replace("−", "-").replace("∞", "Infinity"))


def _assert_render_grid_matches_extract(doc: dict, grid: list[dict[str, str]]) -> None:
    observations = doc["species"]["SiO2"]["observations"]
    typed = {
        row["observation_id"]: row
        for row in observations
        if row["values"].get("quantity") == "equilibrium_phase_composition"
    }
    assert len(grid) == 61
    assert len(typed) == 61 * 9

    for source_row in grid:
        sample = source_row["sample_id"]
        page = int(source_row["pdf_page"])
        temperature = Decimal(source_row["T_C"])
        for source_field, formula, role in TABLE2_COMPONENTS:
            observation_id = f"inoue_suito_1982_t02_sample_{sample}_{role}_{formula}"
            row = typed[observation_id]
            values = row["values"]
            printed = _decimal(source_row[source_field])
            expected = printed * Decimal("0.0001") if source_field == "metal_V_cell_1e-4_wt_pct" else printed

            assert row["type"] == "composition_series"
            assert row["experiment"] == f"table2-sample-{sample}"
            assert row["locator"]["page"] == page
            assert row["locator"]["pdf_page_index"] == page - 1
            assert row["locator"]["row_label"] == sample
            assert _decimal(values["T_C"]) == temperature
            assert values["formula"] == formula
            assert values["phase_label"] == role
            assert values["basis"] == "wt_percent"
            assert _decimal(values["component_amount"]) == expected

        reference_id = f"inoue_suito_1982_t02_sample_{sample}_no_v_reference_slag_Fe2O3"
        reference = typed[reference_id]
        assert reference["locator"]["page"] == page
        assert reference["locator"]["pdf_page_index"] == page - 1
        assert reference["experiment"] == f"table2-no-v-reference-{sample}"
        assert reference["values"]["formula"] == "Fe2O3"
        assert reference["values"]["phase_label"] == "slag"
        assert _decimal(reference["values"]["T_C"]) == temperature
        assert _decimal(reference["values"]["component_amount"]) == _decimal(
            source_row["Fe2O3_no_V_wt_pct"]
        )

    auxiliary = {
        (int(context["locator"]["page"]), str(context_row["sample_id"])): context_row
        for context in doc["species"]["SiO2"]["context"]
        if context["values"].get("quantity") == "vanadium_valence_ratio_and_delta_Fe2O3"
        for context_row in context["values"]["rows"]
    }
    assert len(auxiliary) == 61
    for source_row in grid:
        page, sample = int(source_row["pdf_page"]), source_row["sample_id"]
        row = auxiliary[(page, sample)]
        assert str(row["T_C"]) == source_row["T_C"]
        assert _decimal(row["delta_Fe2O3_wt_percent"]) == _decimal(
            source_row["delta_Fe2O3_wt_pct"]
        )
        for context_field, render_field in (
            ("V5+_to_V4+_ratio", "V5+_to_V4+"),
            ("V4+_to_V3+_ratio", "V4+_to_V3+"),
        ):
            printed = source_row[render_field]
            expected = printed if printed else "blank"
            assert row[context_field] == expected


def test_inoue_suito_table2_is_independently_bound_to_render_grid() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    grid = _read_grid()
    _assert_render_grid_matches_extract(doc, grid)

    changed_grid = deepcopy(grid)
    changed_grid[0]["slag_CaO_wt_pct"] = "26.71"
    with pytest.raises(AssertionError):
        _assert_render_grid_matches_extract(doc, changed_grid)

    changed_extract = deepcopy(doc)
    changed_extract["species"]["SiO2"]["observations"][0]["values"][
        "component_amount"
    ] = "999"
    with pytest.raises(AssertionError):
        _assert_render_grid_matches_extract(changed_extract, grid)


def test_inoue_suito_phase_compositions_migrate_with_sample_identity_and_temperature(
    tmp_path: Path,
) -> None:
    from tests.battery.test_migrate import _migrate_real_extract

    result = _migrate_real_extract(
        tmp_path,
        EXTRACT.name,
    )
    assert result.validation is not None
    assert result.validation.ok, result.validation.hard_issues
    assert result.validation.hard_issues == (), result.validation.hard_issues
    rows = {
        row.observation_id.split("::", 1)[-1]: row
        for row in result.observations.values()
        if row.source_id == "inoue-suito-1982-vanadium-mgo-saturated-slag"
    }
    assert len(rows) == 61 * 9

    for source_row in _read_grid():
        sample = source_row["sample_id"]
        page = int(source_row["pdf_page"])
        temperature = Decimal(source_row["T_C"]) + Decimal("273.15")
        for source_field, formula, role in TABLE2_COMPONENTS:
            observation_id = f"inoue_suito_1982_t02_sample_{sample}_{role}_{formula}"
            row = rows[observation_id]
            experiment = result.experiments[row.experiment_id]
            source_amount = _decimal(source_row[source_field])
            expected = source_amount * Decimal("0.0001") if source_field == "metal_V_cell_1e-4_wt_pct" else source_amount
            assert row.value.point == expected
            assert row.identity.species.formula == formula
            assert row.identity.phase_role.value.value == role
            assert row.identity.temperature_K.value == temperature
            assert experiment.method.value.value == "static_equilibrium"
            condition = row.point_conditions["temperature_K"]
            assert condition.state.value == temperature
            assert condition.locator is not None
            assert condition.locator.page == page
            assert condition.locator.pdf_page_index == page - 1
            assert all(
                notice.kind.value != "source_value_outside_physical_range"
                for notice in row.notices
            )

        reference = rows[
            f"inoue_suito_1982_t02_sample_{sample}_no_v_reference_slag_Fe2O3"
        ]
        reference_experiment = result.experiments[reference.experiment_id]
        assert reference.value.point == _decimal(source_row["Fe2O3_no_V_wt_pct"])
        assert reference.identity.species.formula == "Fe2O3"
        assert reference.identity.phase_role.value.value == "slag"
        assert reference.identity.temperature_K.value == temperature
        assert reference_experiment.method.value.value == "static_equilibrium"
        assert reference.point_conditions["temperature_K"].locator.page == page


def test_inoue_suito_table1_validation_values_remain_context_only() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = doc["species"]["SiO2"]["context"]
    validation = next(
        row for row in rows
        if row["observation_id"] == "inoue_suito_1982_table1_method_validation"
    )
    assert validation["locator"]["page"] == 2
    assert validation["values"]["rows"][0]["this_method"] == [
        "0.00647", "0.00637", "0.00645"
    ]
    assert validation["values"]["rows"][0]["JIS_AASM"] == ["0.0056", "blank"]
    assert validation["values"]["rows"][2]["table2_metal_V"] == "0.00182 wt_percent"
