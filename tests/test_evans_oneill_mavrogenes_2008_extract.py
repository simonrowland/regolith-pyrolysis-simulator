"""Full table extraction and isolated migration pins for Evans et al. (2008)."""

from __future__ import annotations

import copy
from collections import Counter
from decimal import Decimal

import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT
from tools import validate_literature_extracts as fidelity


SOURCE_ID = "evans-oneill-mavrogenes-2008-sulphur-solubility-mn-ni-w-cu"
EXTRACT = REPO_ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["observations"]
    }


def _point(observation: dict, formula: str) -> dict:
    return next(
        point
        for point in observation["values"]["points"]
        if point["formula"] == formula
    )


def _table_rows(document: dict, table: str) -> list[dict]:
    pages = document["extraction"]["tables_as_published"][table]
    return [row for page in pages for row in page["rows"]]


def test_all_printed_table_rows_and_audited_table3_cells_are_retained() -> None:
    document = _document()
    observations = _observations(document)
    assert len(observations) == 159
    assert Counter(row["locator"]["table"] for row in observations.values()) == {
        "3a": 56,
        "3b": 36,
        "3c": 55,
        "3d": 12,
    }
    assert Counter(row["phase"] for row in observations.values()) == {
        "glass": 157,
        "cr": 2,
    }
    assert sum(
        point["value"] is not None
        for row in observations.values()
        for point in row["values"]["points"]
    ) == 1164
    assert sum(
        point["value"] is None
        for row in observations.values()
        for point in row["values"]["points"]
    ) == 108

    # Independent render pins from three printed cells on three PREPRINT pages.
    t3a = observations["evans08_3a_B030106p1"]
    si = _point(t3a, "Si")
    sulfur = _point(t3a, "S")
    assert (si["value"], si["value_as_published"], si["sigma"]) == (
        19.63,
        "19.63(6)",
        0.06,
    )
    assert si["locator"] == {
        "page": 42,
        "pdf_page_index": 41,
        "table": "3a",
        "row": "B030106p1",
        "column": "Si (wt%)",
    }
    assert (sulfur["value"], sulfur["value_as_published"], sulfur["sigma"]) == (
        0.2651,
        "0.2651(7)",
        0.0007,
    )
    assert sulfur["locator"]["page"] == 42
    copper = _point(observations["evans08_3d_B040106p1"], "Cu")
    assert (copper["value"], copper["value_as_published"]) == (0.00066, "0.00066‡")
    assert copper["locator"] == {
        "page": 51,
        "pdf_page_index": 50,
        "table": "3d",
        "row": "B040106p1",
        "column": "Cu(wt%)",
    }
    assert all(row["units"] == "wt%" for row in observations.values())

    nis_15 = observations["evans08_3c_NiS phase_table_row_15"]
    nis_20 = observations["evans08_3c_NiS phase_table_row_20"]
    assert nis_15["values"]["status"] == "typed_refused_schema_gap"
    assert nis_20["values"]["status"] == "typed_refused_schema_gap"
    assert [
        (_point(row, formula)["value_as_published"], _point(row, formula)["value"])
        for row, formula in ((nis_15, "Fe"), (nis_15, "S"), (nis_15, "Ni"), (nis_20, "Fe"), (nis_20, "S"), (nis_20, "Ni"))
    ] == [
        ("1.46", 1.46),
        ("23.93", 23.93),
        ("71.25", 71.25),
        ("0.32", 0.32),
        ("23.83", 23.83),
        ("72.78", 72.78),
    ]

    for observation in observations.values():
        table = observation["locator"]["table"]
        for point in observation["values"]["points"]:
            locator = point["locator"]
            assert locator["table"] == table
            assert locator["page"] == observation["locator"]["page"]
            assert point["table_row_number"] > 1
            assert point["table_column_number"] > 1
            assert point["value_as_published"]
        assert observation["point_conditions"]["composition"]["state"] == {
            "tag": "unknown",
            "reason": "run_composition_not_reported_per_sample",
        }
        assert observation["point_conditions"]["total_pressure_Pa"]["printed_as"] == "1 bar"
        assert observation["values"]["T_C"] == 1400
        assert observation["values"]["duration_h"] == 24
        if observation["phase"] == "glass":
            assert observation["values"]["quench_preservation"] == "not_stated"
            assert observation["point_conditions"]["quench_state"]["state"]["value"] == "not_stated"
            assert observation["point_conditions"]["quench_state"]["locator"]["page"] == 7

    # Exact Table 1 and Table 2 cells stay associated with their own printed rows.
    table1 = _table_rows(document, "1")
    run_270306 = next(row["cells"] for row in table1 if row["cells"][0] == "B270306")
    assert run_270306[4] == "-1.91"
    assert "evans08_3a_B270307p1" in observations
    assert not any(row["run_id_as_printed"] == "B270306" for row in observations.values())
    table2 = _table_rows(document, "2")
    group2 = next(row["cells"] for row in table2 if row["cells"][0] == "Group 2")
    assert group2[5] == "-1.81"
    discrepancies = next(
        row
        for row in document["species"]["S"]["context"]
        if row["observation_id"] == "evans08_table1_table2_fugacity_discrepancies"
    )
    assert discrepancies["values"]["table1_group2_runs_log_fS2_as_printed"] == -1.85
    assert discrepancies["values"]["group2_table2_log_fS2_as_printed"] == -1.81
    assert discrepancies["values"]["B270306_table1_log_fS2_as_printed"] == -1.91
    assert observations["evans08_3d_B040106p1"]["point_conditions"]["fO2_log"]["state"]["value"] == -8.09

    contexts = document["species"]["S"]["context"]
    starting = next(row for row in contexts if row["observation_id"] == "evans08_starting_composition_and_additive_series")
    assert starting["values"]["base_melt_elemental_composition_as_printed"] == {
        "Si": 19.7,
        "Al": 6.9,
        "Mg": 5.3,
        "Ca": 25.0,
    }
    table4_contexts = {
        row["observation_id"]: row
        for row in contexts
        if row["observation_id"].startswith("evans08_table4_")
    }
    assert len(table4_contexts) == 8
    for row in table4_contexts.values():
        assert row["type"] == "model_parameter_context"
        assert row["values"]["quantity"] == "interaction_parameter"
        assert row["values"]["method_class"] == "model_derived"
        assert row["derivation"]["locator"]["equation"] == "3"
        assert row["derivation"]["locator"]["page"] == 3
        assert len(row["derived_from"]) == 116
        assert all(parent in observations for parent in row["derived_from"])
        assert all(
            observations[parent]["phase"] == "glass"
            and _point(observations[parent], "S")["value"] is not None
            and not observations[parent]["run_id_as_printed"].startswith("B270306")
            for parent in row["derived_from"]
        )
    a0 = table4_contexts["evans08_table4_A_0"]
    assert (
        a0["values"]["value"],
        a0["values"]["value_as_published"],
        a0["values"]["uncertainty"]["value"],
        a0["values"]["uncertainty"]["confidence_level"],
    ) == (-14.286, "-14.286", 0.05, "not_stated")
    table5_context = next(
        row
        for row in contexts
        if row["observation_id"] == "evans08_table5_model_performance_typed_refusal"
    )
    assert table5_context["values"]["status"] == "typed_refused_schema_gap"
    assert len(table5_context["values"]["printed_rows"][0]["rows"]) == 4
    assert len(_table_rows(document, "4")) == 8
    assert len(_table_rows(document, "5")) == 4


