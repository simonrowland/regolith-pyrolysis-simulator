from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, SequenceNode, ScalarNode

from simulator.battery.enums import ValueKind
from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "rb86-stebbins-etal-1984"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"

RENDER_PINS = {
    "rb86_scm84_table3_feab_1": ("104233", 7, "3", 3, 2),
    "rb86_scm84_table4_li2sio3_liq_linear_fit": ("93.05", 8, "4", 5, 3),
    "rb86_scm84_table7_feo_liquid_partial_molar_cp": ("78.9", 10, "7", 6, 2),
    "rb86_scm84_table10_fe2sio4_tf_entropy_of_fusion": (
        "8.56",
        13,
        "10",
        2,
        4,
    ),
}

TYPED_REFUSAL_COUNTS = {
    "relative_enthalpy_increment_as_printed": 137,
    "glass_cp_fit_coefficients_context": 29,
    "constant_volume_heat_capacity_over_3R_context": 18,
    "glass_partial_molar_cp_coefficients_context": 9,
    "cp_temperature_dependent_equation_context": 2,
    "relative_enthalpy_fit_equation_context": 1,
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _raw_observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for body in document["species"].values()
        for row in body.get("observations", [])
    }


def _migrated_observations() -> tuple[dict, Migrator]:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return (
        {
            row.observation_id.rsplit("::", 1)[-1]: row
            for row in migrator.result.observations.values()
        },
        migrator,
    )


def _assert_unique_yaml_mapping_keys(node: object, path: str = "$") -> None:
    if isinstance(node, MappingNode):
        keys: set[tuple[str, str]] = set()
        for key_node, value_node in node.value:
            key = (key_node.tag, key_node.value) if isinstance(key_node, ScalarNode) else (key_node.tag, "")
            assert key not in keys, f"duplicate YAML key at {path}: {key[1]}"
            keys.add(key)
            _assert_unique_yaml_mapping_keys(value_node, f"{path}.{key[1]}")
    elif isinstance(node, SequenceNode):
        for index, value_node in enumerate(node.value):
            _assert_unique_yaml_mapping_keys(value_node, f"{path}[{index}]")


def test_render_pins_match_values_and_locators_and_detect_mutation() -> None:
    text = EXTRACT.read_text(encoding="utf-8")
    _assert_unique_yaml_mapping_keys(yaml.compose(text))
    document = yaml.safe_load(text)
    raw = _raw_observations(document)
    samples = {sample["observation_id"]: sample for sample in document["fidelity_samples"]}

    assert check_all_fidelity_samples_match(document) == []
    for observation_id, (value, page, table, row, column) in RENDER_PINS.items():
        observation = raw[observation_id]
        assert observation["values"]["value"] == value
        assert observation["locator"]["page"] == page
        assert observation["locator"]["table"] == table
        assert observation["locator"]["row"] == row
        assert observation["locator"]["column"] == column
        assert samples[observation_id]["value"] == value
        assert samples[observation_id]["locator"] == {
            "page": page,
            "table": table,
            "row": row,
            "column": column,
        }

    changed = deepcopy(document)
    raw_changed = _raw_observations(changed)
    raw_changed["rb86_scm84_table3_feab_1"]["values"]["value"] = "104234"
    assert check_all_fidelity_samples_match(changed)


