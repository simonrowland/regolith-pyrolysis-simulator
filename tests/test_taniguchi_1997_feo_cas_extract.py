"""Source-grounded schema-gap inventory for Taniguchi et al. (1997)."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT
from tools.validate_literature_extracts import validate_extract_document


EXTRACT = REPO_ROOT / "data/literature/extracts/taniguchi-1997-feo-cas.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _contexts(document: dict) -> list[dict]:
    return [
        context
        for body in document["species"].values()
        for context in body["context"]
    ]


def test_extract_preserves_input_tables_and_all_41_activity_coefficients() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    observations = [
        (species, observation)
        for species, body in document["species"].items()
        for observation in body["observations"]
    ]
    assert len(observations) == 141
    assert Counter(
        observation["locator"]["table"] for _, observation in observations
    ) == {"1": 7, "4": 100, "5": 34}
    assert all(
        observation["type"] == "equilibrium_phase_composition"
        and observation["values"]["quantity"] == "equilibrium_phase_composition"
        and observation["values"]["basis"] == "wt_percent"
        and observation["locator"]["page"]
        and observation["locator"]["published_page"]
        and observation["locator"]["pdf_page_index"] is not None
        for _, observation in observations
    )
    assert all(
        observation["values"]["component_amount"]
        == observation["values"]["value_as_published"]
        for _, observation in observations
    )
    experiments = {experiment["experiment_id"]: experiment for experiment in document["experiments"]}
    for experiment_id, experiment in experiments.items():
        if "sample" not in experiment:
            continue
        sample_map = experiment["sample"]["printed_composition"]["state"]["value"]
        component_rows = {
            ("Fe" if observation["values"]["component_as_published"] == "T.Fe" else observation["values"]["component_as_published"]): observation["values"]["component_amount"]
            for _, observation in observations
            if observation["experiment"] == experiment_id
        }
        assert sample_map == component_rows

    contexts = _contexts(document)
    coefficient_record = next(
        context
        for context in contexts
        if context["observation_id"]
        == "taniguchi_1997_activity_coefficients_schema_gap_t1382"
    )
    assert coefficient_record["schema_gap"] == "t-1382"
    coefficients = coefficient_record["values"]["rows"]
    assert len(coefficients) == 41
    assert Counter(row["method_class_as_published"] for row in coefficients) == {
        "measured_reduced": 35,
        "quoted_attributed": 6,
    }
    assert {
        (row["observation_id"], row["value_as_printed"])
        for row in coefficients
        if row["observation_id"]
        in {
            "taniguchi_1997_table1_sample_101_fe_activity_coefficient",
            "taniguchi_1997_table4_sample_201_feo_activity_coefficient",
            "taniguchi_1997_table5_sample_407_feo_activity_coefficient",
        }
    } == {
        ("taniguchi_1997_table1_sample_101_fe_activity_coefficient", "15.8"),
        ("taniguchi_1997_table4_sample_201_feo_activity_coefficient", "2.91"),
        ("taniguchi_1997_table5_sample_407_feo_activity_coefficient", "33.2"),
    }
    assert all(row["locator"]["page"] for row in coefficients)
    assert all(
        row["locator"]["page"] == 5
        for row in coefficients
        if row["locator"].get("table") == "5"
    )
    assert all(row["reduction_as_published"] for row in coefficients)
    assert all(
        "Eq. (8)" in row["reduction_as_published"]
        for row in coefficients
        if row["locator"].get("table") == "4" and row["T_K"] == "1873"
    )
    assert all(
        "Eqs. (6)-(7)" in row["reduction_as_published"]
        for row in coefficients
        if row["locator"].get("table") == "5"
    )
    assert all(
        row["method_class_as_published"] != "quoted_attributed" or row["attribution"]
        for row in coefficients
    )

    assert not any(context.get("schema_gap") == "t-1385" for context in contexts)
    zero = next(
        context
        for context in contexts
        if context["observation_id"]
        == "taniguchi_1997_table5_row401_caf2_zero_context"
    )
    assert zero["values"]["CaF2_mass_percent"] == "0"
    assert zero["locator"]["published_page"] == 960
    assert not any(
        observation["locator"].get("table") == "5"
        and observation["locator"].get("row") == "401"
        and observation["values"]["component_as_published"] == "CaF2"
        for _, observation in observations
    )
    assert len(document["experiments"]) == 64

    figures = [context for context in contexts if context.get("type") == "figure_record"]
    assert len(figures) == 8
    assert all(
        context["values"]["digitisation"] == "deferred to a figure pass"
        and context["values"]["series"]
        and all("marker_count" in series for series in context["values"]["series"])
        for context in figures
    )


def test_isolated_migration_retains_typed_rows_and_located_conditions() -> None:
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = list(migrator.result.observations.values())
    assert len(observations) == 141
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    assert all(observation.admission.status.value == "admitted" for observation in observations)
    assert all(
        observation.identity.temperature_K.is_value
        and observation.identity.temperature_K.value.is_finite()
        and observation.point_conditions["T_C"].locator is not None
        and observation.point_conditions["T_C"].state.is_value
        and observation.point_conditions["fO2_Pa"].locator is not None
        and observation.point_conditions["fO2_Pa"].inference.relation == "atm_to_Pa"
        and observation.identity.composition.is_value
        and observation.identity.composition.value.amount_basis.value == "mass_percent"
        and observation.identity.fO2_Pa.is_value
        and observation.identity.total_pressure_Pa.is_unknown
        and observation.identity.total_pressure_Pa.reason
        == "no total_pressure_Pa mapped from source"
        and observation.point_conditions["composition"].locator is not None
        for observation in observations
    )
    for observation in observations:
        experiment = migrator.result.experiments[observation.experiment_id]
        printed_map = experiment.sample.printed_composition.state.value
        expected = {
            ("Fe" if name == "T.Fe" else name): Decimal(amount)
            for name, amount in printed_map.items()
        }
        assert observation.identity.composition.value.as_map() == expected
