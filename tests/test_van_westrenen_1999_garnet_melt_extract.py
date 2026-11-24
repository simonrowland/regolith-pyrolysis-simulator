from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator
from tests.literature_extract_test_helpers import assert_all_physical_locators
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/van-westrenen-1999-garnet-melt-trace-partitioning.yaml"
SOURCE_PATH = "tables/van-westrenen-1999-garnet-melt-trace-partitioning/cited-pages.txt"
SOURCE_SHA256 = "e277ab6e73afb0c36d054d1c412c126ea5a391f85180238a951f39b298917927"


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_table4_glass_extrema_match_the_render_and_keep_source_out_of_repo():
    document = _document()
    rows = document["species"]["SiO2"]["observations"]
    by_id = {row["observation_id"]: row for row in rows}

    assert len(rows) == 26
    selected = {
        "cao": ("CaO", "15.2", "0.3", "wt%"),
        "sio2": ("SiO2", "48.9", "0.4", "wt%"),
        "k": ("K", "1731", "178", "ppm"),
        "lu": ("Lu", "1.3", "0.8", "ppm"),
    }
    for suffix, (formula, value, sigma, units) in selected.items():
        row = by_id[f"vw99_exp11_gl_{suffix}"]
        assert row["type"] == "equilibrium_phase_composition"
        assert row["phase"] == "l"
        assert row["units"] == units
        assert row["values"]["formula"] == formula
        assert row["values"]["value"] == value
        assert row["uncertainty"]["value"] == sigma

    assert all(row["values"]["T_C"] == "1565" for row in rows)
    assert all(row["values"]["method_class"] == "measured_direct" for row in rows)
    assert check_all_fidelity_samples_match(document) == []
    table4_context = next(
        row
        for row in document["species"]["SiO2"]["context"]
        if row["observation_id"] == "vw99_table4_grt_q_rows"
    )
    assert table4_context["locator"]["source_path"] == SOURCE_PATH
    assert table4_context["locator"]["source_sha256"] == SOURCE_SHA256
    assert "printed_rows" not in table4_context["values"]
    assert table4_context["values"]["printed_phase_labels"] == ["Grt", "Q"]

    mutated = deepcopy(document)
    mutated_k = next(
        row
        for row in mutated["species"]["SiO2"]["observations"]
        if row["observation_id"] == "vw99_exp11_gl_k"
    )
    mutated_k["values"]["value"] = "1732"
    assert check_all_fidelity_samples_match(mutated)


def test_isolated_migration_carries_run_conditions_to_every_glass_row():
    document = _document()
    source_rows = document["species"]["SiO2"]["observations"]
    expected_formula = {
        row["observation_id"].split("gl_", 1)[1]: row["values"]["formula"]
        for row in source_rows
    }

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 26
    assert len(result.experiments) == 1
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    required_conditions = {"temperature_K", "total_pressure_Pa", "composition"}
    for row in result.observations.values():
        suffix = row.observation_id.split("gl_", 1)[1]
        assert row.identity.species.formula == expected_formula[suffix]
        assert row.value.kind is ValueKind.POINT
        assert row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert row.identity.species.phase.value is Phase.L
        assert row.identity.temperature_K.value == Decimal("1838.15")
        assert required_conditions <= set(row.point_conditions or {})
        assert row.point_conditions["temperature_K"].state.value == Decimal("1838.15")
        assert row.point_conditions["total_pressure_Pa"].state.value == Decimal("3000000000")
        assert row.point_conditions["composition"].state.is_value
        assert row.locator.table == "4"
        assert row.locator.page == 5
        assert row.locator.pdf_page_index == 4
        assert row.locator.published_page == 842


def test_fidelity_samples_and_physical_locators_cover_table_cells_and_figures():
    document = _document()
    assert len(document["fidelity_samples"]) == 3
    assert all(
        sample["locator"]["source_path"] == SOURCE_PATH
        and sample["locator"]["source_sha256"] == SOURCE_SHA256
        for sample in document["fidelity_samples"]
    )
    assert_all_physical_locators(document)
    context = document["species"]["SiO2"]["context"]
    table3 = next(row for row in context if row["observation_id"] == "vw99_table3_standard_checks")
    figure4 = next(row for row in context if row["observation_id"] == "vw99_figure4_partition_coefficients")
    assert table3["locator"]["page"] == 4
    assert table3["locator"]["pdf_page_index"] == 3
    assert figure4["locator"]["page"] == 4
    assert figure4["locator"]["pdf_page_index"] == 3

    figures = {
        row["locator"]["figure"]: row
        for row in context
        if row.get("type") == "figure_record"
    }
    assert set(figures) == {str(number) for number in range(1, 8)}
    expected_counts = {
        "1": [],
        "2": [16, 16, 7, 15],
        "3": [],
        "4": [18, 21, 21, 21, 21, 5, 5],
        "5": [10, 0, 10, 0, 10, 0, 10, 0, 10, 0],
        "6": [5, 0, 0, 5],
        "7": [5, 20, 19, 5, 0],
    }
    for number, figure in figures.items():
        values = figure["values"]
        assert values["quantity"]
        assert values["identity"]
        assert values["panel"]
        assert values["axes"]
        assert values["attribution"]
        assert values["digitisation"]
        assert [row["marker_count"] for row in values["series"]] == expected_counts[number]
        assert all(row["name"] and row["marker"] and row["attribution"] for row in values["series"])