def test_table_counts_glass_70_and_ab_an_di_composition_assignment() -> None:
    document = _document()
    raw = _raw_observations(document)
    counts = document["extraction"]["table_context"]["printed_row_counts"]
    assert counts == {
        "table_1_sample_compositions": 23,
        "table_2_glass_fit_rows": 29,
        "table_3_relative_enthalpy_points": 136,
        "table_4_linear_fit_rows": 17,
        "table_5_glass_component_rows": 9,
        "table_7_liquid_component_rows": 12,
        "table_8_fit_comparison_rows": 7,
        "table_9_liquid_Cv_over_3R_rows": 18,
        "table_10_species_rows": 17,
    }

    glass_70 = raw["rb86_scm84_table2_4_70"]
    printed_components = dict(
        glass_70["point_conditions"]["composition"]["state"]["value"]["components"]
    )
    assert printed_components == {
        "SiO2": "48.83",
        "TiO2": "8.51",
        "Al2O3": "8.29",
        "MgO": "34.34",
    }
    assert sum(map(Decimal, printed_components.values())) == Decimal("99.97")

    ternary_groups = {
        "ab75an0di25": (1, 7, "ab 75 an 0 di 25", "75", "0", "25"),
        "ab50an0di50": (1, 9, "ab 50 an 0 di 50", "50", "0", "50"),
        "ab0an35di65": (1, 11, "ab 0 an 35 di 65", "0", "35", "65"),
        "ab40an35di25": (19, 7, "ab 40 an 35 di 25", "40", "35", "25"),
        "ab40an20di40": (19, 9, "ab 40 an 20 di 40", "40", "20", "40"),
        "ab15an10di75": (19, 11, "ab 15 an 10 di 75", "15", "10", "75"),
        "ab20an60di20": (32, 7, "ab 20 an 60 di 20", "20", "60", "20"),
        "ab0an50di50": (32, 9, "ab 0 an 50 di 50", "0", "50", "50"),
        "ab25an0di75": (32, 11, "ab 25 an 0 di 75", "25", "0", "75"),
    }
    for series, (header_row, header_column, heading, ab, an, di) in ternary_groups.items():
        observations = [
            row
            for observation_id, row in raw.items()
            if observation_id.startswith(f"rb86_scm84_table3_{series}_")
        ]
        assert observations
        for observation in observations:
            assert observation["values"]["composition_as_printed"] == heading
            composition_locator = observation["point_conditions"]["composition"]["locator"]
            assert composition_locator["page"] == 7
            assert composition_locator["table"] == "3"
            assert composition_locator["row"] == header_row
            assert composition_locator["column"] == header_column
            derivation = observation["values"]["composition_derivation"]
            assert f"Ab {ab} mol %, An {an} mol %, Di {di} mol %" in derivation


