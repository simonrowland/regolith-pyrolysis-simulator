"""Render pins and isolated migration for Stebbins and Carmichael (1984)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator, REPO_ROOT


SOURCE_ID = "rb86-stebbins-carmichael-1984"
EXTRACT = REPO_ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"

RENDER_PINS = {
    "sc1984_table1_c2_run238_enthalpy_increment": ("1516.2", 2, "1", "run 238", "ΔH"),
    "sc1984_table1_c1_run294_enthalpy_increment": ("578.9", 2, "1", "run 294", "ΔH"),
    "sc1984_table1_c1_run219_enthalpy_increment": ("1775.2", 2, "1", "run 219", "ΔH"),
    "sc1984_table2_c2_starting_material_sio2_bulk_analysis": (
        "29.4", 3, "2", "SiO2", "C-2 starting material"
    ),
    "sc1984_incongruent_fusion_1490k_1bar": ("88.4", 5, None, None, None),
    "sc1984_hypothetical_congruent_fusion": ("89.3", 5, None, None, None),
}

TABLE1_RUNS = {
    "238", "241", "242", "198", "199", "202", "294", "205", "208",
    "223²", "226²", "210", "211", "213", "215", "218", "219", "221", "230",
}

REFUSAL_CENSUS = {
    "sc1984_incongruent_fusion_1490k_1bar": (
        "The printed reaction produces a multicomponent peritectic liquid plus metallic Fe. "
        "The heat_of_fusion identity represents pure same-formula solid-to-liquid fusion and "
        "would misidentify this reaction; no exact reaction-bearing identity is available."
    ),
}


def _document(path: Path = EXTRACT) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _observations(document: dict) -> dict[str, dict]:
    return {
        observation["observation_id"]: observation
        for block in document["species"].values()
        for observation in block.get("observations", [])
    }


def _assert_render_pins(document: dict) -> None:
    observations = _observations(document)
    assert len(observations) == 45
    assert len(RENDER_PINS) >= 3
    for observation_id, (value, page, table, row, column) in RENDER_PINS.items():
        observation = observations[observation_id]
        assert observation["values"]["value"] == value
        locator = observation["locator"]
        assert locator["page"] == page
        if table is not None:
            assert locator["table"] == table
            assert locator["row"] == row
            assert locator["column"] == column


def test_rendered_values_table_census_and_interior_mutation() -> None:
    document = _document()
    _assert_render_pins(document)

    observations = _observations(document)
    table1 = {
        observation_id: row
        for observation_id, row in observations.items()
        if observation_id.startswith("sc1984_table1_")
            and row.get("values", {}).get("quantity")
            == "enthalpy_increment_H_T_minus_H_300K_as_printed"
    }
    assert len(table1) == 19
    assert {row["values"]["run_number_as_printed"] for row in table1.values()} == TABLE1_RUNS
    assert sum(row["phase"] == "cr" for row in table1.values()) == 8
    assert sum(row["phase"] == "l" for row in table1.values()) == 11

    table2 = {
        observation_id: row
        for observation_id, row in observations.items()
        if observation_id.startswith("sc1984_table2_")
        and row.get("type") == "concentration_series"
    }
    assert len(table2) == 20
    assert sum(row["values"].get("source_value_status") == "value" for row in table2.values()) == 17
    assert sum(row["values"].get("source_value_status") == "not_determined" for row in table2.values()) == 3

    erratum = next(
        row
        for row in document["species"]["Fe2SiO4"]["context"]
        if row["observation_id"] == "sc1984_erratum_laihunite_reference_only"
    )
    assert "reference only" in erratum["values"]["correction_as_printed"]
    assert "Chao (1978)" in erratum["values"]["correction_as_printed"]
    assert "American Mineralogist 63, 424–425" in erratum["values"]["correction_as_printed"]
    assert erratum["values"]["data_or_formula_change"] == "none"
    inherited_enthalpy = next(
        row
        for row in document["species"]["Fe2SiO4"]["context"]
        if row["observation_id"] == "sc1984_inherited_solid_enthalpy_and_quench_mixture_correction"
    )
    assert "Robie, Finch, and Hemingway (1982)" in inherited_enthalpy["values"]["attribution"]

    changed = deepcopy(document)
    changed_row = _observations(changed)["sc1984_table1_c1_run294_enthalpy_increment"]
    changed_row["values"]["value"] = "579.0"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed)


def test_isolated_migration_binds_printed_conditions_and_exact_refusals() -> None:
    document = _document()
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 45
    by_raw_id = {
        observation_id.rsplit("::", 1)[-1]: observation
        for observation_id, observation in result.observations.items()
    }
    assert len(by_raw_id) == 45
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in by_raw_id.values()) == 42

    raw_observations = _observations(document)
    assert set(raw_observations) == set(by_raw_id)
    for raw in raw_observations.values():
        conditions = raw["point_conditions"]
        for axis in ("T_K", "fO2_Pa", "total_pressure_Pa", "composition"):
            assert axis in conditions
            assert "state" in conditions[axis]
            assert "locator" in conditions[axis]
    enthalpy_ids = {
        observation_id
        for observation_id in raw_observations
        if observation_id.startswith("sc1984_table1_")
        and observation_id.endswith("_enthalpy_increment")
    }
    unknown_quantities = {
        observation_id
        for observation_id, observation in by_raw_id.items()
        if observation.identity.quantity.is_unknown
    }
    expected_unknown_quantities = {
        observation_id
        for observation_id, row in raw_observations.items()
        if row.get("type") == "concentration_series"
    } | {
        "sc1984_table1_c1_liquid_fit_coefficients",
        "sc1984_equation6_liquid_enthalpy_fit",
    } | enthalpy_ids | set(REFUSAL_CENSUS)
    assert unknown_quantities == expected_unknown_quantities
    assert not [
        observation_id
        for observation_id, observation in by_raw_id.items()
        if observation.identity.species.phase.is_unknown
    ]

    enthalpy_rows = {
        observation_id: row
        for observation_id, row in by_raw_id.items()
        if observation_id.startswith("sc1984_table1_")
        and observation_id.endswith("_enthalpy_increment")
    }
    assert len(enthalpy_rows) == 19
    for observation_id, row in enthalpy_rows.items():
        raw = raw_observations[observation_id]
        expected_temperature = Decimal(raw["values"]["T_K"])
        assert row.identity.quantity.is_unknown
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value in {Phase.CR, Phase.L}
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == expected_temperature
        assert row.value.kind is ValueKind.UNAVAILABLE
        assert row.identity.subtype is None
        assert raw["reference_temperature_K"] == 300
        assert "H(T) − H(300 K)" in raw["standard_state"]
        conditions = row.point_conditions
        assert conditions is not None
        for axis in ("T_K", "fO2_Pa", "composition"):
            assert axis in conditions
            assert conditions[axis].locator.table == "1"
        assert conditions["T_K"].state.is_value
        assert conditions["T_K"].state.value.point == expected_temperature
        assert conditions["fO2_Pa"].state.is_unknown
        assert conditions["composition"].state.is_unknown
        assert row.identity.total_pressure_Pa is None or not row.identity.total_pressure_Pa.is_value
        assert row.identity.fO2_Pa is None or not row.identity.fO2_Pa.is_value
        assert row.identity.composition is None or not row.identity.composition.is_value
        assert raw["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"

    for observation_id in (
        "sc1984_table2_c2_starting_material_sio2_bulk_analysis",
        "sc1984_table2_c1_after_calorimetry_feo_bulk_analysis",
    ):
        row = by_raw_id[observation_id]
        assert row.identity.temperature_K is None or not row.identity.temperature_K.is_value
        assert row.identity.total_pressure_Pa is None or not row.identity.total_pressure_Pa.is_value
        assert row.identity.fO2_Pa is None or not row.identity.fO2_Pa.is_value
        raw = raw_observations[observation_id]
        assert raw["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
        assert raw["point_conditions"]["fO2_Pa"]["state"]["tag"] == "unknown"
        assert raw["point_conditions"]["T_K"]["state"]["tag"] == "unknown"

    c2_feo = raw_observations["sc1984_table2_c2_after_calorimetry_feo_bulk_analysis"]
    assert c2_feo["values"]["value"] == "62.3"
    assert "colorimetric" in c2_feo["values"]["method_as_printed"]
    assert "titrimetric" in c2_feo["values"]["method_as_printed"]
    assert c2_feo["values"]["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"
    assert "published C-2 post-calorimetry FeO composition" in c2_feo["values"]["derivation"]["raw_inputs"]["locator"]["note"]

    c2_fe2o3 = raw_observations["sc1984_table2_c2_after_calorimetry_fe2o3_bulk_analysis"]
    expected_c2_ferric_inputs = {
        "sc1984_table2_c2_after_calorimetry_feo_bulk_analysis",
        "sc1984_table2_c2_after_calorimetry_feotot_bulk_analysis",
        "sc1984_table2_c2_after_calorimetry_femetallic_bulk_analysis",
    }
    assert c2_fe2o3["values"]["value"] == "6.5"
    assert set(c2_fe2o3["values"]["derivation"]["inputs"]) == expected_c2_ferric_inputs
    assert set(c2_fe2o3["derived_from"]) == expected_c2_ferric_inputs

    incongruent = by_raw_id["sc1984_incongruent_fusion_1490k_1bar"]
    incongruent_refusal = REFUSAL_CENSUS["sc1984_incongruent_fusion_1490k_1bar"]
    incongruent_raw = raw_observations["sc1984_incongruent_fusion_1490k_1bar"]
    assert incongruent_raw["values"]["admission_status"] == "typed_refusal"
    assert incongruent_raw["values"]["refusal_reason"] == incongruent_refusal
    assert incongruent_raw["values"]["value"] == "88.4"
    assert "0.0253Fe°(sol)" in incongruent_raw["values"]["reaction_as_printed"]
    assert incongruent.identity.quantity.is_unknown
    assert incongruent.value.kind is ValueKind.UNAVAILABLE
    assert incongruent.identity.species.phase.is_value
    assert incongruent.identity.fO2_Pa is None or not incongruent.identity.fO2_Pa.is_value
    assert incongruent.identity.composition is None or not incongruent.identity.composition.is_value
    assert incongruent.point_conditions["total_pressure_Pa"].state.is_value
    assert incongruent.point_conditions["total_pressure_Pa"].state.value == Decimal("100000")
    assert incongruent.point_conditions["total_pressure_Pa"].locator.equation == "11"

    congruent = by_raw_id["sc1984_hypothetical_congruent_fusion"]
    congruent_raw = raw_observations["sc1984_hypothetical_congruent_fusion"]
    assert congruent.identity.quantity.is_value
    assert congruent.identity.quantity.value is Quantity.ENTHALPY_OF_FUSION
    assert congruent.value.kind is ValueKind.POINT
    assert congruent.value.point == Decimal("89.3")
    assert congruent_raw["values"]["reaction_as_printed"] == "Fe2SiO4(solid) = Fe2SiO4(liquid)"
    fusion_profile = profile_for(congruent.identity)
    assert {"temperature_K", "subtype"} <= fusion_profile.required
    assert "total_pressure_Pa" not in fusion_profile.required
    assert congruent.identity.temperature_K is None or not congruent.identity.temperature_K.is_value
    assert congruent.identity.total_pressure_Pa is None or not congruent.identity.total_pressure_Pa.is_value
    assert raw_observations["sc1984_hypothetical_congruent_fusion"]["point_conditions"][
        "total_pressure_Pa"
    ]["state"]["tag"] == "unknown"

    for observation_id, expected_temperature, page in (
        ("sc1984_table2_c2_after_equilibrium_liquid_fe2o3", Decimal("1520"), 3),
        ("sc1984_c1_pre_quench_liquid_fe2o3_from_iron_loss", Decimal("1615"), 2),
    ):
        row = by_raw_id[observation_id]
        profile = profile_for(row.identity)
        assert {
            "temperature_K", "subtype", "phase_role", "composition", "fO2_Pa", "total_pressure_Pa"
        } <= profile.required
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == expected_temperature
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa.is_unknown
        assert row.identity.composition.is_unknown
        assert row.point_conditions["T_K"].locator.page == page

    for observation_id, expected_value in (
        ("sc1984_table2_c2_after_equilibrium_liquid_fe2o3", Decimal("2.1")),
        ("sc1984_c1_pre_quench_liquid_fe2o3_from_iron_loss", Decimal("3.7")),
    ):
        assert by_raw_id[observation_id].value.kind is ValueKind.POINT
        assert by_raw_id[observation_id].value.point == expected_value
