"""Evidence-class and lineage pin for Ohara et al. (1987)."""

from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-058-ohara-1987.yaml"
OBSERVATION_ID = "ohara_1987_p4o10_sat_janaf_1643k"


def test_janaf_extrapolation_is_model_derived_with_printed_lineage() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_row = next(
        row
        for row in document["species"]["P4O10"]["observations"]
        if row["observation_id"] == OBSERVATION_ID
    )
    values = source_row["values"]
    assert values["method_class"] == "model_derived"
    assert values["evidence_class"] == "model_derived"
    assert values["T_K"] == 1643.0
    assert values["p_atm"] == 700
    assert "attribution" not in values

    derivation = values["derivation"]
    assert derivation["raw_inputs"]["state"] == {
        "tag": "value",
        "value": "not_printed",
    }
    assert derivation["method"]["locator"] == {
        "page": 3,
        "published_page": 1338,
        "pdf_page_index": 2,
        "section": "2.3",
        "equation": "7-8",
    }
    assert derivation["references"] == [
        "Reference [8]: JANAF Thermochemical Tables, NSRDS-NBS (1971), Supplement (1974)."
    ]

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    migrated = migrator.result.observations[
        f"{document['source_id']}::{OBSERVATION_ID}"
    ]
    assert migrated.evidence.class_.value is EvidenceClass.MODEL_DERIVED