def test_isolated_migration_has_only_typed_refusals_and_preserves_axes() -> None:
    document = _document()
    raw = _raw_observations(document)
    migrated, migrator = _migrated_observations()
    result = migrator.result

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(migrated) == 275
    assert sum(row.value.kind is ValueKind.POINT for row in migrated.values()) == 79
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values()) == 196

    expected_unknown = {
        observation_id
        for observation_id, observation in raw.items()
        if observation["values"].get("quantity") in TYPED_REFUSAL_COUNTS
    }
    actual_unknown = {
        observation_id
        for observation_id, observation in migrated.items()
        if observation.identity.quantity is None or observation.identity.quantity.is_unknown
    }
    assert actual_unknown == expected_unknown
    assert {
        quantity: sum(
            raw[observation_id]["values"].get("quantity") == quantity
            for observation_id in actual_unknown
        )
        for quantity in TYPED_REFUSAL_COUNTS
    } == TYPED_REFUSAL_COUNTS
    assert all(
        observation.identity.species.phase is not None
        and observation.identity.species.phase.is_value
        for observation in migrated.values()
    )

    for observation_id, source in raw.items():
        conditions = source["point_conditions"]
        assert set(conditions) >= {
            "temperature_K",
            "fO2_Pa",
            "total_pressure_Pa",
            "composition",
        }
        for axis in ("temperature_K", "fO2_Pa", "total_pressure_Pa", "composition"):
            condition = conditions[axis]
            assert condition["state"]["tag"] in {"value", "unknown"}
            assert condition["locator"]["page"]

        # This paper prints no total pressure or absolute oxygen fugacity.
        for axis in ("fO2_Pa", "total_pressure_Pa"):
            assert conditions[axis]["state"]["tag"] == "unknown"
            identity_state = getattr(migrated[observation_id].identity, axis)
            assert identity_state is None or not identity_state.is_value

    glass_rows = [
        row
        for observation_id, row in raw.items()
        if observation_id.startswith("rb86_scm84_table2_")
    ]
    assert len(glass_rows) == 29
    for observation in glass_rows:
        quench = observation["point_conditions"]["quench_state"]
        assert quench["state"]["value"] == "not_stated"
        assert quench["locator"]["page"] == 3

    glass_70_condition = migrated["rb86_scm84_table2_4_70"].point_conditions[
        "composition"
    ]
    assert glass_70_condition.state.is_unknown
    assert glass_70_condition.state.reason == (
        "mole-percent composition does not close to 100 (sum=99.97)"
    )

    ternary = migrated["rb86_scm84_table2_19_ab_12_5_an_12_5"]
    composition = ternary.point_conditions["composition"].state.value.as_map()
    assert composition == {
        "SiO2": Decimal("0.53125"),
        "Na2O": Decimal("0.015625"),
        "Al2O3": Decimal("0.046875"),
        "CaO": Decimal("0.21875"),
        "MgO": Decimal("0.1875"),
    }
    assert sum(composition.values()) == Decimal(1)

    fitted_cp = migrated["rb86_scm84_table4_feab_linear_fit"]
    cp_profile = profile_for(fitted_cp.identity)
    assert {"temperature_K", "standard_pressure_Pa", "per"} <= cp_profile.required
    assert fitted_cp.identity.temperature_K.is_unknown
    assert fitted_cp.identity.temperature_K.reason == (
        "source T_range_K [1570, 1826]; no midpoint invented"
    )
    assert fitted_cp.identity.composition.is_not_applicable
    assert fitted_cp.identity.total_pressure_Pa.is_not_applicable
    assert fitted_cp.identity.fO2_Pa.is_not_applicable

    fayalite_migrated = migrated[
        "rb86_scm84_table10_fe2sio4_tf_entropy_of_fusion"
    ]
    fayalite_profile = profile_for(fayalite_migrated.identity)
    assert {"subtype", "temperature_K"} <= fayalite_profile.required
    assert fayalite_migrated.identity.composition.is_unknown
    assert fayalite_migrated.identity.temperature_K.is_value
    assert fayalite_migrated.identity.temperature_K.value == Decimal("1490")
    assert fayalite_migrated.identity.total_pressure_Pa.is_not_applicable
    assert fayalite_migrated.identity.fO2_Pa.is_not_applicable

    mgsio3_fusion = migrated[
        "rb86_scm84_appendix1_mgsio3_heat_of_fusion_authors_solid_data"
    ]
    mgsio3_profile = profile_for(mgsio3_fusion.identity)
    assert {"composition", "subtype", "temperature_K"} <= mgsio3_profile.required
    assert mgsio3_fusion.identity.composition.is_value
    assert mgsio3_fusion.identity.composition.value.as_map() == {
        "MgO": Decimal("0.5"),
        "SiO2": Decimal("0.5"),
    }
    assert mgsio3_fusion.identity.temperature_K.is_unknown
    assert mgsio3_fusion.identity.total_pressure_Pa.is_not_applicable
    assert mgsio3_fusion.identity.fO2_Pa.is_not_applicable

    fayalite = raw["rb86_scm84_table10_fe2sio4_tf_entropy_of_fusion"]
    assert fayalite["values"]["value_as_printed"] == "$8.56±0.11^1$"
    assert fayalite["uncertainty"]["value"] == "0.11"
    assert fayalite["uncertainty"]["confidence_level"] == "not_stated"
    assert fayalite["values"]["source_attribution_as_printed"] == (
        "Stebbins & Carmichael 1983 (fayalite)"
    )
    assert "FeAl2O4" not in document["species"]
