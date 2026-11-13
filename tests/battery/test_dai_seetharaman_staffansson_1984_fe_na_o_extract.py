from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import yaml

import simulator.battery.migrate as migrate_module
from simulator.battery.enums import EvidenceClass, Phase, ValueKind


_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXTRACT = (
    _REPO_ROOT
    / "data/literature/extracts/dai-seetharaman-staffansson-1984-fe-na-o-thermochemistry.yaml"
)
_SOURCE_ID = "dai-seetharaman-staffansson-1984-fe-na-o-thermochemistry"


def _migrated():
    with patch.object(migrate_module, "discover_extracts", return_value=[_EXTRACT]):
        migrator = migrate_module.Migrator(root=_REPO_ROOT)
        migrator.migrate_extracts()
        migrator.finalize()
    return migrator.result


def _source_observation_id(observation):
    return observation.observation_id.split("::", 2)[1]


def _find(observations, observation_id):
    matches = [
        observation
        for observation in observations
        if _source_observation_id(observation) == observation_id
    ]
    assert len(matches) == 1
    return matches[0]


def _raw_row(extract, species, observation_id):
    matches = [
        row
        for row in extract["species"][species]["observations"]
        if row["observation_id"] == observation_id
    ]
    assert len(matches) == 1
    return matches[0]


