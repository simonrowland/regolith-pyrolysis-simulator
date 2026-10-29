from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Phase, Quantity
from simulator.battery.identity import profile_for
from simulator.battery.migrate import torr_to_pa
from tests.battery.test_migrate import REPO_ROOT, _migrate_real_extract


SID = "kems-015-hashimoto-1983"
EXTRACT = f"{SID}.yaml"
SOURCE = REPO_ROOT / "data" / "literature" / "extracts" / EXTRACT


def _target_source_rows(doc: dict) -> list[dict]:
    return [
        observation
        for species in doc["species"].values()
        for observation in species.get("observations", [])
        if observation.get("observation_id", "").startswith("hashimoto_1983_table3_")
        and "__run_" in observation.get("observation_id", "")
    ]


def _locator(condition: dict, *, page: int, table: str | None = None, pdf_index: int) -> None:
    assert condition["state"]
    locator = condition["locator"]
    assert locator["published_page"] == page
    assert locator["pdf_page_index"] == pdf_index
    assert locator.get("table") == table


def test_hashimoto_table3_rows_keep_run_conditions_and_initial_charge(tmp_path: Path) -> None:
    doc = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    table3 = next(
        observation
        for observation in doc["species"]["Fe"]["observations"]
        if observation["observation_id"] == "hashimoto_1983_table3_residue_composition_series"
    )
    table3_rows = table3["values"]["series"]
    assert len(table3_rows) == 31
    assert Counter(row["composition_status"] for row in table3_rows) == {
        "measured": 24,
        "n.d.": 4,
        "valence_only": 2,
        "valence_only_VF_absent": 1,
    }

    source_rows = _target_source_rows(doc)
    assert len(source_rows) == 127
    required_conditions = {
        "temperature_K",
        "time_min",
        "total_pressure_Pa",
        "atmosphere",
        "fO2_control",
        "fO2_Pa",
    }
    experiment_by_id = {item["experiment_id"]: item for item in doc["experiments"]}
    for observation in source_rows:
        point = observation["values"]["series"][0]
        conditions = point["point_conditions"]
        assert required_conditions <= set(conditions)
        assert all(item["state"] and item["locator"] for item in conditions.values())
        _locator(conditions["temperature_K"], page=114, table="2", pdf_index=3)
        _locator(conditions["time_min"], page=114, table="2", pdf_index=3)
        _locator(conditions["total_pressure_Pa"], page=118, pdf_index=7)
        _locator(conditions["atmosphere"], page=118, pdf_index=7)
        _locator(conditions["fO2_control"], page=127, pdf_index=16)
        _locator(conditions["fO2_Pa"], page=127, pdf_index=16)
        assert conditions["fO2_Pa"]["state"] == {
            "tag": "unknown",
            "reason": "unbuffered_no_numerical_run_fO2_or_pO2_printed",
        }
        experiment = experiment_by_id[observation["experiment"]]
        assert experiment["bench_id"] == "graphite-vacuum-furnace"
        assert experiment["sample"]["initial_composition"]["locator"]["table"] == "1"
        assert experiment["sample"]["initial_composition"]["locator"]["published_page"] == 113

    migrated = _migrate_real_extract(tmp_path, EXTRACT)
    assert migrated.validation.hard_issues == ()
    observations = [
        observation
        for observation in migrated.observations.values()
        if observation.source_id == SID
        and "table3_" in observation.observation_id
        and "__run_" in observation.observation_id
    ]
    assert len(observations) == 216
    assert all(
        observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and observation.identity.species.phase.is_value
        and observation.identity.species.phase.value is Phase.L
        for observation in observations
    )
    assert all(
        {"temperature_K", "time_min", "total_pressure_Pa", "atmosphere", "fO2_Pa"}
        <= set(observation.point_conditions or {})
        for observation in observations
    )
    assert all(
        observation.identity.fO2_Pa is not None
        and observation.identity.fO2_Pa.is_unknown
        and observation.point_conditions["fO2_Pa"].state.is_unknown
        and observation.point_conditions["fO2_Pa"].state.reason
        == "unbuffered_no_numerical_run_fO2_or_pO2_printed"
        for observation in observations
    )

    pressure = torr_to_pa(Decimal("1e-4"))
    by_formula = {}
    for observation in observations:
        if (
            "__run_17c3-2::" in observation.observation_id
            and ":row=c3-(2):" in observation.observation_id
        ):
            by_formula[observation.identity.species.formula] = observation
    expected = {"SiO2": Decimal("39.09"), "Al2O3": Decimal("3.57"), "FeO": Decimal("27.92")}
    assert set(by_formula) >= set(expected)
    for formula, value in expected.items():
        observation = by_formula[formula]
        assert observation.value.point == value
        assert observation.locator.published_page == 116
        assert observation.locator.pdf_page_index == 5
        assert observation.locator.table == "3"
        assert observation.identity.temperature_K.value == Decimal("1973.15")
        assert observation.identity.total_pressure_Pa.value == pressure
        assert observation.point_conditions["temperature_K"].state.value == Decimal("1973.15")
        assert observation.point_conditions["temperature_K"].locator.published_page == 114
        assert observation.point_conditions["temperature_K"].locator.table == "2"
        assert observation.point_conditions["time_min"].state.value == Decimal("16.7")
        assert observation.point_conditions["total_pressure_Pa"].state.value.point == pressure
        assert observation.point_conditions["fO2_Pa"].locator.published_page == 127
        experiment = migrated.experiments[observation.experiment_id]
        assert experiment.method.value.value == "langmuir_free_evaporation"
        assert experiment.pressure_environment.total_pressure_Pa.state.value.point == pressure
        assert experiment.sample.initial_composition.locator.published_page == 113
        assert experiment.sample.initial_composition.locator.table == "1"
        assert observation.identity.composition.is_value
        assert dict(observation.identity.composition.value.components)["SiO2"] == Decimal(
            "0.3379238246485715130851197644"
        )
        assert experiment.sample.mass_kg.state.value == Decimal("0.0001")
        bench = migrated.benches[experiment.bench_id]
        assert bench.apparatus_family.state.value == "vacuum furnace"
        assert bench.cell_material_and_liner.state.value == "high-purity graphite crucible"

    profile = profile_for(by_formula["SiO2"].identity)
    assert "composition" in profile.required
    assert "total_pressure_Pa" in profile.required
    assert "fO2_Pa" in profile.required
    assert "temperature_K" in profile.required
