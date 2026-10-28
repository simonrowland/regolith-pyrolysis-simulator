from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "kido-2004-mn-cr-high-temperature-glass-spectra"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def test_spectra_only_paper_stays_context_without_redox_ratio_points() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    species = document["species"]["Cr"]
    assert species["observations"] == []
    context = species["context"]
    figures = [record for record in context if record["type"] == "figure_record"]
    assert len(figures) == 4
    assert all(record["values"]["quantity_token"] == "unavailable" for record in figures)
    assert all(
        record["digitisation"] == "deferred to a figure pass"
        for record in figures
    )
    assert all(record["values"].get("points") is None for record in figures)
    assert all(
        set(record["values"]["species"]) == {"Cr", "Mn"}
        for record in figures
    )
    assert all(
        "phase" in record["values"]
        and "temperature_as_printed" in record["values"]
        for record in figures
    )
    assert all(
        "axes_as_printed" in record["values"]
        and "series" in record["values"]
        for record in figures
    )

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.observations
    assert not migrator.result.validation.hard_issues
