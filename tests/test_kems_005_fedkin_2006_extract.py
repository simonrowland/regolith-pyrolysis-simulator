"""Source-grounded provenance pins for Fedkin et al. (2006) Table 4."""

from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-005-fedkin-2006.yaml"


def test_alexander_table4_estimates_remain_quoted_external_context(tmp_path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("context", [])
    }
    table4 = context["fedkin_2006_table4_inverse_fit_outputs"]["values"]
    rows = {row["source_observation_id"]: row["values"] for row in table4}

    for observation_id in (
        "fedkin_2006_t4_mg_alexander_2001_inverse",
        "fedkin_2006_t4_sio_alexander_2001_inverse",
    ):
        values = rows[observation_id]
        assert values["method_class"] == "quoted_attributed"
        assert values["evidence_class"] == "quoted_attributed"
        assert values["model"] == "Alexander_2001_EQR"
        assert "Alexander (2001)" in values["attribution"]
        assert "Fedkin et al. (2006), Table 4" in values["attribution"]

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert not {
        "kems-005-fedkin-2006::fedkin_2006_t4_mg_alexander_2001_inverse",
        "kems-005-fedkin-2006::fedkin_2006_t4_sio_alexander_2001_inverse",
    }.intersection(migrator.result.observations)
