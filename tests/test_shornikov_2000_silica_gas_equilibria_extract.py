from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT
from simulator.battery.validate import validate_corpus


EXTRACT = REPO_ROOT / "data/literature/extracts/shornikov-2000-silica-gas-equilibria.yaml"


def _context_by_id(extract):
    return {
        item["observation_id"]: item
        for item in extract["species"]["SiO2"]["context"]
    }


def test_all_six_tables_and_deferred_figure_are_retained_from_rendered_pages():
    extract = yaml.safe_load(EXTRACT.read_text())
    context = _context_by_id(extract)

    expected_table_rows = {
        "shornikov_2000_table1_reaction_1": 24,
        "shornikov_2000_table2_reaction_4": 23,
        "shornikov_2000_table4_appearance_potentials": 9,
        "shornikov_2000_table5_reaction_5": 5,
        "shornikov_2000_table6_reaction_17": 19,
    }
    for observation_id, expected_count in expected_table_rows.items():
        item = context[observation_id]
        assert len(item["values"]["rows"]) == expected_count
        assert item["locator"]["pdf_page_index"] >= 0

    table3 = context["shornikov_2000_table3_previous_mass_spectra"]
    assert table3["values"]["source_row_count"] == 10
    assert len(table3["values"]["rows"]) == 9
    this_work = context["shornikov_2000_table3_this_work_ratios"]
    assert this_work["values"]["method_class"] == "measured_direct"
    assert this_work["values"]["admission_status"] == "typed_refusal"
    assert this_work["values"]["refusal_reason"] == "unresolved_ion_intensity_ratio_subtype"
    ratios = this_work["values"]["rows"]
    assert len(ratios) == 4
    assert [row["ion_intensity_ratio"] for row in ratios] == [
        3.03e-2,
        1.0,
        1.13e-3,
        1.39e-2,
    ]
    assert this_work["locator"]["published_page"] == 363
    assert this_work["locator"]["pdf_page_index"] == 3

    figure_record = context["shornikov_2000_figure1_reaction5"]
    figure = figure_record["values"]
    assert figure_record["locator"]["published_page"] == 368
    assert figure_record["locator"]["pdf_page_index"] == 8
    assert figure["digitisation"] == "deferred to a figure pass"
    assert "points" not in figure
    assert [series["marker_count"] for series in figure["series"]] == [0, 1, 0, 0, 0, 0]


def test_extract_migrates_without_hard_issues_while_ratio_identity_is_pending():
    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    report = validate_corpus(
        result.works,
        result.experiments,
        result.observations,
        residuals=None,
    )
    assert report.hard_issues == ()
    assert not result.observations
    assert len(result.works) == 1
