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

TRAILING_DECIMAL_PINS = (
    ("evans08_3a_B230306p5", "O", "41.(1)", "41", "1", 42),
    ("evans08_3a_*B230306p11", "Ca", "21.(1)", "21", "1", 42),
    ("evans08_3a_*B230306p11", "Mn", "6.(2)", "6", "2", 42),
    ("evans08_3a_*B230306p11", "O", "39.(2)", "39", "2", 42),
    ("evans08_3a_B230306p12", "Ca", "20.(1)", "20", "1", 42),
    ("evans08_3a_B230306p12", "O", "39.(1)", "39", "1", 42),
    ("evans08_3a_B050406p2", "O", "41.(1)", "41", "1", 42),
    ("evans08_3a_B110406p4", "Ca", "23.(1)", "23", "1", 43),
    ("evans08_3a_B110406p4", "Mn", "6.(4)", "6", "4", 43),
    ("evans08_3a_B110406p5", "Si", "18.(1)", "18", "1", 43),
    ("evans08_3a_B110406p5", "Ca", "24.(1)", "24", "1", 43),
    ("evans08_3a_B110406p5", "Mn", "5.(3)", "5", "3", 43),
    ("evans08_3a_B110406p5", "O", "41.(1)", "41", "1", 43),
    ("evans08_3a_*B111006p2", "Ca", "24.(1)", "24", "1", 44),
    ("evans08_3a_*B111006p4", "Si", "20.(1)", "20", "1", 44),
    ("evans08_3a_*B111006p4", "Ca", "25.(1)", "25", "1", 44),
    ("evans08_3c_B220306p5", "Si", "21.(3)", "21", "3", 48),
    ("evans08_3c_B220306p5", "Ca", "21.(6)", "21", "6", 48),
    ("evans08_3c_B220306p5", "O", "43.(2)", "43", "2", 48),
    ("evans08_3c_B120406p6", "O", "41.(1)", "41", "1", 49),
)

TRAILING_DECIMAL_TOTAL_PINS = (
    ("evans08_3a_B230306p3", "99.(2)", "99", "2", 42),
    ("evans08_3a_B230306p5", "98.(3)", "98", "3", 42),
    ("evans08_3a_*B230306p11", "95.(5)", "95", "5", 42),
    ("evans08_3a_B230306p12", "96.(4)", "96", "4", 42),
    ("evans08_3a_B050406p2", "98.(3)", "98", "3", 42),
    ("evans08_3a_B110406p5", "99.(1)", "99", "1", 43),
    ("evans08_3b_*B220806p1", "101.(1)", "101", "1", 46),
    ("evans08_3c_B220306p1", "99.(2)", "99", "2", 48),
    ("evans08_3c_*B070406p2", "100.(1)", "100", "1", 48),
    ("evans08_3c_B120406p6", "97.(3)", "97", "3", 49),
    ("evans08_3c_B191006p2", "100.(2)", "100", "2", 50),
    ("evans08_3d_B210306p4", "96.(2)", "96", "2", 51),
    ("evans08_3d_B210306p6", "99.(1)", "99", "1", 51),
)


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


