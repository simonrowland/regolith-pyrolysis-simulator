from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import to_plain
from tests.battery.test_migrate import _migrate_real_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/jacquet-2026-alkali-recondensation.yaml"


def _assert_sc456_model_rows(document: dict) -> None:
    rows = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }
    expected = {
        "jacquet_2026_table3_ci_normalized_alkali_averages": "Lodders (2003)",
        "jacquet_2026_table_a5_na_activity_coefficient": "Mathieu (2009)",
    }
    for observation_id, reference in expected.items():
        row = rows[observation_id]
        values = row["values"]
        assert values["method_class"] == "model_derived"
        assert values["evidence_class"] == "model_derived"
        assert "attribution" not in values
        derivation = row["derivation"]
        assert derivation["raw_inputs"]["state"]["value"] == "not_printed"
        assert derivation["method"]["state"]["value"]
        assert derivation["method"]["locator"]["page"] > 0
        assert derivation["method"]["locator"]["section"]
        assert derivation["references"]
        assert any(reference in item for item in derivation["references"])


def test_sc456_author_calculations_are_model_derived_and_mutation_sensitive():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_sc456_model_rows(document)

    mutated = deepcopy(document)
    row = next(
        row
        for species in mutated["species"].values()
        for row in species.get("observations", [])
        if row["observation_id"] == "jacquet_2026_table_a5_na_activity_coefficient"
    )
    row["values"]["method_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_sc456_model_rows(mutated)


def test_jacquet_table_rows_match_render_and_refuse_incomplete_identities(tmp_path):
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = doc["species"]
    table2 = [
        point
        for element in ("Li", "K", "Rb", "Ge", "Te", "Zn", "Cd")
        for point in species[element]["observations"][0]["values"]["points"]
    ]
    assert len(table2) == 14
    assert table2[0]["delta_permil"] == "3.6"
    assert table2[3]["delta_permil"] == "-0.33"
    assert table2[3]["sigma_permil"] == "0.12"
    assert table2[7]["uncertainty_interval_permil"] == ["-1.13", "0.87"]
    assert len(species["Chondrule"]["observations"][0]["values"]["points"]) == 5
    assert len(species["Na"]["observations"][0]["values"]["points"]) == 5

    migrated = _migrate_real_extract(
        tmp_path, EXTRACT.name, extract_doc=doc
    )
    plain = [to_plain(item) for item in migrated.observations.values()]
    assert len(plain) == 32
    assert migrated.registry_issues == []
    assert all(item["admission"]["status"] == "rejected" for item in plain)
    assert all(item["value"]["kind"] == "unavailable" for item in plain)
    assert sum(item["locator"].get("table") == "2" for item in plain) == 14
    assert sum(item["locator"].get("table") == "A.5" for item in plain) == 5
    assert sum(item["locator"].get("table") == "3" for item in plain) == 5
    a5 = [item for item in plain if item["locator"].get("table") == "A.5"]
    assert all(item["identity"]["temperature_K"] == {
        "tag": "value", "value": "1673.15"
    } for item in a5)
    assert all(item["identity"]["total_pressure_Pa"]["tag"] == "unknown" for item in a5)
    assert all(item["identity"]["composition"]["tag"] == "unknown" for item in a5)


def test_jacquet_render_fidelity_pin_detects_mutation():
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert check_all_fidelity_samples_match(doc, label="jacquet") == []
    doc["species"]["K"]["observations"][0]["values"]["points"][1][
        "delta_permil"
    ] = "-0.34"
    assert check_all_fidelity_samples_match(doc, label="jacquet-mutated")
