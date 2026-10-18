from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2023-cai-melt-evaporation.yaml"


def test_shornikov_2023_table_and_deferred_figures_migrate_as_context() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    contexts = [
        context
        for work_contexts in result.context_by_work.values()
        for context in work_contexts
    ]
    assert len(result.observations) == 0
    assert len(contexts) == 10
    assert not result.validation.hard_issues

    table = next(
        context
        for context in document["species"]["CaO"]["context"]
        if context["observation_id"]
        == "shornikov_2023_table1_initial_compositions"
    )
    rows = table["values"]["rows"]
    assert len(rows) == 4
    assert table["values"]["numeric_cell_count"] == 12
    assert table["locator"]["page"] == 4
    assert [row["oxide"] for row in rows] == ["CaO", "MgO", "Al2O3", "SiO2"]
    # Values were checked against the printed table on PDF page 4 at 600 dpi.
    assert rows[0]["sample_compositions_wt_pct"] == {
        "A": "36.2",
        "B": "27.6",
        "M": "4.4",
    }
    assert rows[1]["sample_compositions_wt_pct"]["A"] == "5.0"
    assert rows[2]["sample_compositions_wt_pct"]["B"] == "32.3"
    assert rows[3]["sample_compositions_wt_pct"]["M"] == "45.9"
    assert all(row["method_class"] == "quoted_attributed" for row in rows)

    figure_records = [
        context
        for record in document["species"].values()
        for context in record["context"]
        if context["type"] == "figure_record"
    ]
    assert len(figure_records) == 6
    assert {item["locator"]["figure"] for item in figure_records} == {
        "1",
        "2",
        "3",
    }
    for figure in figure_records:
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["quantity"]
        assert values["species_as_printed"]
        assert values["phase_as_printed"]
        assert values["temperature_as_printed"]
        assert values["axes_as_printed"]
        assert values["caption_as_printed"]
        assert values["panel"]
        assert values["attribution"]
        assert values["series_list"]
        series_keys = {
            next(iter(entry))
            if isinstance(entry, dict)
            else entry.split(":", maxsplit=1)[0]
            for entry in values["series_list"]
        }
        assert set(values["marker_count_per_series"]) == {
            str(key) for key in series_keys
        }
        assert all(
            isinstance(count, int) and count > 0
            for count in values["marker_count_per_series"].values()
        )
        assert "points" not in values
