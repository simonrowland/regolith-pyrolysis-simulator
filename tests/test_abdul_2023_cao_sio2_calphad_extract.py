"""Source-grounded attribution pins for Abdul et al. (2023)."""

from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/abdul-2023-cao-sio2-calphad.yaml"


def test_external_context_keeps_quoted_class_and_names_the_cited_work(tmp_path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("context", [])
    }
    citations = document["source"]["cited_references"]

    correction = context["abdul_2023_cao_reassessment_correction_context"]["values"]
    assert correction["evidence_class"] == "quoted_attributed"
    assert correction["attribution"] == citations["10"]

    assert context["abdul_2023_table1_structure"]["values"]["method_class"] == "quoted_attributed"
    assert context["abdul_2023_table2_solid_transitions"]["values"]["method_class"] == "quoted_attributed"

    table8 = context["abdul_2023_table8_liquid_associate_model"]["values"]["rows"]
    assert table8[0]["method_class"] == "quoted_attributed"
    assert table8[0]["attribution"] == citations["6"]
    assert table8[1]["method_class"] == "quoted_attributed"
    assert table8[1]["attribution"] == citations["7"]

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert not set(context).intersection(migrator.result.observations)
