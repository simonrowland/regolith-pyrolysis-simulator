from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from literature_extract_assertions import assert_all_physical_locators
from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/van-westrenen-1999-garnet-melt-trace-partitioning.yaml"
MAJOR_PINS = {
    "cao": ("15.2", "0.3"),
    "mgo": ("19.9", "0.3"),
    "al2o3": ("15.9", "0.2"),
    "sio2": ("48.9", "0.4"),
}
TRACE_PINS = {
    "li": ("Li", "10", "1"),
    "b": ("B", "599", "27"),
    "k": ("K", "1731", "178"),
    "sc": ("Sc", "7.0", "0.1"),
    "ti": ("Ti", "64", "7"),
    "sr": ("Sr", "241", "13"),
    "y": ("Y", "5.5", "0.08"),
    "zr": ("Zr", "32.1", "0.4"),
    "nb": ("Nb", "31", "1"),
    "cd": ("Cd", "129", "10"),
    "in": ("In", "102", "2"),
    "la": ("La", "94", "3"),
    "pr": ("Pr", "24.2", "0.4"),
    "sm": ("Sm", "13.7", "0.6"),
    "tb": ("Tb", "9.0", "0.1"),
    "er": ("Er", "5.9", "0.2"),
    "yb": ("Yb", "4.1", "0.2"),
    "lu": ("Lu", "1.3", "0.8"),
    "hf": ("Hf", "12", "0.5"),
    "ta": ("Ta", "563", "16"),
    "th": ("Th", "1421", "39"),
    "u": ("U", "249", "10"),
}


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_table4_glass_major_and_trace_compositions_match_the_render():
    document = _document()
    rows = document["species"]["SiO2"]["observations"]
    by_id = {row["observation_id"]: row for row in rows}

    assert len(rows) == 26
    major_formulas = {"cao": "CaO", "mgo": "MgO", "al2o3": "Al2O3", "sio2": "SiO2"}
    for suffix, (value, sigma) in MAJOR_PINS.items():
        row = by_id[f"vw99_exp11_gl_{suffix}"]
        assert row["type"] == "equilibrium_phase_composition"
        assert row["phase"] == "l"
        assert row["units"] == "wt%"
        assert row["values"]["formula"] == major_formulas[suffix]
        assert row["values"]["value"] == value
        assert row["uncertainty"]["value"] == sigma
        assert row["values"]["T_C"] == "1565"

    for suffix, (element, value, sigma) in TRACE_PINS.items():
        row = by_id[f"vw99_exp11_gl_{suffix}"]
        assert row["type"] == "equilibrium_phase_composition"
        assert row["phase"] == "l"
        assert row["units"] == "ppm"
        assert row["values"]["formula"] == element
        assert row["values"]["quantity"] == "equilibrium_phase_composition"
        assert row["values"]["method_class"] == "measured_direct"
        assert row["values"]["basis_as_published"] == f"{element} ppm in glass (Gl), Table 4"
        assert row["values"]["printed_phase_label"] == "Gl"
        assert row["values"]["value"] == value
        assert row["uncertainty"]["value"] == sigma
        assert row["uncertainty"]["verbatim"] == sigma
        assert row["values"]["trace_suite_context"] == "vw99_table4_grt_q_rows"

    assert "Tm" not in {element for element, _, _ in TRACE_PINS.values()}
    table4_context = next(
        row
        for row in document["species"]["SiO2"]["context"]
        if row["observation_id"] == "vw99_table4_grt_q_rows"
    )
    assert "Grt" in table4_context["values"]["printed_phase_labels"]
    assert "Q" in table4_context["values"]["printed_phase_labels"]
    assert "trace-element suite is typed" in table4_context["values"]["reason"]


def test_isolated_migration_has_no_hard_issues_and_carries_conditions_to_every_row():
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 26
    assert len(result.experiments) == 1
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    expected_formula = {
        **{suffix: formula for suffix, formula in {
            "cao": "CaO", "mgo": "MgO", "al2o3": "Al2O3", "sio2": "SiO2",
        }.items()},
        **{suffix: element for suffix, (element, _value, _sigma) in TRACE_PINS.items()},
    }
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


def test_fidelity_samples_cover_the_cells_and_detect_a_mutated_trace_value():
    document = _document()
    assert len(document["fidelity_samples"]) == 27
    assert check_all_fidelity_samples_match(document) == []

    mutated = deepcopy(document)
    li = next(
        row
        for row in mutated["species"]["SiO2"]["observations"]
        if row["observation_id"] == "vw99_exp11_gl_li"
    )
    li["values"]["value"] = "11"
    assert check_all_fidelity_samples_match(mutated)


def test_all_records_have_physical_locators_and_figures_have_full_inventories():
    document = _document()
    assert_all_physical_locators(document)
    context = document["species"]["SiO2"]["context"]
    table3 = next(row for row in context if row["observation_id"] == "vw99_table3_standard_checks")
    figure4 = next(row for row in context if row["observation_id"] == "vw99_figure4_partition_coefficients")
    assert table3["locator"] == {
        "table": "3",
        "page": 4,
        "published_page": 841,
        "pdf_page_index": 3,
    }
    assert figure4["locator"] == {
        "figure": "4",
        "page": 4,
        "published_page": 841,
        "pdf_page_index": 3,
    }

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
        series = values["series"]
        assert [row["marker_count"] for row in series] == expected_counts[number]
        assert all(row["name"] and row["marker"] and row["attribution"] for row in series)
