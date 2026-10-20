from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
SID = "wright-okamura-1977-makaopuhi-cooling"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
CORPUS = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))
PDF = CORPUS / "raw" / SID / f"{SID}.pdf"

# Independent transcription of the three electron-microprobe columns on the
# 600 dpi render of physical PDF page 42. Dashes in P2O5 and MnO remain unknown.
RENDER_GRID = {
    ("SiO2", "68-1-7"): "75.0", ("SiO2", "68-1-14"): "75.1", ("SiO2", "A12-9"): "75.9",
    ("Al2O3", "68-1-7"): "12.6", ("Al2O3", "68-1-14"): "12.6", ("Al2O3", "A12-9"): "11.9",
    ("FeO", "68-1-7"): ".76", ("FeO", "68-1-14"): "1.64", ("FeO", "A12-9"): "1.1",
    ("MgO", "68-1-7"): ".03", ("MgO", "68-1-14"): ".05", ("MgO", "A12-9"): ".05",
    ("CaO", "68-1-7"): ".40", ("CaO", "68-1-14"): ".43", ("CaO", "A12-9"): ".54",
    ("Na2O", "68-1-7"): "3.25", ("Na2O", "68-1-14"): "3.44", ("Na2O", "A12-9"): "3.37",
    ("K2O", "68-1-7"): "5.82", ("K2O", "68-1-14"): "5.64", ("K2O", "A12-9"): "5.99",
    ("TiO2", "68-1-7"): ".45", ("TiO2", "68-1-14"): ".33", ("TiO2", "A12-9"): ".24",
}


@pytest.mark.skipif(not PDF.is_file(), reason=f"corpus PDF is unavailable: {PDF}")
def test_table17_render_grid_and_migration_bind_each_component_to_sample():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = {
        (formula, row["values"]["sample_id_as_published"]): row
        for formula, body in source["species"].items()
        for row in body["observations"]
    }
    assert len(observations) == 24
    assert {key: row["values"]["value"] for key, row in observations.items()} == RENDER_GRID
    assert all(row["locator"]["page"] == 42 for row in observations.values())
    assert all(row["locator"]["pdf_page_index"] == 41 for row in observations.values())
    assert all(row["locator"]["table"] == "17" for row in observations.values())
    assert all(row["phase"] == "glass" for row in observations.values())
    assert all(row["point_conditions"]["T_C"]["state"]["tag"] == "unknown" for row in observations.values())
    assert all(row["point_conditions"]["quench_state"]["state"]["value"] == "not_stated" for row in observations.values())

    index = {
        SID: {
            "corpus": {
                "raw": {"path": str(PDF), "present": True},
                "text": {"path": str(CORPUS / "text" / SID / "pdftotext-layout.txt"), "exists": True},
            }
        }
    }
    migrator = Migrator(root=ROOT, index=index, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = [row for row in result.observations.values() if row.source_id == SID]
    assert len(migrated) == 24
    assert not result.validation.hard_issues
    assert all(row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION for row in migrated)
    source_formula_by_id = {
        row["observation_id"]: formula
        for formula, body in source["species"].items()
        for row in body["observations"]
    }
    for row in migrated:
        source_id = row.observation_id.split("::", 2)[1]
        assert row.identity.species.formula == source_formula_by_id[source_id]
    assert all(row.identity.temperature_K.tag.value == "unknown" for row in migrated)
    assert all("T_C" in row.point_conditions for row in migrated)
    assert all(row.point_conditions["T_C"].locator.page == 42 for row in migrated)


@pytest.mark.skipif(not PDF.is_file(), reason=f"corpus PDF is unavailable: {PDF}")
def test_every_typed_quantity_has_a_species_formula_and_all_tables_figures_are_audited():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = [
        (formula, row)
        for formula, body in source["species"].items()
        for row in body["observations"]
    ]
    assert len(observations) == 24
    assert all(row["values"]["quantity"] == "equilibrium_phase_composition" for _, row in observations)
    assert all(row["values"]["component_as_published"] == formula for formula, row in observations)
    context = source["species"]["SiO2"]["context"]
    table_contexts = [row for row in context if row["type"] == "measured_table_context" or row["type"] == "method_context" or row["type"] == "model_context"]
    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(table_contexts) == 29
    assert len(figures) == 28
    for figure in figures:
        values = figure["values"]
        assert values["axes_as_printed"]
        assert values["caption"]
        assert values["series"]
        assert len(values["marker_count_per_series"]) == len(values["series"])
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values
