from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/hamuyuni-2016-copper-oxide-solubility-thesis-summary.yaml"


def test_hamuyuni_summary_keeps_restatements_and_deferred_figures_context_only() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    assert document["source"]["year"] == 2016
    assert document["species"]["SiO2"]["observations"] == []
    records = document["species"]["SiO2"]["context"]
    figures = [record for record in records if record["type"] == "figure_record"]
    assert len(figures) == 16
    assert {record["locator"]["figure"] for record in figures} == {str(i) for i in range(1, 17)}
    assert all(record["values"]["digitisation"] == "deferred to a figure pass" for record in figures)
    assert all(record["values"]["caption"] for record in figures)
    assert all(record["locator"]["page"] == record["locator"]["pdf_page_index"] + 1 for record in figures)

    summary = next(record for record in records if record["observation_id"] == "hamuyuni_summary_printed_numeric_results_context")
    printed = summary["values"]["printed_values_as_published"]
    assert "2.36–3.39 wt.%" in printed
    assert "1112 ± 2 °C" in printed
    assert "2.06 wt.% Cr2O3" in printed

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.observations
    assert not migrator.result.validation.hard_issues


def test_hamuyuni_printed_solubilities_are_preserved_as_context() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    records = document["species"]["SiO2"]["context"]
    expected = {
        "hamuyuni_cao_liquid_and_cuo_in_cao_context": (37, ("13 and 19 wt.%", "≤1 wt.%")),
        "hamuyuni_cao_liquid_copper_and_cao_in_cuo_context": (38, ("4.35 wt.%", "≤1 wt.%")),
        "hamuyuni_cuo_in_solid_phases_context": (41, ("0.5 to 2.23 wt.%",)),
        "hamuyuni_cuo_in_calcium_aluminate_phases_context": (42, ("1.0 to 2.5 wt.%", "nearly ≥0.5 wt.%", "≥1.25 wt.%")),
        "hamuyuni_liquidus_deviation_context": (47, ("1350 and 1400 °C", "up to 40 wt.%")),
    }
    by_id = {record["observation_id"]: record for record in records}
    for record_id, (page, printed_values) in expected.items():
        record = by_id[record_id]
        assert record["type"] == "context_only"
        assert record["locator"]["page"] == page
        assert record["locator"]["pdf_page_index"] == page - 1
        printed = record["values"]["printed_values_as_published"]
        assert all(value in printed for value in printed_values)
