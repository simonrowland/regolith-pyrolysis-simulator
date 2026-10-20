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
