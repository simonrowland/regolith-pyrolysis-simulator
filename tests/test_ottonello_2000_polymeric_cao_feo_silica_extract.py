"""Regression pins for Ottonello's model parameters and deferred figures."""

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/ottonello-2000-polymeric-cao-feo-silica.yaml"


def test_ottonello_model_parameters_and_figures_migrate_as_context() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    contexts = document["species"]["SiO2"]["context"]
    parameters = next(row for row in contexts if row["locator"].get("table") == "1")
    figures = [row for row in contexts if row["type"] == "figure"]

    assert parameters["values"]["method_class"] == "model_derived"
    assert "ln Kp,FeO-SiO2 = -3600/T" in parameters["values"]["printed_parameters"]
    assert "chi4,CaO-SiO2 = 67.579 - 0.028095 T" in parameters["values"]["printed_parameters"]
    assert len(figures) == 17
    for number, row in enumerate(figures, start=1):
        assert row["locator"]["figure"] == str(number)
        assert row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1
        assert row["values"]["typed_quantity"]
        assert row["values"]["phase_system"]
        assert row["values"]["x_axis_as_printed"]
        assert row["values"]["y_axis_as_printed"]
        assert row["values"]["caption_as_printed"]
        assert row["values"]["series"]
        assert all("marker_count" in series for series in row["values"]["series"])
        assert row["values"]["digitisation"] == "deferred to a figure pass"

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not [row for row in migrator.result.observations.values() if row.source_id == EXTRACT.stem]
    migrated_contexts = [
        row
        for rows in migrator.result.context_by_work.values()
        for row in rows
        if row["context_id"].startswith(f"{EXTRACT.stem}::context::")
    ]
    assert len(migrated_contexts) == 18
    assert not migrator.result.validation.hard_issues
