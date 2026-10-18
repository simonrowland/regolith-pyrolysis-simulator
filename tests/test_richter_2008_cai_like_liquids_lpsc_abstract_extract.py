"""Source-grounded checks for the context-only Richter et al. abstract extract."""

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/richter-2008-cai-like-liquids-lpsc-abstract.yaml"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_context_only_extract_preserves_printed_fits_conditions_and_figures() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    species = document["species"]["SiO2"]
    assert species["observations"] == []
    context = species["context"]
    figures = [row for row in context if row["type"] == "figure_record"]
    assert [row["values"]["species"] for row in figures] == [
        ["Mg", "SiO"],
        ["Mg"],
        ["Mg"],
    ]
    expected = {
        "1": {"pdf_page_index": 0, "marker_counts": [1, 1, 4, 4]},
        "2": {"pdf_page_index": 1, "marker_counts": [6, 5, 0, 0]},
        "3": {"pdf_page_index": 1, "marker_counts": [1, 4, 0, 0, 0]},
    }
    expected_axes = {
        "1": {
            "x": "10000/T(K)",
            "top_x": "T (°C)",
            "y": "log evaporation rate (moles cm−2 s−1)",
        },
        "2": {"x": "−ln(f^24Mg)", "y": "1000 ln(R/R0)"},
        "3": {
            "x": "10000/T(K)",
            "top_x": "T (°C)",
            "y": "10000(1−α)",
        },
    }
    for figure in figures:
        values = figure["values"]
        figure_number = figure["locator"]["figure"]
        expected_figure = expected[figure_number]
        assert figure["locator"]["published_page"] == "1385.pdf"
        assert figure["locator"]["pdf_page_index"] == expected_figure["pdf_page_index"]
        assert values["phase"]
        assert values["axes_as_printed"] == expected_axes[figure_number]
        assert values["caption"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values
        assert [series["marker_count"] for series in values["series"]] == expected_figure[
            "marker_counts"
        ]
    assert "about two orders of magnitude larger that the vacuum rate" in figures[0][
        "values"
    ]["caption"]

    fits = next(
        row["values"]
        for row in context
        if row["observation_id"] == "richter_2008_figure2_printed_alpha_fits"
    )
    assert fits["quantity_token"] == "unavailable"
    assert [fit["value_as_printed"] for fit in fits["fits"]] == [
        "0.97980",
        "0.98797",
    ]
    assert fits["fits"][1]["uncertainty_as_printed"] == "± 0.00022"

    pressure = next(
        row["values"]
        for row in context
        if row["observation_id"] == "richter_2008_text_hydrogen_pressure_dependence"
    )
    assert pressure["relations"][0]["threshold"]["value"] == {
        "kind": "point",
        "point": "10−7",
        "approximate": True,
    }

    rate_comparison = next(
        row["values"]
        for row in context
        if row["observation_id"] == "richter_2008_figure1_approximate_rate_comparison"
    )
    assert rate_comparison["rate_ratio"]["value"]["approximate"] is True


def test_context_only_extract_migrates_without_scored_observations() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert migrator.result.observations == {}
    assert sum(len(rows) for rows in migrator.result.context_by_work.values()) == 6
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
