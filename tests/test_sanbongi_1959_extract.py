"""Rendered-cell pins and isolated migration for Sanbongi and Omori (1959)."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import re

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import Migrator, REPO_ROOT
from tools.validate_literature_extracts import validate_extract_document

EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/sanbongi-1959-blast-furnace-slag-activities.yaml"
)
RENDER_PINS = (
    (
        "sanbongi_1959_blast_furnace_slag_activities_p005_t01_r03_c07",
        "0.008",
        5,
        "1",
    ),
    (
        "sanbongi_1959_blast_furnace_slag_activities_p009_t02_r04_c08",
        "0.04",
        9,
        "2",
    ),
    (
        "sanbongi_1959_blast_furnace_slag_activities_p010_t03_r04_c10",
        "0.08",
        10,
        "3",
    ),
    (
        "sanbongi_1959_blast_furnace_slag_activities_p018_t05_r02_c02",
        "0.03",
        18,
        "4",
    ),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_render_pins(document: dict) -> None:
    observations = {
        row["observation_id"]: row
        for row in document["species"]["SiO2"]["observations"]
    }
    for observation_id, value, page, table in RENDER_PINS:
        row = observations[observation_id]
        assert row["values"]["points"][0]["activity"] == value
        assert row["locator"]["page"] == page
        assert row["locator"]["table"] == table


@pytest.fixture(scope="module")
def migrated_extract():
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return _document(), migrator.result


def test_render_pins_row_counts_and_evidence_lineage(migrated_extract) -> None:
    document, result = migrated_extract
    rows = document["species"]["SiO2"]["observations"]
    assert len(rows) == 144
    assert Counter(row["values"]["quantity"] for row in rows) == {
        "activity": 81,
        "activity_coefficient": 63,
    }
    assert len(document["species"]["SiO2"]["context"]) == 1
    refusal = document["species"]["SiO2"]["context"][0]
    assert refusal["values"]["admission_status"] == "typed_refusal"
    assert refusal["values"]["value_as_printed"] == "0.16~0.17"

    printed_rows = {
        (row["locator"]["page"], row["locator"]["table"], re.search(r"_r(\d+)_c\d+$", row["observation_id"]).group(1))
        for row in rows
    }
    table3_rows = {
        row for row in printed_rows if row[1] == "3"
    }
    assert sum(1 for row in printed_rows if row[1] == "1") == 12
    assert sum(1 for row in printed_rows if row[1] == "2") == 11
    assert len(table3_rows) == 52
    assert sum(1 for row in printed_rows if row[1] == "4") == 6
    assert len(printed_rows) + 1 == 82  # add the refused Table 4 interval row

    _assert_render_pins(document)
    cb7 = next(row for row in rows if "Printed row CB- 7" in row["locator"]["note"])
    assert cb7["locator"]["page"] == 9
    wt_components = dict(
        cb7["point_conditions"]["composition_weight_percent"]["state"]["value"]["components"]
    )
    mol_components = dict(
        cb7["values"]["points"][0]["composition_as_published"]["components"]
    )
    assert wt_components == {"CaO": "32.0", "SiO2": "59.5"}
    assert mol_components == {"CaO": "41.8", "SiO2": "58.2"}
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    assert len(result.observations) == 144
    assert not result.validation.hard_issues
    assert not result.registry_issues
    assert not [
        row
        for row in result.observations.values()
        if row.value.kind is ValueKind.UNAVAILABLE
    ]

    source_by_id = {row["observation_id"]: row for row in rows}
    for observation_id, source in source_by_id.items():
        migrated = next(
            observation
            for observation in result.observations.values()
            if observation.observation_id.startswith(f"{EXTRACT.stem}::{observation_id}")
        )
        assert migrated.value.kind is ValueKind.POINT, observation_id
        assert migrated.identity.temperature_K is not None
        assert migrated.identity.temperature_K.is_value
        assert migrated.point_conditions is not None
        temperature = migrated.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == migrated.identity.temperature_K.value
        assert temperature.locator is not None

        raw_conditions = source["point_conditions"]
        for key, raw in raw_conditions.items():
            condition = migrated.point_conditions.get(key)
            if key in {"total_pressure_Pa", "fO2_Pa"}:
                # The source prints neither condition; the migrator represents
                # them as unknown identity axes and omits absent point values.
                assert raw["state"]["tag"] == "unknown", (observation_id, key)
                assert condition is None or condition.state.is_unknown, (observation_id, key)
                continue
            assert condition is not None, (observation_id, key)
            assert condition.locator is not None, (observation_id, key)
            assert condition.state.tag.value == raw["state"]["tag"], (observation_id, key)

        if source["locator"]["table"] == "3":
            printed_row = source["locator"]["note"].split(";", 1)[0].removeprefix(
                "Printed row "
            )
            saturation = migrated.point_conditions.get("phase_assemblage")
            if printed_row.startswith("AS-"):
                assert saturation is not None, observation_id
                assert saturation.state.is_value
                assert saturation.state.value == (
                    "slag series saturated with mullite and corundum"
                )
                assert saturation.locator is not None
                assert saturation.locator.page == 9
                assert saturation.locator.table == "3"
            else:
                assert printed_row.startswith("T"), (observation_id, printed_row)
                assert saturation is None, observation_id

        printed_composition = raw_conditions["composition"]["state"]["tag"] == "value"
        composition = migrated.identity.composition
        if printed_composition:
            assert composition is not None and composition.is_value, observation_id
        else:
            assert composition is None or composition.is_unknown, observation_id

        assert migrated.identity.total_pressure_Pa is not None
        assert migrated.identity.total_pressure_Pa.is_unknown, observation_id
        assert migrated.identity.fO2_Pa is not None
        assert migrated.identity.fO2_Pa.is_unknown, observation_id
        pressure = migrated.point_conditions.get("total_pressure_Pa")
        oxygen = migrated.point_conditions.get("fO2_Pa")
        assert pressure is None or pressure.state.is_unknown
        assert oxygen is None or oxygen.state.is_unknown
        assert "gas_mix" not in raw_conditions
        assert "atmosphere" not in raw_conditions

    gamma_rows = [row for row in rows if row["values"]["quantity"] == "activity_coefficient"]
    for row in gamma_rows:
        activity_id = row["derived_from"][0]
        assert activity_id in source_by_id
        assert source_by_id[activity_id]["values"]["quantity"] == Quantity.ACTIVITY.value
        assert row["values"]["method_class"] == "measured_reduced"
        assert row["values"]["evidence_class"] == "measured_reduced"
        assert row["values"]["derivation"]["inputs"] == [activity_id]
        migrated = next(
            observation
            for observation in result.observations.values()
            if observation.observation_id.startswith(
                f"{EXTRACT.stem}::{row['observation_id']}"
            )
        )
        activity_migrated = next(
            observation
            for observation in result.observations.values()
            if observation.observation_id.startswith(f"{EXTRACT.stem}::{activity_id}")
        )
        assert migrated.derived_from == (activity_migrated.observation_id,)
        assert migrated.derivation is not None

    for row in rows:
        if row["values"]["quantity"] == Quantity.ACTIVITY.value:
            assert row["values"]["method_class"] == "quoted_attributed"
            assert row["values"]["evidence_class"] == "quoted_attributed"


def test_render_pin_rejects_a_mutated_table_cell() -> None:
    mutated = deepcopy(_document())
    row = next(
        row
        for row in mutated["species"]["SiO2"]["observations"]
        if row["observation_id"] == RENDER_PINS[0][0]
    )
    row["values"]["points"][0]["activity"] = "0.009"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)