def test_dai_1984_printed_tables_survive_the_real_migrator_and_render_pins():
    result = _migrated()
    observations = [
        observation
        for observation in result.observations.values()
        if observation.source_id == _SOURCE_ID
    ]
    assert len(observations) == 67
    assert not result.validation.hard_issues, "\n".join(
        str(issue) for issue in result.validation.hard_issues
    )
    assert sum(
        observation.value.kind is ValueKind.UNAVAILABLE
        for observation in observations
    ) == 67

    table_counts = Counter(observation.locator.table for observation in observations)
    assert table_counts == {
        "I": 11,
        "II": 18,
        "III": 3,
        "IV": 3,
        "V": 9,
        "VI": 11,
        "VII": 12,
    }

    expected_quantity_unknown_ids = {
        *(f"dai_1984_table1_r{row}_{species}" for row, species in (
            (3, "na2o"),
            (4, "na2o"),
            (5, "na2o"),
            (6, "na2o"),
            (8, "na2o"),
            (9, "feo"),
            (10, "na"),
            (11, "fe"),
        )),
        *(f"dai_1984_table2_{species}_{suffix}" for species, suffixes in (
            ("feo", ("heat_evolution_1", "heat_evolution_2", "heat_evolution_3", "heat_evolution_average")),
            ("na2o", ("heat_evolution_1", "heat_evolution_2", "heat_evolution_3", "heat_evolution_average")),
            ("na2feo2", ("heat_evolution_1", "heat_evolution_2", "heat_evolution_3", "heat_evolution_4", "heat_evolution_average", "enthalpy_of_mixing")),
            ("na4feo3", ("heat_evolution_1", "heat_evolution_2", "heat_evolution_average", "enthalpy_of_mixing")),
        ) for suffix in suffixes),
        "dai_1984_table3_na2fe2o4_gibbs_from_oxides",
        "dai_1984_table3_na2feo2_gibbs_from_oxides",
        "dai_1984_table3_na4feo3_gibbs_from_oxides",
        *(f"dai_1984_table5_r{row}_reaction_gibbs" for row in range(1, 10)),
        *(f"dai_1984_table6_r{row}_log_pressure_relation" for row in range(1, 12)),
        "dai_1984_table7_r1_873_o2_log_pressure",
        "dai_1984_table7_r1_873_na_log_pressure",
        "dai_1984_table7_r1_1173_o2_log_pressure",
        "dai_1984_table7_r1_1173_na_log_pressure",
        *(f"dai_1984_table7_r{row}_873_{species}_log_pressure" for row in range(2, 6) for species in ("o2", "na")),
    }
    quantity_unknown_ids = {
        _source_observation_id(observation)
        for observation in observations
        if observation.identity.quantity.is_unknown
    }
    assert quantity_unknown_ids == expected_quantity_unknown_ids
    assert len(quantity_unknown_ids) == 61
    assert all(
        observation.identity.quantity.is_value
        for observation in observations
        if _source_observation_id(observation) not in expected_quantity_unknown_ids
    )
    assert all(
        observation.identity.species is not None
        and observation.identity.species.phase.is_value
        for observation in observations
    )
    extract = yaml.safe_load(_EXTRACT.read_text(encoding="utf-8"))
    raw_rows = {
        row["observation_id"]: row
        for block in extract["species"].values()
        for row in block.get("observations", [])
    }
    for observation in observations:
        conditions = observation.point_conditions
        assert {"phase_assemblage", "fO2_Pa"} <= set(conditions)
        assert "temperature_K" in conditions or "temperature_C" in conditions
        for condition in conditions.values():
            assert condition.state is not None
            assert condition.locator is not None
            assert condition.locator.page > 0
        raw_conditions = raw_rows[_source_observation_id(observation)]["point_conditions"]
        assert {"phase_assemblage", "fO2_Pa", "total_pressure_Pa"} <= set(raw_conditions)
        assert raw_conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"
        assert raw_conditions["total_pressure_Pa"]["locator"]["page"] > 0
        assert raw_conditions["phase_assemblage"]["locator"]["page"] > 0

    # The source gives no numeric total pressure or oxygen pressure for these rows.
    for observation in observations:
        for axis_name in ("total_pressure_Pa", "fO2_Pa"):
            axis = getattr(observation.identity, axis_name)
            assert axis is None or not axis.is_value

    calorimetry = _find(observations, "dai_1984_table2_feo_heat_evolution_1")
    assert calorimetry.identity.temperature_K.is_value
    assert calorimetry.identity.temperature_K.value == Decimal("313.15")
    assert calorimetry.point_conditions["temperature_C"].state.value == Decimal("40")
    assert calorimetry.point_conditions["temperature_C"].locator.page == 4
    assert calorimetry.locator.table == "II"

    phase_equilibrium = _find(observations, "dai_1984_table7_r1_873_o2_log_pressure")
    assert phase_equilibrium.identity.species.phase.value is Phase.G
    assert phase_equilibrium.identity.temperature_K.value == Decimal("873")
    assert phase_equilibrium.point_conditions["temperature_K"].state.value == Decimal("873")
    assert phase_equilibrium.locator.page == 6
    assert phase_equilibrium.locator.table == "VII"

    table_i_range = _find(observations, "dai_1984_table1_r1_fe2o3")
    assert table_i_range.identity.temperature_K.is_unknown
    assert table_i_range.point_conditions["temperature_K"].state.is_unknown

    table3_na2feo2 = _find(observations, "dai_1984_table3_na2feo2_gibbs_from_oxides")
    assert table3_na2feo2.identity.temperature_K.value == Decimal("1075")
    assert table3_na2feo2.point_conditions["temperature_K"].locator.page == 4
    assert table3_na2feo2.point_conditions["temperature_K"].locator.equation == "30"
    table3_na4feo3 = _find(observations, "dai_1984_table3_na4feo3_gibbs_from_oxides")
    assert table3_na4feo3.identity.temperature_K.value == Decimal("904")
    assert table3_na4feo3.point_conditions["temperature_K"].locator.page == 5
    assert table3_na4feo3.point_conditions["temperature_K"].locator.equation == "33"
    assert table3_na4feo3.identity.composition.is_unknown
    assert "basis and denominator are not printed" in table3_na4feo3.identity.composition.reason
    composition_condition = table3_na4feo3.point_conditions["composition"]
    assert composition_condition.state.is_unknown
    assert composition_condition.locator.page == 5
    assert composition_condition.locator.equation == "33"

    # Independent render pins: the raw printed cells and their page/table locators.
    rendered_cells = (
        (
            _raw_row(extract, "Na2O", "dai_1984_table1_r3_na2o"),
            "equation_as_printed",
            "-559460 + 264.14T",
            3,
            "I",
        ),
        (
            _raw_row(extract, "Na2FeO2", "dai_1984_table2_na2feo2_heat_evolution_2"),
            "value_as_printed",
            "345600",
            4,
            "II",
        ),
        (
            _raw_row(extract, "Na2Fe2O4", "dai_1984_table3_na2fe2o4_gibbs_from_oxides"),
            "table_cell_as_printed",
            "-86000 - 61.89T",
            5,
            "III",
        ),
        (
            _raw_row(extract, "Na2Fe2O4", "dai_1984_table3_na2fe2o4_gibbs_from_oxides"),
            "equation_16_as_printed",
            "-86002 - 61.89T",
            4,
            None,
        ),
        (
            _raw_row(extract, "O2", "dai_1984_table7_r1_873_o2_log_pressure"),
            "value_as_printed",
            "-25.27",
            6,
            "VII",
        ),
    )
    for row, field, expected, page, table in rendered_cells:
        assert row["values"][field] == expected
        if table is None:
            assert row["values"]["equation_16_as_printed"] == expected
        else:
            assert row["locator"]["page"] == page
            assert row["locator"]["table"] == table
    equation_16_sample = next(
        sample
        for sample in extract["fidelity_samples"]
        if sample["path"].endswith("values.equation_16_as_printed")
    )
    assert equation_16_sample["locator"]["page"] == 4
    assert equation_16_sample["locator"]["equation"] == "16"

    oxide_formation = _raw_row(
        extract, "Na4FeO3", "dai_1984_table3_na4feo3_gibbs_from_oxides"
    )
    assert oxide_formation["values"]["melt_Na_fraction_as_printed"] == "0.85"
    assert oxide_formation["values"]["melt_composition_as_printed"] == "0.85 Na-fraction"
    assert oxide_formation["values"]["melt_composition_locator"]["equation"] == "33"
    assert oxide_formation["values"]["table_cell_as_printed"] == "-147988 + 164.80T"
    assert oxide_formation["values"]["equation_34_as_printed"] == "-147998 + 164.8T"
    equation_34_sample = next(
        sample
        for sample in extract["fidelity_samples"]
        if sample["path"].endswith("values.equation_34_as_printed")
    )
    assert equation_34_sample["locator"]["page"] == 5
    assert equation_34_sample["locator"]["equation"] == "34"

    table4_na2feo2 = _raw_row(
        extract, "Na2FeO2", "dai_1984_table4_na2feo2_gibbs_from_elements"
    )
    table4_na4feo3 = _raw_row(
        extract, "Na4FeO3", "dai_1984_table4_na4feo3_gibbs_from_elements"
    )
    assert "dai_1984_table1_r6_na2o" in table4_na2feo2["derivation"]["inputs"]
    assert "dai_1984_table1_r6_na2o" in table4_na4feo3["derivation"]["inputs"]
    assert "dai_1984_table1_r5_na2o" not in table4_na2feo2["derivation"]["inputs"]
    assert "dai_1984_table1_r5_na2o" not in table4_na4feo3["derivation"]["inputs"]

    expected_table_v_inputs = {
        "dai_1984_table5_r1_reaction_gibbs": [
            "dai_1984_table3_na2fe2o4_gibbs_from_oxides",
            "dai_1984_table1_r1_fe2o3",
            "dai_1984_table1_r4_na2o",
        ],
        "dai_1984_table5_r2_reaction_gibbs": [
            "dai_1984_table4_na2feo2_gibbs_from_elements",
            "dai_1984_table1_r4_na2o",
            "dai_1984_table1_r6_na2o",
        ],
        "dai_1984_table5_r3_reaction_gibbs": [
            "dai_1984_table4_na4feo3_gibbs_from_elements",
            "dai_1984_table1_r4_na2o",
            "dai_1984_table1_r6_na2o",
        ],
        "dai_1984_table5_r4_reaction_gibbs": [
            "dai_1984_table3_na2fe2o4_gibbs_from_oxides",
            "dai_1984_table3_na2feo2_gibbs_from_oxides",
            "dai_1984_table1_r1_fe2o3",
            "dai_1984_table1_r2_feo",
        ],
        "dai_1984_table5_r5_reaction_gibbs": [
            "dai_1984_table4_na2feo2_gibbs_from_elements",
            "dai_1984_table3_na2fe2o4_gibbs_from_oxides",
            "dai_1984_table1_r1_fe2o3",
            "dai_1984_table1_r4_na2o",
            "dai_1984_table1_r6_na2o",
        ],
        "dai_1984_table5_r7_reaction_gibbs": [
            "dai_1984_table4_na4feo3_gibbs_from_elements",
            "dai_1984_table4_na2feo2_gibbs_from_elements",
            "dai_1984_table1_r4_na2o",
            "dai_1984_table1_r6_na2o",
        ],
        "dai_1984_table5_r8_reaction_gibbs": [
            "dai_1984_table4_na4feo3_gibbs_from_elements",
            "dai_1984_table3_na2fe2o4_gibbs_from_oxides",
            "dai_1984_table1_r1_fe2o3",
            "dai_1984_table1_r4_na2o",
            "dai_1984_table1_r6_na2o",
        ],
        "dai_1984_table5_r9_reaction_gibbs": [
            "dai_1984_table4_na4feo3_gibbs_from_elements",
            "dai_1984_table4_na2feo2_gibbs_from_elements",
            "dai_1984_table3_na2fe2o4_gibbs_from_oxides",
            "dai_1984_table1_r1_fe2o3",
            "dai_1984_table1_r4_na2o",
            "dai_1984_table1_r6_na2o",
        ],
    }
    expected_table_v_equations = {
        "dai_1984_table5_r1_reaction_gibbs": "-1512440 + 491.31T",
        "dai_1984_table5_r2_reaction_gibbs": "-1004546 + 481.25T",
        "dai_1984_table5_r3_reaction_gibbs": "-1649668 + 837.58T",
        "dai_1984_table5_r4_reaction_gibbs": "-507896 + 10.06T",
        "dai_1984_table5_r5_reaction_gibbs": "-496650 + 471.19T",
        "dai_1984_table5_r7_reaction_gibbs": "-645122 + 356.33T",
        "dai_1984_table5_r8_reaction_gibbs": "-1786896 + 1183.85T",
        "dai_1984_table5_r9_reaction_gibbs": "-1141774 + 827.52T",
    }
    for observation_id, expected_inputs in expected_table_v_inputs.items():
        row = raw_rows[observation_id]
        assert row["derived_from"] == expected_inputs
        assert row["derivation"]["inputs"] == expected_inputs
        assert row["values"]["equation_as_printed"] == expected_table_v_equations[
            observation_id
        ]
    assert "dai_1984_table4_na2fe2o4_gibbs_from_elements" not in (
        raw_rows["dai_1984_table5_r1_reaction_gibbs"]["derived_from"]
    )

    table2_na2feo2_mixing = _raw_row(
        extract, "Na2FeO2", "dai_1984_table2_na2feo2_enthalpy_of_mixing"
    )
    table2_na4feo3_mixing = _raw_row(
        extract, "Na4FeO3", "dai_1984_table2_na4feo3_enthalpy_of_mixing"
    )
    assert table2_na2feo2_mixing["values"]["value_as_printed"] == "-119106"
    assert table2_na2feo2_mixing["values"]["uncertainty_as_printed"] == "±3730 J/mol"
    assert table2_na2feo2_mixing["values"]["reaction_number_as_printed"] == "(21)"
    assert table2_na2feo2_mixing["values"]["reaction_as_printed"] == (
        "Na2O(s) + FeO(s) = Na2FeO2(s)"
    )
    assert table2_na2feo2_mixing["point_conditions"]["phase_assemblage"]["state"]["value"] == (
        "Na2O(s) + FeO(s) = Na2FeO2(s)"
    )
    assert table2_na2feo2_mixing["derivation"]["method"]["locator"]["page"] == 4
    assert table2_na2feo2_mixing["derivation"]["method"]["locator"]["equation"] == "21"
    assert table2_na4feo3_mixing["values"]["value_as_printed"] == "-147988"
    assert table2_na4feo3_mixing["values"]["uncertainty_as_printed"] == "±8210 J/mol"
    assert table2_na4feo3_mixing["values"]["reaction_number_as_printed"] == "(22)"
    assert table2_na4feo3_mixing["values"]["reaction_as_printed"] == (
        "2Na2O(s) + FeO(s) = Na4FeO3(s)"
    )
    assert table2_na4feo3_mixing["point_conditions"]["phase_assemblage"]["state"]["value"] == (
        "2Na2O(s) + FeO(s) = Na4FeO3(s)"
    )
    assert table2_na4feo3_mixing["derivation"]["method"]["locator"]["page"] == 4
    assert table2_na4feo3_mixing["derivation"]["method"]["locator"]["equation"] == "22"
    assert "Na2O average enters twice" in table2_na4feo3_mixing["derivation"]["relation"]

    table4_na2fe2o4 = raw_rows["dai_1984_table4_na2fe2o4_gibbs_from_elements"]
    assert table4_na2fe2o4["values"]["equation_as_printed"] == "-1323130 + 321.90T"
    assert "9920 J/mol constant-term gap" in table4_na2fe2o4["values"]["source_consistency_note"]
    table6_r10 = raw_rows["dai_1984_table6_r10_log_pressure_relation"]
    assert "derived_from" not in table6_r10
    assert table6_r10["values"]["method_class"] == "model_derived"
    assert table6_r10["values"]["relation_as_printed"] == "log(P_O2) = -19385/T - 3.07"
    assert not table6_r10["derivation"]["inputs"]
    assert table6_r10["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"
    assert table6_r10["derivation"]["raw_inputs"]["locator"]["page"] == 5
    assert table6_r10["derivation"]["method"]["locator"]["table"] == "VI"
    assert table6_r10["derivation"]["method"]["locator"]["row"] == "10"
    assert table6_r10["derivation"]["method"]["locator"]["equation"] == (
        "log(P_O2) = -19385/T - 3.07"
    )
    migrated_table6_r10 = _find(
        observations, "dai_1984_table6_r10_log_pressure_relation"
    )
    assert migrated_table6_r10.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    assert not migrated_table6_r10.derived_from
    assert migrated_table6_r10.derivation.declares_unprinted_raw_inputs
    assert migrated_table6_r10.derivation.has_located_unprinted_raw_inputs
    assert migrated_table6_r10.derivation.has_located_method
    assert migrated_table6_r10.derivation.method.state.value == (
        "Table VI pressure functions are calculated from Gibbs energy functions "
        "given in this work; row 10 prints log(P_O2) = -19385/T - 3.07."
    )

    emf_context = extract["species"]["Na2Fe2O4"]["context"]
    assert len(emf_context) == 1
    assert emf_context[0]["type"] == "measurement_context"
    assert emf_context[0]["values"]["admission_status"] == "typed_refusal"
    assert emf_context[0]["values"]["quantity_as_printed"] == "electromotive force (EMF)"
    assert "no electrochemical measurement observation type" in emf_context[0]["values"]["schema_gap"]
    assert emf_context[0]["values"]["equation_as_printed"].startswith("E (mV) = 461.98")
    table3_emf_reduction = _raw_row(
        extract, "Na2Fe2O4", "dai_1984_table3_na2fe2o4_gibbs_from_oxides"
    )
    assert table3_emf_reduction["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"
    assert table3_emf_reduction["derivation"]["raw_inputs"]["locator"]["figure"] == "4"
    assert emf_context[0]["point_conditions"]["reference_melt_composition"]["locator"]["page"] == 1
