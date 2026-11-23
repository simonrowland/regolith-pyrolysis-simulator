from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator
from simulator.yaml_cache import yaml_mapping_source_issues
from tools.validate_literature_extracts import validate_extract_document, validate_extract_file

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kilinc-carmichael-rivers-sack-1983-ferric-ferrous-air.yaml"
SOURCE_ID = "kilinc-carmichael-rivers-sack-1983-ferric-ferrous-air"
EXPECTED_ROWS = (36, 10, 5)
PRINTED_OXIDES = {"SiO2", "TiO2", "Al2O3", "Fe2O3", "Cr2O3", "FeO", "MnO", "MgO", "CaO", "Na2O", "K2O", "P2O5"}


def test_kilinc_extract_fidelity_and_source_mapping() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert validate_extract_file(EXTRACT, check_fidelity_match=True) == []
    assert yaml_mapping_source_issues(EXTRACT.read_text(encoding="utf-8")) == []
    observations = document["species"]["Fe"]["observations"]
    assert tuple(len(item["values"]["rows"]) for item in observations) == EXPECTED_ROWS
    for item in observations[:2]:
        for row in item["values"]["rows"]:
            assert set(row["oxide_wt_percent"]) == PRINTED_OXIDES
            assert isinstance(row["T_C"], str)
            assert isinstance(row["T_C_as_printed"], str)
            assert isinstance(row["run_id_as_printed"], str)
            assert all(isinstance(value, str) for value in row["oxide_wt_percent"].values())
            assert isinstance(row["Fe2O3_wt_pct"], str)
            assert isinstance(row["FeO_wt_pct"], str)
            assert row["cell_locators"]["Fe2O3_wt_pct"] == "Fe2O3"
            assert row["cell_locators"]["FeO_wt_pct"] == "FeO"
            assert row["point_conditions"]["T_C"]["state"]["value"] == row["T_C"]
            assert row["point_conditions"]["T_C"]["locator"] == row["locator"] | {"column": "T °C"}
            assert row["point_conditions"]["composition"]["state"]["value"]["components"] == row["oxide_wt_percent"]
    timed_rows = observations[2]["values"]["rows"]
    assert all("Cr2O3" not in row["oxide_wt_percent"] for row in timed_rows)
    assert all(isinstance(row["T_C"], str) for row in timed_rows)
    assert all(isinstance(row["T_C_as_printed"], str) for row in timed_rows)
    assert all(isinstance(value, str) for row in timed_rows for value in row["oxide_wt_percent"].values())


def test_kilinc_row_coordinates_reach_every_migrated_observation() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == sum(EXPECTED_ROWS)
    assert len(result.queue) == 0
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    for observation in result.observations.values():
        conditions = observation.point_conditions
        assert conditions["T_C"].state.is_value
        assert conditions["composition"].state.is_value
        assert conditions["atmosphere"].state.value == "air"
        assert conditions["fO2_log"].state.is_unknown
        assert observation.identity.quantity.value.value == "fe3_fe2_ratio"


def test_kilinc_fidelity_pin_detects_changed_primary_cell() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    mutated["species"]["Fe"]["observations"][0]["values"]["rows"][0]["oxide_wt_percent"]["FeO"] = "0.65"
    assert validate_extract_document(mutated, expected_source_id=SOURCE_ID, check_fidelity_match=True)
