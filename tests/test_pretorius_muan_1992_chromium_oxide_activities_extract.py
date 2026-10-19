from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/pretorius-muan-1992-chromium-oxide-activities.yaml"


def test_activity_tables_are_preserved_without_unverified_reduction_lineage():
    extract = yaml.safe_load(EXTRACT.read_text())
    context = extract["species"]["CrO"]["context"]
    table_ii = next(row for row in context if row["observation_id"].endswith("table_2_context"))
    table_iii = next(row for row in context if row["observation_id"].endswith("table_3_context"))
    assert len(table_ii["values"]["printed_rows"]) == 32
    assert len(table_iii["values"]["printed_rows"]) == 21
    assert table_ii["values"]["printed_rows"][0][0] == "Sample No."
    assert table_iii["values"]["printed_rows"][-1][0] == "CACrS 20"
    assert not extract["species"]["CrO"]["observations"]
