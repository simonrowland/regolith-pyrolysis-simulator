from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import (
    REPO_ROOT,
    convert_pressure_to_pa,
)
from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root
from tests.helpers.pdf_text import pdftotext_page_text
from tools.validate_literature_extracts import check_all_fidelity_samples_match


SID = "neubert-2025-vanadium-slag-evaporation"
EXTRACT = REPO_ROOT / "data/literature/extracts" / f"{SID}.yaml"
TABLE1_COMPONENTS = (
    "CaO", "SiO2", "Al2O3", "MnO", "TiO2", "MgO", "K2O", "Na2O", "Fe2O3", "P2O5", "V2O5"
)
TABLE3_COMPONENTS = (
    "CaO", "SiO2", "Fe2O3", "MgO", "Al2O3", "MnO", "Cr2O3", "K2O", "P2O5", "Na2O", "TiO2", "V2O5"
)

# Full numeric grid transcribed from the 600 dpi physical PDF page 4 render.
TABLE3_GRID = tuple(
    tuple(line.split())
    for line in """
01 V1 10 35 500 36.18 30.47 1.00 6.10 15.52 0.09 0.35 0.24 0.00 0.16 0.50 9.39
02 V1 10 35 500 36.76 31.00 1.00 6.15 14.40 0.09 0.34 0.25 0.00 0.17 0.51 9.35
03 V1 20 35 500 35.01 29.62 0.89 5.90 18.11 0.09 0.34 0.23 0.00 0.15 0.49 9.16
04 V1 20 35 500 35.67 30.44 1.02 5.98 16.41 0.09 0.34 0.24 0.00 0.16 0.50 9.15
05 V1 20 35 500 34.96 29.14 1.07 5.81 18.63 0.09 0.33 0.24 0.00 0.27 0.48 8.99
06 V1 30 35 500 34.01 28.31 1.04 5.71 20.84 0.09 0.32 0.24 0.00 0.17 0.47 8.79
07 V1 60 35 500 34.01 28.73 0.92 5.72 20.38 0.09 0.32 0.22 0.00 0.15 0.47 9.01
08 V1 60 35 500 35.08 29.65 0.94 5.90 18.39 0.09 0.32 0.22 0.00 0.15 0.50 8.77
09 V1 60 35 500 31.80 26.60 1.00 5.28 25.91 0.08 0.31 0.21 0.00 0.13 0.43 8.25
10 V1 60 85 500 32.66 27.42 0.91 5.53 23.78 0.09 0.31 0.21 0.00 0.14 0.46 8.49
11 V1 60 85 500 30.19 25.17 0.87 5.11 29.97 0.08 0.28 0.19 0.00 0.12 0.42 7.60
12 V1 20 35 250 35.48 30.03 0.99 5.96 17.73 0.09 0.33 0.22 0.00 0.15 0.50 8.52
13 V1 20 35 250 35.92 30.26 0.96 6.04 16.65 0.09 0.33 0.23 0.00 0.15 0.51 8.87
14 V1 20 85 250 35.85 30.38 1.03 6.03 16.42 0.09 0.33 0.24 0.00 0.16 0.50 8.98
15 V1 20 85 250 35.89 30.30 0.98 6.02 16.37 0.09 0.35 0.24 0.00 0.16 0.51 9.10
16 V1 20 35 0 34.34 28.98 0.93 5.75 19.59 0.09 0.34 0.22 0.00 0.15 0.49 9.12
17 V1 20 35 0 34.12 28.92 0.99 5.70 19.62 0.09 0.34 0.22 0.00 0.14 0.48 9.39
18 V1 20 85 0 35.80 29.97 1.02 5.99 16.47 0.09 0.35 0.22 0.00 0.17 0.50 9.43
19 V1 20 85 0 35.83 30.15 0.89 6.00 16.37 0.09 0.35 0.24 0.00 0.16 0.50 9.43
20 V3 20 35 0 33.59 27.98 0.95 5.55 12.51 0.09 0.32 0.23 9.46 0.17 0.46 8.69
21 V3 20 35 0 33.49 27.93 0.91 5.58 11.25 0.09 0.30 0.23 11.47 0.17 0.46 8.12
22 V3 20 85 0 33.06 27.38 0.88 5.52 12.92 0.08 0.34 0.22 9.70 0.17 0.46 9.29
23 V3 20 85 0 33.29 27.59 0.92 5.53 12.95 0.09 0.35 0.22 9.10 0.17 0.46 9.32
24 V1 10 85 500 36.09 30.25 1.04 5.96 15.98 0.08 0.36 0.24 0.00 0.15 0.50 9.37
25 V1 10 85 500 36.58 30.15 1.02 6.16 15.80 0.09 0.34 0.26 0.00 0.18 0.50 8.94
26 V1 20 85 500 35.01 29.38 1.02 5.83 18.46 0.08 0.35 0.23 0.00 0.14 0.49 9.02
27 V1 20 85 500 35.68 29.57 0.99 5.99 17.64 0.09 0.34 0.24 0.00 0.14 0.49 8.85
28 V1 30 85 500 32.04 26.69 1.01 5.34 25.57 0.08 0.31 0.21 0.00 0.13 0.45 8.18
29 V1 30 85 500 35.42 29.58 0.98 5.92 18.23 0.08 0.33 0.24 0.00 0.14 0.49 8.60
30 V2 20 85 0 30.46 25.47 7.94 5.16 21.32 0.07 0.31 0.20 0.00 0.10 0.41 8.56
31 V2 20 85 0 31.58 26.19 8.30 5.19 18.59 0.08 0.33 0.20 0.00 0.11 0.43 9.01
32 V1 10 35 250 37.92 31.77 1.11 6.29 12.11 0.10 0.36 0.26 0.00 0.17 0.53 9.39
33 V1 30 35 250 33.96 28.19 1.03 5.68 21.23 0.09 0.33 0.23 0.00 0.14 0.47 8.67
34 V1 40 35 250 32.78 27.31 1.01 5.45 23.42 0.09 0.32 0.24 0.00 0.28 0.45 8.65
35 V1 10 85 250 37.45 31.08 1.06 6.37 13.11 0.10 0.36 0.26 0.00 0.19 0.52 9.51
36 V1 30 85 250 35.44 29.61 1.06 5.96 17.53 0.09 0.34 0.24 0.00 0.17 0.49 9.08
37 V1 40 85 250 33.98 28.40 1.04 5.64 20.79 0.09 0.33 0.23 0.00 0.16 0.47 8.87
""".strip().splitlines()
)