def test_fidelity_pins_fail_when_a_rendered_cell_is_mutated() -> None:
    document = _document()
    assert all(
        fidelity.check_fidelity_sample_matches(document, pin) == []
        for pin in document["fidelity_samples"]
    )

    mutated = copy.deepcopy(document)
    pin = mutated["fidelity_samples"][0]
    mutated["species"]["S"]["observations"][0]["values"]["points"][0]["value"] = 19.64
    assert "mismatch" in " ".join(fidelity.check_fidelity_sample_matches(mutated, pin))


def test_isolated_migration_maps_conditions_and_preserves_measured_cells() -> None:
    document = _document()
    source_rows = _observations(document)
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    rows = list(result.observations.values())
    assert len(rows) == 1256
    assert len(result.validation.hard_issues) == 0
    assert Counter(row.value.kind.value for row in rows) == {"point": 1158, "unavailable": 98}
    assert all(row.identity.quantity.is_value for row in rows)
    assert all(row.identity.quantity.value.value == "equilibrium_phase_composition" for row in rows)
    assert all(row.identity.species.phase.is_value for row in rows)
    assert all(row.identity.species.phase.value.value in {"glass", "cr"} for row in rows)
    assert all(row.identity.species.formula != "S" or row.identity.quantity.is_value for row in rows)

    # The only non-hard validation issue is the explicitly ruled unknown per-run composition.
    assert len(result.validation.issues) == 1256
    assert {issue.detail for issue in result.validation.issues} == {
        "required axis composition is unknown"
    }

    for row in rows:
        parent_id = row.observation_id.split("::")[1]
        source = source_rows[parent_id]
        condition = row.point_conditions
        assert condition["temperature_K"].state.value == Decimal("1673.15")
        assert condition["temperature_K"].locator.page == 7
        assert condition["total_pressure_Pa"].state.value == Decimal("100000")
        assert condition["total_pressure_Pa"].locator.page == 6
        assert condition["fO2_log"].state.is_value
        assert condition["fS2_log"].state.is_value
        assert condition["gas_mix"].state.is_value
        assert condition["composition"].state.is_unknown
        assert condition["composition"].state.reason == "run_composition_not_reported_per_sample"
        assert row.identity.temperature_K.value == Decimal("1673.15")
        assert row.identity.total_pressure_Pa.value == Decimal("100000")
        assert row.identity.fO2_Pa.is_value
        assert row.identity.composition.is_unknown
        assert row.identity.composition.reason == "run_composition_not_reported_per_sample"
        if row.value.kind.value == "unavailable":
            matching_source_point = next(
                point
                for point in source["values"]["points"]
                if point["formula"] == row.identity.species.formula
                and point["value"] is None
            )
        else:
            matching_source_point = next(
                point
                for point in source["values"]["points"]
                if point["formula"] == row.identity.species.formula
                and point["value"] is not None
                and Decimal(str(point["value"])) == row.value.point
            )
        assert matching_source_point["value_as_published"]
        assert row.locator.page == matching_source_point["locator"]["page"]
        if row.identity.species.phase.value.value == "glass":
            assert condition["quench_state"].state.value == "not_stated"
            assert condition["quench_state"].locator.page == 7

    for expected_value, expected_formula, expected_log_fO2 in (
        ("19.63", "Si", Decimal("-9.6")),
        ("0.2651", "S", Decimal("-9.6")),
        ("0.00066", "Cu", Decimal("-8.09")),
    ):
        row = next(
            row
            for row in rows
            if row.value.point == Decimal(expected_value)
            and row.identity.species.formula == expected_formula
        )
        assert row.identity.temperature_K.value == Decimal("1673.15")
        assert row.identity.total_pressure_Pa.value == Decimal("100000")
        assert row.point_conditions["fO2_log"].state.value == expected_log_fO2
        assert row.identity.fO2_Pa.is_value
        assert row.identity.composition.reason == "run_composition_not_reported_per_sample"

    phase_refusals = [
        queued
        for queued in result.queue
        if queued.axes == ["phase_composition"]
    ]
    assert {
        queued.observation_id.split("::")[1] for queued in phase_refusals
    } == {
        "evans08_3c_NiS phase_table_row_15",
        "evans08_3c_NiS phase_table_row_20",
    }
    assert len(phase_refusals) == 2
