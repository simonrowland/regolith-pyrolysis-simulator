from pathlib import Path

import yaml


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/kropf-2009-alkalis-chondrules-thesis.yaml"
)


def test_measured_table_rows_are_preserved_as_context() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = document["species"]["Na2O"]["context"]
    assert [len(row["values"]["rows"]) for row in rows] == [16, 16, 18]
    assert rows[0]["values"]["rows"][0]["SIMS"] == 0.0100
    assert rows[1]["values"]["rows"][0]["mesostasis"] == 1.83
    assert rows[2]["values"]["rows"][-1]["olivine"] == 0.0065