TABLE1_GRID = tuple(
    tuple(line.split())
    for line in """
V0 42.08 37.36 10.17 0.10 0.64 7.64 0.36 0.25 1.40 0.00 0.00
V1 37.87 33.63 9.15 0.09 0.58 6.88 0.32 0.23 1.26 0.00 10.00
V2 34.14 30.32 8.25 0.08 0.52 6.20 0.29 0.20 10.00 0.00 10.00
V3 33.66 29.89 8.14 0.08 0.51 6.11 0.29 0.20 1.12 10.00 10.00
""".strip().splitlines()
)


def _table3_grid(document: dict) -> tuple[tuple[str, ...], ...]:
    grid: dict[str, dict[str, str]] = {}
    for container_formula, container in document["species"].items():
        for observation in container.get("observations") or []:
            values = observation["values"]
            if values["quantity"] != Quantity.RESIDUE_COMPONENT_COMPOSITION.value:
                continue
            assert values["analyte"] == container_formula
            for point in values["points"]:
                run_id = point["sample_id"]
                conditions = point["point_conditions"]
                row = grid.setdefault(
                    run_id,
                    {
                        "run_id": run_id,
                        "composition_id": point["composition_id"],
                        "duration_min": point["duration_min"],
                        "pressure_kPa": str(int(conditions["total_pressure_Pa"]["state"]["value"]) // 1000),
                        "oxygen_flow_cm3_min": conditions["flow_rate_cm3_min"]["state"]["value"],
                    },
                )
                row[container_formula] = point[f"{container_formula}_wt_pct"]
        for context in container.get("context") or []:
            if context["type"] != "context_no_token" or context["values"].get("quantity") != "post-run slag oxide composition":
                continue
            for point in context["values"]["rows"]:
                row = grid.setdefault(
                    point["sample_id"],
                    {
                        "run_id": point["sample_id"],
                        "composition_id": point["composition_id"],
                        "duration_min": point["duration_min"],
                        "pressure_kPa": point["pressure_kPa_as_published"],
                        "oxygen_flow_cm3_min": point["flow_rate_cm3_min"],
                    },
                )
                row[container_formula] = point[f"{container_formula}_wt_pct_as_published"]
    return tuple(
        tuple(
            grid[run][key]
            for key in ("run_id", "composition_id", "duration_min", "pressure_kPa", "oxygen_flow_cm3_min", *TABLE3_COMPONENTS)
        )
        for run in sorted(grid)
    )


def _assert_table3_grid(document: dict, expected: tuple[tuple[str, ...], ...]) -> None:
    actual = _table3_grid(document)
    assert actual == expected
    assert len(actual) == 37
    assert all(len(row) == 17 for row in actual)


def _table1_grid(document: dict) -> tuple[tuple[str, ...], ...]:
    records = [
        row
        for row in document["species"]["SiO2"]["context"]
        if row["type"] == "initial_composition"
    ]
    by_sample = {row["values"]["sample_id"]: row for row in records}
    return tuple(
        tuple(
            [sample]
            + [str(by_sample[sample]["values"]["composition_wt_pct"][formula]) for formula in TABLE1_COMPONENTS]
        )
        for sample in ("V0", "V1", "V2", "V3")
    )


def test_neubert_phase_compositions_migrate_with_species_and_printed_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues, result.validation.hard_issues

    source_rows = [row for row in result.observations.values() if row.source_id == SID]
    assert len(source_rows) == 148
    assert all(row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION for row in source_rows)
    assert all(row.value.kind is ValueKind.POINT for row in source_rows)

    expected = Counter()
    raw_by_id = {}
    for formula, container in document["species"].items():
        for raw in container.get("observations") or []:
            values = raw["values"]
            for point in values["points"]:
                expected[(formula, Decimal(point[f"{formula}_wt_pct"]))] += 1
            raw_by_id[raw["observation_id"]] = raw
    migrated = Counter((row.identity.species.formula, row.value.point) for row in source_rows)
    assert migrated == expected

    experiments_by_sample: dict[str, set[str]] = defaultdict(set)
    for observation in source_rows:
        source_observation_id = next(
            key
            for key in raw_by_id
            if observation.observation_id.split("::", 1)[-1].startswith(f"{key}::")
        )
        raw = raw_by_id[source_observation_id]
        values = raw["values"]
        conditions = observation.point_conditions or {}
        formula = values["analyte"]
        assert observation.identity.species.formula == formula
        assert observation.identity.species.phase.value == "l"
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == Decimal("1873")
        assert observation.identity.subtype.is_value
        assert observation.identity.subtype.value == "oxide_wt_percent"
        assert values["composition_wt_pct"]["V2O5"] == "10.00"
        point = values["points"][0]
        expected_starting_composition = next(row for row in TABLE1_GRID if row[0] == point["composition_id"])
        assert {key: values["composition_wt_pct"][key] for key in TABLE1_COMPONENTS} == dict(
            zip(TABLE1_COMPONENTS, expected_starting_composition[1:], strict=True)
        )
        assert observation.identity.total_pressure_Pa.is_value
        total_pressure = point["point_conditions"]["total_pressure_Pa"]["state"]["value"]
        expected_pressure = Decimal(total_pressure)
        converted_pressure, relation = convert_pressure_to_pa(str(int(total_pressure) // 1000), "kPa")
        assert expected_pressure == converted_pressure
        assert relation == "kPa_to_Pa"
        assert observation.identity.total_pressure_Pa.value == expected_pressure
        assert observation.value.point == Decimal(point[f"{formula}_wt_pct"])
        assert not any(notice.kind.value == "source_value_outside_physical_range" for notice in observation.notices)

        for key in ("temperature_K", "duration_min", "total_pressure_Pa", "flow_rate_cm3_min"):
            assert key in conditions, (observation.observation_id, key)
            assert conditions[key].state.is_value, (observation.observation_id, key)
            assert conditions[key].locator is not None, (observation.observation_id, key)
        assert conditions["temperature_K"].state.value == Decimal("1873")
        assert conditions["starting_composition"].state.is_value
        assert conditions["starting_composition"].locator is not None
        assert {
            formula: Decimal(amount)
            for formula, amount in conditions["starting_composition"].state.value.items()
        } == {
            formula: Decimal(amount)
            for formula, amount in values["composition_wt_pct"].items()
            if formula in TABLE1_COMPONENTS
        }
        assert conditions["duration_min"].state.value == Decimal(point["duration_min"])
        assert conditions["total_pressure_Pa"].state.value == expected_pressure
        assert conditions["flow_rate_cm3_min"].state.value == Decimal(point["point_conditions"]["flow_rate_cm3_min"]["state"]["value"])
        if conditions["flow_rate_cm3_min"].state.value == 0:
            assert conditions["atmosphere"].state.value == "argon"
            oxygen = conditions["pO2_Pa"].state.value
            assert oxygen.kind is ValueKind.INTERVAL
            assert oxygen.interval_low == Decimal("0.0101325")
            assert oxygen.interval_high == Decimal("0.101325")
        else:
            assert "pO2_Pa" not in conditions
            if "atmosphere" in conditions:
                assert not conditions["atmosphere"].state.is_value
        assert "fO2_log" not in conditions

        experiments_by_sample[point["sample_id"]].add(observation.experiment_id)
    assert len(experiments_by_sample) == 37
    assert all(len(ids) == 1 for ids in experiments_by_sample.values())
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []


def test_neubert_full_tables_are_render_pinned_and_both_grid_mutations_fail() -> None:
    pdf = regolith_corpus_root() / "raw" / SID / f"{SID}.pdf"
    if not pdf.is_file():
        pytest.skip(f"corpus PDF unavailable: {pdf}")

    page2 = pdftotext_page_text(pdf, 2)
    page3 = pdftotext_page_text(pdf, 3)
    page4 = pdftotext_page_text(pdf, 4)
    assert "Table 1." in page2 and "Table 2." in page2
    assert "1873 K" in page3 and "10" in page3 and "cooling of the melts" in page3
    assert "Table 3." in page4 and "36.18" in page4 and "29.97" in page4 and "8.87" in page4

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert _table1_grid(document) == TABLE1_GRID
    _assert_table3_grid(document, TABLE3_GRID)

    changed_grid = [list(row) for row in TABLE3_GRID]
    changed_grid[1][5] = "999"
    with pytest.raises(AssertionError):
        _assert_table3_grid(document, tuple(tuple(row) for row in changed_grid))

    changed_extract = deepcopy(document)
    row = next(
        item
        for item in changed_extract["species"]["CaO"]["observations"]
        if item["observation_id"] == "neubert_2025_table_3_run_01_cao_slag_composition"
    )
    row["values"]["points"][0]["CaO_wt_pct"] = "999"
    with pytest.raises(AssertionError):
        _assert_table3_grid(changed_extract, TABLE3_GRID)


def test_neubert_context_keeps_initial_charge_and_text_rates_and_defers_all_figures() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = document["species"]["SiO2"]["context"]
    initial = [row for row in context if row["type"] == "initial_composition"]
    assert len(initial) == 4
    assert all(row["T_C_is_initial_composition"] is True for row in initial)

    rates = next(row for row in context if row["observation_id"].endswith("extraction_rates_without_store_token"))
    printed_rates = rates["values"]["values_as_published"]
    assert len(printed_rates) == 8
    assert {row["value"] for row in printed_rates} >= {"13.5", "14", "7", "15.4", "14–18"}
    assert "table-1.csv" in rates["values"]["input_tables_csv"]
    assert "table-3.csv" in rates["values"]["input_tables_csv"]

    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 9
    assert {row["locator"]["figure"] for row in figures} == {str(number) for number in range(1, 10)}
    for record in figures:
        assert record["digitisation"] == "deferred to a figure pass"
        assert record["values"]["numeric_points"] == []
        assert record["values"]["series"]
        assert all(series["marker_count"] >= 0 for series in record["values"]["series"])
