from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import REPO_ROOT
from simulator.battery.validate import validate_corpus
from simulator.yaml_cache import YAML12SafeLoader
from tests.battery.test_migrate import _migrate_real_extract
from tests.test_yaml_flow_scalar_commas import _walk_mapping_source_issues


EXTRACT_NAME = "toguri-1961-mgo-silicon-reduction.yaml"
EXTRACT = REPO_ROOT / "data/literature/extracts" / EXTRACT_NAME
SOURCE_PREFIX = "toguri-1961-mgo-silicon-reduction::"


def test_reaction_table_and_deferred_figures_remain_context_only(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = document["species"]["SiO2"]["context"]
    by_id = {item["observation_id"]: item for item in context}

    table = by_id["toguri_1961_table1_calculated_reaction_thermodynamics"]
    reactions = table["values"]["reactions"]
    assert [item["equation"] for item in reactions] == ["IV", "V", "VII"]
    assert [item["delta_G_as_printed"] for item in reactions] == [
        "47.8 kcal/mole Si",
        "44.9 kcal/mole Si",
        "-27.0 kcal/mole MgO",
    ]
    assert [item["equilibrium_P_SiO_as_printed"] for item in reactions] == [
        "3.26 × 10^-1 mm Hg",
        "2.27 × 10^-1 mm Hg",
        "1.18 × 10^-1 mm Hg",
    ]
    assert table["locator"]["pdf_page_index"] == 5

    quoted = by_id["toguri_1961_quoted_silicon_vapor_pressure"]["values"]
    assert quoted["method_class"] == quoted["evidence_class"] == "quoted_attributed"
    assert quoted["value"]["approximate"] is True

    figures = [item for item in context if item["type"] == "figure_only"]
    assert [item["locator"]["figure"] for item in figures] == ["1", "2", "3", "4"]
    assert [item["locator"]["pdf_page_index"] for item in figures] == [1, 2, 3, 4]
    assert [len(item["values"]["series"]) for item in figures] == [0, 8, 3, 1]
    for figure in figures:
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert all(series["marker_count"] >= 0 for series in values["series"])
        assert "points" not in values
    assert [
        series["marker_count"]
        for series in figures[1]["values"]["series"]
    ] == [9, 9, 9, 9, 9, 9, 8, 0]
    assert [
        series["marker_count"]
        for series in figures[2]["values"]["series"]
    ] == [7, 7, 7]

    source = EXTRACT.read_text(encoding="utf-8")
    loader = YAML12SafeLoader(source)
    try:
        node = loader.get_single_node()
        assert node is not None
        assert _walk_mapping_source_issues(node, loader, source) == []
        loader.construct_document(node)
    finally:
        loader.dispose()

    result = _migrate_real_extract(tmp_path / "isolated", EXTRACT_NAME)
    assert not any(key.startswith(SOURCE_PREFIX) for key in result.observations)
    validation = validate_corpus(
        result.works,
        result.experiments,
        result.observations,
        residuals=None,
    )
    assert validation.hard_issues == ()
