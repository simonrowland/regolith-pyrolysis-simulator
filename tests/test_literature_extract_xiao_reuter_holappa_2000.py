from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery import migrate as battery_migrate

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/xiao-reuter-holappa-2000-chromium-slag-oxidation-review.yaml"
FORMULAS = ("CaO", "SiO2", "CrO", "CrO1.5")
FORMULA_SLUGS = {"CaO": "cao", "SiO2": "sio2", "CrO": "cro", "CrO1.5": "cro1_5"}

# Independent Table II transcription from the 600 dpi render of physical PDF page 11.
TABLE_II_GRID = [
    ("A1", "0.28", "39.66", "45.69", "14.37", "0.01"),
    ("A2", "0.00", "36.80", "29.06", "34.14", "0.00"),
    ("A3", "0.11", "37.33", "45.37", "17.18", "0.00"),
    ("A4", "0.63", "17.65", "41.63", "40.09", "0.04"),
    ("A5", "0.64", "14.97", "35.93", "48.46", "0.04"),
    ("A6", "1.05", "25.78", "34.29", "38.88", "0.04"),
    ("A7", "1.11", "25.79", "34.26", "38.85", "0.04"),
    ("A8", "0.74", "37.57", "31.13", "30.56", "0.02"),
    ("A9", "0.85", "28.09", "39.98", "31.08", "0.03"),
    ("B1", "46.05", "43.60", "8.72", "1.62", "1.06"),
    ("B2", "46.00", "43.39", "9.40", "1.20", "1.06"),
    ("B3", "46.04", "42.88", "10.08", "0.99", "1.07"),
    ("B4", "43.72", "40.98", "13.46", "1.84", "1.07"),
    ("B5", "35.36", "34.50", "19.71", "10.42", "1.02"),
    ("B6", "34.65", "34.09", "21.90", "9.36", "1.02"),
    ("B7", "36.90", "34.48", "23.80", "4.81", "1.07"),
    ("B8", "41.49", "41.05", "14.91", "2.55", "1.01"),
    ("B9", "41.08", "41.22", "13.58", "4.13", "1.00"),
    ("B10", "40.72", "41.79", "14.98", "2.51", "0.97"),
    ("C1", "42.38", "31.34", "13.73", "12.55", "1.35"),
    ("C2", "44.48", "32.53", "13.11", "9.88", "1.37"),
]


def _table_ii_grid(document):
    rows = {}
    for observation in document["species"]["Cr"]["observations"]:
        locator = observation["locator"]
        if locator.get("table") != "II":
            continue
        formula = observation["values"]["formula"]
        sample = locator["row"]
        rows.setdefault(sample, {})[formula] = observation["values"]["value_as_published"]
    ratios = {
        row["sample"]: row["N_CaO_over_N_SiO2"]
        for row in next(
            context
            for context in document["species"]["Cr"]["context"]
            if context["observation_id"] == "xiao_2000_table2_basicity_ratios"
        )["values"]["values_by_sample"]
    }
    return [
        (sample, *(rows[sample][formula] for formula in FORMULAS), ratios[sample])
        for sample in rows
    ]


def test_render_grid_and_mutations_bind_every_table_ii_cell():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    grid = _table_ii_grid(document)
    assert grid == TABLE_II_GRID
    assert len(grid) == 21

    mutated_expected_grid = list(TABLE_II_GRID)
    changed_expected_row = list(mutated_expected_grid[5])
    changed_expected_row[2] = "34.78"
    mutated_expected_grid[5] = tuple(changed_expected_row)
    with pytest.raises(AssertionError):
        assert grid == mutated_expected_grid

    mutated_document = deepcopy(document)
    mutated_cell = next(
        observation
        for observation in mutated_document["species"]["Cr"]["observations"]
        if observation["observation_id"] == "xiao_2000_sample_B5_cro"
    )
    mutated_cell["values"]["value_as_published"] = "19.17"
    with pytest.raises(AssertionError):
        assert _table_ii_grid(mutated_document) == TABLE_II_GRID


def test_isolated_migration_scores_all_table_ii_components_and_conditions():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = battery_migrate.Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
    }
    assert len(observations) == 84
    assert migrator.result.validation.hard_issues == ()

    expected_rows = {row[0]: row[1:5] for row in TABLE_II_GRID}
    experiment_ids_by_sample = {}
    for sample, printed_components in expected_rows.items():
        for formula, printed in zip(FORMULAS, printed_components, strict=True):
            observation_id = f"xiao_2000_sample_{sample}_{FORMULA_SLUGS[formula]}"
            observation = observations[observation_id]
            experiment_ids_by_sample.setdefault(sample, set()).add(observation.experiment_id)
            assert observation.value.kind.value == "point"
            assert observation.value.point == Decimal(printed) / Decimal(100)
            assert observation.identity.quantity.value.value == "equilibrium_phase_composition"
            assert observation.identity.species.formula == formula
            assert observation.identity.species.phase.is_value
            assert observation.identity.species.phase.value.value == "l"
            assert observation.identity.phase_role.is_value
            assert observation.identity.phase_role.value.value == "slag"
            assert observation.identity.subtype.is_value
            assert observation.identity.subtype.value == "mole_fraction"
            assert observation.identity.temperature_K.is_value
            assert observation.identity.temperature_K.value == Decimal("1863.15")
            argon = observation.point_conditions["gas_mix"]
            assert argon.state.is_value and argon.state.value == "Ar"
            assert argon.locator.page == 11
            assert observation.locator.page == 11
            assert observation.locator.pdf_page_index == 10
            assert not observation.notices
            assert "fO2_log" not in observation.point_conditions
    assert len(experiment_ids_by_sample) == 21
    assert all(len(experiment_ids) == 1 for experiment_ids in experiment_ids_by_sample.values())
    assert len({next(iter(ids)) for ids in experiment_ids_by_sample.values()}) == 21

    figure_records = [
        row
        for row in document["species"]["Cr"]["context"]
        if row["type"] == "figure_record"
    ]
    assert len(figure_records) == 6
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figure_records)
    assert all("points" not in row["values"] for row in figure_records)
    assert all(
        row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1
        for row in [*document["species"]["Cr"]["observations"], *document["species"]["Cr"]["context"]]
    )

    table_i = next(
        row
        for row in document["species"]["Cr"]["context"]
        if row["observation_id"] == "xiao_2000_table1_literature_summary"
    )
    assert len(table_i["values"]["rows_as_printed"]) == 10
    assert table_i["values"]["rows_as_printed"][0]["Cr2_plus_over_total_Cr"] == "0.89–0.99"
    assert table_i["values"]["rows_as_printed"][2]["pO2_as_printed"] == "H2O/H2 < 0.05"
    assert len(
        next(
            row
            for row in document["species"]["Cr"]["context"]
            if row["observation_id"] == "xiao_2000_table2_basicity_ratios"
        )["values"]["values_by_sample"]
    ) == 21
