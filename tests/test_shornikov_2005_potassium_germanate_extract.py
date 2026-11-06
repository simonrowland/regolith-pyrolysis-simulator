from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.test_migrate import REPO_ROOT, _migrate_real_extract


SOURCE_ID = "shornikov-2005-potassium-germanate"
EXTRACT_NAME = f"{SOURCE_ID}.yaml"
EXTRACT_PATH = REPO_ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME


def test_table2_fusion_entropies_are_author_calculations_and_temperatures_are_attributed():
    document = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    observations = {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block.get("observations", [])
    }
    calculated_ids = (
        "ge_2005_t2_K2O_SiO2_dfusS",
        "ge_2005_t2_K2O_2SiO2_dfusS",
        "ge_2005_t2_Na2O_SiO2_dfusS",
        "ge_2005_t2_Na2O_2SiO2_dfusS",
        "ge_2005_t2_3Na2O_8SiO2_dfusS",
    )
    for observation_id in calculated_ids:
        row = observations[observation_id]
        assert row["values"]["method_class"] == "typed_refusal"
        assert row["values"]["method_class_as_printed"] == "model_derived"
        assert row["values"]["admission_status"] == "typed_refusal"
        assert row["values"]["method_as_printed"].startswith(
            "Calculation results in Table 2"
        )
        assert "Table 2" in row["values"]["method_description"]
        assert "equation" in row["values"]["reason"]
        assert "derivation" not in row

    external_temperature_ids = (
        "ge_2005_t2_K2O_SiO2_Tm",
        "ge_2005_t2_K2O_2SiO2_Tm",
        "ge_2005_t2_K2O_4SiO2_Tm",
        "ge_2005_t2_Na2O_SiO2_Tm",
        "ge_2005_t2_Na2O_2SiO2_Tm",
        "ge_2005_t2_3Na2O_8SiO2_Tm",
    )
    for observation_id in external_temperature_ids:
        values = observations[observation_id]["values"]
        assert values["method_class"] == "quoted_attributed"
        assert "[20, 21, 51–53]" in values["attribution"]


def test_isolated_migration_preserves_temperature_conditions_and_lineage(tmp_path: Path):
    result = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    assert result.validation is not None
    assert result.observations

    temperatures = 0
    for observation in result.observations.values():
        assert observation.source_id == SOURCE_ID
        assert observation.point_conditions is not None
        condition = observation.point_conditions.get("temperature_K")
        assert condition is not None, observation.observation_id
        assert condition.locator is not None, observation.observation_id
        if condition.state.is_value:
            temperatures += 1
            identity_temperature = observation.identity.temperature_K
            if identity_temperature is not None and identity_temperature.is_value:
                assert identity_temperature.value == condition.state.value
    assert temperatures > 0
