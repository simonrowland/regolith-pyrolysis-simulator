from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, ValueKind
from simulator.battery.migrate import Migrator, load_index


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/gca-366-210-berry-2024.yaml"
SOURCE_ID = "gca-366-210-berry-2024"


def _source_document() -> dict:
    return yaml.safe_load(EXTRACT.read_text())


def _assert_render_pins(document: dict) -> None:
    observations = document["species"]["Ti"]["observations"]
    rows = {row["observation_id"]: row for row in observations}
    pins = (
        ("berry_2024_AD_table2_fit_quoted", 2, "AD", "0.019"),
        ("berry_2024_AD_Fo_table2_fit_quoted", 7, "AD+Fo", "0.239"),
        ("berry_2024_AD_Qz_table2_fit_quoted", 10, "AD+Qz", "0.674"),
    )
    for observation_id, row_number, column, expected in pins:
        point = next(
            point
            for point in rows[observation_id]["values"]["points"]
            if point["locator"].get("row") == row_number
            and point["locator"].get("column") == column
        )
        assert point["locator"]["page"] == 214
        assert point["locator"]["table"] == "2"
        assert str(point["value"]) == expected


def _migrate():
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator


def _migrated_cell(
    migrator: Migrator, observation_id: str, nominal_log_fO2: Decimal
):
    matches = [
        observation
        for observation in migrator.result.observations.values()
        if observation.observation_id.startswith(f"{SOURCE_ID}::{observation_id}::")
        if observation.locator is not None
        and observation.locator.page == 214
        and observation.locator.table == "2"
        and observation.point_conditions is not None
        and observation.point_conditions["log_fO2_nominal"].state.value
        == nominal_log_fO2
    ]
    assert len(matches) == 1
    return matches[0]


def test_berry_render_pins_and_all_table2_rows_reach_v2() -> None:
    document = _source_document()
    _assert_render_pins(document)

    table2 = document["species"]["Ti"]["observations"]
    assert len(table2) == 7  # five melt series plus two printed dash cells
    numeric_points = sum(
        len(row["values"].get("points", []))
        for row in table2
        if "table2_fit_quoted" in row["observation_id"]
    )
    assert numeric_points == 48

    index_row = load_index(ROOT / "data/literature/INDEX.yaml")[SOURCE_ID]
    assert index_row["extracts"][0]["rows"] == 7

    migrator = _migrate()
    result = migrator.result
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.observations) == 50  # 48 fractions and two unavailable cells
    assert len(result.experiments) == 1
    assert sum(len(rows) for rows in result.context_by_work.values()) == 3
    assert sum(
        observation.value.kind is ValueKind.UNAVAILABLE
        for observation in result.observations.values()
    ) == 2

    required_conditions = {
        "temperature_K",
        "pressure_atm",
        "melt_SiO2_wt_pct",
        "melt_Al2O3_wt_pct",
        "melt_MgO_wt_pct",
        "melt_CaO_wt_pct",
        "melt_TiO2_wt_pct",
        "log_fO2_nominal",
        "log_fO2_calculated",
        "delta_FMQ_calculated",
    }
    for observation in result.observations.values():
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == Decimal("1673.15")
        assert observation.identity.species.phase.is_value
        assert observation.identity.species.phase.value is Phase.L
        assert observation.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        assert observation.read_from == f"pdf:{SOURCE_ID}"
        assert observation.identity.valence_basis.is_value
        assert observation.identity.valence_basis.value.value_form.value == "fraction"
        conditions = observation.point_conditions
        assert conditions is not None
        assert required_conditions <= set(conditions)
        assert all(conditions[key].locator is not None for key in required_conditions)

    for observation_id, nominal_log_fO2, expected in (
        ("berry_2024_AD_table2_fit_quoted", Decimal("-9.4"), Decimal("0.019")),
        ("berry_2024_AD_Fo_table2_fit_quoted", Decimal("-14.0"), Decimal("0.239")),
        ("berry_2024_AD_Qz_table2_fit_quoted", Decimal("-20.0"), Decimal("0.674")),
    ):
        observation = _migrated_cell(migrator, observation_id, nominal_log_fO2)
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == expected

    for observation_id in (
        "berry_2024_AD_En_table2_dash",
        "berry_2024_AD_Qz_table2_dash",
    ):
        observation = _migrated_cell(
            migrator, observation_id, Decimal("-13.0")
        )
        assert observation.value.kind is ValueKind.UNAVAILABLE


def test_berry_page_render_pin_rejects_a_changed_cell() -> None:
    document = deepcopy(_source_document())
    point = next(
        point
        for observation in document["species"]["Ti"]["observations"]
        if observation["observation_id"] == "berry_2024_AD_table2_fit_quoted"
        for point in observation["values"]["points"]
        if point["locator"].get("row") == 2
    )
    point["value"] = "0.020"
    with pytest.raises(AssertionError):
        _assert_render_pins(document)
