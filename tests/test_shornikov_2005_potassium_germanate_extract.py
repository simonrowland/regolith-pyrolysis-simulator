from __future__ import annotations

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
        assert row["values"]["method_class"] == "model_derived"
        assert row["values"]["value"] == row["values"]["value_as_printed"]
        assert row["values"]["method_as_printed"].startswith(
            "Calculation results in Table 2"
        )
        assert "Table 2" in row["values"]["method_description"]
        assert row["derivation"]["method"]["locator"]["section"] == "Conclusions"
        assert row["derivation"]["references"]
        assert all("none_printed" not in ref for ref in row["derivation"]["references"])
        assert row["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"

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


def test_table1_fit_and_alpha_rows_keep_located_methods_and_score(tmp_path: Path):
    document = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    observations = {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block.get("observations", [])
    }
    expected = {
        "ge_2005_t1_geo_03": ("1.51e-5", "(1.51×10^-5)", "18473", "9.389"),
        "ge_2005_t1_geo_05": ("1.39e-5", "(1.39×10^-5)", "17957", "8.955"),
        "ge_2005_t1_geo_06": ("1.58e-5", "(1.58×10^-5)", "18551", "9.469"),
        "ge_2005_t1_o2_08": ("7.56e-6", "(7.56×10^-6)", "18487", "9.100"),
        "ge_2005_t1_o2_10": ("6.50e-6", "(6.50×10^-6)", "17746", "8.464"),
    }
    pressure_references = ("[23]", "[24]", "[29]", "[30]")
    for observation_id, (value, printed, log_a, log_b) in expected.items():
        row = observations[observation_id]
        assert row["values"]["value"] == value
        assert row["values"]["value_as_printed"] == printed
        assert row["values"]["log10_p_A_K"] == log_a
        assert row["values"]["log10_p_B"] == log_b
        derivation = row["derivation"]
        locator = derivation["method"]["locator"]
        assert locator["published_page"] == 1347
        assert locator["equation"] == "5"
        assert all(
            reference in " ".join(derivation["references"])
            for reference in pressure_references
        )
        assert derivation["raw_inputs"]["state"]["value"] == "not_printed"
        assert "derived_from" not in row
        assert "inputs" not in derivation

    alpha_expected = {
        "ge_2005_alpha_geo": ("0.227", "(2.27 ± 0.43) × 10^−1"),
        "ge_2005_alpha_o2": ("0.198", "(1.98 ± 0.85) × 10^−1"),
    }
    for observation_id, (value, printed) in alpha_expected.items():
        row = observations[observation_id]
        assert row["values"]["value"] == value
        assert row["values"]["value_as_printed"] == printed
        derivation = row["derivation"]
        locator = derivation["method"]["locator"]
        assert locator["published_page"] == 1348
        assert locator["paragraph"] == "partial vaporization coefficients"
        assert all(
            reference in " ".join(derivation["references"])
            for reference in ("[28]", "[30]")
        )
        assert derivation["raw_inputs"]["state"]["value"] == "not_printed"
        assert "derived_from" not in row
        assert "inputs" not in derivation

    result = _migrate_real_extract(tmp_path / "migration", EXTRACT_NAME)
    assert result.validation is not None
    migrated_by_raw_id = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in result.observations.values()
    }
    for observation_id in expected:
        migrated = migrated_by_raw_id[observation_id]
        assert migrated.value.kind.value == "point"
        assert migrated.value.point is not None and migrated.value.point > 0
        assert migrated.evidence.class_.value.value == "model_derived"
        assert migrated.admission.status.value == "admitted"
    for observation_id in alpha_expected:
        migrated = migrated_by_raw_id[observation_id]
        assert migrated.value.kind.value == "point"
        assert migrated.value.point is not None and migrated.value.point > 0
        assert migrated.evidence.class_.value.value == "model_derived"
        assert migrated.admission.status.value == "admitted"
    target_ids = expected.keys() | alpha_expected.keys()
    assert not any(
        observation_id.rsplit("::", 1)[-1] in target_ids
        for observation_id in result.validation.hard_issues_by_observation
    )
