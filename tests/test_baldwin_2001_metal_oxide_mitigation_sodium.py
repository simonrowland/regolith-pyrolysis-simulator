from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/baldwin-2001-metal-oxide-mitigation-sodium.yaml"


def _migrate():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(
        root=ROOT,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return document, migrator


def test_baldwin_context_tables_migrate_without_scoring_unsupported_quantities():
    document, migrator = _migrate()

    assert migrator.result.validation is not None
    assert migrator.result.validation.ok
    assert migrator.result.validation.hard_issues == ()
    assert migrator.result.observations == {}

    context = document["species"]["Na2O"]["context"]
    by_id = {item["observation_id"]: item for item in context}
    assert len(context) == 11
    assert "<0.5" in by_id["baldwin_2001_table1_doe_sodium_inventories"]["values"][
        "rows_as_printed"
    ]
    table3 = by_id["baldwin_2001_table3_candidate_feed_material_test_results"]
    assert "30 to 80%" in table3["values"]["additional_test_result_as_printed"]
    table4 = by_id["baldwin_2001_table4_mixing_temperature_series"]
    assert "~300-400" in table4["values"]["rows_as_printed"]
    assert "about 800 C" in table4["values"]["maximum_temperature_approximate_as_printed"]
    assert "745 C rise to 1272 C" in by_id[
        "baldwin_2001_table6_calculated_adiabatic_temperatures"
    ]["values"]["rows_as_printed"]

    figures = [item for item in context if item["type"] == "figure"]
    assert len(figures) == 4
    assert all(item["values"]["points"] == [] for item in figures)
    assert all(
        item["values"]["digitisation"] == "deferred to a figure pass"
        for item in figures
    )
