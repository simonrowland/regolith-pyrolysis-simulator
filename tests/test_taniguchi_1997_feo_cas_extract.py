"""Source-grounded schema-gap inventory for Taniguchi et al. (1997)."""

from __future__ import annotations

from collections import Counter
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
    assert all(not body["observations"] for body in document["species"].values())

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

    input_tables = {
        context["locator"]["table"]: context
        for context in contexts
        if context.get("schema_gap") == "t-1385"
    }
    assert set(input_tables) == {"1", "4", "5"}
    assert {
        table: len(input_tables[table]["values"]["rows"])
        for table in input_tables
    } == {"1": 7, "4": 20, "5": 7}
    assert input_tables["4"]["values"]["rows"][12]["SiO2_mass_percent"] == "6.52"
    assert input_tables["5"]["values"]["rows"][0]["CaF2_mass_percent"] == "0"
    assert all(
        row["T_K"] and row["pO2_atm"]
        for context in input_tables.values()
        for row in context["values"]["rows"]
    )

    figures = [context for context in contexts if context.get("type") == "figure_record"]
    assert len(figures) == 8
    assert all(
        context["values"]["digitisation"] == "deferred to a figure pass"
        and context["values"]["series"]
        and all("marker_count" in series for series in context["values"]["series"])
        for context in figures
    )


def test_isolated_migration_has_no_observations_or_hard_issues() -> None:
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert not migrator.result.observations
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