def _assert_trailing_decimal_pins(observations: dict[str, dict]) -> None:
    # Render-checked Table 3 cells: the footnote defines (n) as one SD on the final digit.
    assert len(TRAILING_DECIMAL_PINS) == 20
    for observation_id, formula, printed, value, sigma, page in TRAILING_DECIMAL_PINS:
        point = _point(observations[observation_id], formula)
        assert (
            point["value_as_published"],
            Decimal(str(point["value"])),
            Decimal(str(point["sigma"])),
            point["sigma_as_published"],
            point["sigma_confidence_level"],
            point["locator"]["page"],
        ) == (
            printed,
            Decimal(value),
            Decimal(sigma),
            f"({sigma})",
            "one_sigma",
            page,
        )

    assert len(TRAILING_DECIMAL_TOTAL_PINS) == 13
    for observation_id, printed, value, sigma, page in TRAILING_DECIMAL_TOTAL_PINS:
        observation = observations[observation_id]
        total = observation["values"]["printed_total"]
        printed_cells = observation["values"]["printed_cells"]
        assert printed in printed_cells.values()
        assert observation["locator"]["page"] == page
        assert (
            total["value_as_published"],
            Decimal(str(total["value"])),
            Decimal(str(total["uncertainty"]["value"])),
            total["uncertainty"]["value_as_published"],
            total["uncertainty"]["confidence_level"],
        ) == (printed, Decimal(value), Decimal(sigma), f"({sigma})", "one_sigma")


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
    _assert_trailing_decimal_pins(observations)

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
    for observation in observations.values():
        if observation["locator"]["table"] == "3d":
            assert observation["point_conditions"]["gas_mix"]["locator"] == {
                "page": 41,
                "pdf_page_index": 40,
                "table": "2",
                "row": "Copper",
            }
            assert observation["point_conditions"]["fO2_log"]["locator"] == {
                "page": 39,
                "pdf_page_index": 38,
                "table": "1",
                "row": observation["run_id_as_printed"],
            }

    assert "H. St.C. O’Neill" in document["source"]["citation"]

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
    expected_coefficients = {
        "A_0": ("-14.286", "0.05", "measured_reduced"),
        "A_Ca": ("7.56", "0.13", "quoted_attributed"),
        "A_Mg": ("4.48", "0.13", "quoted_attributed"),
        "A_NaorK": ("4.24", "0.79", "quoted_attributed"),
        "A_Fe": ("26.31", "0.24", "quoted_attributed"),
        "A_Al": ("1.06", "0.18", "quoted_attributed"),
        "A_Mn": ("29.5", "1.7", "model_derived"),
        "A_W": ("8.5", "6.9", "model_derived"),
    }
    by_symbol = {row["values"]["quantity_as_published"]: row for row in table4_contexts.values()}
    assert set(by_symbol) == set(expected_coefficients)
    for symbol, (value, sigma, method_class) in expected_coefficients.items():
        row = by_symbol[symbol]
        assert row["type"] == "model_parameter_context"
        assert row["values"]["quantity"] == "interaction_parameter"
        assert row["values"]["method_class"] == method_class
        assert row["values"]["value_as_published"] == value
        assert row["values"]["uncertainty"]["value_as_published"] == sigma
        assert row["locator"] == {
            "page": 52,
            "pdf_page_index": 51,
            "table": "4",
            "row": symbol,
        }
        assert "derived_from" not in row
    for symbol in ("A_Ca", "A_Mg", "A_NaorK", "A_Fe", "A_Al"):
        assert "O’Neill and Mavrogenes (2002)" in by_symbol[symbol]["values"]["attribution"]
    for symbol in ("A_Mn", "A_W"):
        row = by_symbol[symbol]
        assert row["derivation"]["locator"] == {
            "page": 4,
            "pdf_page_index": 3,
            "equation": "3",
        }
        assert row["derivation"]["raw_inputs"]["state"] == "not_printed"
        assert row["derivation"]["raw_inputs"]["locator"]["page"] == 26
        assert "B270306 was not included" in row["derivation"]["raw_inputs"]["locator"]["note"]
    assert "Ni-bearing data were not fitted" in by_symbol["A_Mn"]["derivation"]["method"]
    a0 = by_symbol["A_0"]
    assert (
        a0["values"]["value"],
        a0["values"]["value_as_published"],
        a0["values"]["uncertainty"]["value"],
        a0["values"]["uncertainty"]["confidence_level"],
    ) == (-14.286, "-14.286", 0.05, "not_stated")
    assert a0["derivation"]["locator"]["page"] == 4
    assert a0["derivation"]["raw_inputs"]["locator"]["page"] == 24
    assert all("A_Ni" not in symbol for symbol in by_symbol)
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
    _assert_trailing_decimal_pins(source_rows)
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    rows = list(result.observations.values())
    assert len(rows) == 1256
    assert len(result.validation.hard_issues) == 0
    assert Counter(row.value.kind.value for row in rows) == {"point": 1178, "unavailable": 78}
    assert Counter(row.uncertainty.kind.value for row in rows) == {"printed": 1160, "none": 96}
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
        if matching_source_point.get("sigma") is not None:
            assert row.uncertainty.kind.value == "printed"
            assert row.uncertainty.verbatim["confidence_level"] == "one_sigma"
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
