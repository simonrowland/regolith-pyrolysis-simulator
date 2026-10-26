"""Render pins and isolated-migration checks for Asai and Yokokawa (1982)."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import yaml

from simulator.battery.enums import AdmissionStatus, StateTag, ValueKind
from simulator.battery.migrate import Migrator, REPO_ROOT


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/asai-yokokawa-1982-nasoborosilicate-activity.yaml"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _source_observations(document: dict) -> dict[str, dict]:
    return {
        observation["observation_id"]: observation
        for species in document["species"].values()
        for observation in species["observations"]
    }


def _migrate() -> Migrator:
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator


def test_render_pins_cover_both_tables_and_three_physical_pages() -> None:
    document = _document()
    source = _source_observations(document)
    pins = {
        # PDF pages 2, 3, and 4, printed Table 1.
        "asai82_t1_p2_r004_c02": (2, "1", "1201", "-171.6"),
        "asai82_t1_p3_r004_c02": (3, "1", "1206", "338.8"),
        "asai82_t1_p4_r057_c02": (4, "1", "1264", "117.5"),
    }
    for observation_id, (page, table, temperature, emf) in pins.items():
        observation = source[observation_id]
        assert observation["locator"]["page"] == page
        assert observation["locator"]["table"] == table
        assert observation["values"]["T_K"] == temperature
        assert observation["values"]["value"] == emf

    table2 = source["asai82_t2_r003_c06"]
    assert table2["locator"]["page"] == 4
    assert table2["locator"]["table"] == "2"
    assert table2["values"]["value"] == "-25.8"
    assert table2["uncertainty"]["verbatim"] == "± 1.0"

    contexts = {
        context["observation_id"]: context
        for context in document["species"]["Na2O"]["context"]
    }
    assert contexts["asai82_concentration_cell_apparatus"]["locator"]["page"] == 1
    assert contexts["asai82_electrode_measurement_details"]["locator"]["page"] == 5
    assert contexts["asai82_melt_preparation_context"]["locator"]["page"] == 4
    assert contexts["asai82_figure_read_candidates"]["locator"]["page"] == 5


def test_isolated_migration_preserves_all_conditions_and_refuses_only_schema_gaps() -> None:
    source = _source_observations(_document())
    migrator = _migrate()
    result = migrator.result
    migrated = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in result.observations.values()
    }

    assert len(source) == len(migrated) == 880
    assert Counter(
        observation["type"] for observation in source.values()
    ) == Counter(
        {"activity_coefficient": 748, "gibbs_table": 132}
    )
    assert Counter(observation.value.kind for observation in migrated.values()) == {
        ValueKind.UNAVAILABLE: 506,
        ValueKind.POINT: 374,
    }
    assert all(
        observation.admission.status is AdmissionStatus.REJECTED
        for observation in migrated.values()
    )
    assert result.validation.hard_issues == ()
    assert result.registry_issues == []

    # Every source row has only its printed temperature and composition conditions.
    for observation_id, observation in migrated.items():
        assert set(observation.point_conditions) == {"temperature_K", "composition"}
        for name in ("temperature_K", "composition"):
            condition = observation.point_conditions[name]
            assert condition.state.tag is StateTag.VALUE, observation_id
            assert condition.locator is not None, observation_id

    # Identity requirements for activity rows are mapped through the established
    # quantity paths; pressure and fO2 remain unknown because the paper prints neither.
    for page, row, emf in ((2, 4, "-171.6"), (3, 4, "338.8"), (4, 57, "117.5")):
        source_id = f"asai82_t1_p{page}_r{row:03}_c02"
        activity_id = source_id.replace("_t1_", "_log_activity_")
        activity = migrated[activity_id]
        assert source[activity_id]["derived_from"] == [source_id]
        assert source[activity_id]["values"]["emf_mV_input_as_published"] == emf
        assert activity.identity.temperature_K.is_value
        assert activity.identity.composition.is_value
        assert activity.identity.fO2_Pa.tag is StateTag.UNKNOWN
        assert activity.identity.total_pressure_Pa.tag is StateTag.UNKNOWN
        assert "no fO2_Pa mapped from source" in activity.identity.fO2_Pa.reason
        assert "no total_pressure_Pa mapped from source" in activity.identity.total_pressure_Pa.reason
        assert set(activity.point_conditions) == {"temperature_K", "composition"}

    # Every Table 2 row is reduced from all printed Table 1 points for its
    # composition; parent lists contain no rows from another series.
    table1_by_composition: dict[str, list[str]] = {}
    for observation_id, observation in source.items():
        if observation_id.startswith("asai82_t1_"):
            key = str(observation["values"]["composition_mol"])
            table1_by_composition.setdefault(key, []).append(observation_id)
    table2_rows = [
        (observation_id, observation)
        for observation_id, observation in source.items()
        if observation_id.startswith("asai82_t2_")
    ]
    assert len(table2_rows) == 132
    for _observation_id, observation in table2_rows:
        key = str(observation["values"]["composition_mol"])
        parents = observation["derived_from"]
        assert len(parents) == len(table1_by_composition[key])
        assert set(parents) == set(table1_by_composition[key])
        assert all(source[parent]["type"] == "activity_coefficient" for parent in parents)


def test_equation_3_signed_activity_anchors_migrate_to_linear_activity() -> None:
    source = _source_observations(_document())
    migrated = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in _migrate().result.observations.values()
    }
    # Independent signed anchors reduced from Eq. (3), spanning PDF pages 2–4.
    anchors = {
        "asai82_log_activity_p2_r004_c02": (
            "asai82_t1_p2_r004_c02",
            "-171.6",
            "1.4401764140",
            "27.55347721012188974143685573",
        ),
        "asai82_log_activity_p3_r004_c02": (
            "asai82_t1_p3_r004_c02",
            "338.8",
            "-2.8316365658",
            "0.001473545105407372857568017529",
        ),
        "asai82_log_activity_p4_r057_c02": (
            "asai82_t1_p4_r057_c02",
            "117.5",
            "-0.9369840772",
            "0.1156154630208163870038188817",
        ),
    }
    for activity_id, (emf_id, emf_mV, log_activity, linear_activity) in anchors.items():
        activity = source[activity_id]
        assert activity["derived_from"] == [emf_id]
        assert source[emf_id]["values"]["value"] == emf_mV
        assert activity["values"]["activity"] == log_activity
        assert activity["values"]["log10_activity_ratio"] == log_activity
        assert activity["locator"]["equation"] == "3"
        assert migrated[activity_id].value.kind is ValueKind.POINT
        assert (
            abs(migrated[activity_id].value.point - Decimal(linear_activity))
            < Decimal("1e-24")
        )
