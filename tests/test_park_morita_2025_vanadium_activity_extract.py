from __future__ import annotations

import os
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.identity import atm_to_pa

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/park-morita-2025-vanadium-activity.yaml"
SID = "park-morita-2025-vanadium-activity"
CORPUS = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))


@pytest.mark.skipif(not (CORPUS / "raw" / SID / f"{SID}.pdf").is_file(), reason="corpus source is absent")
def test_table4_independent_grid_and_migration(tmp_path: Path) -> None:
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    expected = {
        "CVAS01": ["0.394", "0.468", "0.079", "0.058"],
        "CVAS02": ["0.067", "0.801", "0.132", "0.000"],
        "CVAS03": ["0.140", "0.678", "0.182", "0.000"],
        "CVAS04": ["0.131", "0.696", "0.167", "0.006"],
        "CVAS05": ["0.119", "0.531", "0.304", "0.045"],
        "CVAS06": ["0.039", "0.741", "0.220", "0.000"],
        "CVAS07": ["0.061", "0.818", "0.121", "0.000"],
        "CVAS08": ["0.000", "0.638", "0.320", "0.042"],
        "CVAS09": ["0.000", "0.570", "0.424", "0.006"],
        "CVAS10": ["0.000", "0.527", "0.473", "0.000"],
        "CVAS11": ["0.000", "0.399", "0.601", "0.000"],
        "CVAS12": ["0.000", "0.302", "0.698", "0.000"],
        "CVAS13": ["0.000", "0.607", "0.393", "0.000"],
        "CVAS14": ["0.000", "0.536", "0.464", "0.000"],
    }
    by_id = {o["observation_id"]: o for o in source["species"]["V"]["observations"]}
    assert len(by_id) == 56

    def assert_grid_matches(document: dict, grid: dict[str, list[str]]) -> None:
        rows = {o["observation_id"]: o for o in document["species"]["V"]["observations"]}
        for sample, values in grid.items():
            for state, printed in zip((2, 3, 4, 5), values):
                record = rows[f"park_2025_table4_{sample.lower()}_v{state}_fraction"]
                point = record["values"]["points"][0]
                assert point["value"] == printed
                assert point["value_as_published"] == printed

    assert_grid_matches(source, expected)
    bad_grid = deepcopy(expected)
    bad_grid["CVAS01"][0] = "0.395"
    with pytest.raises(AssertionError):
        assert_grid_matches(source, bad_grid)
    bad_extract = deepcopy(source)
    bad_extract["species"]["V"]["observations"][0]["values"]["points"][0]["value_as_published"] = "0.395"
    with pytest.raises(AssertionError):
        assert_grid_matches(bad_extract, expected)
    bad_value = deepcopy(source)
    bad_value["species"]["V"]["observations"][0]["values"]["points"][0]["value"] = "0.395"
    with pytest.raises(AssertionError):
        assert_grid_matches(bad_value, expected)

    for sample, values in expected.items():
        for state, printed in zip((2, 3, 4, 5), values):
            row = by_id[f"park_2025_table4_{sample.lower()}_v{state}_fraction"]
            assert row["sample_id"] == sample
            assert row["values"]["valence_basis"]["numerator_oxidation_states"] == [state]
            point = row["values"]["points"][0]
            assert point["value"] == printed
            assert point["value_as_published"] == printed
            assert point["T_K"] == "1873"
            oxygen = point["point_conditions"]["oxygen_partial_pressure"]
            assert oxygen["value"] == oxygen["state"]["value"]["point"]
            assert oxygen["as_printed"] in {
                "1.94 × 10^-11 atm", "1.90 × 10^-7 atm"
            }
            assert point["point_conditions"]["temperature_K"]["state"]["value"] == "1873"

    from tests.battery.test_migrate import _migrate_real_extract

    migrated = _migrate_real_extract(tmp_path, EXTRACT.name)
    observations = [o for o in migrated.observations.values() if o.source_id == SID]
    assert len(observations) == 56
    assert all(o.value.kind.value == "point" for o in observations)
    assert all(o.identity.species.formula == "V" for o in observations)
    assert all(o.point_conditions and "oxygen_partial_pressure" in o.point_conditions for o in observations)
    by_observation_id = {o.observation_id: o for o in observations}
    for sample, values in expected.items():
        expected_pressure = Decimal("1.94e-11") if int(sample[-2:]) <= 7 else Decimal("1.90e-7")
        for state, printed in zip((2, 3, 4, 5), values):
            source_id = f"park_2025_table4_{sample.lower()}_v{state}_fraction"
            source_row = by_id[source_id]
            matches = [
                row for key, row in by_observation_id.items()
                if key.startswith(f"{SID}::{source_id}::")
            ]
            assert len(matches) == 1
            observation = matches[0]
            assert observation.value.point == Decimal(printed)
            assert observation.identity.temperature_K.is_value
            assert observation.identity.temperature_K.value == Decimal("1873")
            assert observation.identity.fO2_Pa.is_value
            expected_from_extract = atm_to_pa(Decimal(source_row["values"]["points"][0]["point_conditions"]["oxygen_partial_pressure"]["value"]))
            assert observation.identity.fO2_Pa.value == expected_from_extract == atm_to_pa(expected_pressure)
