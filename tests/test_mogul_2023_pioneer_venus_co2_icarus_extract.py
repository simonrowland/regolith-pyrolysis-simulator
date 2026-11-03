"""Pin Mogul's reduced measurements and model-output lineage."""

from pathlib import Path

import yaml

from simulator.battery import migrate as battery_migrate

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "mogul-2023-pioneer-venus-co2-icarus"
EXTRACT = (
    ROOT
    / "data"
    / "literature"
    / "extracts"
    / f"{SOURCE_ID}.yaml"
)


def test_mogul_derived_rows_have_located_lineage_and_keep_values() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_rows = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }
    isotope = source_rows["mogul_2023_co2_isotope_ratios"]
    density = source_rows["mogul_2023_co2_density_profile"]
    methane = source_rows["mogul_2023_ch4_mixing_ratio_model"]

    assert isotope["values"]["method_class"] == "measured_reduced"
    assert [row["value"] for row in isotope["values"]["rows"]] == [
        "1.260e-2 ± 0.072e-2",
        "2.195e-3 ± 0.156e-3",
    ]
    isotope_inputs = isotope["values"]["derivation"]["raw_inputs"]
    assert isotope_inputs["state"]["value"] == "not_printed"
    assert isotope_inputs["locator"]["page"] == 13
    assert isotope_inputs["locator"]["section"] == "3.9"

    density_parent = (
        f"{SOURCE_ID}::context::mogul_2023_quoted_reference_conditions"
    )
    assert density["values"]["method_class"] == "model_derived"
    assert density["derived_from"] == [density_parent]
    assert density["values"]["derivation"]["inputs"] == [density_parent]
    assert density["values"]["derivation"]["method"]["locator"] == {
        "page": 14,
        "section": "3.11",
        "figure": "7",
    }
    assert density["values"]["printed_points"][0]["co2_density_kg_m3"] == (
        "0.38 ± 0.06"
    )

    methane_parents = ["mogul_2023_co2_density_profile"]
    assert methane["values"]["method_class"] == "model_derived"
    assert [row["value"] for row in methane["values"]["rows"]] == [
        "~15 ± 12 ppm",
        "~130 ± 100 ppm",
        "~1200 ± 1000 ppm",
    ]
    assert methane["derived_from"] == methane_parents
    assert methane["values"]["derivation"]["inputs"] == methane_parents
    assert methane["values"]["derivation"]["method"]["locator"] == {
        "page": 19,
        "section": "4.3",
        "note": (
            "The paper names no external input references for this conversion; "
            "Donahue and Hodges (1993) and Oyama et al. (1980) are comparison "
            "values in this section."
        ),
    }

    migrator = battery_migrate.Migrator(ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()

    migrated = {
        observation_id: observation
        for observation_id, observation in migrator.result.observations.items()
        if observation.source_id == SOURCE_ID
    }
    assert len(migrated) == 6
    isotope_rows = [
        observation
        for observation_id, observation in migrated.items()
        if "mogul_2023_co2_isotope_ratios::rows:" in observation_id
    ]
    assert len(isotope_rows) == 2
    for observation in isotope_rows:
        assert observation.evidence.class_.value.value == "measured_reduced"
        assert observation.derivation is not None
        assert observation.derivation.raw_inputs is not None
        assert (
            observation.derivation.raw_inputs.state.value.value
            == "not_printed"
        )

    density_observation = migrated[
        f"{SOURCE_ID}::mogul_2023_co2_density_profile"
    ]
    assert density_observation.evidence.class_.value.value == "model_derived"
    assert density_observation.derived_from == (density_parent,)
    assert density_observation.derivation is not None
    assert density_observation.derivation.method is not None
    assert density_observation.derivation.method.locator.page == 14
    assert density_observation.derivation.method.locator.section == "3.11"

    methane_rows = [
        observation
        for observation_id, observation in migrated.items()
        if "mogul_2023_ch4_mixing_ratio_model::rows:" in observation_id
    ]
    assert len(methane_rows) == 3
    migrated_methane_parents = (
        f"{SOURCE_ID}::mogul_2023_co2_density_profile",
    )
    for observation in methane_rows:
        assert observation.evidence.class_.value.value == "model_derived"
        assert observation.derived_from == migrated_methane_parents
        assert observation.derivation is not None
        assert observation.derivation.method is not None
        assert observation.derivation.method.locator.page == 19
        assert observation.derivation.method.locator.section == "4.3"
